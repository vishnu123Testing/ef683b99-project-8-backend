"""Unit tests for In-App persistence connector in backend/src/connectors/in_app.py [FR-5, FR-8]."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.connectors.in_app import (
    InAppAdapter,
    InAppConnector,
    InAppPersistenceAdapter,
    InAppPersistenceConnector,
)
from src.models.alerting import Base, NotificationLog
from src.services.alerting.ports import (
    DispatchResult,
    InAppDispatcherPort,
    InAppDispatchError,
    InAppPayload,
)


def test_in_app_connector_class_aliases():
    """Verify class aliases for InAppPersistenceConnector [FR-5, FR-8]."""
    assert InAppConnector is InAppPersistenceConnector
    assert InAppAdapter is InAppPersistenceConnector
    assert InAppPersistenceAdapter is InAppPersistenceConnector
    assert issubclass(InAppPersistenceConnector, InAppDispatcherPort)


def test_in_app_connector_init():
    """Verify connector initialization parameters."""
    mock_session = MagicMock()
    connector = InAppPersistenceConnector(
        session=mock_session,
        dry_run=True,
        auto_commit=False,
        simulate_failure=False,
    )
    assert connector.session is mock_session
    assert connector.dry_run is True
    assert connector.auto_commit is False
    assert connector.simulate_failure is False
    assert connector.sent_notifications == []


def test_send_in_app_dry_run():
    """Verify send_in_app in dry_run mode returns successful DispatchResult [FR-5, FR-8]."""
    connector = InAppPersistenceConnector(dry_run=True)
    recipient_id = uuid.uuid4()
    trigger_id = uuid.uuid4()

    payload = InAppPayload(
        recipient_id=recipient_id,
        title="High Balance Alert",
        message_text="Invoice #2002 threshold exceeded.",
        urgency="High",
        correlation_id="corr-inapp-123",
        trigger_id=trigger_id,
        metadata={"amount": 15000.0},
    )

    result = connector.send_in_app(payload)

    assert isinstance(result, DispatchResult)
    assert result.success is True
    assert result.channel == "InApp"
    assert result.recipient == str(recipient_id)
    assert result.correlation_id == "corr-inapp-123"
    assert result.message_id.startswith("inapp-")
    assert result.details["dry_run"] is True
    assert result.details["title"] == "High Balance Alert"
    assert result.details["trigger_id"] == str(trigger_id)

    # Check in-memory tracking
    notifications = connector.get_in_app_notifications()
    assert len(notifications) == 1
    assert notifications[0]["recipient_id"] == str(recipient_id)
    assert notifications[0]["title"] == "High Balance Alert"


def test_send_in_app_convenience_methods():
    """Verify dispatch and send convenience alias methods [FR-8]."""
    connector = InAppPersistenceConnector(dry_run=True)
    recipient_id = uuid.uuid4()

    payload = InAppPayload(
        recipient_id=recipient_id,
        title="Payment Received",
        message_text="Payment of $5,000 processed.",
        urgency="Low",
    )

    result_dispatch = connector.dispatch(payload)
    assert result_dispatch.success is True

    result_send = connector.send(payload)
    assert result_send.success is True

    assert len(connector.get_in_app_notifications()) == 2


def test_send_in_app_invalid_payload_type():
    """Verify send_in_app raises InAppDispatchError when payload is invalid type."""
    connector = InAppPersistenceConnector(dry_run=True)
    with pytest.raises(InAppDispatchError, match="Invalid payload type"):
        connector.send_in_app("invalid-payload")  # type: ignore


def test_send_in_app_missing_recipient_id():
    """Verify send_in_app raises InAppDispatchError when recipient_id is empty."""
    connector = InAppPersistenceConnector(dry_run=True)
    payload = InAppPayload(
        recipient_id="",
        title="Test Title",
        message_text="Test Body",
    )

    with pytest.raises(InAppDispatchError, match="Recipient ID is required"):
        connector.send_in_app(payload)


def test_send_in_app_missing_title_and_body():
    """Verify send_in_app raises InAppDispatchError when title and message_text are missing."""
    connector = InAppPersistenceConnector(dry_run=True)
    payload = InAppPayload(
        recipient_id=uuid.uuid4(),
        title="",
        message_text="  ",
    )

    with pytest.raises(InAppDispatchError, match="In-app notification title or message text is required"):
        connector.send_in_app(payload)


def test_send_in_app_invalid_uuid():
    """Verify send_in_app raises InAppDispatchError when recipient_id is not a valid UUID."""
    connector = InAppPersistenceConnector(dry_run=True)
    payload = InAppPayload(
        recipient_id="not-a-valid-uuid",
        title="Title",
        message_text="Text",
    )

    with pytest.raises(InAppDispatchError, match="Invalid recipient_id UUID format"):
        connector.send_in_app(payload)


def test_send_in_app_invalid_trigger_id_uuid():
    """Verify send_in_app raises InAppDispatchError when trigger_id is invalid UUID."""
    connector = InAppPersistenceConnector(dry_run=True)
    payload = InAppPayload(
        recipient_id=uuid.uuid4(),
        trigger_id="invalid-trigger-uuid",
        title="Title",
        message_text="Text",
    )

    with pytest.raises(InAppDispatchError, match="Invalid trigger_id UUID format"):
        connector.send_in_app(payload)


def test_send_in_app_simulated_failure():
    """Verify send_in_app raises InAppDispatchError when simulate_failure is True."""
    connector = InAppPersistenceConnector(dry_run=True, simulate_failure=True)
    payload = InAppPayload(
        recipient_id=uuid.uuid4(),
        title="Title",
        message_text="Text",
    )

    with pytest.raises(InAppDispatchError, match="Simulated in-app notification persistence failure"):
        connector.send_in_app(payload)


def test_send_in_app_with_db_session():
    """Verify send_in_app persists NotificationLog to database session [FR-5, FR-8]."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)
    db = TestingSessionLocal()

    try:
        connector = InAppPersistenceConnector(session=db, dry_run=False, auto_commit=True)
        recipient_id = uuid.uuid4()
        trigger_id = uuid.uuid4()

        payload = InAppPayload(
            recipient_id=recipient_id,
            title="Overdue Invoice Warning",
            message_text="Invoice #3003 is 30 days overdue.",
            urgency="Medium",
            correlation_id="corr-db-test-999",
            trigger_id=trigger_id,
        )

        result = connector.send_in_app(payload)
        assert result.success is True
        assert "notification_id" in result.details

        # Query database to confirm NotificationLog persistence
        log_entry = db.query(NotificationLog).filter_by(recipient_id=recipient_id).first()
        assert log_entry is not None
        assert log_entry.recipient_id == recipient_id
        assert log_entry.trigger_id == trigger_id
        assert log_entry.urgency == "Medium"
        assert log_entry.channel == "InApp"
        assert log_entry.delivery_status == "Sent"
        assert log_entry.correlation_id == "corr-db-test-999"
    finally:
        db.close()


def test_send_in_app_with_session_factory():
    """Verify send_in_app uses session_factory when session is not provided [FR-5, FR-8]."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(bind=engine)

    connector = InAppPersistenceConnector(
        session_factory=TestingSessionLocal,
        dry_run=False,
        auto_commit=True,
    )

    recipient_id = uuid.uuid4()
    payload = InAppPayload(
        recipient_id=recipient_id,
        title="Session Factory Test",
        message_text="Created via factory",
    )

    result = connector.send_in_app(payload)
    assert result.success is True

    db = TestingSessionLocal()
    try:
        log_entry = db.query(NotificationLog).filter_by(recipient_id=recipient_id).first()
        assert log_entry is not None
        assert log_entry.channel == "InApp"
    finally:
        db.close()


def test_send_in_app_db_error_handling():
    """Verify send_in_app raises InAppDispatchError on database error."""
    mock_session = MagicMock()
    mock_session.add.side_effect = Exception("Database connection lost")

    connector = InAppPersistenceConnector(session=mock_session, dry_run=False)
    payload = InAppPayload(
        recipient_id=uuid.uuid4(),
        title="Error Test Title",
        message_text="Error Test Message",
    )

    with pytest.raises(InAppDispatchError, match="Database error persisting in-app notification"):
        connector.send_in_app(payload)

    mock_session.rollback.assert_called_once()


def test_get_in_app_notifications_filter_and_clear():
    """Verify filtering notifications by recipient_id and clearing tracking [FR-5]."""
    connector = InAppPersistenceConnector(dry_run=True)
    user1_id = uuid.uuid4()
    user2_id = uuid.uuid4()

    connector.send_in_app(InAppPayload(recipient_id=user1_id, title="User 1 Note", message_text="Msg 1"))
    connector.send_in_app(InAppPayload(recipient_id=user2_id, title="User 2 Note", message_text="Msg 2"))

    assert len(connector.get_in_app_notifications()) == 2
    user1_notes = connector.get_in_app_notifications(recipient_id=user1_id)
    assert len(user1_notes) == 1
    assert user1_notes[0]["title"] == "User 1 Note"

    connector.clear()
    assert len(connector.get_in_app_notifications()) == 0
