"""FastAPI security dependencies for bearer token authentication and RBAC validation."""

from datetime import datetime, timedelta, timezone
from typing import Any, Sequence
import uuid

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import OAuth2PasswordBearer
import jwt
from sqlalchemy.orm import Session

from src.lib.config import settings
from src.models.database import get_db
from src.models.user import User
from src.services.access_control.audit import AuditService
from src.services.access_control.rbac import (
    AccessDeniedError,
    TenantBoundaryViolationError,
    UserAccessContext,
    UserNotFoundError,
    evaluate_access,
    get_user_access_context,
    validate_tenant_boundary,
)

oauth2_scheme = OAuth2PasswordBearer(
    tokenUrl="/api/v1/auth/login",
    auto_error=False,
)

audit_service = AuditService()


def create_access_token(
    data: dict[str, Any],
    expires_delta: timedelta | None = None,
) -> str:
    """Generate a signed JWT access token with expiration and issue timestamp claims."""
    to_encode = data.copy()
    now = datetime.now(timezone.utc)
    if expires_delta:
        expire = now + expires_delta
    else:
        expire = now + timedelta(minutes=settings.access_token_expire_minutes)

    to_encode.update(
        {
            "exp": expire,
            "iat": now,
        }
    )
    encoded_jwt = jwt.encode(
        to_encode,
        settings.secret_key,
        algorithm=settings.algorithm,
    )
    return encoded_jwt


def decode_access_token(token: str) -> dict[str, Any]:
    """Decode and validate a JWT access token.

    Raises HTTPException 401 Unauthorized if token signature is invalid or expired.
    """
    try:
        payload = jwt.decode(
            token,
            settings.secret_key,
            algorithms=[settings.algorithm],
        )
        return payload
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired authentication token",
            headers={"WWW-Authenticate": "Bearer"},
        )


def get_current_user(
    token: str | None = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """Extract, decode bearer token, and retrieve authenticated User entity.

    Raises HTTPException 401 Unauthorized if token is missing, invalid, or user not found.
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication token required",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    user_id_str = payload.get("sub") or payload.get("user_id")
    if not user_id_str:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token subject claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        user_uuid = uuid.UUID(str(user_id_str))
    except (ValueError, TypeError):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid user ID format in token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user = db.query(User).filter(User.user_id == user_uuid).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authenticated user not found",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return user


def get_current_access_context(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> UserAccessContext:
    """Construct UserAccessContext encapsulating user ID, tenant ID, and assigned roles."""
    try:
        return get_user_access_context(db, user_id=current_user.user_id)
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )


class RequireRole:
    """FastAPI dependency for verifying user role authorization."""

    def __init__(self, allowed_roles: str | Sequence[str]) -> None:
        self.allowed_roles = allowed_roles

    def __call__(
        self,
        context: UserAccessContext = Depends(get_current_access_context),
        request: Request = None,
        db: Session = Depends(get_db),
    ) -> UserAccessContext:
        if not context.has_role(self.allowed_roles):
            client_ip = request.client.host if (request and request.client) else None
            resource_id = request.url.path if request else None
            audit_service.log_event(
                action="RBAC_CHECK",
                outcome_status="DENIED",
                user_id=context.user_id,
                assigned_role=",".join(context.roles) if context.roles else None,
                tenant_id=context.tenant_id,
                resource_id=resource_id,
                client_ip=client_ip,
                db=db,
            )
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Forbidden: required role {self.allowed_roles} missing",
            )
        return context


def require_tenant_access(
    target_tenant_id: uuid.UUID,
    context: UserAccessContext = Depends(get_current_access_context),
    request: Request = None,
    db: Session = Depends(get_db),
) -> UserAccessContext:
    """Enforce tenant isolation boundary check against target tenant."""
    try:
        validate_tenant_boundary(
            user_tenant_id=context.tenant_id,
            target_tenant_id=target_tenant_id,
            roles=context.roles,
        )
        return context
    except TenantBoundaryViolationError as exc:
        client_ip = request.client.host if (request and request.client) else None
        resource_id = request.url.path if request else str(target_tenant_id)
        audit_service.log_event(
            action="TENANT_BOUNDARY_CHECK",
            outcome_status="DENIED",
            user_id=context.user_id,
            assigned_role=",".join(context.roles) if context.roles else None,
            tenant_id=context.tenant_id,
            resource_id=resource_id,
            client_ip=client_ip,
            db=db,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )


def evaluate_request_access(
    db: Session,
    user_id: uuid.UUID,
    target_tenant_id: uuid.UUID,
    required_roles: str | Sequence[str] | None = None,
    request: Request = None,
) -> UserAccessContext:
    """Evaluate access via sub-20ms RBAC engine and translate errors to HTTP status codes."""
    try:
        context = evaluate_access(
            db=db,
            user_id=user_id,
            target_tenant_id=target_tenant_id,
            required_roles=required_roles,
        )
        return context
    except TenantBoundaryViolationError as exc:
        client_ip = request.client.host if (request and request.client) else None
        resource_id = request.url.path if request else str(target_tenant_id)
        audit_service.log_event(
            action="RBAC_EVALUATION",
            outcome_status="DENIED",
            user_id=user_id,
            tenant_id=target_tenant_id,
            resource_id=resource_id,
            client_ip=client_ip,
            db=db,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except AccessDeniedError as exc:
        client_ip = request.client.host if (request and request.client) else None
        resource_id = request.url.path if request else None
        audit_service.log_event(
            action="RBAC_EVALUATION",
            outcome_status="DENIED",
            user_id=user_id,
            tenant_id=target_tenant_id,
            resource_id=resource_id,
            client_ip=client_ip,
            db=db,
        )
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )
    except UserNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(exc),
            headers={"WWW-Authenticate": "Bearer"},
        )
