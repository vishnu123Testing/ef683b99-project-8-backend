"""Unit tests for AuditLog ORM model."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine, inspect
from sqlalchemy.orm import Session, sessionmaker

from src.models.audit import AuditLog
from src.models.database import Base


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_audit_log_model_defaults_and_repr():
    """Test AuditLog model default values and repr formatting."""
    log = AuditLog(
        action="LOGIN_ATTEMPT",
        outcome_status="SUCCESS",
    )

    assert log.action == "LOGIN_ATTEMPT"
    assert log.outcome_status == "SUCCESS"
    assert isinstance(log.audit_id, uuid.UUID)
    assert isinstance(log.logged_at, datetime)
    assert "LOGIN_ATTEMPT" in repr(log)
    assert "SUCCESS" in repr(log)


def test_audit_log_to_dict():
    """Test AuditLog.to_dict serialization method."""
    u_id = uuid.uuid4()
    t_id = uuid.uuid4()
    c_id = uuid.uuid4()

    log = AuditLog(
        user_id=u_id,
        assigned_role="ClientAdmin",
        tenant_id=t_id,
        action="READ_CONTRACTS",
        resource_id="/api/v1/contracts",
        client_ip="10.0.0.1",
        outcome_status="SUCCESS",
        correlation_id=c_id,
    )

    data = log.to_dict()
    assert data["event_id"] == str(log.audit_id)
    assert data["user_id"] == str(u_id)
    assert data["assigned_role"] == "ClientAdmin"
    assert data["tenant_id"] == str(t_id)
    assert data["action"] == "READ_CONTRACTS"
    assert data["resource_id"] == "/api/v1/contracts"
    assert data["client_ip"] == "10.0.0.1"
    assert data["outcome_status"] == "SUCCESS"
    assert data["correlation_id"] == str(c_id)
    assert "logged_at" in data


def test_audit_log_db_persistence_and_query(db_session: Session):
    """Test persisting AuditLog to database and querying by correlation_id and tenant_id."""
    u_id = uuid.uuid4()
    t_id = uuid.uuid4()
    c_id = uuid.uuid4()

    audit_entry = AuditLog(
        user_id=u_id,
        assigned_role="ClientViewer",
        tenant_id=t_id,
        action="ACCESS_DENIED",
        resource_id="/api/v1/financials",
        client_ip="192.168.1.50",
        outcome_status="FORBIDDEN",
        correlation_id=c_id,
    )

    db_session.add(audit_entry)
    db_session.commit()
    db_session.refresh(audit_entry)

    # Query by correlation_id
    queried_corr = db_session.query(AuditLog).filter_by(correlation_id=c_id).first()
    assert queried_corr is not None
    assert queried_corr.audit_id == audit_entry.audit_id
    assert queried_corr.action == "ACCESS_DENIED"

    # Query by tenant_id
    queried_tenant = db_session.query(AuditLog).filter_by(tenant_id=t_id).all()
    assert len(queried_tenant) == 1
    assert queried_tenant[0].audit_id == audit_entry.audit_id


def test_audit_log_nullable_fields(db_session: Session):
    """Test AuditLog persistence with optional nullable fields set to None."""
    log = AuditLog(
        action="SYSTEM_INITIALIZATION",
        outcome_status="COMPLETED",
        user_id=None,
        assigned_role=None,
        tenant_id=None,
        resource_id=None,
        client_ip=None,
        correlation_id=None,
    )

    db_session.add(log)
    db_session.commit()
    db_session.refresh(log)

    assert log.audit_id is not None
    assert log.user_id is None
    assert log.tenant_id is None
    assert log.assigned_role is None
    assert log.resource_id is None
    assert log.client_ip is None
    assert log.correlation_id is None


def test_audit_log_indexes_created(db_session: Session):
    """Test that audit_logs composite and column indexes are created in database schema."""
    engine = db_session.get_bind()
    inspector = inspect(engine)
    indexes = inspector.get_indexes("audit_logs")
    index_names = [idx["name"] for idx in indexes]

    assert "ix_audit_logs_tenant_id_logged_at" in index_names
    assert "ix_audit_logs_correlation_id" in index_names
