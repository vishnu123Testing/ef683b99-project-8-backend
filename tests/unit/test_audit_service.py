"""Unit tests for non-blocking structured audit logging service in backend/src/services/access_control/audit.py."""

from datetime import datetime, timezone
import io
import json
from unittest.mock import MagicMock
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.lib.logging import reset_correlation_id, set_correlation_id
from src.models.audit import AuditLog
from src.models.database import Base
from src.services.access_control.audit import (
    AuditService,
    _format_datetime_utc,
    _to_uuid,
    log_audit_event,
)


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


def test_to_uuid_helper():
    """Test _to_uuid conversion logic for UUID, str, and invalid/None inputs."""
    u = uuid.uuid4()
    assert _to_uuid(u) == u
    assert _to_uuid(str(u)) == u
    assert _to_uuid(None) is None
    assert _to_uuid("invalid-uuid") is None


def test_format_datetime_utc_helper():
    """Test _format_datetime_utc formatting and ISO string rendering."""
    now_utc = datetime.now(timezone.utc)
    dt, iso_str = _format_datetime_utc(now_utc)
    assert iso_str.endswith("Z") or "+00:00" not in iso_str

    naive_dt = datetime(2026, 10, 6, 8, 0, 0)
    dt_naive, iso_naive = _format_datetime_utc(naive_dt)
    assert dt_naive.tzinfo == timezone.utc
    assert iso_naive.startswith("2026-10-06T08:00:00")


def test_audit_service_stdout_json_emission():
    """Test AuditService emits machine-readable JSON matching the event schema."""
    output_stream = io.StringIO()
    service = AuditService(writer=output_stream)

    u_id = uuid.uuid4()
    t_id = uuid.uuid4()
    c_id = uuid.uuid4()

    event = service.log_event(
        action="READ_NAVIGATION_MENU",
        outcome_status="SUCCESS",
        user_id=u_id,
        assigned_role="ClientViewer",
        tenant_id=t_id,
        resource_id="/api/v1/navigation/menu",
        client_ip="192.168.1.100",
        correlation_id=c_id,
    )

    assert event is not None
    assert event["action"] == "READ_NAVIGATION_MENU"
    assert event["outcome_status"] == "SUCCESS"
    assert event["user_id"] == str(u_id)
    assert event["assigned_role"] == "ClientViewer"
    assert event["tenant_id"] == str(t_id)
    assert event["resource_id"] == "/api/v1/navigation/menu"
    assert event["client_ip"] == "192.168.1.100"
    assert event["correlation_id"] == str(c_id)
    assert "event_id" in event
    assert "logged_at" in event

    # Verify JSON written to stream
    written_json = output_stream.getvalue().strip()
    parsed = json.loads(written_json)
    assert parsed["event_id"] == event["event_id"]
    assert parsed["action"] == "READ_NAVIGATION_MENU"


def test_audit_service_correlation_id_context_fallback():
    """Test that context correlation ID is automatically retrieved when not explicitly passed."""
    output_stream = io.StringIO()
    service = AuditService(writer=output_stream)

    context_cid = str(uuid.uuid4())
    token = set_correlation_id(context_cid)

    try:
        event = service.log_event(
            action="CHECK_PERMISSIONS",
            outcome_status="SUCCESS",
        )
        assert event is not None
        assert event["correlation_id"] == context_cid
    finally:
        reset_correlation_id(token)


def test_audit_service_sensitive_data_masking():
    """Test that credentials and tokens in extra parameters are masked."""
    output_stream = io.StringIO()
    service = AuditService(writer=output_stream)

    event = service.log_event(
        action="LOGIN_ATTEMPT",
        outcome_status="FAILED",
        extra={
            "username": "user@example.com",
            "password": "super-secret-password",
            "access_token": "bearer-token-123",
            "details": {"api_key": "key-xyz"},
        },
    )

    assert event is not None
    assert event["extra"]["username"] == "user@example.com"
    assert event["extra"]["password"] == "***MASKED***"
    assert event["extra"]["access_token"] == "***MASKED***"
    assert event["extra"]["details"]["api_key"] == "***MASKED***"

    # Confirm JSON output stream also contains masked values
    raw_output = output_stream.getvalue()
    assert "super-secret-password" not in raw_output
    assert "bearer-token-123" not in raw_output


def test_audit_service_database_persistence(db_session: Session):
    """Test optional DB persistence when Session parameter is supplied."""
    output_stream = io.StringIO()
    service = AuditService(writer=output_stream)

    u_id = uuid.uuid4()
    t_id = uuid.uuid4()
    c_id = uuid.uuid4()

    event = service.log_event(
        action="UPDATE_USER_ROLE",
        outcome_status="SUCCESS",
        user_id=u_id,
        assigned_role="ClientAdmin",
        tenant_id=t_id,
        resource_id="/api/v1/users/123",
        client_ip="10.0.0.5",
        correlation_id=c_id,
        db=db_session,
    )

    assert event is not None

    # Query database to confirm AuditLog record was persisted
    persisted = db_session.query(AuditLog).filter_by(audit_id=uuid.UUID(event["event_id"])).first()
    assert persisted is not None
    assert persisted.action == "UPDATE_USER_ROLE"
    assert persisted.outcome_status == "SUCCESS"
    assert persisted.user_id == u_id
    assert persisted.tenant_id == t_id
    assert persisted.assigned_role == "ClientAdmin"
    assert persisted.correlation_id == c_id


def test_audit_service_get_audit_logs_query(db_session: Session):
    """Test get_audit_logs method returns tenant-scoped paginated audit entries."""
    t_id1 = uuid.uuid4()
    t_id2 = uuid.uuid4()

    service = AuditService(writer=io.StringIO())

    # Create logs for tenant 1
    for i in range(5):
        service.log_event(
            action=f"ACTION_TENANT1_{i}",
            outcome_status="SUCCESS",
            tenant_id=t_id1,
            db=db_session,
        )

    # Create logs for tenant 2
    for i in range(3):
        service.log_event(
            action=f"ACTION_TENANT2_{i}",
            outcome_status="SUCCESS",
            tenant_id=t_id2,
            db=db_session,
        )

    # Query tenant 1
    logs1, count1 = AuditService.get_audit_logs(db_session, tenant_id=t_id1, limit=10)
    assert count1 == 5
    assert len(logs1) == 5
    assert all(log.tenant_id == t_id1 for log in logs1)

    # Query tenant 2
    logs2, count2 = AuditService.get_audit_logs(db_session, tenant_id=t_id2, limit=2)
    assert count2 == 3
    assert len(logs2) == 2
    assert all(log.tenant_id == t_id2 for log in logs2)


def test_audit_service_fail_safe_on_writer_exception():
    """Test FR-10 fail-safe behavior when the writer stream raises an exception."""
    failing_writer = MagicMock()
    failing_writer.write.side_effect = OSError("Disk full or stream closed")

    service = AuditService(writer=failing_writer)

    # Function must catch the exception and return None without crashing
    result = service.log_event(
        action="CRITICAL_ACTION",
        outcome_status="SUCCESS",
    )

    assert result is None


def test_audit_service_fail_safe_on_db_exception(db_session: Session):
    """Test fail-safe behavior when DB session commit fails."""
    output_stream = io.StringIO()
    service = AuditService(writer=output_stream)

    mock_db = MagicMock(spec=Session)
    mock_db.commit.side_effect = Exception("Database connection lost")

    # Should safely log to stream, catch DB error, and return masked event dict
    result = service.log_event(
        action="CREATE_CLIENT",
        outcome_status="SUCCESS",
        db=mock_db,
    )

    assert result is not None
    assert result["action"] == "CREATE_CLIENT"
    mock_db.rollback.assert_called_once()


def test_convenience_log_audit_event():
    """Test log_audit_event module-level function."""
    output_stream = io.StringIO()

    event = log_audit_event(
        action="AUTH_REGISTER",
        outcome_status="SUCCESS",
        writer=output_stream,
    )

    assert event is not None
    assert event["action"] == "AUTH_REGISTER"
    assert "AUTH_REGISTER" in output_stream.getvalue()
