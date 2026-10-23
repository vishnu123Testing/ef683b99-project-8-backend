"""Unit tests for Deal, ActionItem, Stakeholder, StakeholderActivity, and DealForecast ORM models."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.deal import (
    ActionItem,
    Deal,
    DealForecast,
    Stakeholder,
    StakeholderActivity,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session for testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_deal_model_defaults_and_to_dict():
    """Test Deal model initialization, representation, and to_dict conversion."""
    now = datetime.now(timezone.utc)
    deal = Deal(
        title="Enterprise License Renewal",
        deal_value=50000.0,
        milestone_stage="qualification",
        win_probability=0.75,
        created_on=now,
        updated_on=now,
    )

    assert deal.title == "Enterprise License Renewal"
    assert deal.deal_value == 50000.0
    assert deal.milestone_stage == "qualification"
    assert deal.win_probability == 0.75
    assert deal.created_on == now
    assert deal.updated_on == now

    repr_str = repr(deal)
    assert "Enterprise License Renewal" in repr_str
    assert "qualification" in repr_str

    d = deal.to_dict()
    assert d["title"] == "Enterprise License Renewal"
    assert d["deal_value"] == 50000.0
    assert d["milestone_stage"] == "qualification"
    assert d["win_probability"] == 0.75
    assert d["created_on"] == now.isoformat()


def test_deal_db_persistence_and_relationships(db_session: Session):
    """Test inserting Deal and related models into the database."""
    deal = Deal(
        title="Cloud Migration Project",
        deal_value=120000.0,
        milestone_stage="proposal",
        win_probability=0.6,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)

    assert deal.id is not None
    assert isinstance(deal.id, str)

    # Add ActionItem
    due_date = datetime.now(timezone.utc)
    action_item = ActionItem(
        deal_id=deal.id,
        title="Send proposal deck",
        assigned_owner_id="usr_123",
        target_due_date=due_date,
        sla_threshold_hours=24,
    )
    db_session.add(action_item)

    # Add Stakeholder
    stakeholder = Stakeholder(
        deal_id=deal.id,
        full_name="Jane Doe",
        email="jane.doe@example.com",
        org_role="VP of Engineering",
        influence_level="High",
    )
    db_session.add(stakeholder)
    db_session.commit()
    db_session.refresh(stakeholder)

    # Add StakeholderActivity
    occurred_now = datetime.now(timezone.utc)
    activity = StakeholderActivity(
        stakeholder_id=stakeholder.id,
        deal_id=deal.id,
        activity_type="Meeting",
        summary="Technical review call completed successfully.",
        occurred_on=occurred_now,
    )
    db_session.add(activity)

    # Add DealForecast
    forecast = DealForecast(
        deal_id=deal.id,
        estimated_completion_date=due_date,
        confidence_score_pct=85.0,
        status_code="READY",
    )
    db_session.add(forecast)
    db_session.commit()

    # Query back from DB and verify relationships
    queried_deal = db_session.query(Deal).filter_by(id=deal.id).first()
    assert queried_deal is not None
    assert len(queried_deal.action_items) == 1
    assert queried_deal.action_items[0].title == "Send proposal deck"
    assert len(queried_deal.stakeholders) == 1
    assert queried_deal.stakeholders[0].full_name == "Jane Doe"
    assert len(queried_deal.stakeholder_activities) == 1
    assert queried_deal.stakeholder_activities[0].activity_type == "Meeting"
    assert queried_deal.forecast is not None
    assert queried_deal.forecast.confidence_score_pct == 85.0
    assert queried_deal.forecast.status_code == "READY"


def test_action_item_to_dict():
    """Test ActionItem to_dict method."""
    now = datetime.now(timezone.utc)
    action = ActionItem(
        deal_id="deal_1",
        title="Follow up call",
        assigned_owner_id="owner_1",
        target_due_date=now,
        sla_threshold_hours=48,
        is_completed=True,
        completed_on=now,
        is_escalated=False,
    )
    d = action.to_dict()
    assert d["deal_id"] == "deal_1"
    assert d["title"] == "Follow up call"
    assert d["sla_threshold_hours"] == 48
    assert d["is_completed"] is True
    assert d["completed_on"] == now.isoformat()


def test_stakeholder_and_activity_to_dict():
    """Test Stakeholder and StakeholderActivity to_dict methods."""
    now = datetime.now(timezone.utc)
    stk = Stakeholder(
        deal_id="deal_1",
        full_name="John Smith",
        email="john@example.com",
        org_role="CTO",
        influence_level="High",
        is_active=True,
    )
    d_stk = stk.to_dict()
    assert d_stk["full_name"] == "John Smith"
    assert d_stk["email"] == "john@example.com"
    assert d_stk["org_role"] == "CTO"

    act = StakeholderActivity(
        stakeholder_id="stk_1",
        deal_id="deal_1",
        activity_type="Email",
        summary="Sent follow up email.",
        occurred_on=now,
    )
    d_act = act.to_dict()
    assert d_act["activity_type"] == "Email"
    assert d_act["summary"] == "Sent follow up email."
    assert d_act["occurred_on"] == now.isoformat()


def test_deal_forecast_to_dict():
    """Test DealForecast to_dict method."""
    now = datetime.now(timezone.utc)
    forecast = DealForecast(
        deal_id="deal_1",
        estimated_completion_date=None,
        confidence_score_pct=None,
        status_code="INSUFFICIENT_DATA",
        calculated_on=now,
    )
    d = forecast.to_dict()
    assert d["deal_id"] == "deal_1"
    assert d["estimated_completion_date"] is None
    assert d["confidence_score_pct"] is None
    assert d["status_code"] == "INSUFFICIENT_DATA"
    assert d["calculated_on"] == now.isoformat()


def test_cascade_deletion(db_session: Session):
    """Test that deleting a deal cascades deletion to related models."""
    deal = Deal(title="Test Cascade Deal", milestone_stage="lead")
    db_session.add(deal)
    db_session.commit()

    action = ActionItem(
        deal_id=deal.id,
        title="Task 1",
        assigned_owner_id="user_1",
        target_due_date=datetime.now(timezone.utc),
    )
    stk = Stakeholder(
        deal_id=deal.id,
        full_name="Alice",
        email="alice@example.com",
        org_role="Manager",
        influence_level="Medium",
    )
    db_session.add_all([action, stk])
    db_session.commit()

    act = StakeholderActivity(
        stakeholder_id=stk.id,
        deal_id=deal.id,
        activity_type="Call",
        summary="Initial contact",
        occurred_on=datetime.now(timezone.utc),
    )
    forecast = DealForecast(
        deal_id=deal.id,
        status_code="COMPUTING",
    )
    db_session.add_all([act, forecast])
    db_session.commit()

    # Delete the deal
    db_session.delete(deal)
    db_session.commit()

    # Verify that action items, stakeholders, activities, and forecast were cascade-deleted
    assert db_session.query(ActionItem).filter_by(deal_id=deal.id).count() == 0
    assert db_session.query(Stakeholder).filter_by(deal_id=deal.id).count() == 0
    assert db_session.query(StakeholderActivity).filter_by(deal_id=deal.id).count() == 0
    assert db_session.query(DealForecast).filter_by(deal_id=deal.id).count() == 0
