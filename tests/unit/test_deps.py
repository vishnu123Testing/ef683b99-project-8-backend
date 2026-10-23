"""Unit tests for FastAPI security dependencies in backend/src/api/deps.py."""

from datetime import timedelta
import io
import uuid

from fastapi import HTTPException, status
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.api.deps import (
    RequireRole,
    create_access_token,
    decode_access_token,
    evaluate_request_access,
    get_current_access_context,
    get_current_user,
    require_tenant_access,
)
from src.models.database import Base
from src.models.role import (
    ROLE_CLIENT_ADMIN,
    ROLE_CLIENT_VIEWER,
    ROLE_SYSTEM_ADMIN,
    Role,
)
from src.models.tenant import Tenant
from src.models.user import User, hash_password
from src.services.access_control.rbac import UserAccessContext, assign_role_to_user


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session with foreign key constraints enabled."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()

    # Pre-seed system roles
    for role_name in (ROLE_SYSTEM_ADMIN, ROLE_CLIENT_ADMIN, ROLE_CLIENT_VIEWER):
        session.add(Role(name=role_name, description=f"{role_name} description"))
    session.commit()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def sample_user(db_session: Session) -> User:
    """Fixture supplying a tenant and user for dependency testing."""
    tenant = Tenant(name="Test Security Corp")
    db_session.add(tenant)
    db_session.commit()

    user = User(
        email="security.user@test.com",
        password_hash=hash_password("SecurePass123!"),
        tenant_id=tenant.tenant_id,
    )
    db_session.add(user)
    db_session.commit()

    assign_role_to_user(
        db_session, user.user_id, ROLE_CLIENT_VIEWER, tenant.tenant_id
    )
    return user


def test_create_and_decode_access_token_success():
    """Verify that create_access_token produces a token decoded correctly by decode_access_token."""
    user_id_str = str(uuid.uuid4())
    tenant_id_str = str(uuid.uuid4())
    payload = {"sub": user_id_str, "tenant_id": tenant_id_str}

    token = create_access_token(data=payload)
    decoded = decode_access_token(token)

    assert decoded["sub"] == user_id_str
    assert decoded["tenant_id"] == tenant_id_str
    assert "exp" in decoded
    assert "iat" in decoded


def test_decode_access_token_expired():
    """Verify that expired access tokens raise 401 Unauthorized."""
    payload = {"sub": str(uuid.uuid4())}
    token = create_access_token(data=payload, expires_delta=timedelta(seconds=-10))

    with pytest.raises(HTTPException) as exc_info:
        decode_access_token(token)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
    assert "expired" in exc_info.value.detail.lower()


def test_decode_access_token_invalid():
    """Verify that malformed or tampered tokens raise 401 Unauthorized."""
    with pytest.raises(HTTPException) as exc_info:
        decode_access_token("not.a.valid.jwt.token")

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_current_user_success(db_session: Session, sample_user: User):
    """Verify get_current_user extracts the user from a valid bearer token."""
    token = create_access_token({"sub": str(sample_user.user_id)})
    user = get_current_user(token=token, db=db_session)

    assert user.user_id == sample_user.user_id
    assert user.email == "security.user@test.com"


def test_get_current_user_missing_token(db_session: Session):
    """Verify get_current_user raises 401 when no token is provided."""
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token=None, db=db_session)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_current_user_missing_subject(db_session: Session):
    """Verify get_current_user raises 401 when token payload lacks sub claim."""
    token = create_access_token({"custom_field": "no_sub"})
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token=token, db=db_session)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_current_user_invalid_uuid(db_session: Session):
    """Verify get_current_user raises 401 when sub claim is not a valid UUID."""
    token = create_access_token({"sub": "invalid-uuid-string"})
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token=token, db=db_session)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_current_user_user_not_found(db_session: Session):
    """Verify get_current_user raises 401 when sub UUID matches no database user."""
    unknown_uuid = str(uuid.uuid4())
    token = create_access_token({"sub": unknown_uuid})
    with pytest.raises(HTTPException) as exc_info:
        get_current_user(token=token, db=db_session)

    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED


def test_get_current_access_context(db_session: Session, sample_user: User):
    """Verify get_current_access_context retrieves tenant and assigned roles."""
    context = get_current_access_context(current_user=sample_user, db=db_session)

    assert context.user_id == sample_user.user_id
    assert context.tenant_id == sample_user.tenant_id
    assert ROLE_CLIENT_VIEWER in context.roles


def test_require_role_permitted(db_session: Session, sample_user: User):
    """Verify RequireRole passes when user holds the allowed role."""
    context = UserAccessContext(
        user_id=sample_user.user_id,
        tenant_id=sample_user.tenant_id,
        roles=[ROLE_CLIENT_VIEWER],
    )
    checker = RequireRole(ROLE_CLIENT_VIEWER)
    res = checker(context=context, request=None, db=db_session)
    assert res == context


def test_require_role_denied(db_session: Session, sample_user: User):
    """Verify RequireRole raises 403 Forbidden when user lacks required role."""
    context = UserAccessContext(
        user_id=sample_user.user_id,
        tenant_id=sample_user.tenant_id,
        roles=[ROLE_CLIENT_VIEWER],
    )
    checker = RequireRole(ROLE_CLIENT_ADMIN)

    with pytest.raises(HTTPException) as exc_info:
        checker(context=context, request=None, db=db_session)

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_require_tenant_access_same_tenant(db_session: Session, sample_user: User):
    """Verify require_tenant_access permits access within the user's tenant."""
    context = UserAccessContext(
        user_id=sample_user.user_id,
        tenant_id=sample_user.tenant_id,
        roles=[ROLE_CLIENT_VIEWER],
    )
    res = require_tenant_access(
        target_tenant_id=sample_user.tenant_id,
        context=context,
        request=None,
        db=db_session,
    )
    assert res == context


def test_require_tenant_access_cross_tenant_denied(db_session: Session, sample_user: User):
    """Verify require_tenant_access raises 403 Forbidden for cross-tenant attempts."""
    other_tenant_id = uuid.uuid4()
    context = UserAccessContext(
        user_id=sample_user.user_id,
        tenant_id=sample_user.tenant_id,
        roles=[ROLE_CLIENT_VIEWER],
    )

    with pytest.raises(HTTPException) as exc_info:
        require_tenant_access(
            target_tenant_id=other_tenant_id,
            context=context,
            request=None,
            db=db_session,
        )

    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN


def test_require_tenant_access_system_admin_allowed(db_session: Session, sample_user: User):
    """Verify SystemAdmin role bypasses single tenant boundary restriction."""
    other_tenant_id = uuid.uuid4()
    context = UserAccessContext(
        user_id=sample_user.user_id,
        tenant_id=sample_user.tenant_id,
        roles=[ROLE_SYSTEM_ADMIN],
    )
    res = require_tenant_access(
        target_tenant_id=other_tenant_id,
        context=context,
        request=None,
        db=db_session,
    )
    assert res == context


def test_evaluate_request_access_success_and_failures(db_session: Session, sample_user: User):
    """Verify evaluate_request_access handles valid evaluation and raises appropriate HTTP exceptions."""
    # 1. Success
    ctx = evaluate_request_access(
        db=db_session,
        user_id=sample_user.user_id,
        target_tenant_id=sample_user.tenant_id,
        required_roles=ROLE_CLIENT_VIEWER,
    )
    assert ctx.user_id == sample_user.user_id

    # 2. Cross-tenant violation -> 403
    other_tenant = uuid.uuid4()
    with pytest.raises(HTTPException) as exc_info:
        evaluate_request_access(
            db=db_session,
            user_id=sample_user.user_id,
            target_tenant_id=other_tenant,
        )
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    # 3. Role requirement failure -> 403
    with pytest.raises(HTTPException) as exc_info:
        evaluate_request_access(
            db=db_session,
            user_id=sample_user.user_id,
            target_tenant_id=sample_user.tenant_id,
            required_roles=ROLE_CLIENT_ADMIN,
        )
    assert exc_info.value.status_code == status.HTTP_403_FORBIDDEN

    # 4. User not found -> 401
    missing_user_id = uuid.uuid4()
    with pytest.raises(HTTPException) as exc_info:
        evaluate_request_access(
            db=db_session,
            user_id=missing_user_id,
            target_tenant_id=sample_user.tenant_id,
        )
    assert exc_info.value.status_code == status.HTTP_401_UNAUTHORIZED
