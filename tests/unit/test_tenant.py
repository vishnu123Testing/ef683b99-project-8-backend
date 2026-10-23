"""Unit tests for Tenant ORM model."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.tenant import Tenant


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session for testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_tenant_model_defaults():
    """Test Tenant model initialization with default values."""
    tenant = Tenant(name="Acme Corp")
    assert tenant.name == "Acme Corp"
    assert tenant.tenant_id is None or isinstance(tenant.tenant_id, uuid.UUID)


def test_tenant_db_persistence(db_session: Session):
    """Test inserting and querying a Tenant in database session."""
    tenant = Tenant(name="Enterprise Corp")
    db_session.add(tenant)
    db_session.commit()
    db_session.refresh(tenant)

    assert isinstance(tenant.tenant_id, uuid.UUID)
    assert tenant.name == "Enterprise Corp"
    assert isinstance(tenant.created_at, datetime)

    # Query back from DB
    queried = db_session.query(Tenant).filter_by(name="Enterprise Corp").first()
    assert queried is not None
    assert queried.tenant_id == tenant.tenant_id


def test_tenant_unique_name_constraint(db_session: Session):
    """Test that creating two tenants with identical names raises IntegrityError."""
    tenant1 = Tenant(name="Duplicate Corp")
    tenant2 = Tenant(name="Duplicate Corp")

    db_session.add(tenant1)
    db_session.commit()

    db_session.add(tenant2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_tenant_repr():
    """Test string representation of Tenant model."""
    tenant_id = uuid.uuid4()
    tenant = Tenant(tenant_id=tenant_id, name="Test Org")
    assert str(tenant_id) in repr(tenant)
    assert "Test Org" in repr(tenant)
