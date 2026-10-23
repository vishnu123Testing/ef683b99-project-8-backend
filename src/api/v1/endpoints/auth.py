"""Authentication endpoints for user registration, login, and active identity lookup."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.api.deps import create_access_token, get_current_user
from src.models.database import get_db
from src.models.role import ROLE_CLIENT_VIEWER, Role
from src.models.tenant import Tenant
from src.models.user import User, hash_password
from src.services.access_control.audit import AuditService
from src.services.access_control.rbac import assign_role_to_user, get_user_roles

router = APIRouter(prefix="/auth", tags=["Authentication"])
audit_service = AuditService()


class UserRegisterRequest(BaseModel):
    """Payload schema for user registration."""

    email: EmailStr
    password: str = Field(min_length=6, description="Password (minimum 6 characters)")
    tenant_id: uuid.UUID | None = Field(
        default=None,
        description="Optional tenant ID to bind user to",
    )


class UserRegisterResponse(BaseModel):
    """Response schema for successful user registration."""

    user_id: uuid.UUID
    email: str
    tenant_id: uuid.UUID
    roles: list[str] = Field(default_factory=list)


class UserLoginRequest(BaseModel):
    """Payload schema for user authentication."""

    email: str | None = Field(default=None, description="User email address")
    username: str | None = Field(default=None, description="Alternative email alias field")
    password: str = Field(..., description="User plain text password")


class UserLoginResponse(BaseModel):
    """Response schema for successful authentication with bearer token."""

    access_token: str
    token_type: str = "bearer"
    user_id: uuid.UUID
    tenant_id: uuid.UUID


class UserMeResponse(BaseModel):
    """Response schema for active identity lookup."""

    user_id: uuid.UUID
    email: str
    tenant_id: uuid.UUID
    roles: list[str] = Field(default_factory=list)


@router.post(
    "/register",
    response_model=UserRegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account with default ClientViewer role",
)
def register(
    payload: UserRegisterRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> UserRegisterResponse:
    """Register a new user account, assign default ClientViewer role, and log audit event.

    Returns HTTP 409 Conflict if email is already registered.
    """
    client_ip = request.client.host if request and request.client else None
    email_clean = payload.email.strip().lower()

    # 1. Check for duplicate email in database
    existing_user = db.query(User).filter(User.email == email_clean).first()
    if existing_user is not None:
        audit_service.log_event(
            action="USER_REGISTER",
            outcome_status="CONFLICT",
            user_id=None,
            tenant_id=payload.tenant_id,
            resource_id="/api/v1/auth/register",
            client_ip=client_ip,
            db=db,
            extra={"reason": "Duplicate email", "email": email_clean},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email address already exists.",
        )

    # 2. Resolve or create tenant
    tenant: Tenant | None = None
    if payload.tenant_id is not None:
        tenant = db.query(Tenant).filter(Tenant.tenant_id == payload.tenant_id).first()
        if tenant is None:
            tenant = Tenant(tenant_id=payload.tenant_id, name=f"Tenant-{payload.tenant_id}")
            db.add(tenant)
            db.flush()
    else:
        tenant = db.query(Tenant).first()
        if tenant is None:
            tenant = Tenant(name="Default Tenant")
            db.add(tenant)
            db.flush()

    # 3. Create user entity
    try:
        user = User(
            email=email_clean,
            password_hash=hash_password(payload.password),
            tenant_id=tenant.tenant_id,
        )
        db.add(user)
        db.flush()

        # 4. Ensure ClientViewer role exists and assign it
        role = db.query(Role).filter(Role.name == ROLE_CLIENT_VIEWER).first()
        if role is None:
            role = Role(name=ROLE_CLIENT_VIEWER, description="ClientViewer default role")
            db.add(role)
            db.flush()

        assign_role_to_user(
            db=db,
            user_id=user.user_id,
            role_name=ROLE_CLIENT_VIEWER,
            tenant_id=tenant.tenant_id,
        )
        db.commit()
        db.refresh(user)
    except IntegrityError:
        db.rollback()
        audit_service.log_event(
            action="USER_REGISTER",
            outcome_status="CONFLICT",
            user_id=None,
            tenant_id=tenant.tenant_id if tenant else None,
            resource_id="/api/v1/auth/register",
            client_ip=client_ip,
            db=db,
            extra={"reason": "Integrity constraint failure / duplicate email"},
        )
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email address already exists.",
        )

    # 5. Emit successful registration audit event
    audit_service.log_event(
        action="USER_REGISTER",
        outcome_status="SUCCESS",
        user_id=user.user_id,
        assigned_role=ROLE_CLIENT_VIEWER,
        tenant_id=tenant.tenant_id,
        resource_id="/api/v1/auth/register",
        client_ip=client_ip,
        db=db,
    )

    return UserRegisterResponse(
        user_id=user.user_id,
        email=user.email,
        tenant_id=tenant.tenant_id,
        roles=[ROLE_CLIENT_VIEWER],
    )


@router.post(
    "/login",
    response_model=UserLoginResponse,
    status_code=status.HTTP_200_OK,
    summary="Authenticate user and return access bearer token",
)
def login(
    payload: UserLoginRequest,
    request: Request,
    db: Session = Depends(get_db),
) -> UserLoginResponse:
    """Authenticate user credentials, return signed JWT bearer token, and emit audit event.

    Returns HTTP 401 Unauthorized for invalid credentials.
    """
    client_ip = request.client.host if request and request.client else None
    email_input = payload.email or payload.username
    if not email_input:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email address is required.",
        )

    email_clean = email_input.strip().lower()
    user = db.query(User).filter(User.email == email_clean).first()

    if user is None or not user.check_password(payload.password):
        audit_service.log_event(
            action="LOGIN",
            outcome_status="FAILURE",
            user_id=user.user_id if user else None,
            tenant_id=user.tenant_id if user else None,
            resource_id="/api/v1/auth/login",
            client_ip=client_ip,
            db=db,
            extra={"email": email_clean},
        )
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    roles = get_user_roles(db, user_id=user.user_id, tenant_id=user.tenant_id)
    access_token = create_access_token(
        data={
            "sub": str(user.user_id),
            "tenant_id": str(user.tenant_id),
            "email": user.email,
        }
    )

    audit_service.log_event(
        action="LOGIN",
        outcome_status="SUCCESS",
        user_id=user.user_id,
        assigned_role=",".join(roles) if roles else None,
        tenant_id=user.tenant_id,
        resource_id="/api/v1/auth/login",
        client_ip=client_ip,
        db=db,
    )

    return UserLoginResponse(
        access_token=access_token,
        token_type="bearer",
        user_id=user.user_id,
        tenant_id=user.tenant_id,
    )


@router.get(
    "/me",
    response_model=UserMeResponse,
    status_code=status.HTTP_200_OK,
    summary="Retrieve active identity user profile and assigned roles",
)
def get_active_identity(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserMeResponse:
    """Retrieve active authenticated identity details and assigned server-side roles."""
    client_ip = request.client.host if request and request.client else None
    roles = get_user_roles(db, user_id=current_user.user_id, tenant_id=current_user.tenant_id)

    audit_service.log_event(
        action="IDENTITY_LOOKUP",
        outcome_status="SUCCESS",
        user_id=current_user.user_id,
        assigned_role=",".join(roles) if roles else None,
        tenant_id=current_user.tenant_id,
        resource_id="/api/v1/auth/me",
        client_ip=client_ip,
        db=db,
    )

    return UserMeResponse(
        user_id=current_user.user_id,
        email=current_user.email,
        tenant_id=current_user.tenant_id,
        roles=roles,
    )
