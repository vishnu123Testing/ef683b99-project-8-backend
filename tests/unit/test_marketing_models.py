"""Unit tests for Territory, LeadMeeting, and FollowupTask ORM models."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from src.models.database import Base
from src.models.marketing import FollowupTask, LeadMeeting, Territory


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for testing marketing models."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_territory_model_to_dict_and_repr():
    """Test Territory initialization, repr, and to_dict method."""
    now = datetime.now(timezone.utc)
    territory = Territory(
        territory_code="US-WEST",
        territory_name="US West Coast",
        postal_code_patterns="90*, 91*, 92*",
        created_on=now,
    )

    assert territory.territory_code == "US-WEST"
    assert territory.territory_name == "US West Coast"
    assert territory.postal_code_patterns == "90*, 91*, 92*"
    assert territory.created_on == now

    repr_str = repr(territory)
    assert "US-WEST" in repr_str
    assert "US West Coast" in repr_str

    d = territory.to_dict()
    assert d["territory_code"] == "US-WEST"
    assert d["territory_name"] == "US West Coast"
    assert d["postal_code_patterns"] == "90*, 91*, 92*"
    assert d["created_on"] == now.isoformat()


def test_lead_meeting_model_to_dict_and_repr():
    """Test LeadMeeting initialization, repr, and to_dict method."""
    now = datetime.now(timezone.utc)
    lead_id = uuid.uuid4()
    meeting = LeadMeeting(
        lead_id=lead_id,
        scheduled_time=now,
        prep_notes="Review pricing proposal",
        meeting_notes="Client accepted demo terms",
        created_on=now,
    )

    assert meeting.lead_id == lead_id
    assert meeting.scheduled_time == now
    assert meeting.prep_notes == "Review pricing proposal"
    assert meeting.meeting_notes == "Client accepted demo terms"

    repr_str = repr(meeting)
    assert str(lead_id) in repr_str

    d = meeting.to_dict()
    assert d["lead_id"] == str(lead_id)
    assert d["scheduled_time"] == now.isoformat()
    assert d["prep_notes"] == "Review pricing proposal"
    assert d["meeting_notes"] == "Client accepted demo terms"
    assert d["followup_tasks"] == []


def test_followup_task_model_to_dict_and_repr():
    """Test FollowupTask initialization, repr, and to_dict method."""
    now = datetime.now(timezone.utc)
    due_dt = datetime(2026, 4, 15, 17, 0, tzinfo=timezone.utc)
    meeting_id = uuid.uuid4()
    lead_id = uuid.uuid4()
    assigned_user_id = uuid.uuid4()

    task = FollowupTask(
        meeting_id=meeting_id,
        lead_id=lead_id,
        assigned_user_id=assigned_user_id,
        due_date_val=due_dt,
        is_completed=False,
        task_description="Send formal SLA contract draft",
        created_on=now,
    )

    assert task.meeting_id == meeting_id
    assert task.lead_id == lead_id
    assert task.assigned_user_id == assigned_user_id
    assert task.due_date_val == due_dt
    assert task.is_completed is False
    assert task.task_description == "Send formal SLA contract draft"

    repr_str = repr(task)
    assert str(assigned_user_id) in repr_str
    assert "False" in repr_str

    d = task.to_dict()
    assert d["meeting_id"] == str(meeting_id)
    assert d["lead_id"] == str(lead_id)
    assert d["assigned_user_id"] == str(assigned_user_id)
    assert d["due_date_val"] == due_dt.isoformat()
    assert d["is_completed"] is False
    assert d["task_description"] == "Send formal SLA contract draft"


def test_marketing_models_db_persistence(db_session):
    """Test saving and querying Territory, LeadMeeting, and FollowupTask in database."""
    territory = Territory(
        territory_code="EU-CENTRAL",
        territory_name="Central Europe",
        postal_code_patterns="10*, 20*, 30*",
    )
    db_session.add(territory)

    lead_id = uuid.uuid4()
    user_id = uuid.uuid4()
    meeting = LeadMeeting(
        lead_id=lead_id,
        scheduled_time=datetime.now(timezone.utc),
        prep_notes="Initial discovery call",
    )
    db_session.add(meeting)
    db_session.commit()

    task = FollowupTask(
        meeting_id=meeting.id,
        lead_id=lead_id,
        assigned_user_id=user_id,
        due_date_val=datetime.now(timezone.utc),
        task_description="Follow up on custom feature request",
    )
    db_session.add(task)
    db_session.commit()

    # Query back
    saved_territory = db_session.query(Territory).filter_by(territory_code="EU-CENTRAL").first()
    assert saved_territory is not None
    assert saved_territory.territory_name == "Central Europe"

    saved_meeting = db_session.query(LeadMeeting).filter_by(id=meeting.id).first()
    assert saved_meeting is not None
    assert len(saved_meeting.followup_tasks) == 1
    assert saved_meeting.followup_tasks[0].task_description == "Follow up on custom feature request"
    assert saved_meeting.followup_tasks[0].meeting == saved_meeting


def test_territory_unique_code_constraint(db_session):
    """Test that territory_code unique constraint raises IntegrityError on duplicate."""
    t1 = Territory(territory_code="APAC-EAST", territory_name="East Asia")
    t2 = Territory(territory_code="APAC-EAST", territory_name="East Asia Duplicate")
    db_session.add(t1)
    db_session.commit()

    db_session.add(t2)
    with pytest.raises(IntegrityError):
        db_session.commit()


def test_lead_meeting_cascade_delete_tasks(db_session):
    """Test that deleting a LeadMeeting deletes all associated FollowupTask records."""
    lead_id = uuid.uuid4()
    user_id = uuid.uuid4()

    meeting = LeadMeeting(
        lead_id=lead_id,
        scheduled_time=datetime.now(timezone.utc),
    )
    db_session.add(meeting)
    db_session.commit()

    task1 = FollowupTask(
        meeting_id=meeting.id,
        lead_id=lead_id,
        assigned_user_id=user_id,
        task_description="Task 1",
    )
    task2 = FollowupTask(
        meeting_id=meeting.id,
        lead_id=lead_id,
        assigned_user_id=user_id,
        task_description="Task 2",
    )
    db_session.add_all([task1, task2])
    db_session.commit()

    assert db_session.query(FollowupTask).count() == 2

    db_session.delete(meeting)
    db_session.commit()

    assert db_session.query(FollowupTask).count() == 0
