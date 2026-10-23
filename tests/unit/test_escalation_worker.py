"""Unit tests for Celery SLA escalation worker task [FR-005, NFR-004]."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.deal import ActionItem, Deal
from src.services.action_items.service import ActionItemService
from src.workers.escalation_worker import (
    celery_app,
    process_sla_breach_scans,
    run_scheduled_sla_scan,
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


@pytest.fixture
def sample_deal(db_session: Session) -> Deal:
    """Fixture creating a sample Deal for testing."""
    deal = Deal(
        title="Acme Corp Expansion",
        deal_value=250000.0,
        milestone_stage="Contract Negotiation",
        win_probability=0.8,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)
    return deal


def test_process_sla_breach_scans_happy_path(db_session: Session, sample_deal: Deal):
    """Verify SLA breach scan escalates overdue action items and returns summary stats [FR-005, NFR-004]."""
    service = ActionItemService(db=db_session)

    # Overdue action item
    past_due = datetime.now(timezone.utc) - timedelta(hours=5)
    item_overdue = service.create_action_item(
        deal_id=sample_deal.id,
        title="Submit security review questionnaire",
        assigned_owner_id="security_lead",
        target_due_date=past_due,
        sla_threshold_hours=24,
    )

    # Future action item
    future_due = datetime.now(timezone.utc) + timedelta(hours=24)
    service.create_action_item(
        deal_id=sample_deal.id,
        title="Schedule legal sync",
        assigned_owner_id="legal_lead",
        target_due_date=future_due,
        sla_threshold_hours=24,
    )

    result = process_sla_breach_scans(db=db_session, correlation_id="test-cid-123")

    assert result["status"] == "success"
    assert result["scanned_count"] == 2
    assert result["escalated_count"] == 1
    assert result["suppressed_count"] == 0
    assert result["correlation_id"] == "test-cid-123"
    assert len(result["escalated_items"]) == 1
    assert result["escalated_items"][0]["id"] == item_overdue["id"]

    # Verify persisted escalation state in database
    db_item = db_session.query(ActionItem).filter(ActionItem.id == item_overdue["id"]).first()
    assert db_item is not None
    assert db_item.is_escalated is True
    assert db_item.escalated_on is not None


def test_process_sla_breach_scans_suppresses_duplicate_alerts(db_session: Session, sample_deal: Deal):
    """Verify SLA breach scan suppresses duplicate escalation alerts for previously escalated items [FR-005]."""
    service = ActionItemService(db=db_session)
    past_due = datetime.now(timezone.utc) - timedelta(hours=10)

    service.create_action_item(
        deal_id=sample_deal.id,
        title="Overdue pricing approval",
        assigned_owner_id="finance_lead",
        target_due_date=past_due,
        sla_threshold_hours=24,
    )

    # First scan: escalates item
    scan1 = process_sla_breach_scans(db=db_session)
    assert scan1["escalated_count"] == 1
    assert scan1["suppressed_count"] == 0

    # Second scan: suppresses duplicate alert
    scan2 = process_sla_breach_scans(db=db_session)
    assert scan2["escalated_count"] == 0
    assert scan2["suppressed_count"] == 1


def test_process_sla_breach_scans_dispatches_notifications(db_session: Session, sample_deal: Deal):
    """Verify notification port receives escalation alerts when configured [FR-005]."""
    service = ActionItemService(db=db_session)
    past_due = datetime.now(timezone.utc) - timedelta(hours=12)

    item = service.create_action_item(
        deal_id=sample_deal.id,
        title="Urgent executive sponsor outreach",
        assigned_owner_id="vp_sales",
        target_due_date=past_due,
        sla_threshold_hours=24,
    )

    mock_notification_port = MagicMock()
    result = process_sla_breach_scans(
        db=db_session,
        notification_port=mock_notification_port,
    )

    assert result["escalated_count"] == 1
    mock_notification_port.send_escalation_alert.assert_called_once()
    call_kwargs = mock_notification_port.send_escalation_alert.call_args.kwargs
    assert call_kwargs["action_item_id"] == item["id"]
    assert call_kwargs["deal_id"] == sample_deal.id
    assert call_kwargs["assigned_owner_id"] == "vp_sales"


def test_process_sla_breach_scans_auto_db_session(sample_deal: Deal):
    """Verify process_sla_breach_scans handles db Session creation and cleanup when db is None."""
    with patch("src.workers.escalation_worker.SessionLocal") as mock_session_local:
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        # Mock ActionItemService scan
        with patch("src.workers.escalation_worker.ActionItemService") as mock_service_cls:
            mock_service_inst = MagicMock()
            mock_service_inst.scan_and_escalate_sla_breaches.return_value = {
                "scanned_count": 0,
                "escalated_count": 0,
                "suppressed_count": 0,
                "escalated_items": [],
            }
            mock_service_cls.return_value = mock_service_inst

            res = process_sla_breach_scans(db=None)

            assert res["status"] == "success"
            mock_session_local.assert_called_once()
            mock_db.close.assert_called_once()


def test_run_scheduled_sla_scan_task(db_session: Session, sample_deal: Deal):
    """Verify scheduled Celery task invocation parses ISO date string and executes scan [FR-005]."""
    past_due = datetime(2026, 5, 1, 10, 0, 0, tzinfo=timezone.utc)
    eval_time = datetime(2026, 5, 1, 18, 0, 0, tzinfo=timezone.utc)

    service = ActionItemService(db=db_session)
    service.create_action_item(
        deal_id=sample_deal.id,
        title="Breached task",
        assigned_owner_id="owner_1",
        target_due_date=past_due,
        sla_threshold_hours=4,
    )

    with patch("src.workers.escalation_worker.SessionLocal", return_value=db_session):
        db_session.close = MagicMock()

        res = run_scheduled_sla_scan(
            current_time_iso=eval_time.isoformat()
        )

        assert res["status"] == "success"
        assert res["scanned_count"] == 1
        assert res["escalated_count"] == 1
