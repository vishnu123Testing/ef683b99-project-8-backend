"""Unit tests for AlertTrigger, NotificationPreference, NotificationLog, and AlertSuppression models in backend/src/models/alerting.py."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.alerting import (
    AlertSuppression,
    AlertTrigger,
    NotificationLog,
    NotificationPreference,
)
from src.models.database import Base


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session for testing."""
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


def test_alert_trigger_model_instantiation_repr_to_dict():
    """Test AlertTrigger initialization, repr, and to_dict method."""
    trigger_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    trigger = AlertTrigger(
        trigger_id=trigger_id,
        rule_name="High Past Due Balance",
        financial_criteria="past_due_balance_exceeded",
        threshold_amount=Decimal("5000.00"),
        urgency="High",
        is_active=True,
        created_at=now,
        updated_at=now,
    )

    assert trigger.trigger_id == trigger_id
    assert trigger.rule_name == "High Past Due Balance"
    assert trigger.financial_criteria == "past_due_balance_exceeded"
    assert trigger.threshold_amount == Decimal("5000.00")
    assert trigger.urgency == "High"
    assert trigger.is_active is True

    repr_str = repr(trigger)
    assert "AlertTrigger" in repr_str
    assert "High Past Due Balance" in repr_str

    d = trigger.to_dict()
    assert d["trigger_id"] == str(trigger_id)
    assert d["rule_name"] == "High Past Due Balance"
    assert d["threshold_amount"] == 5000.00
    assert d["urgency"] == "High"
    assert d["is_active"] is True


def test_notification_preference_model_instantiation_repr_to_dict():
    """Test NotificationPreference initialization, repr, and to_dict method."""
    pref_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    pref = NotificationPreference(
        preference_id=pref_id,
        role_name="financial_admin",
        event_type="invoice_overdue",
        urgency="High",
        channels_json=["InApp", "Email", "SMS"],
        version=1,
        created_at=now,
        updated_at=now,
    )

    assert pref.preference_id == pref_id
    assert pref.role_name == "financial_admin"
    assert pref.event_type == "invoice_overdue"
    assert pref.urgency == "High"
    assert pref.channels_json == ["InApp", "Email", "SMS"]
    assert pref.version == 1

    repr_str = repr(pref)
    assert "NotificationPreference" in repr_str
    assert "financial_admin" in repr_str

    d = pref.to_dict()
    assert d["preference_id"] == str(pref_id)
    assert d["role_name"] == "financial_admin"
    assert d["channels_json"] == ["InApp", "Email", "SMS"]
    assert d["version"] == 1


def test_notification_log_model_instantiation_repr_to_dict():
    """Test NotificationLog initialization, repr, and to_dict method."""
    notif_id = uuid.uuid4()
    recipient_id = uuid.uuid4()
    trigger_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    corr_id = "corr-123456"

    log_item = NotificationLog(
        notification_id=notif_id,
        recipient_id=recipient_id,
        trigger_id=trigger_id,
        urgency="High",
        channel="Email",
        delivery_status="Sent",
        dispatched_at=now,
        correlation_id=corr_id,
    )

    assert log_item.notification_id == notif_id
    assert log_item.recipient_id == recipient_id
    assert log_item.trigger_id == trigger_id
    assert log_item.urgency == "High"
    assert log_item.channel == "Email"
    assert log_item.delivery_status == "Sent"
    assert log_item.correlation_id == corr_id

    repr_str = repr(log_item)
    assert "NotificationLog" in repr_str
    assert corr_id in repr_str

    d = log_item.to_dict()
    assert d["notification_id"] == str(notif_id)
    assert d["recipient_id"] == str(recipient_id)
    assert d["trigger_id"] == str(trigger_id)
    assert d["channel"] == "Email"
    assert d["delivery_status"] == "Sent"


def test_alert_suppression_model_instantiation_repr_to_dict():
    """Test AlertSuppression initialization, repr, and to_dict method."""
    supp_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    expires = now + timedelta(minutes=15)
    dedup = "a1b2c3d4e5f67890"

    suppression = AlertSuppression(
        suppression_id=supp_id,
        dedup_hash=dedup,
        last_triggered_at=now,
        expires_at=expires,
    )

    assert suppression.suppression_id == supp_id
    assert suppression.dedup_hash == dedup
    assert suppression.last_triggered_at == now
    assert suppression.expires_at == expires

    repr_str = repr(suppression)
    assert "AlertSuppression" in repr_str
    assert dedup in repr_str

    d = suppression.to_dict()
    assert d["suppression_id"] == str(supp_id)
    assert d["dedup_hash"] == dedup
    assert d["last_triggered_at"] == now.isoformat()
    assert d["expires_at"] == expires.isoformat()


def test_alerting_models_db_persistence(db_session):
    """Test full database persistence, relationships, and queries for alerting models."""
    now = datetime.now(timezone.utc)

    # 1. Create AlertTrigger
    trigger = AlertTrigger(
        rule_name="Critical Overdue Invoice",
        financial_criteria="past_due_amount",
        threshold_amount=Decimal("10000.00"),
        urgency="High",
        is_active=True,
    )
    db_session.add(trigger)

    # 2. Create NotificationPreference
    pref = NotificationPreference(
        role_name="finance_manager",
        event_type="past_due_alert",
        urgency="High",
        channels_json=["InApp", "Email", "SMS"],
        version=1,
    )
    db_session.add(pref)

    # 3. Create AlertSuppression
    suppression = AlertSuppression(
        dedup_hash="hash_value_12345",
        last_triggered_at=now,
        expires_at=now + timedelta(minutes=30),
    )
    db_session.add(suppression)

    db_session.commit()

    # Query back trigger and verify ID generated
    fetched_trigger = db_session.query(AlertTrigger).filter_by(rule_name="Critical Overdue Invoice").first()
    assert fetched_trigger is not None
    assert fetched_trigger.trigger_id is not None

    # 4. Create NotificationLog linked to trigger
    recipient_id = uuid.uuid4()
    log_entry = NotificationLog(
        recipient_id=recipient_id,
        trigger_id=fetched_trigger.trigger_id,
        urgency="High",
        channel="SMS",
        delivery_status="Sent",
        dispatched_at=now,
        correlation_id="corr-999",
    )
    db_session.add(log_entry)
    db_session.commit()

    # Query back NotificationLog and verify relationship
    fetched_log = db_session.query(NotificationLog).filter_by(correlation_id="corr-999").first()
    assert fetched_log is not None
    assert fetched_log.trigger is not None
    assert fetched_log.trigger.rule_name == "Critical Overdue Invoice"
    assert len(fetched_trigger.notification_logs) == 1
    assert fetched_trigger.notification_logs[0].notification_id == fetched_log.notification_id

    # Verify preference query
    fetched_pref = db_session.query(NotificationPreference).filter_by(role_name="finance_manager").first()
    assert fetched_pref is not None
    assert fetched_pref.channels_json == ["InApp", "Email", "SMS"]

    # Verify suppression query
    fetched_suppression = db_session.query(AlertSuppression).filter_by(dedup_hash="hash_value_12345").first()
    assert fetched_suppression is not None
    assert fetched_suppression.suppression_id is not None
