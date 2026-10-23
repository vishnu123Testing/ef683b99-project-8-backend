"""Unit tests for multi-channel message dispatcher ports in backend/src/services/alerting/ports.py [FR-8]."""

from datetime import datetime, timezone
import uuid

pytest_plugins = []
import pytest

from src.services.alerting.ports import (
    AlertingPortError,
    DispatchResult,
    EmailDispatcherPort,
    EmailDispatchError,
    EmailPayload,
    EmailPort,
    InAppDispatcherPort,
    InAppDispatchError,
    InAppPayload,
    InAppPort,
    SMSDispatcherPort,
    SMSDispatchError,
    SMSPayload,
    SMSPort,
)


class DummyEmailAdapter(EmailDispatcherPort):
    """Dummy adapter for testing EmailDispatcherPort interface contract."""

    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.dispatched_payloads = []

    def send_email(self, payload: EmailPayload) -> DispatchResult:
        if self.should_fail:
            raise EmailDispatchError("Failed to connect to SMTP server.")
        self.dispatched_payloads.append(payload)
        return DispatchResult(
            success=True,
            channel="Email",
            recipient=payload.recipient_email,
            dispatched_at=datetime.now(timezone.utc),
            correlation_id=payload.correlation_id,
            message_id=f"msg-{len(self.dispatched_payloads)}",
        )


class DummySMSAdapter(SMSDispatcherPort):
    """Dummy adapter for testing SMSDispatcherPort interface contract."""

    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.dispatched_payloads = []

    def send_sms(self, payload: SMSPayload) -> DispatchResult:
        if self.should_fail:
            raise SMSDispatchError("SMS gateway timeout.")
        self.dispatched_payloads.append(payload)
        return DispatchResult(
            success=True,
            channel="SMS",
            recipient=payload.recipient_phone,
            dispatched_at=datetime.now(timezone.utc),
            correlation_id=payload.correlation_id,
            message_id=f"sms-{len(self.dispatched_payloads)}",
        )


class DummyInAppAdapter(InAppDispatcherPort):
    """Dummy adapter for testing InAppDispatcherPort interface contract."""

    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.dispatched_payloads = []

    def send_in_app(self, payload: InAppPayload) -> DispatchResult:
        if self.should_fail:
            raise InAppDispatchError("In-App persistence failed.")
        self.dispatched_payloads.append(payload)
        return DispatchResult(
            success=True,
            channel="InApp",
            recipient=str(payload.recipient_id),
            dispatched_at=datetime.now(timezone.utc),
            correlation_id=payload.correlation_id,
            message_id=f"inapp-{len(self.dispatched_payloads)}",
        )


def test_exception_hierarchy():
    """Verify exception hierarchy for alerting ports."""
    assert issubclass(EmailDispatchError, AlertingPortError)
    assert issubclass(SMSDispatchError, AlertingPortError)
    assert issubclass(InAppDispatchError, AlertingPortError)

    err = EmailDispatchError("SMTP failure")
    assert str(err) == "SMTP failure"
    assert isinstance(err, AlertingPortError)


def test_email_payload_and_to_dict():
    """Verify EmailPayload creation and dictionary conversion [FR-8]."""
    recip_id = uuid.uuid4()
    trig_id = uuid.uuid4()
    payload = EmailPayload(
        recipient_email="finance@example.com",
        subject="High Urgency Invoice Alert",
        body_text="Invoice #1001 is past due.",
        body_html="<p>Invoice #1001 is past due.</p>",
        urgency="High",
        correlation_id="corr-123",
        recipient_id=recip_id,
        trigger_id=trig_id,
        metadata={"invoice_id": "inv-1001"},
    )

    d = payload.to_dict()
    assert d["recipient_email"] == "finance@example.com"
    assert d["subject"] == "High Urgency Invoice Alert"
    assert d["body_text"] == "Invoice #1001 is past due."
    assert d["body_html"] == "<p>Invoice #1001 is past due.</p>"
    assert d["urgency"] == "High"
    assert d["correlation_id"] == "corr-123"
    assert d["recipient_id"] == str(recip_id)
    assert d["trigger_id"] == str(trig_id)
    assert d["metadata"] == {"invoice_id": "inv-1001"}


def test_sms_payload_and_to_dict():
    """Verify SMSPayload creation and dictionary conversion [FR-8]."""
    payload = SMSPayload(
        recipient_phone="+15551234567",
        message_text="URGENT: Quote expired.",
        urgency="High",
        correlation_id="corr-456",
    )

    d = payload.to_dict()
    assert d["recipient_phone"] == "+15551234567"
    assert d["message_text"] == "URGENT: Quote expired."
    assert d["urgency"] == "High"
    assert d["correlation_id"] == "corr-456"
    assert d["recipient_id"] is None
    assert d["trigger_id"] is None


def test_in_app_payload_and_to_dict():
    """Verify InAppPayload creation and dictionary conversion [FR-8]."""
    recip_id = uuid.uuid4()
    payload = InAppPayload(
        recipient_id=recip_id,
        title="Payment Received",
        message_text="Payment of $5,000 received for Invoice #1002.",
        urgency="Low",
        correlation_id="corr-789",
    )

    d = payload.to_dict()
    assert d["recipient_id"] == str(recip_id)
    assert d["title"] == "Payment Received"
    assert d["message_text"] == "Payment of $5,000 received for Invoice #1002."
    assert d["urgency"] == "Low"
    assert d["correlation_id"] == "corr-789"


def test_dispatch_result_and_to_dict():
    """Verify DispatchResult creation and dictionary conversion [FR-8]."""
    now = datetime.now(timezone.utc)
    res = DispatchResult(
        success=True,
        channel="Email",
        recipient="user@example.com",
        dispatched_at=now,
        correlation_id="corr-abc",
        message_id="msg-999",
        details={"smtp_code": 250},
    )

    d = res.to_dict()
    assert d["success"] is True
    assert d["channel"] == "Email"
    assert d["recipient"] == "user@example.com"
    assert d["dispatched_at"] == now.isoformat()
    assert d["correlation_id"] == "corr-abc"
    assert d["message_id"] == "msg-999"
    assert d["error_message"] is None
    assert d["details"] == {"smtp_code": 250}


def test_cannot_instantiate_abstract_ports():
    """Verify that abstract port classes cannot be instantiated directly."""
    with pytest.raises(TypeError):
        EmailDispatcherPort()  # type: ignore

    with pytest.raises(TypeError):
        SMSDispatcherPort()  # type: ignore

    with pytest.raises(TypeError):
        InAppDispatcherPort()  # type: ignore


def test_dummy_email_adapter():
    """Verify dummy email adapter implementation conforming to EmailDispatcherPort [FR-8]."""
    adapter = DummyEmailAdapter()
    payload = EmailPayload(
        recipient_email="admin@example.com",
        subject="Alert",
        body_text="Test alert body",
        correlation_id="corr-email",
    )

    # Test send_email
    res = adapter.send_email(payload)
    assert res.success is True
    assert res.channel == "Email"
    assert res.recipient == "admin@example.com"
    assert res.message_id == "msg-1"

    # Test alias methods
    res_dispatch = adapter.dispatch(payload)
    assert res_dispatch.success is True

    res_send = adapter.send(payload)
    assert res_send.success is True

    # Test failure mode
    failing_adapter = DummyEmailAdapter(should_fail=True)
    with pytest.raises(EmailDispatchError, match="SMTP server"):
        failing_adapter.send_email(payload)


def test_dummy_sms_adapter():
    """Verify dummy SMS adapter implementation conforming to SMSDispatcherPort [FR-8]."""
    adapter = DummySMSAdapter()
    payload = SMSPayload(
        recipient_phone="+15559876543",
        message_text="Test SMS alert",
        correlation_id="corr-sms",
    )

    res = adapter.send_sms(payload)
    assert res.success is True
    assert res.channel == "SMS"
    assert res.recipient == "+15559876543"

    res_alias = adapter.dispatch(payload)
    assert res_alias.success is True

    failing_adapter = DummySMSAdapter(should_fail=True)
    with pytest.raises(SMSDispatchError, match="SMS gateway timeout"):
        failing_adapter.send_sms(payload)


def test_dummy_in_app_adapter():
    """Verify dummy In-App adapter implementation conforming to InAppDispatcherPort [FR-8]."""
    adapter = DummyInAppAdapter()
    recip_id = uuid.uuid4()
    payload = InAppPayload(
        recipient_id=recip_id,
        title="Notice",
        message_text="Test in-app notification",
        correlation_id="corr-inapp",
    )

    res = adapter.send_in_app(payload)
    assert res.success is True
    assert res.channel == "InApp"
    assert res.recipient == str(recip_id)

    res_alias = adapter.dispatch(payload)
    assert res_alias.success is True

    failing_adapter = DummyInAppAdapter(should_fail=True)
    with pytest.raises(InAppDispatchError, match="In-App persistence failed"):
        failing_adapter.send_in_app(payload)


def test_port_aliases():
    """Verify that port aliases point to the correct abstract classes."""
    assert EmailPort is EmailDispatcherPort
    assert SMSPort is SMSDispatcherPort
    assert InAppPort is InAppDispatcherPort
