"""Unit tests for ActionItemService in backend/src/services/action_items/service.py."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.deal import ActionItem, Deal, Stakeholder, StakeholderActivity
from src.services.action_items.service import (
    ActionItemDealNotFoundError,
    ActionItemNotFoundError,
    ActionItemService,
    InvalidActionItemError,
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
    """Fixture providing a sample Deal record in the database."""
    deal = Deal(
        title="Enterprise Software Deal",
        deal_value=120000.0,
        milestone_stage="Solution Proposal",
        win_probability=0.6,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)
    return deal


def test_create_action_item_success(db_session: Session, sample_deal: Deal):
    """Test creating an action item for a deal [FR-004]."""
    service = ActionItemService(db=db_session)
    due_date = datetime.now(timezone.utc) + timedelta(days=2)

    result = service.create_action_item(
        deal_id=sample_deal.id,
        title="Send revised pricing terms",
        assigned_owner_id="john_doe",
        target_due_date=due_date,
        sla_threshold_hours=48,
    )

    assert result["id"] is not None
    assert result["deal_id"] == sample_deal.id
    assert result["title"] == "Send revised pricing terms"
    assert result["assigned_owner_id"] == "john_doe"
    assert result["sla_threshold_hours"] == 48
    assert result["is_completed"] is False
    assert result["is_escalated"] is False

    # Verify persisted in database
    item_in_db = db_session.query(ActionItem).filter(ActionItem.id == result["id"]).first()
    assert item_in_db is not None
    assert item_in_db.title == "Send revised pricing terms"


def test_create_action_item_invalid_deal(db_session: Session):
    """Test creating an action item with a non-existent deal ID raises error."""
    service = ActionItemService(db=db_session)
    due_date = datetime.now(timezone.utc) + timedelta(days=1)

    with pytest.raises(ActionItemDealNotFoundError):
        service.create_action_item(
            deal_id="non-existent-deal-id",
            title="Follow up call",
            assigned_owner_id="jane_doe",
            target_due_date=due_date,
        )


def test_create_action_item_validation_errors(db_session: Session, sample_deal: Deal):
    """Test validation checks on title and assigned owner ID."""
    service = ActionItemService(db=db_session)
    due_date = datetime.now(timezone.utc) + timedelta(days=1)

    with pytest.raises(InvalidActionItemError, match="title"):
        service.create_action_item(
            deal_id=sample_deal.id,
            title="   ",
            assigned_owner_id="jane_doe",
            target_due_date=due_date,
        )

    with pytest.raises(InvalidActionItemError, match="owner"):
        service.create_action_item(
            deal_id=sample_deal.id,
            title="Valid title",
            assigned_owner_id="",
            target_due_date=due_date,
        )


def test_assign_action_item(db_session: Session, sample_deal: Deal):
    """Test reassigning an action item and updating target due date [FR-004]."""
    service = ActionItemService(db=db_session)
    due_date = datetime.now(timezone.utc) + timedelta(days=1)

    created = service.create_action_item(
        deal_id=sample_deal.id,
        title="Prepare SOW document",
        assigned_owner_id="owner_1",
        target_due_date=due_date,
    )

    new_due_date = datetime.now(timezone.utc) + timedelta(days=3)
    updated = service.assign_action_item(
        item_id=created["id"],
        assigned_owner_id="owner_2",
        target_due_date=new_due_date,
    )

    assert updated["assigned_owner_id"] == "owner_2"
    assert updated["target_due_date"] is not None


def test_list_action_items_for_deal(db_session: Session, sample_deal: Deal):
    """Test listing action items associated with a specific deal [FR-004]."""
    service = ActionItemService(db=db_session)
    due_date = datetime.now(timezone.utc) + timedelta(days=1)

    service.create_action_item(sample_deal.id, "Task 1", "owner_a", due_date)
    service.create_action_item(sample_deal.id, "Task 2", "owner_b", due_date)

    items = service.list_action_items_for_deal(sample_deal.id)
    assert len(items) == 2
    titles = {item["title"] for item in items}
    assert titles == {"Task 1", "Task 2"}


def test_complete_action_item_clears_escalation(db_session: Session, sample_deal: Deal):
    """Test marking an action item complete clears escalation state [FR-005, Story 2.2]."""
    service = ActionItemService(db=db_session)
    due_date = datetime.now(timezone.utc) - timedelta(hours=5)

    # Create an item and escalate it
    created = service.create_action_item(sample_deal.id, "Overdue Task", "owner_a", due_date)
    item_id = created["id"]

    # Scan to trigger escalation
    scan_res = service.scan_and_escalate_sla_breaches()
    assert scan_res["escalated_count"] == 1

    item_before = service.get_action_item(item_id)
    assert item_before["is_escalated"] is True

    # Complete the task
    completed = service.complete_action_item(item_id)
    assert completed["is_completed"] is True
    assert completed["completed_on"] is not None
    assert completed["is_escalated"] is False
    assert completed["escalated_on"] is None


def test_complete_action_item_records_stakeholder_activity(db_session: Session, sample_deal: Deal):
    """Test completing an action item creates a StakeholderActivity log when stakeholder exists [Story 2.2]."""
    # Create a stakeholder for the deal
    stakeholder = Stakeholder(
        deal_id=sample_deal.id,
        full_name="Alice Smith",
        email="alice@example.com",
        org_role="Decision Maker",
        influence_level="High",
    )
    db_session.add(stakeholder)
    db_session.commit()

    service = ActionItemService(db=db_session)
    due_date = datetime.now(timezone.utc) + timedelta(days=1)
    created = service.create_action_item(sample_deal.id, "Review contract draft", "owner_a", due_date)

    service.complete_action_item(created["id"])

    # Verify StakeholderActivity record created
    activity = db_session.query(StakeholderActivity).filter(StakeholderActivity.deal_id == sample_deal.id).first()
    assert activity is not None
    assert "Action item completed" in activity.summary


def test_scan_and_escalate_sla_breaches_and_suppress_duplicates(db_session: Session, sample_deal: Deal):
    """Test SLA breach scanning escalates overdue items and suppresses duplicate alerts [FR-005, NFR-004]."""
    mock_notification_port = MagicMock()
    service = ActionItemService(db=db_session, notification_port=mock_notification_port)

    # Overdue due_date
    past_due = datetime.now(timezone.utc) - timedelta(hours=10)
    item1 = service.create_action_item(
        deal_id=sample_deal.id,
        title="Overdue task 1",
        assigned_owner_id="owner_x",
        target_due_date=past_due,
        sla_threshold_hours=24,
    )

    # Future due_date (not breached)
    future_due = datetime.now(timezone.utc) + timedelta(hours=24)
    service.create_action_item(
        deal_id=sample_deal.id,
        title="Future task 2",
        assigned_owner_id="owner_y",
        target_due_date=future_due,
        sla_threshold_hours=24,
    )

    # First scan: should escalate item1
    scan1 = service.scan_and_escalate_sla_breaches()
    assert scan1["scanned_count"] == 2
    assert scan1["escalated_count"] == 1
    assert scan1["suppressed_count"] == 0
    assert scan1["escalated_items"][0]["id"] == item1["id"]

    # Verify notification dispatched
    assert mock_notification_port.send_escalation_alert.called

    # Reset mock call counter
    mock_notification_port.reset_mock()

    # Second scan: item1 is already escalated, so duplicate alert is suppressed
    scan2 = service.scan_and_escalate_sla_breaches()
    assert scan2["scanned_count"] == 2
    assert scan2["escalated_count"] == 0
    assert scan2["suppressed_count"] == 1

    # Verify notification was NOT dispatched again
    assert not mock_notification_port.send_escalation_alert.called


def test_clear_escalation(db_session: Session, sample_deal: Deal):
    """Test explicitly clearing an escalation flag."""
    service = ActionItemService(db=db_session)
    past_due = datetime.now(timezone.utc) - timedelta(hours=5)

    created = service.create_action_item(sample_deal.id, "Task to escalate", "owner_a", past_due)
    service.scan_and_escalate_sla_breaches()

    item_escalated = service.get_action_item(created["id"])
    assert item_escalated["is_escalated"] is True

    cleared = service.clear_escalation(created["id"])
    assert cleared["is_escalated"] is False
    assert cleared["escalated_on"] is None


def test_delete_action_item(db_session: Session, sample_deal: Deal):
    """Test deleting an action item."""
    service = ActionItemService(db=db_session)
    due_date = datetime.now(timezone.utc) + timedelta(days=1)

    created = service.create_action_item(sample_deal.id, "Task to delete", "owner_a", due_date)
    item_id = created["id"]

    service.delete_action_item(item_id)

    with pytest.raises(ActionItemNotFoundError):
        service.get_action_item(item_id)


def test_delete_non_existent_action_item_raises_error(db_session: Session):
    """Test deleting a non-existent action item raises ActionItemNotFoundError."""
    service = ActionItemService(db=db_session)
    with pytest.raises(ActionItemNotFoundError):
        service.delete_action_item("non-existent-id")
