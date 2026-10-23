"""Unit tests for User ORM model and password hashing helpers."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.tenant import Tenant
from src.models.user import User, hash_password, verify_password


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session with schema initialized."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_password_hash_and_verify_helpers():
    """Test standalone password hashing and verification helper functions."""
    plain = "SecurePassword123!"
    hashed = hash_password(plain)

    assert hashed != plain
    assert verify_password(plain, hashed) is True
    assert verify_password("WrongPassword123!", hashed) is False
    assert verify_password(plain, "invalid_hash_format") is False


def test_user_model_password_methods():
    """Test set_password and check_password helper methods on User ORM model."""
    user = User(email="user@example.com")
    user.set_password("SecretPass!456")

    assert user.password_hash != "SecretPass!456"
    assert user.check_password("SecretPass!456") is True
    assert user.check_password("WrongPass!456") is False


def test_user_db_persistence_and_tenant_relationship(db_session: Session):
    """Test inserting User bound to Tenant, querying from DB, and verifying relationships."""
    tenant = Tenant(name="Acme Corp")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)

    user = User(
        email="john.doe@acme.com",
        password_hash=hash_password("SuperSecret789!"),
        tenant_id=tenant.tenant_id,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert isinstance(user.user_id, uuid.UUID)
    assert user.email == "john.doe@acme.com"
    assert user.tenant_id == tenant.tenant_id
    assert isinstance(user.created_at, datetime)
    assert user.tenant.name == "Acme Corp"

    # Query back from DB by email
    queried_user = db_session.query(User).filter_by(email="john.doe@acme.com").first()
    assert queried_user is not None
    assert queried_user.user_id == user.user_id
    assert queried_user.tenant.tenant_id == tenant.tenant_id


def test_user_unique_email_constraint(db_session: Session):
    """Test that creating two users with the same email raises IntegrityError."""
    tenant = Tenant(name="Beta Corp")
    db_session.add(tenant)
    db_session.commit()

    user1 = User(
        email="duplicate@beta.com",
        password_hash="hash1",
        tenant_id=tenant.tenant_id,
    )
    user2 = User(
        email="duplicate@beta.com",
        password_hash="hash2",
        tenant_id=tenant.tenant_id,
    )

    db_session.add(user1)
    db_session.commit()

    db_session.add(user2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_user_repr():
    """Test string representation of User model."""
    user_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    user = User(user_id=user_id, email="alice@example.com", tenant_id=tenant_id)

    representation = repr(user)
    assert str(user_id) in representation
    assert "alice@example.com" in representation
    assert str(tenant_id) in representation
