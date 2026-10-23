"""Unit tests for RBAC policy evaluation engine in backend/src/services/access_control/rbac.py."""

import time
import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.role import (
    ROLE_CLIENT_ADMIN,
    ROLE_CLIENT_VIEWER,
    ROLE_SYSTEM_ADMIN,
    Role,
    UserRole,
)
from src.models.tenant import Tenant
from src.models.user import User, hash_password
from src.services.access_control.rbac import (
    AccessDeniedError,
    RBACError,
    TenantBoundaryViolationError,
    UserAccessContext,
    UserNotFoundError,
    assign_role_to_user,
    check_route_access,
    evaluate_access,
    get_user_access_context,
    get_user_roles,
    has_role,
    validate_tenant_boundary,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session with foreign key support."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()

    # Pre-seed default system roles
    for role_name in (ROLE_SYSTEM_ADMIN, ROLE_CLIENT_ADMIN, ROLE_CLIENT_VIEWER):
        session.add(Role(name=role_name, description=f"{role_name} role"))
    session.commit()

    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def sample_data(db_session: Session):
    """Fixture supplying sample tenants, users, and assigned roles for testing."""
    tenant1 = Tenant(name="Alpha Corp")
    tenant2 = Tenant(name="Beta Industries")
    db_session.add_all([tenant1, tenant2])
    db_session.commit()

    user_client_viewer = User(
        email="viewer@alpha.com",
        password_hash=hash_password("Pass123!"),
        tenant_id=tenant1.tenant_id,
    )
    user_client_admin = User(
        email="admin@alpha.com",
        password_hash=hash_password("Pass123!"),
        tenant_id=tenant1.tenant_id,
    )
    user_system_admin = User(
        email="sysadmin@platform.com",
        password_hash=hash_password("Pass123!"),
        tenant_id=tenant1.tenant_id,
    )
    user_beta_viewer = User(
        email="viewer@beta.com",
        password_hash=hash_password("Pass123!"),
        tenant_id=tenant2.tenant_id,
    )

    db_session.add_all(
        [user_client_viewer, user_client_admin, user_system_admin, user_beta_viewer]
    )
    db_session.commit()

    # Assign roles
    assign_role_to_user(
        db_session, user_client_viewer.user_id, ROLE_CLIENT_VIEWER, tenant1.tenant_id
    )
    assign_role_to_user(
        db_session, user_client_admin.user_id, ROLE_CLIENT_ADMIN, tenant1.tenant_id
    )
    assign_role_to_user(
        db_session, user_system_admin.user_id, ROLE_SYSTEM_ADMIN, tenant1.tenant_id
    )
    assign_role_to_user(
        db_session, user_beta_viewer.user_id, ROLE_CLIENT_VIEWER, tenant2.tenant_id
    )

    return {
        "tenant1": tenant1,
        "tenant2": tenant2,
        "user_client_viewer": user_client_viewer,
        "user_client_admin": user_client_admin,
        "user_system_admin": user_system_admin,
        "user_beta_viewer": user_beta_viewer,
    }


def test_user_access_context_has_role():
    """Test UserAccessContext.has_role method logic."""
    ctx = UserAccessContext(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        roles=[ROLE_CLIENT_VIEWER],
    )
    assert ctx.has_role(ROLE_CLIENT_VIEWER) is True
    assert ctx.has_role([ROLE_CLIENT_ADMIN, ROLE_CLIENT_VIEWER]) is True
    assert ctx.has_role(ROLE_CLIENT_ADMIN) is False

    # SystemAdmin overrides
    sys_ctx = UserAccessContext(
        user_id=uuid.uuid4(),
        tenant_id=uuid.uuid4(),
        roles=[ROLE_SYSTEM_ADMIN],
    )
    assert sys_ctx.has_role(ROLE_CLIENT_ADMIN) is True
    assert sys_ctx.has_role("AnyArbitraryRole") is True


def test_get_user_roles(db_session: Session, sample_data: dict):
    """Test retrieving assigned role names for a user."""
    viewer = sample_data["user_client_viewer"]
    tenant1 = sample_data["tenant1"]

    roles = get_user_roles(db_session, user_id=viewer.user_id)
    assert roles == [ROLE_CLIENT_VIEWER]

    roles_filtered = get_user_roles(
        db_session, user_id=viewer.user_id, tenant_id=tenant1.tenant_id
    )
    assert roles_filtered == [ROLE_CLIENT_VIEWER]

    roles_other_tenant = get_user_roles(
        db_session, user_id=viewer.user_id, tenant_id=uuid.uuid4()
    )
    assert roles_other_tenant == []


def test_get_user_access_context(db_session: Session, sample_data: dict):
    """Test building UserAccessContext for valid and missing users."""
    admin = sample_data["user_client_admin"]
    ctx = get_user_access_context(db_session, user_id=admin.user_id)

    assert ctx.user_id == admin.user_id
    assert ctx.tenant_id == admin.tenant_id
    assert ctx.roles == [ROLE_CLIENT_ADMIN]

    with pytest.raises(UserNotFoundError):
        get_user_access_context(db_session, user_id=uuid.uuid4())


def test_validate_tenant_boundary():
    """Test tenant isolation boundary enforcement logic."""
    t1 = uuid.uuid4()
    t2 = uuid.uuid4()

    # Same tenant
    assert validate_tenant_boundary(t1, t1, [ROLE_CLIENT_VIEWER]) is True

    # SystemAdmin cross-tenant
    assert validate_tenant_boundary(t1, t2, [ROLE_SYSTEM_ADMIN]) is True

    # Non-SystemAdmin cross-tenant
    with pytest.raises(TenantBoundaryViolationError):
        validate_tenant_boundary(t1, t2, [ROLE_CLIENT_ADMIN])


def test_has_role():
    """Test has_role function for single and list role requirements."""
    assert has_role([ROLE_CLIENT_ADMIN], ROLE_CLIENT_ADMIN) is True
    assert has_role([ROLE_CLIENT_VIEWER], [ROLE_CLIENT_ADMIN, ROLE_CLIENT_VIEWER]) is True
    assert has_role([ROLE_CLIENT_VIEWER], ROLE_CLIENT_ADMIN) is False
    assert has_role([ROLE_SYSTEM_ADMIN], ROLE_CLIENT_ADMIN) is True


def test_evaluate_access_authorized(db_session: Session, sample_data: dict):
    """Test successful evaluation of role and tenant access within sub-20ms threshold."""
    viewer = sample_data["user_client_viewer"]
    tenant1 = sample_data["tenant1"]

    start = time.perf_counter()
    ctx = evaluate_access(
        db_session,
        user_id=viewer.user_id,
        target_tenant_id=tenant1.tenant_id,
        required_roles=ROLE_CLIENT_VIEWER,
    )
    elapsed_ms = (time.perf_counter() - start) * 1000.0

    assert ctx.user_id == viewer.user_id
    assert ctx.tenant_id == tenant1.tenant_id
    assert elapsed_ms < 20.0, f"Role evaluation took {elapsed_ms:.2f}ms, expected < 20ms"


def test_evaluate_access_cross_tenant_rejection(db_session: Session, sample_data: dict):
    """Test that cross-tenant access attempts are rejected with TenantBoundaryViolationError."""
    viewer = sample_data["user_client_viewer"]
    tenant2 = sample_data["tenant2"]

    with pytest.raises(TenantBoundaryViolationError):
        evaluate_access(
            db_session,
            user_id=viewer.user_id,
            target_tenant_id=tenant2.tenant_id,
            required_roles=ROLE_CLIENT_VIEWER,
        )


def test_evaluate_access_insufficient_role_rejection(db_session: Session, sample_data: dict):
    """Test that insufficient roles trigger AccessDeniedError within authorized tenant."""
    viewer = sample_data["user_client_viewer"]
    tenant1 = sample_data["tenant1"]

    with pytest.raises(AccessDeniedError):
        evaluate_access(
            db_session,
            user_id=viewer.user_id,
            target_tenant_id=tenant1.tenant_id,
            required_roles=ROLE_CLIENT_ADMIN,
        )


def test_evaluate_access_system_admin_cross_tenant(db_session: Session, sample_data: dict):
    """Test that SystemAdmin can access cross-tenant resources."""
    sysadmin = sample_data["user_system_admin"]
    tenant2 = sample_data["tenant2"]

    ctx = evaluate_access(
        db_session,
        user_id=sysadmin.user_id,
        target_tenant_id=tenant2.tenant_id,
        required_roles=ROLE_CLIENT_ADMIN,
    )
    assert ctx.user_id == sysadmin.user_id


def test_assign_role_to_user(db_session: Session, sample_data: dict):
    """Test assigning role to user and handling duplicate assignments and non-existent roles."""
    viewer = sample_data["user_client_viewer"]
    tenant1 = sample_data["tenant1"]

    # Assign new role
    assigned = assign_role_to_user(
        db_session,
        user_id=viewer.user_id,
        role_name=ROLE_CLIENT_ADMIN,
        tenant_id=tenant1.tenant_id,
    )
    assert assigned.user_id == viewer.user_id

    # Re-assign same role (idempotent)
    assigned_duplicate = assign_role_to_user(
        db_session,
        user_id=viewer.user_id,
        role_name=ROLE_CLIENT_ADMIN,
        tenant_id=tenant1.tenant_id,
    )
    assert assigned_duplicate.user_id == viewer.user_id

    # Assign invalid role
    with pytest.raises(RBACError):
        assign_role_to_user(
            db_session,
            user_id=viewer.user_id,
            role_name="NonExistentRole",
            tenant_id=tenant1.tenant_id,
        )


def test_check_route_access():
    """Test route authorization checks against assigned roles."""
    assert check_route_access([ROLE_CLIENT_VIEWER], "/dashboard") is True
    assert check_route_access([ROLE_CLIENT_ADMIN], "/contracts") is True
    assert check_route_access([ROLE_SYSTEM_ADMIN], "/api/v1/navigation/menu") is True
    assert check_route_access([], "/dashboard") is False
    assert check_route_access(["UnknownRole"], "/dashboard") is False
