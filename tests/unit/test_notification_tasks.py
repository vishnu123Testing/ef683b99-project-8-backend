"""Unit tests for Celery notification tasks in backend/src/workers/notification_tasks.py [FR-8, FR-12, NFR-2, NFR-4]."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.alerting import NotificationLog
from src.models.database import Base
from src.services.alerting.ports import DispatchResult, EmailPayload, InAppPayload, SMSPayload, SMSDispatchError
from src.workers.notification_tasks import (
    celery_app,
    execute_sms_fallback,
    process_multi_channel_dispatch,
    record_notification_audit_log,
    run_dispatch_multi_channel_alert,
    run_execute_sms_fallback,
    run_record_notification_audit_log,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_record_notification_audit_log_success(db_session: Session):
    """Verify recording a notification audit log in database [FR-12, NFR-2]."""
    recipient_id = uuid.uuid4()
    trigger_id = uuid.uuid4()
    cid = "test-corr-audit-123"

    log_dict = record_notification_audit_log(
        recipient_id=recipient_id,
        trigger_id=trigger_id,
        urgency="High",
        channel="Email",
        delivery_status="Sent",
        correlation_id=cid,
        db=db_session,
    )

    assert log_dict["notification_id"] is not None
    assert log_dict["recipient_id"] == str(recipient_id)
    assert log_dict["trigger_id"] == str(trigger_id)
    assert log_dict["urgency"] == "High"
    assert log_dict["channel"] == "Email"
    assert log_dict["delivery_status"] == "Sent"
    assert log_dict["correlation_id"] == cid

    # Verify DB record
    db_record = (
        db_session.query(NotificationLog)
        .filter(NotificationLog.correlation_id == cid)
        .first()
    )
    assert db_record is not None
    assert db_record.recipient_id == recipient_id
    assert db_record.trigger_id == trigger_id
    assert db_record.channel == "Email"
    assert db_record.delivery_status == "Sent"


def test_record_notification_audit_log_string_uuids(db_session: Session):
    """Verify string UUIDs are parsed and stored correctly."""
    rec_str = str(uuid.uuid4())
    trig_str = str(uuid.uuid4())

    log_dict = record_notification_audit_log(
        recipient_id=rec_str,
        trigger_id=trig_str,
        urgency="Medium",
        channel="InApp",
        delivery_status="Sent",
        correlation_id="test-string-uuid-123",
        db=db_session,
    )

    assert log_dict["recipient_id"] == rec_str
    assert log_dict["trigger_id"] == trig_str


def test_process_multi_channel_dispatch_all_channels_success(db_session: Session):
    """Verify multi-channel dispatch across InApp, Email, and SMS channels [FR-8, FR-12]."""
    recipient_id = uuid.uuid4()
    trigger_id = uuid.uuid4()
    cid = "test-corr-dispatch-all"

    mock_email = MagicMock()
    mock_email.send_email.return_value = DispatchResult(
        success=True,
        channel="Email",
        recipient="user@example.com",
        dispatched_at=datetime.now(timezone.utc),
        correlation_id=cid,
        message_id="msg-email-1",
    )

    mock_sms = MagicMock()
    mock_sms.send_sms.return_value = DispatchResult(
        success=True,
        channel="SMS",
        recipient="+15551234567",
        dispatched_at=datetime.now(timezone.utc),
        correlation_id=cid,
        message_id="msg-sms-1",
    )

    mock_inapp = MagicMock()
    mock_inapp.send_in_app.return_value = DispatchResult(
        success=True,
        channel="InApp",
        recipient=str(recipient_id),
        dispatched_at=datetime.now(timezone.utc),
        correlation_id=cid,
        message_id="msg-inapp-1",
    )

    summary = process_multi_channel_dispatch(
        recipient_id=recipient_id,
        recipient_email="user@example.com",
        recipient_phone="+15551234567",
        trigger_id=trigger_id,
        urgency="High",
        channels=["InApp", "Email", "SMS"],
        title="Critical Balance Warning",
        message_text="Outstanding balance exceeds $10,000 threshold.",
        correlation_id=cid,
        db=db_session,
        email_connector=mock_email,
        sms_connector=mock_sms,
        in_app_connector=mock_inapp,
    )

    assert summary["status"] == "success"
    assert summary["recipient_id"] == str(recipient_id)
    assert summary["correlation_id"] == cid
    assert "InApp" in summary["successful_channels"]
    assert "Email" in summary["successful_channels"]
    assert "SMS" in summary["successful_channels"]

    mock_inapp.send_in_app.assert_called_once()
    mock_email.send_email.assert_called_once()
    mock_sms.send_sms.assert_called_once()

    # Verify audit logs in DB
    logs = (
        db_session.query(NotificationLog)
        .filter(NotificationLog.correlation_id == cid)
        .all()
    )
    assert len(logs) >= 2  # Email and SMS logs recorded (InApp persisted via connector)


def test_process_multi_channel_dispatch_sms_failure_triggers_fallback(db_session: Session):
    """Verify SMS failure triggers fallback dispatches to Email and InApp with audit logs recorded [FR-8, FR-12, NFR-4]."""
    recipient_id = uuid.uuid4()
    trigger_id = uuid.uuid4()
    cid = "test-corr-sms-failure-fallback"

    mock_email = MagicMock()
    mock_email.send_email.return_value = DispatchResult(
        success=True,
        channel="Email",
        recipient="user@example.com",
        dispatched_at=datetime.now(timezone.utc),
        correlation_id=cid,
        message_id="msg-email-fallback",
    )

    mock_sms = MagicMock()
    mock_sms.send_sms.side_effect = SMSDispatchError("SMS Gateway Connection Timeout")

    mock_inapp = MagicMock()
    mock_inapp.send_in_app.return_value = DispatchResult(
        success=True,
        channel="InApp",
        recipient=str(recipient_id),
        dispatched_at=datetime.now(timezone.utc),
        correlation_id=cid,
        message_id="msg-inapp-fallback",
    )

    summary = process_multi_channel_dispatch(
        recipient_id=recipient_id,
        recipient_email="user@example.com",
        recipient_phone="+15551234567",
        trigger_id=trigger_id,
        urgency="High",
        channels=["SMS"],
        title="Past Due Invoice",
        message_text="Invoice #1001 is 30 days past due.",
        correlation_id=cid,
        db=db_session,
        email_connector=mock_email,
        sms_connector=mock_sms,
        in_app_connector=mock_inapp,
    )

    assert summary["status"] == "success"
    assert summary["results"]["SMS"]["status"] == "failed"
    assert "SMS_Fallback" in summary["results"]

    fallback_res = summary["results"]["SMS_Fallback"]
    assert fallback_res["fallback_triggered"] is True
    assert fallback_res["email_fallback"]["success"] is True
    assert fallback_res["in_app_fallback"]["success"] is True

    # Check recorded audit logs in DB
    sms_log = (
        db_session.query(NotificationLog)
        .filter(
            NotificationLog.correlation_id == cid,
            NotificationLog.channel == "SMS",
        )
        .first()
    )
    assert sms_log is not None
    assert sms_log.delivery_status == "Failed"

    fallback_logs = (
        db_session.query(NotificationLog)
        .filter(
            NotificationLog.correlation_id == cid,
            NotificationLog.delivery_status == "Fallback",
        )
        .all()
    )
    assert len(fallback_logs) == 2


def test_execute_sms_fallback_standalone(db_session: Session):
    """Verify executing SMS fallback directly dispatches to Email and InApp."""
    recipient_id = uuid.uuid4()
    cid = "test-corr-fallback-direct"

    mock_email = MagicMock()
    mock_email.send_email.return_value = DispatchResult(
        success=True,
        channel="Email",
        recipient="user@example.com",
        dispatched_at=datetime.now(timezone.utc),
        correlation_id=cid,
    )

    mock_inapp = MagicMock()
    mock_inapp.send_in_app.return_value = DispatchResult(
        success=True,
        channel="InApp",
        recipient=str(recipient_id),
        dispatched_at=datetime.now(timezone.utc),
        correlation_id=cid,
    )

    result = execute_sms_fallback(
        recipient_id=recipient_id,
        recipient_email="user@example.com",
        urgency="High",
        title="Direct Fallback Test",
        message_text="Testing direct fallback execution.",
        correlation_id=cid,
        db=db_session,
        email_connector=mock_email,
        in_app_connector=mock_inapp,
    )

    assert result["fallback_triggered"] is True
    assert result["email_fallback"]["success"] is True
    assert result["in_app_fallback"]["success"] is True


def test_celery_task_wrappers():
    """Verify Celery task functions invoke underlying worker functions."""
    with patch("src.workers.notification_tasks.record_notification_audit_log") as mock_record, \
         patch("src.workers.notification_tasks.process_multi_channel_dispatch") as mock_dispatch, \
         patch("src.workers.notification_tasks.execute_sms_fallback") as mock_fallback:

        mock_record.return_value = {"status": "recorded"}
        mock_dispatch.return_value = {"status": "dispatched"}
        mock_fallback.return_value = {"status": "fallback_done"}

        res1 = run_record_notification_audit_log(
            recipient_id="rec-123",
            channel="Email",
            delivery_status="Sent",
        )
        assert res1 == {"status": "recorded"}
        mock_record.assert_called_once()

        res2 = run_dispatch_multi_channel_alert(
            recipient_id="rec-123",
            recipient_email="user@example.com",
            urgency="High",
        )
        assert res2 == {"status": "dispatched"}
        mock_dispatch.assert_called_once()

        res3 = run_execute_sms_fallback(
            recipient_id="rec-123",
            recipient_email="user@example.com",
            urgency="High",
        )
        assert res3 == {"status": "fallback_done"}
        mock_fallback.assert_called_once()
