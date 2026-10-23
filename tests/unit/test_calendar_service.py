"""Unit tests for Meeting Preparation and Follow-Up Calendar Service."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.analytics import LeadEvent
from src.models.database import Base
from src.models.marketing import FollowupTask, LeadMeeting
from src.services.marketing.calendar import (
    CalendarMeetingNotFoundError,
    CalendarServiceError,
    InvalidCalendarFilterError,
    InvalidNotePayloadError,
    TaskNotFoundError,
    complete_followup_task,
    create_followup_task,
    create_meeting,
    get_meeting_by_id,
    query_calendar_meetings,
    submit_meeting_notes_and_tasks,
)


@pytest.fixture
def db_session():
    """In-memory SQLite database session fixture."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_create_meeting_and_get_by_id(db_session):
    """Test creating a lead meeting and retrieving it by ID."""
    lead_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    meeting = create_meeting(
        db=db_session,
        lead_id=lead_id,
        scheduled_time=now,
        prep_notes="Review SLA and pricing terms",
        meeting_notes="Initial discovery chat",
    )

    assert meeting.id is not None
    assert meeting.lead_id == lead_id
    assert meeting.prep_notes == "Review SLA and pricing terms"

    fetched = get_meeting_by_id(db_session, meeting.id)
    assert fetched.id == meeting.id
    assert fetched.lead_id == lead_id


def test_get_meeting_by_id_not_found(db_session):
    """Test retrieving a non-existent or invalid meeting ID raises CalendarMeetingNotFoundError."""
    random_id = uuid.uuid4()
    with pytest.raises(CalendarMeetingNotFoundError):
        get_meeting_by_id(db_session, random_id)

    with pytest.raises(CalendarMeetingNotFoundError):
        get_meeting_by_id(db_session, "invalid-uuid-string")


def test_create_meeting_invalid_params(db_session):
    """Test create_meeting validation for invalid lead_id or scheduled_time."""
    with pytest.raises(InvalidNotePayloadError):
        create_meeting(db_session, lead_id="not-a-uuid", scheduled_time=datetime.now(timezone.utc))

    with pytest.raises(InvalidCalendarFilterError):
        create_meeting(db_session, lead_id=uuid.uuid4(), scheduled_time="not-a-datetime")


def test_query_calendar_meetings_date_filtering(db_session):
    """Test calendar queries with date range filters."""
    lead_id = uuid.uuid4()
    base_time = datetime(2026, 5, 10, 10, 0, tzinfo=timezone.utc)

    m1 = create_meeting(db_session, lead_id=lead_id, scheduled_time=base_time)
    m2 = create_meeting(db_session, lead_id=lead_id, scheduled_time=base_time + timedelta(days=2))
    m3 = create_meeting(db_session, lead_id=lead_id, scheduled_time=base_time + timedelta(days=5))

    # Query all
    results = query_calendar_meetings(db_session)
    assert len(results) == 3

    # Filter within range
    start_val = (base_time + timedelta(days=1)).isoformat()
    end_val = (base_time + timedelta(days=3)).isoformat()
    filtered = query_calendar_meetings(
        db_session,
        start_date_val=start_val,
        end_date_val=end_val,
    )
    assert len(filtered) == 1
    assert filtered[0]["id"] == str(m2.id)

    # Invalid range (start > end)
    with pytest.raises(InvalidCalendarFilterError):
        query_calendar_meetings(
            db_session,
            start_date_val=end_val,
            end_date_val=start_val,
        )


def test_query_calendar_meetings_user_and_lead_filtering(db_session):
    """Test calendar queries filtering by assigned user_id and lead_id."""
    lead1 = uuid.uuid4()
    lead2 = uuid.uuid4()
    user1 = uuid.uuid4()
    user2 = uuid.uuid4()

    now = datetime.now(timezone.utc)
    m1 = create_meeting(db_session, lead_id=lead1, scheduled_time=now)
    m2 = create_meeting(db_session, lead_id=lead2, scheduled_time=now + timedelta(hours=2))

    # Add follow-up task to m1 assigned to user1
    create_followup_task(
        db_session,
        lead_id=lead1,
        assigned_user_id=user1,
        task_description="Send product brochure",
        meeting_id=m1.id,
    )

    # Query by lead_id
    lead1_results = query_calendar_meetings(db_session, lead_id=lead1)
    assert len(lead1_results) == 1
    assert lead1_results[0]["id"] == str(m1.id)

    # Query by user_id
    user1_results = query_calendar_meetings(db_session, user_id=user1)
    assert len(user1_results) == 1
    assert user1_results[0]["id"] == str(m1.id)

    user2_results = query_calendar_meetings(db_session, user_id=user2)
    assert len(user2_results) == 0


def test_submit_meeting_notes_and_tasks(db_session):
    """Test logging meeting notes, transitioning lead status, and generating follow-up tasks."""
    lead_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Add initial lead event
    event = LeadEvent(
        lead_id=lead_id,
        stage_name="SCHEDULED",
        quality_score=85,
        deal_value=50000.0,
        event_timestamp=now - timedelta(hours=1),
    )
    db_session.add(event)
    db_session.commit()

    meeting = create_meeting(
        db_session,
        lead_id=lead_id,
        scheduled_time=now,
        prep_notes="Prepare demo environment",
    )

    due_dt = (now + timedelta(days=2)).isoformat()
    tasks_payload = [
        {
            "task_description": "Follow up with custom proposal",
            "assigned_user_id": str(user_id),
            "due_date_val": due_dt,
        }
    ]

    update_res = submit_meeting_notes_and_tasks(
        db=db_session,
        meeting_id=meeting.id,
        meeting_notes="Client accepted demo, requested proposal",
        lead_status="QUALIFIED",
        tasks=tasks_payload,
    )

    assert update_res["meeting"]["meeting_notes"] == "Client accepted demo, requested proposal"
    assert update_res["lead_status"] == "QUALIFIED"
    assert len(update_res["created_tasks"]) == 1
    assert update_res["created_tasks"][0]["task_description"] == "Follow up with custom proposal"

    # Verify new LeadEvent recorded for lead status transition
    latest_event = (
        db_session.query(LeadEvent)
        .filter(LeadEvent.lead_id == lead_id)
        .order_by(LeadEvent.event_timestamp.desc())
        .first()
    )
    assert latest_event is not None
    assert latest_event.stage_name == "QUALIFIED"
    assert latest_event.quality_score == 85  # Preserved from previous event

    # Verify calendar query returns updated lead_status
    calendar_res = query_calendar_meetings(db_session, lead_id=lead_id)
    assert len(calendar_res) == 1
    assert calendar_res[0]["lead_status"] == "QUALIFIED"
    assert len(calendar_res[0]["followup_tasks"]) == 1


def test_submit_meeting_notes_invalid_payloads(db_session):
    """Test submit_meeting_notes_and_tasks raises errors for invalid tasks or meeting ID."""
    meeting = create_meeting(db_session, lead_id=uuid.uuid4(), scheduled_time=datetime.now(timezone.utc))

    # Missing task description
    with pytest.raises(InvalidNotePayloadError):
        submit_meeting_notes_and_tasks(
            db_session,
            meeting_id=meeting.id,
            tasks=[{"assigned_user_id": str(uuid.uuid4()), "task_description": ""}],
        )

    # Invalid user ID
    with pytest.raises(InvalidNotePayloadError):
        submit_meeting_notes_and_tasks(
            db_session,
            meeting_id=meeting.id,
            tasks=[{"assigned_user_id": "not-a-uuid", "task_description": "Valid task"}],
        )

    # Invalid meeting ID
    with pytest.raises(CalendarMeetingNotFoundError):
        submit_meeting_notes_and_tasks(db_session, meeting_id=uuid.uuid4(), meeting_notes="Notes")


def test_create_and_complete_followup_task(db_session):
    """Test standalone creation and completion toggle of FollowupTask entities."""
    lead_id = uuid.uuid4()
    user_id = uuid.uuid4()

    task = create_followup_task(
        db_session,
        lead_id=lead_id,
        assigned_user_id=user_id,
        task_description="Send contract draft",
        due_date_val="2026-06-01T12:00:00Z",
    )

    assert task.id is not None
    assert task.is_completed is False
    assert task.task_description == "Send contract draft"

    completed = complete_followup_task(db_session, task.id, is_completed=True)
    assert completed.is_completed is True

    # Non-existent task
    with pytest.raises(TaskNotFoundError):
        complete_followup_task(db_session, uuid.uuid4())
