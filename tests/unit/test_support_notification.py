"""Unit tests for notification connector adapter in client support operations [FR-5, FR-6, NFR-3, NFR-4, NFR-5]."""

from datetime import datetime, timezone
import uuid

import pytest

from src.connectors.notification import (
    EscalationAlertPayload,
    EscalationNotificationPayload,
    NotificationAdapter,
    NotificationChannel,
    NotificationConnector,
    NotificationDispatchError,
    NotificationDispatchResult,
    NotificationError,
    NotificationPort,
    NotificationTimeoutError,
    NotificationType,
    NotificationValidationError,
    SLABreachNotificationPayload,
    SLAWarningNotificationPayload,
    SupportNotificationConnector,
)


@pytest.fixture
def notification_connector():
    """Fixture providing a clean NotificationConnector instance."""
    return NotificationConnector()


def test_connector_initialization(notification_connector):
    """Test NotificationConnector initialization defaults and interfaces [NFR-4]."""
    assert isinstance(notification_connector, NotificationPort)
    assert notification_connector.default_channel == "IN_APP"
    assert notification_connector.enable_external_delivery is True
    assert notification_connector.get_dispatched_notifications() == []


def test_dispatch_breach_warning(notification_connector):
    """Test dispatching a proactive SLA breach warning notification [FR-5]."""
    ticket_id = uuid.uuid4()
    client_id = uuid.uuid4()
    now_utc = datetime.now(timezone.utc)

    payload = SLAWarningNotificationPayload(
        ticket_id=ticket_id,
        client_id=client_id,
        priority="P1",
        target_type="first_response",
        target_due_at=now_utc,
        elapsed_percent=85.0,
        time_remaining_minutes=9.0,
        assigned_tier="tier1",
        recipients=["support_manager@example.com"],
    )

    result = notification_connector.dispatch_breach_warning(payload)

    assert isinstance(result, NotificationDispatchResult)
    assert result.notification_type == NotificationType.SLA_BREACH_WARNING.value
    assert result.ticket_id == ticket_id
    assert result.status == "DELIVERED"
    assert "85.0%" in result.message
    assert "support_manager@example.com" in result.recipients

    dispatched = notification_connector.get_dispatched_notifications(ticket_id=ticket_id)
    assert len(dispatched) == 1
    assert dispatched[0].notification_id == result.notification_id


def test_dispatch_breach_alert(notification_connector):
    """Test dispatching an SLA breach alert notification [FR-5, FR-7]."""
    ticket_id = uuid.uuid4()
    client_id = uuid.uuid4()
    now_utc = datetime.now(timezone.utc)

    payload = SLABreachNotificationPayload(
        ticket_id=ticket_id,
        client_id=client_id,
        priority="P2",
        target_type="resolution",
        target_due_at=now_utc,
        breached_at=now_utc,
        assigned_tier="tier1",
    )

    result = notification_connector.dispatch_breach_alert(payload)

    assert isinstance(result, NotificationDispatchResult)
    assert result.notification_type == NotificationType.SLA_BREACH_ALERT.value
    assert result.ticket_id == ticket_id
    assert result.status == "DELIVERED"
    assert "breached its resolution SLA target" in result.message

    dispatched = notification_connector.get_dispatched_notifications(
        notification_type=NotificationType.SLA_BREACH_ALERT.value
    )
    assert len(dispatched) == 1
    assert dispatched[0].ticket_id == ticket_id


def test_dispatch_escalation_alert(notification_connector):
    """Test dispatching a support tier escalation alert notification [FR-6]."""
    ticket_id = uuid.uuid4()
    client_id = uuid.uuid4()
    now_utc = datetime.now(timezone.utc)

    payload = EscalationNotificationPayload(
        ticket_id=ticket_id,
        client_id=client_id,
        priority="P1",
        source_tier="tier1",
        target_tier="tier2",
        reason="80% resolution SLA elapsed without agent assignment",
        escalated_at=now_utc,
        recipients=["tier2_lead@example.com"],
    )

    result = notification_connector.dispatch_escalation_alert(payload)

    assert isinstance(result, NotificationDispatchResult)
    assert result.notification_type == NotificationType.TIER_ESCALATION.value
    assert result.ticket_id == ticket_id
    assert result.status == "DELIVERED"
    assert "escalated from tier1 to tier2" in result.message
    assert "tier2_lead@example.com" in result.recipients


def test_send_generic_notification(notification_connector):
    """Test sending a custom or generic notification alert."""
    ticket_id = uuid.uuid4()

    result = notification_connector.send_notification(
        notification_type="TICKET_REASSIGNED",
        ticket_id=ticket_id,
        recipients=["agent@example.com"],
        message_body="Ticket #102 reassigned to Agent Smith",
        metadata={"previous_agent_id": str(uuid.uuid4())},
    )

    assert result.notification_type == "TICKET_REASSIGNED"
    assert result.ticket_id == ticket_id
    assert result.message == "Ticket #102 reassigned to Agent Smith"


def test_simulation_triggers():
    """Test simulation flags for failure and timeout testing."""
    failing_connector = NotificationConnector(simulate_failure=True)
    payload = SLAWarningNotificationPayload(
        ticket_id=uuid.uuid4(),
        client_id=uuid.uuid4(),
        priority="P1",
        target_type="first_response",
        target_due_at=datetime.now(timezone.utc),
        elapsed_percent=90.0,
        time_remaining_minutes=5.0,
    )

    with pytest.raises(NotificationDispatchError, match="Simulated notification dispatch operational failure"):
        failing_connector.dispatch_breach_warning(payload)

    timing_out_connector = NotificationConnector(simulate_timeout=True)
    with pytest.raises(NotificationTimeoutError, match="Simulated notification dispatch request timeout"):
        timing_out_connector.dispatch_breach_warning(payload)


def test_clear_dispatched_notifications(notification_connector):
    """Test clearing recorded in-memory dispatched notifications."""
    ticket_id = uuid.uuid4()
    now_utc = datetime.now(timezone.utc)

    payload = SLABreachNotificationPayload(
        ticket_id=ticket_id,
        client_id=uuid.uuid4(),
        priority="P1",
        target_type="first_response",
        target_due_at=now_utc,
        breached_at=now_utc,
    )
    notification_connector.dispatch_breach_alert(payload)

    assert len(notification_connector.get_dispatched_notifications()) == 1
    notification_connector.clear_dispatched_notifications()
    assert len(notification_connector.get_dispatched_notifications()) == 0


def test_payload_validation_missing_ticket_id(notification_connector):
    """Test that missing ticket_id raises NotificationValidationError."""
    payload = SLAWarningNotificationPayload(
        ticket_id=None,  # type: ignore
        client_id=uuid.uuid4(),
        priority="P1",
        target_type="first_response",
        target_due_at=datetime.now(timezone.utc),
        elapsed_percent=80.0,
        time_remaining_minutes=10.0,
    )

    with pytest.raises(NotificationValidationError, match="ticket_id is required"):
        notification_connector.dispatch_breach_warning(payload)


def test_connector_aliases():
    """Test class aliases for NotificationConnector."""
    assert SupportNotificationConnector is NotificationConnector
    assert NotificationAdapter is NotificationConnector
    assert EscalationAlertPayload is EscalationNotificationPayload
