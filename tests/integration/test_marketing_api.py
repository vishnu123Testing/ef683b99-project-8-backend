"""Integration tests for FastAPI marketing calendar API endpoints [FR-009, NFR-003, NFR-004]."""

from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.main import app
from src.models.analytics import LeadEvent
from src.models.database import Base, get_db
from src.models.marketing import FollowupTask, LeadMeeting

# Isolated in-memory SQLite database setup for API integration testing
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Dependency override providing isolated SQLite test session."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_database():
    """Fixture creating fresh database schema for every test."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


client = TestClient(app)


def test_get_calendar_meetings_empty():
    """Verify GET /api/v1/marketing/calendar/meetings returns 200 OK and [] when database is empty."""
    response = client.get("/api/v1/marketing/calendar/meetings")
    assert response.status_code == 200
    assert response.json() == []


def test_get_calendar_meetings_success():
    """Verify GET /api/v1/marketing/calendar/meetings returns scheduled meetings, briefings, lead_status, and tasks [FR-009]."""
    db = TestingSessionLocal()

    lead_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # 1. Create LeadEvent to represent lead stage
    event = LeadEvent(
        id=uuid.uuid4(),
        lead_id=lead_id,
        stage_name="Qualified Lead",
        quality_score=85,
        deal_value=12000.0,
        revenue_amount=0.0,
        event_timestamp=now - timedelta(hours=2),
    )
    db.add(event)

    # 2. Create LeadMeeting
    meeting = LeadMeeting(
        id=uuid.uuid4(),
        lead_id=lead_id,
        scheduled_time=now + timedelta(days=1),
        prep_notes="Review executive summary and pricing sheet.",
        meeting_notes="Initial contact completed.",
        created_on=now,
    )
    db.add(meeting)
    db.commit()

    # 3. Create FollowupTask linked to meeting
    task = FollowupTask(
        id=uuid.uuid4(),
        meeting_id=meeting.id,
        lead_id=lead_id,
        assigned_user_id=user_id,
        due_date_val=now + timedelta(days=3),
        is_completed=False,
        task_description="Send custom proposal PDF",
        created_on=now,
    )
    db.add(task)
    db.commit()
    db.close()

    response = client.get("/api/v1/marketing/calendar/meetings")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1

    meeting_item = data[0]
    assert meeting_item["id"] == str(meeting.id)
    assert meeting_item["lead_id"] == str(lead_id)
    assert meeting_item["prep_notes"] == "Review executive summary and pricing sheet."
    assert meeting_item["meeting_notes"] == "Initial contact completed."
    assert meeting_item["lead_status"] == "Qualified Lead"
    assert len(meeting_item["followup_tasks"]) == 1

    task_item = meeting_item["followup_tasks"][0]
    assert task_item["id"] == str(task.id)
    assert task_item["meeting_id"] == str(meeting.id)
    assert task_item["lead_id"] == str(lead_id)
    assert task_item["assigned_user_id"] == str(user_id)
    assert task_item["is_completed"] is False
    assert task_item["task_description"] == "Send custom proposal PDF"


def test_get_calendar_meetings_filtering_and_sanitization():
    """Verify filtering by user_id, lead_id, date range, and input whitespace sanitization [NFR-004]."""
    db = TestingSessionLocal()

    lead_id_1 = uuid.uuid4()
    lead_id_2 = uuid.uuid4()
    user_id_1 = uuid.uuid4()
    user_id_2 = uuid.uuid4()

    base_time = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)

    # Meeting 1: lead_id_1, task user_id_1, time June 15
    m1 = LeadMeeting(
        id=uuid.uuid4(),
        lead_id=lead_id_1,
        scheduled_time=base_time,
        prep_notes="Prep M1",
        created_on=base_time - timedelta(days=1),
    )
    db.add(m1)
    db.commit()

    t1 = FollowupTask(
        id=uuid.uuid4(),
        meeting_id=m1.id,
        lead_id=lead_id_1,
        assigned_user_id=user_id_1,
        task_description="Task M1 U1",
        created_on=base_time,
    )
    db.add(t1)

    # Meeting 2: lead_id_2, task user_id_2, time June 20
    m2 = LeadMeeting(
        id=uuid.uuid4(),
        lead_id=lead_id_2,
        scheduled_time=base_time + timedelta(days=5),
        prep_notes="Prep M2",
        created_on=base_time,
    )
    db.add(m2)
    db.commit()

    t2 = FollowupTask(
        id=uuid.uuid4(),
        meeting_id=m2.id,
        lead_id=lead_id_2,
        assigned_user_id=user_id_2,
        task_description="Task M2 U2",
        created_on=base_time,
    )
    db.add(t2)
    db.commit()
    db.close()

    # Filter by user_id with leading/trailing spaces
    res = client.get(f"/api/v1/marketing/calendar/meetings?user_id=  {user_id_1}  ")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["id"] == str(m1.id)

    # Filter by lead_id with spaces
    res = client.get(f"/api/v1/marketing/calendar/meetings?lead_id=\t{lead_id_2}\n")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["id"] == str(m2.id)

    # Filter by date range
    start_str = (base_time - timedelta(days=1)).isoformat()
    end_str = (base_time + timedelta(days=2)).isoformat()
    res = client.get(
        f"/api/v1/marketing/calendar/meetings?start_date_val={start_str}&end_date_val={end_str}"
    )
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 1
    assert data[0]["id"] == str(m1.id)


def test_get_calendar_meetings_invalid_date_range():
    """Verify HTTP 400 Bad Request returned when start_date_val is after end_date_val."""
    start_str = "2026-06-20T00:00:00Z"
    end_str = "2026-06-10T00:00:00Z"
    res = client.get(
        f"/api/v1/marketing/calendar/meetings?start_date_val={start_str}&end_date_val={end_str}"
    )
    assert res.status_code == 400
    assert "start_date_val" in res.json()["detail"]


def test_submit_meeting_notes_success():
    """Verify POST /api/v1/marketing/calendar/meetings/{id}/notes updates notes, lead status, and creates tasks [FR-010, NFR-004]."""
    db = TestingSessionLocal()
    lead_id = uuid.uuid4()
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # 1. Seed initial LeadEvent and LeadMeeting
    initial_event = LeadEvent(
        id=uuid.uuid4(),
        lead_id=lead_id,
        stage_name="CONTACTED",
        quality_score=75,
        event_timestamp=now - timedelta(hours=2),
    )
    db.add(initial_event)

    meeting = LeadMeeting(
        id=uuid.uuid4(),
        lead_id=lead_id,
        scheduled_time=now,
        prep_notes="Initial discovery call",
        created_on=now,
    )
    db.add(meeting)
    db.commit()
    meeting_id = meeting.id
    db.close()

    # 2. Submit notes, lead status update, and follow-up task
    payload = {
        "meeting_notes": "Call went well, client wants custom proposal.",
        "lead_status": "QUALIFIED",
        "tasks": [
            {
                "task_description": "Draft proposal document",
                "assigned_user_id": str(user_id),
                "due_date_val": "2026-06-30T17:00:00Z",
            }
        ],
    }

    response = client.post(
        f"/api/v1/marketing/calendar/meetings/  {meeting_id}  /notes",
        json=payload,
    )

    assert response.status_code == 200
    data = response.json()

    assert data["lead_status"] == "QUALIFIED"
    assert data["lead_id"] == str(lead_id)
    assert data["meeting"]["meeting_notes"] == "Call went well, client wants custom proposal."
    assert len(data["created_tasks"]) == 1

    created_task = data["created_tasks"][0]
    assert created_task["task_description"] == "Draft proposal document"
    assert created_task["assigned_user_id"] == str(user_id)

    # 3. Verify database persistence
    db_verify = TestingSessionLocal()
    updated_meeting = db_verify.query(LeadMeeting).filter(LeadMeeting.id == meeting_id).first()
    assert updated_meeting is not None
    assert updated_meeting.meeting_notes == "Call went well, client wants custom proposal."

    latest_event = (
        db_verify.query(LeadEvent)
        .filter(LeadEvent.lead_id == lead_id)
        .order_by(LeadEvent.event_timestamp.desc())
        .first()
    )
    assert latest_event is not None
    assert latest_event.stage_name == "QUALIFIED"

    tasks = db_verify.query(FollowupTask).filter(FollowupTask.meeting_id == meeting_id).all()
    assert len(tasks) == 1
    assert tasks[0].task_description == "Draft proposal document"
    assert tasks[0].assigned_user_id == user_id
    db_verify.close()


def test_submit_meeting_notes_not_found():
    """Verify POST /api/v1/marketing/calendar/meetings/{id}/notes returns 404 for non-existent meeting."""
    random_id = uuid.uuid4()
    payload = {"meeting_notes": "Notes for missing meeting"}
    response = client.post(f"/api/v1/marketing/calendar/meetings/{random_id}/notes", json=payload)
    assert response.status_code == 404


def test_submit_meeting_notes_invalid_payload():
    """Verify POST /api/v1/marketing/calendar/meetings/{id}/notes returns 400 when task description is empty."""
    db = TestingSessionLocal()
    lead_id = uuid.uuid4()
    user_id = uuid.uuid4()

    meeting = LeadMeeting(
        id=uuid.uuid4(),
        lead_id=lead_id,
        scheduled_time=datetime.now(timezone.utc),
    )
    db.add(meeting)
    db.commit()
    meeting_id = meeting.id
    db.close()

    # Task missing valid task description
    payload = {
        "meeting_notes": "Meeting notes",
        "tasks": [
            {
                "task_description": "   ",
                "assigned_user_id": str(user_id),
            }
        ],
    }

    response = client.post(f"/api/v1/marketing/calendar/meetings/{meeting_id}/notes", json=payload)
    assert response.status_code == 400
    assert "requires a non-empty task description" in response.json()["detail"]
