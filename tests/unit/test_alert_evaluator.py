"""Unit tests for trigger evaluation engine, alert deduplication hashing, and role/urgency channel matrix routing [FR-6, FR-7, FR-8, FR-11]."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.alerting import (
    AlertSuppression,
    AlertTrigger,
    NotificationPreference,
)
from src.models.database import Base
from src.services.alerting.evaluator import (
    DEFAULT_CHANNEL_MATRIX,
    AlertEvaluatorEngine,
    EvaluationEvent,
    RoutedAlert,
    build_dispatch_payloads,
    compute_dedup_hash,
    evaluate_active_triggers,
    evaluate_trigger_rule,
    get_routed_channels,
    is_alert_suppressed,
    record_suppression,
)
from src.services.alerting.ports import (
    DispatchResult,
    EmailDispatcherPort,
    EmailDispatchError,
    EmailPayload,
    InAppDispatcherPort,
    InAppPayload,
    SMSDispatcherPort,
    SMSPayload,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for testing."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


class DummyEmailAdapter(EmailDispatcherPort):
    """Dummy adapter for testing EmailDispatcherPort integration."""

    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.payloads = []

    def send_email(self, payload: EmailPayload) -> DispatchResult:
        if self.should_fail:
            raise EmailDispatchError("Email gateway error")
        self.payloads.append(payload)
        return DispatchResult(
            success=True,
            channel="Email",
            recipient=payload.recipient_email,
            dispatched_at=datetime.now(timezone.utc),
            correlation_id=payload.correlation_id,
            message_id="msg-123",
        )


class DummySMSAdapter(SMSDispatcherPort):
    """Dummy adapter for testing SMSDispatcherPort integration."""

    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.payloads = []

    def send_sms(self, payload: SMSPayload) -> DispatchResult:
        if self.should_fail:
            raise Exception("SMS network error")
        self.payloads.append(payload)
        return DispatchResult(
            success=True,
            channel="SMS",
            recipient=payload.recipient_phone,
            dispatched_at=datetime.now(timezone.utc),
            correlation_id=payload.correlation_id,
            message_id="sms-123",
        )


class DummyInAppAdapter(InAppDispatcherPort):
    """Dummy adapter for testing InAppDispatcherPort integration."""

    def __init__(self, should_fail: bool = False) -> None:
        self.should_fail = should_fail
        self.payloads = []

    def send_in_app(self, payload: InAppPayload) -> DispatchResult:
        if self.should_fail:
            raise Exception("InApp db error")
        self.payloads.append(payload)
        return DispatchResult(
            success=True,
            channel="InApp",
            recipient=str(payload.recipient_id),
            dispatched_at=datetime.now(timezone.utc),
            correlation_id=payload.correlation_id,
            message_id="inapp-123",
        )


def test_compute_dedup_hash():
    """Verify compute_dedup_hash produces deterministic SHA-256 strings [FR-11]."""
    h1 = compute_dedup_hash("High Past Due", "invoice_overdue", "inv-001", "role-admin")
    h2 = compute_dedup_hash("High Past Due", "invoice_overdue", "inv-001", "role-admin")
    h3 = compute_dedup_hash("High Past Due", "invoice_overdue", "inv-002", "role-admin")

    assert len(h1) == 64
    assert h1 == h2
    assert h1 != h3


def test_evaluate_trigger_rule():
    """Verify evaluate_trigger_rule threshold matching and active check [FR-6]."""
    trigger_active = AlertTrigger(
        rule_name="Large Payment Threshold",
        financial_criteria="large_payment",
        threshold_amount=Decimal("10000.00"),
        urgency="High",
        is_active=True,
    )
    trigger_inactive = AlertTrigger(
        rule_name="Inactive Threshold",
        financial_criteria="large_payment",
        threshold_amount=Decimal("10000.00"),
        urgency="High",
        is_active=False,
    )

    assert evaluate_trigger_rule(trigger_active, 15000.00) is True
    assert evaluate_trigger_rule(trigger_active, 10000.00) is True
    assert evaluate_trigger_rule(trigger_active, 5000.00) is False
    assert evaluate_trigger_rule(trigger_inactive, 15000.00) is False


def test_evaluate_active_triggers(db_session):
    """Verify evaluate_active_triggers queries matching triggers from DB session [FR-6]."""
    t1 = AlertTrigger(
        rule_name="Past Due Over 1000",
        financial_criteria="past_due_balance_exceeded",
        threshold_amount=Decimal("1000.00"),
        urgency="Medium",
        is_active=True,
    )
    t2 = AlertTrigger(
        rule_name="Past Due Over 5000",
        financial_criteria="past_due_balance_exceeded",
        threshold_amount=Decimal("5000.00"),
        urgency="High",
        is_active=True,
    )
    t3 = AlertTrigger(
        rule_name="Quote Expired Rule",
        financial_criteria="quote_expired",
        threshold_amount=Decimal("0.00"),
        urgency="Low",
        is_active=True,
    )
    db_session.add_all([t1, t2, t3])
    db_session.commit()

    matched = evaluate_active_triggers(db_session, "past_due_balance_exceeded", 2500.00)
    assert len(matched) == 1
    assert matched[0].rule_name == "Past Due Over 1000"

    matched_high = evaluate_active_triggers(db_session, "past_due_balance_exceeded", 6000.00)
    assert len(matched_high) == 2


def test_suppression_window_tracking(db_session):
    """Verify is_alert_suppressed and record_suppression lifecycle [FR-11]."""
    dedup_hash = compute_dedup_hash("Rule1", "type1", "entity1")

    assert is_alert_suppressed(db_session, dedup_hash) is False

    # Record suppression
    record_suppression(db_session, dedup_hash, suppression_window_seconds=300)
    assert is_alert_suppressed(db_session, dedup_hash) is True

    # Test expired suppression
    suppression_entry = (
        db_session.query(AlertSuppression)
        .filter(AlertSuppression.dedup_hash == dedup_hash)
        .first()
    )
    suppression_entry.expires_at = datetime.now(timezone.utc) - timedelta(seconds=10)
    db_session.commit()

    assert is_alert_suppressed(db_session, dedup_hash) is False


def test_get_routed_channels(db_session):
    """Verify get_routed_channels looks up NotificationPreference or uses default matrix [FR-7, FR-8]."""
    # Preference exists
    pref = NotificationPreference(
        role_name="financial_admin",
        event_type="invoice_overdue",
        urgency="High",
        channels_json=["InApp", "Email"],
    )
    db_session.add(pref)
    db_session.commit()

    channels = get_routed_channels(db_session, "financial_admin", "invoice_overdue", "High")
    assert channels == ["InApp", "Email"]

    # Fallback default matrix when no preference matches
    fallback_high = get_routed_channels(db_session, "other_role", "invoice_overdue", "High")
    assert fallback_high == ["InApp", "Email", "SMS"]

    fallback_medium = get_routed_channels(db_session, "other_role", "invoice_overdue", "Medium")
    assert fallback_medium == ["InApp", "Email"]


def test_build_dispatch_payloads():
    """Verify build_dispatch_payloads constructs proper channel payloads [FR-8]."""
    user_id = uuid.uuid4()
    event = EvaluationEvent(
        financial_criteria="past_due_balance_exceeded",
        value=12000.00,
        entity_id="inv-999",
        event_type="invoice_overdue",
        role_name="financial_admin",
        recipient_id=user_id,
        recipient_email="admin@example.com",
        recipient_phone="+15551234567",
    )

    payloads = build_dispatch_payloads(
        event=event,
        trigger_rule_name="Critical Balance Rule",
        urgency="High",
        channels=["InApp", "Email", "SMS"],
        correlation_id="corr-123",
    )

    assert "InApp" in payloads
    assert "Email" in payloads
    assert "SMS" in payloads

    assert payloads["InApp"].recipient_id == user_id
    assert payloads["Email"].recipient_email == "admin@example.com"
    assert payloads["SMS"].recipient_phone == "+15551234567"


def test_alert_evaluator_engine_full_workflow(db_session):
    """Verify AlertEvaluatorEngine end-to-end evaluation, deduplication, routing, and dispatching [FR-6, FR-7, FR-8, FR-11]."""
    trigger = AlertTrigger(
        rule_name="High Past Due Threshold",
        financial_criteria="past_due_balance_exceeded",
        threshold_amount=Decimal("5000.00"),
        urgency="High",
        is_active=True,
    )
    db_session.add(trigger)
    db_session.commit()

    email_adapter = DummyEmailAdapter()
    sms_adapter = DummySMSAdapter()
    in_app_adapter = DummyInAppAdapter()

    engine = AlertEvaluatorEngine(
        session=db_session,
        email_port=email_adapter,
        sms_port=sms_adapter,
        in_app_port=in_app_adapter,
    )

    event = EvaluationEvent(
        financial_criteria="past_due_balance_exceeded",
        value=7500.00,
        entity_id="inv-001",
        event_type="invoice_overdue",
        role_name="financial_admin",
        recipient_id=uuid.uuid4(),
        recipient_email="admin@example.com",
        recipient_phone="+15550001111",
    )

    # First evaluation: triggers and is not suppressed
    alerts = engine.evaluate_event(event)
    assert len(alerts) == 1
    routed = alerts[0]
    assert routed.rule_name == "High Past Due Threshold"
    assert routed.is_suppressed is False
    assert routed.channels == ["InApp", "Email", "SMS"]

    # Dispatch non-suppressed alert
    results = engine.dispatch_routed_alert(routed)
    assert results["InApp"].success is True
    assert results["Email"].success is True
    assert results["SMS"].success is True

    assert len(email_adapter.payloads) == 1
    assert len(sms_adapter.payloads) == 1
    assert len(in_app_adapter.payloads) == 1

    # Second evaluation immediately after: should be suppressed
    alerts2 = engine.evaluate_event(event)
    assert len(alerts2) == 1
    assert alerts2[0].is_suppressed is True

    # Dispatching suppressed alert skips sending
    results2 = engine.dispatch_routed_alert(alerts2[0])
    assert results2 == {}
    assert len(email_adapter.payloads) == 1  # count unchanged
