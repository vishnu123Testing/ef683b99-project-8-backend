"""Unit tests for StakeholderService in backend/src/services/stakeholders/service.py."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.deal import Deal, Stakeholder, StakeholderActivity
from src.services.stakeholders.service import (
    InvalidActivityError,
    InvalidStakeholderError,
    StakeholderDealNotFoundError,
    StakeholderNotFoundError,
    StakeholderService,
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
    """Fixture creating a sample deal for stakeholder testing."""
    deal = Deal(
        title="Acme Cloud Platform Deal",
        deal_value=250000.0,
        milestone_stage="Proposal",
        win_probability=0.60,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)
    return deal


def test_create_stakeholder_success(db_session: Session, sample_deal: Deal):
    """Test mapping a new stakeholder contact to a deal [FR-006]."""
    service = StakeholderService(db=db_session)

    stakeholder = service.create_stakeholder(
        deal_id=sample_deal.id,
        full_name="Jane Doe",
        email="jane.doe@acme.com",
        org_role="VP of Engineering",
        influence_level="High",
    )

    assert stakeholder["id"] is not None
    assert stakeholder["deal_id"] == sample_deal.id
    assert stakeholder["full_name"] == "Jane Doe"
    assert stakeholder["email"] == "jane.doe@acme.com"
    assert stakeholder["org_role"] == "VP of Engineering"
    assert stakeholder["influence_level"] == "High"
    assert stakeholder["is_active"] is True

    # Check persistence in database
    db_record = db_session.query(Stakeholder).filter(Stakeholder.id == stakeholder["id"]).first()
    assert db_record is not None
    assert db_record.full_name == "Jane Doe"


def test_create_stakeholder_validation_errors(db_session: Session, sample_deal: Deal):
    """Test validation errors when creating stakeholders with invalid inputs [FR-006]."""
    service = StakeholderService(db=db_session)

    # Empty name
    with pytest.raises(InvalidStakeholderError, match="full name cannot be empty"):
        service.create_stakeholder(sample_deal.id, "", "jane@acme.com", "CTO", "High")

    # Invalid email
    with pytest.raises(InvalidStakeholderError, match="valid email address"):
        service.create_stakeholder(sample_deal.id, "Jane Doe", "invalid-email", "CTO", "High")

    # Empty org role
    with pytest.raises(InvalidStakeholderError, match="role cannot be empty"):
        service.create_stakeholder(sample_deal.id, "Jane Doe", "jane@acme.com", "", "High")

    # Invalid influence level
    with pytest.raises(InvalidStakeholderError, match="Invalid influence level"):
        service.create_stakeholder(sample_deal.id, "Jane Doe", "jane@acme.com", "CTO", "Extreme")

    # Deal not found
    with pytest.raises(StakeholderDealNotFoundError):
        service.create_stakeholder("non-existent-deal-id", "Jane Doe", "jane@acme.com", "CTO", "High")


def test_get_and_update_stakeholder(db_session: Session, sample_deal: Deal):
    """Test retrieving and updating stakeholder details and influence tracking [FR-006]."""
    service = StakeholderService(db=db_session)

    created = service.create_stakeholder(
        sample_deal.id, "John Smith", "john@acme.com", "Procurement Manager", "Medium"
    )
    stk_id = created["id"]

    fetched = service.get_stakeholder(stk_id)
    assert fetched["id"] == stk_id
    assert fetched["influence_level"] == "Medium"

    # Update role and influence level
    updated = service.update_stakeholder(
        stk_id,
        org_role="Chief Procurement Officer",
        influence_level="High",
    )
    assert updated["org_role"] == "Chief Procurement Officer"
    assert updated["influence_level"] == "High"

    # Verify not found error
    with pytest.raises(StakeholderNotFoundError):
        service.get_stakeholder("invalid-stk-id")

    with pytest.raises(StakeholderNotFoundError):
        service.update_stakeholder("invalid-stk-id", full_name="New Name")


def test_list_stakeholders_for_deal(db_session: Session, sample_deal: Deal):
    """Test listing mapped stakeholders for a deal filtering by active state [FR-006]."""
    service = StakeholderService(db=db_session)

    s1 = service.create_stakeholder(sample_deal.id, "Alice", "alice@acme.com", "CEO", "High")
    s2 = service.create_stakeholder(sample_deal.id, "Bob", "bob@acme.com", "DevOps Lead", "Low")

    # Deactivate Bob
    service.update_stakeholder(s2["id"], is_active=False)

    active_stks = service.list_stakeholders_for_deal(sample_deal.id, active_only=True)
    assert len(active_stks) == 1
    assert active_stks[0]["id"] == s1["id"]

    all_stks = service.list_stakeholders_for_deal(sample_deal.id, active_only=False)
    assert len(all_stks) == 2


def test_delete_stakeholder(db_session: Session, sample_deal: Deal):
    """Test deleting a stakeholder record and verifying cascading deletion [FR-006]."""
    service = StakeholderService(db=db_session)

    stk = service.create_stakeholder(sample_deal.id, "Charlie", "charlie@acme.com", "Architect", "Medium")
    stk_id = stk["id"]

    service.delete_stakeholder(stk_id)

    with pytest.raises(StakeholderNotFoundError):
        service.get_stakeholder(stk_id)

    with pytest.raises(StakeholderNotFoundError):
        service.delete_stakeholder(stk_id)


def test_log_activity_and_feed(db_session: Session, sample_deal: Deal):
    """Test logging stakeholder activities and retrieving a chronological activity feed [FR-007]."""
    service = StakeholderService(db=db_session)

    stk = service.create_stakeholder(sample_deal.id, "Diana Prince", "diana@acme.com", "VP Product", "High")
    stk_id = stk["id"]

    now = datetime.now(timezone.utc)
    t1 = now - timedelta(hours=3)
    t2 = now - timedelta(hours=1)

    act1 = service.log_activity(
        stakeholder_id=stk_id,
        deal_id=sample_deal.id,
        activity_type="Meeting",
        summary="Initial discovery call to review requirements.",
        occurred_on=t1,
    )

    act2 = service.log_activity(
        stakeholder_id=stk_id,
        deal_id=sample_deal.id,
        activity_type="Email",
        summary="Followed up with product roadmap presentation deck.",
        occurred_on=t2,
    )

    assert act1["activity_type"] == "Meeting"
    assert act1["stakeholder_name"] == "Diana Prince"

    feed = service.get_activity_feed_for_deal(sample_deal.id)
    assert len(feed) == 2
    # Verify chronological descending order (t2 before t1)
    assert feed[0]["id"] == act2["id"]
    assert feed[1]["id"] == act1["id"]
    assert feed[0]["stakeholder_name"] == "Diana Prince"


def test_log_activity_validation_errors(db_session: Session, sample_deal: Deal):
    """Test validation errors when logging activities [FR-007]."""
    service = StakeholderService(db=db_session)

    stk = service.create_stakeholder(sample_deal.id, "Eve", "eve@acme.com", "CIO", "High")

    # Empty summary
    with pytest.raises(InvalidActivityError, match="summary cannot be empty"):
        service.log_activity(stk["id"], sample_deal.id, "Call", "")

    # Stakeholder not found
    with pytest.raises(StakeholderNotFoundError):
        service.log_activity("invalid-stk-id", sample_deal.id, "Call", "Spoke on phone")

    # Deal not found
    with pytest.raises(StakeholderDealNotFoundError):
        service.log_activity(stk["id"], "invalid-deal-id", "Call", "Spoke on phone")

    # Stakeholder mapped to different deal
    other_deal = Deal(title="Other Deal", deal_value=50000.0, milestone_stage="Discovery")
    db_session.add(other_deal)
    db_session.commit()

    with pytest.raises(InvalidActivityError, match="not mapped to deal"):
        service.log_activity(stk["id"], other_deal.id, "Call", "Spoke on phone")


def test_get_stakeholders_and_activities(db_session: Session, sample_deal: Deal):
    """Test retrieving combined stakeholder list and chronological activity feed for a deal [FR-006, FR-007]."""
    service = StakeholderService(db=db_session)

    stk = service.create_stakeholder(sample_deal.id, "Frank", "frank@acme.com", "IT Director", "Medium")
    service.log_activity(stk["id"], sample_deal.id, "Call", "Technical architecture review.")

    combined = service.get_stakeholders_and_activities(sample_deal.id)

    assert "stakeholders" in combined
    assert "recent_activities" in combined
    assert len(combined["stakeholders"]) == 1
    assert len(combined["recent_activities"]) == 1
    assert combined["stakeholders"][0]["full_name"] == "Frank"
    assert combined["recent_activities"][0]["summary"] == "Technical architecture review."
