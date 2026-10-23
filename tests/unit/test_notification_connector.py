"""Unit tests for NotificationConnector implementation [FR-9]."""

from datetime import date as date_type, datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.connectors.notification_connector import NotificationConnector
from src.models.contract import Contract, RenewalAlert
from src.models.database import Base
from src.services.contract.models import ContractStatus, RenewalThreshold
from src.services.contract.notification_port import (
    AlertSuppressedError,
    InvalidAlertThresholdError,
    NotificationDispatchError,
    RenewalAlertPayload,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for connector testing."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()


def test_notification_connector_init():
    """Verify initialization of NotificationConnector with defaults."""
    connector = NotificationConnector()
    assert connector.db is None
    assert connector.default_channel == "IN_APP"
    assert connector.enable_external_delivery is True


def test_dispatch_renewal_alert_in_memory_happy_path():
    """Verify dispatching alerts for 90, 60, and 30 day thresholds without database session."""
    connector = NotificationConnector()
    contract_id = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    for threshold in [RenewalThreshold.DAYS_90, RenewalThreshold.DAYS_60, RenewalThreshold.DAYS_30]:
        payload = RenewalAlertPayload(
            contract_id=contract_id,
            threshold_days=threshold,
            contract_title="Master Software Agreement",
            counterparty="Acme Corp",
            expires_on=exp_date,
            contract_status=ContractStatus.ACTIVE,
            recipients=["admin@acme.com"],
        )
        result = connector.dispatch_renewal_alert(payload)

        assert result.contract_id == contract_id
        assert result.threshold_days == threshold.value
        assert result.is_suppressed is False
        assert result.channel == "IN_APP"
        assert result.recipients == ["admin@acme.com"]
        assert result.details["contract_title"] == "Master Software Agreement"

    alerts = connector.list_dispatched_alerts(contract_id=contract_id)
    assert len(alerts) == 3


def test_dispatch_renewal_alert_suppresses_terminated_or_expired():
    """Verify that terminated or expired contract alerts are suppressed [FR-9]."""
    connector = NotificationConnector()
    contract_id = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    for status in [ContractStatus.TERMINATED, ContractStatus.EXPIRED, "RENEWED"]:
        payload = RenewalAlertPayload(
            contract_id=contract_id,
            threshold_days=90,
            contract_title="Terminated Agreement",
            counterparty="Old Client",
            expires_on=exp_date,
            contract_status=status,
        )
        assert connector.should_suppress_alert(status, contract_id, 90) is True

        with pytest.raises(AlertSuppressedError):
            connector.dispatch_renewal_alert(payload)


def test_dispatch_renewal_alert_suppresses_duplicate_threshold():
    """Verify duplicate alert dispatch for the same threshold raises AlertSuppressedError."""
    connector = NotificationConnector()
    contract_id = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    payload = RenewalAlertPayload(
        contract_id=contract_id,
        threshold_days=60,
        contract_title="SLA Agreement",
        counterparty="Cloud Provider",
        expires_on=exp_date,
        contract_status=ContractStatus.ACTIVE,
    )

    result = connector.dispatch_renewal_alert(payload)
    assert result.threshold_days == 60

    with pytest.raises(AlertSuppressedError) as exc_info:
        connector.dispatch_renewal_alert(payload)
    assert "already dispatched" in str(exc_info.value)


def test_dispatch_renewal_alert_invalid_threshold():
    """Verify unsupported threshold days raise InvalidAlertThresholdError."""
    connector = NotificationConnector()
    contract_id = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    payload = RenewalAlertPayload(
        contract_id=contract_id,
        threshold_days=15,
        contract_title="Invalid Threshold",
        counterparty="Test Corp",
        expires_on=exp_date,
        contract_status=ContractStatus.ACTIVE,
    )

    with pytest.raises(InvalidAlertThresholdError):
        connector.dispatch_renewal_alert(payload)


def test_get_and_list_dispatched_alerts():
    """Verify retrieving and filtering dispatched alerts."""
    connector = NotificationConnector()
    c1 = uuid.uuid4()
    c2 = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    connector.dispatch_renewal_alert(
        RenewalAlertPayload(c1, 90, "Contract 1", "Vendor A", exp_date)
    )
    connector.dispatch_renewal_alert(
        RenewalAlertPayload(c1, 60, "Contract 1", "Vendor A", exp_date)
    )
    connector.dispatch_renewal_alert(
        RenewalAlertPayload(c2, 90, "Contract 2", "Vendor B", exp_date)
    )

    alert_c1_90 = connector.get_dispatched_alert(c1, 90)
    assert alert_c1_90 is not None
    assert alert_c1_90.contract_id == c1
    assert alert_c1_90.threshold_days == 90

    assert connector.get_dispatched_alert(c1, 30) is None

    c1_alerts = connector.list_dispatched_alerts(contract_id=c1)
    assert len(c1_alerts) == 2

    thresh_90_alerts = connector.list_dispatched_alerts(threshold_days=90)
    assert len(thresh_90_alerts) == 2


def test_cancel_pending_alerts():
    """Verify cancelling pending alerts suppresses future dispatches and returns count."""
    connector = NotificationConnector()
    contract_id = uuid.uuid4()
    exp_date = date_type(2026, 12, 31)

    # Dispatch 90-day alert first
    connector.dispatch_renewal_alert(
        RenewalAlertPayload(contract_id, 90, "Active SOW", "Partner Corp", exp_date)
    )

    # Cancel pending alerts (60 and 30 day alerts remain pending, so count should be 2)
    pending_count = connector.cancel_pending_alerts(contract_id, reason="Early Renewal")
    assert pending_count == 2

    assert connector.should_suppress_alert(ContractStatus.ACTIVE, contract_id, 60) is True

    payload = RenewalAlertPayload(
        contract_id=contract_id,
        threshold_days=60,
        contract_title="Active SOW",
        counterparty="Partner Corp",
        expires_on=exp_date,
        contract_status=ContractStatus.ACTIVE,
    )
    with pytest.raises(AlertSuppressedError) as exc_info:
        connector.dispatch_renewal_alert(payload)
    assert "Early Renewal" in str(exc_info.value)


def test_notification_connector_with_database_session(db_session):
    """Verify NotificationConnector database persistence with SQLAlchemy session."""
    contract_id = uuid.uuid4()

    # Create dummy contract record in DB
    contract_orm = Contract(
        id=contract_id,
        title="DB Contract",
        category="Contract",
        status="ACTIVE",
        counterparty="DB Corp",
        expires_on=date_type(2026, 12, 31),
    )
    db_session.add(contract_orm)
    db_session.commit()

    connector = NotificationConnector(db=db_session)
    exp_date = date_type(2026, 12, 31)

    payload = RenewalAlertPayload(
        contract_id=contract_id,
        threshold_days=90,
        contract_title="DB Contract",
        counterparty="DB Corp",
        expires_on=exp_date,
        contract_status=ContractStatus.ACTIVE,
    )

    result = connector.dispatch_renewal_alert(payload)
    db_session.commit()

    assert result.contract_id == contract_id
    assert result.threshold_days == 90

    # Verify ORM record was written to renewal_alerts table
    alert_in_db = (
        db_session.query(RenewalAlert)
        .filter(RenewalAlert.contract_id == contract_id, RenewalAlert.threshold_days == 90)
        .first()
    )
    assert alert_in_db is not None
    assert alert_in_db.id == result.alert_id

    # Query connector for dispatched alert
    retrieved = connector.get_dispatched_alert(contract_id, 90)
    assert retrieved is not None
    assert retrieved.id == result.alert_id

    # Duplicate dispatch should be suppressed
    assert connector.should_suppress_alert(ContractStatus.ACTIVE, contract_id, 90) is True
    with pytest.raises(AlertSuppressedError):
        connector.dispatch_renewal_alert(payload)
