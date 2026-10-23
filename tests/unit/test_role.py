"""Unit tests for Role and UserRole ORM models."""

import uuid

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.exc import IntegrityError
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


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session with foreign key support enabled."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_role_model_defaults_and_repr():
    """Test Role model defaults and repr representation."""
    role = Role(name=ROLE_CLIENT_VIEWER, description="Read-only client viewer role")
    assert role.name == "ClientViewer"
    assert role.description == "Read-only client viewer role"
    assert "ClientViewer" in repr(role)


def test_role_db_persistence_and_unique_constraint(db_session: Session):
    """Test inserting Role records and enforcing unique name constraint."""
    role1 = Role(name=ROLE_CLIENT_ADMIN, description="Client Administrator")
    db_session.add(role1)
    db_session.commit()
    db_session.refresh(role1)

    assert isinstance(role1.role_id, uuid.UUID)

    # Attempt inserting duplicate role name
    role2 = Role(name=ROLE_CLIENT_ADMIN, description="Duplicate Admin Role")
    db_session.add(role2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_user_role_db_persistence_and_relationships(db_session: Session):
    """Test creating UserRole mappings and retrieving related entities."""
    tenant = Tenant(name="Acme Security Corp")
    db_session.add(tenant)
    db_session.commit()

    user = User(
        email="admin@acme.com",
        password_hash=hash_password("Pass12345!"),
        tenant_id=tenant.tenant_id,
    )
    db_session.add(user)

    role = Role(name=ROLE_SYSTEM_ADMIN, description="System Administrator")
    db_session.add(role)

    db_session.commit()

    user_role = UserRole(
        user_id=user.user_id,
        role_id=role.role_id,
        tenant_id=tenant.tenant_id,
    )
    db_session.add(user_role)
    db_session.commit()

    # Query back from DB
    queried = (
        db_session.query(UserRole)
        .filter_by(user_id=user.user_id, tenant_id=tenant.tenant_id)
        .first()
    )
    assert queried is not None
    assert queried.user.email == "admin@acme.com"
    assert queried.role.name == ROLE_SYSTEM_ADMIN
    assert queried.tenant.name == "Acme Security Corp"
    assert "UserRole" in repr(queried)


def test_user_role_primary_key_uniqueness(db_session: Session):
    """Test composite primary key constraint on UserRole (user_id, role_id, tenant_id)."""
    tenant = Tenant(name="Beta Org")
    db_session.add(tenant)
    db_session.commit()

    user = User(
        email="user@beta.com",
        password_hash=hash_password("Pass12345!"),
        tenant_id=tenant.tenant_id,
    )
    role = Role(name=ROLE_CLIENT_VIEWER, description="Client Viewer")
    db_session.add_all([user, role])
    db_session.commit()

    ur1 = UserRole(user_id=user.user_id, role_id=role.role_id, tenant_id=tenant.tenant_id)
    ur2 = UserRole(user_id=user.user_id, role_id=role.role_id, tenant_id=tenant.tenant_id)

    db_session.add(ur1)
    db_session.commit()

    db_session.add(ur2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_user_role_foreign_key_constraint(db_session: Session):
    """Test that referencing non-existent foreign keys raises IntegrityError."""
    fake_id = uuid.uuid4()
    ur = UserRole(user_id=fake_id, role_id=fake_id, tenant_id=fake_id)
    db_session.add(ur)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()
