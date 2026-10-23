"""Meeting Preparation and Follow-Up Calendar Service.

Provides business logic for calendar queries, meeting note updates,
lead status transitions, and follow-up task generation [FR-009, FR-010].
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Union
import uuid

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.models.analytics import LeadEvent
from src.models.marketing import FollowupTask, LeadMeeting

logger = logging.getLogger(__name__)


class CalendarServiceError(Exception):
    """Base exception class for calendar marketing service errors."""

    pass


class CalendarMeetingNotFoundError(CalendarServiceError):
    """Raised when a specified lead meeting is not found."""

    pass


class InvalidCalendarFilterError(CalendarServiceError):
    """Raised when date range or query parameters are invalid or malformed."""

    pass


class InvalidNotePayloadError(CalendarServiceError):
    """Raised when meeting note or follow-up task parameters are invalid."""

    pass


class TaskNotFoundError(CalendarServiceError):
    """Raised when a specified follow-up task is not found."""

    pass


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime object is timezone-aware and set to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_uuid(val: Optional[Union[uuid.UUID, str]]) -> Optional[uuid.UUID]:
    """Parse a string or UUID into a UUID object."""
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val).strip())
    except (ValueError, AttributeError):
        return None


def _parse_datetime(val: Optional[Union[datetime, str]]) -> Optional[datetime]:
    """Parse an ISO format string or datetime into a UTC datetime object."""
    if val is None:
        return None
    if isinstance(val, datetime):
        return _ensure_utc(val)
    if isinstance(val, str):
        cleaned = val.strip()
        if not cleaned:
            return None
        # Replace trailing 'Z' with UTC offset if present
        if cleaned.endswith("Z") or cleaned.endswith("z"):
            cleaned = cleaned[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(cleaned)
            return _ensure_utc(parsed)
        except ValueError as exc:
            raise InvalidCalendarFilterError(f"Invalid ISO datetime string: {val!r}") from exc
    raise InvalidCalendarFilterError(f"Invalid datetime parameter type: {type(val)}")


def create_meeting(
    db: Session,
    lead_id: Union[uuid.UUID, str],
    scheduled_time: Union[datetime, str],
    prep_notes: Optional[str] = None,
    meeting_notes: Optional[str] = None,
) -> LeadMeeting:
    """Create and persist a new lead meeting entry."""
    parsed_lead_id = _parse_uuid(lead_id)
    if not parsed_lead_id:
        raise InvalidNotePayloadError(f"Invalid lead_id: {lead_id!r}")

    parsed_scheduled_time = _parse_datetime(scheduled_time)
    if not parsed_scheduled_time:
        raise InvalidNotePayloadError("scheduled_time is required and must be a valid datetime.")

    meeting = LeadMeeting(
        id=uuid.uuid4(),
        lead_id=parsed_lead_id,
        scheduled_time=parsed_scheduled_time,
        prep_notes=prep_notes,
        meeting_notes=meeting_notes,
        created_on=datetime.now(timezone.utc),
    )
    db.add(meeting)
    db.commit()
    db.refresh(meeting)
    logger.info("Created lead meeting %s for lead %s", meeting.id, parsed_lead_id)
    return meeting


def get_meeting_by_id(db: Session, meeting_id: Union[uuid.UUID, str]) -> LeadMeeting:
    """Retrieve a LeadMeeting entity by ID, or raise CalendarMeetingNotFoundError."""
    parsed_id = _parse_uuid(meeting_id)
    if not parsed_id:
        raise CalendarMeetingNotFoundError(f"Invalid meeting ID format: {meeting_id!r}")

    meeting = db.query(LeadMeeting).filter(LeadMeeting.id == parsed_id).first()
    if not meeting:
        raise CalendarMeetingNotFoundError(f"Meeting with ID {meeting_id} not found.")

    return meeting


def query_calendar_meetings(
    db: Session,
    user_id: Optional[Union[uuid.UUID, str]] = None,
    lead_id: Optional[Union[uuid.UUID, str]] = None,
    start_date_val: Optional[Union[datetime, str]] = None,
    end_date_val: Optional[Union[datetime, str]] = None,
) -> List[Dict[str, Any]]:
    """Query scheduled meetings, linked prep briefings, and follow-up tasks [FR-009].

    Filterable by user_id, lead_id, and date range (start_date_val to end_date_val).
    """
    start_dt = _parse_datetime(start_date_val)
    end_dt = _parse_datetime(end_date_val)

    if start_dt and end_dt and start_dt > end_dt:
        raise InvalidCalendarFilterError(
            f"start_date_val ({start_dt.isoformat()}) cannot be after "
            f"end_date_val ({end_dt.isoformat()})."
        )

    query = db.query(LeadMeeting)

    if start_dt:
        query = query.filter(LeadMeeting.scheduled_time >= start_dt)
    if end_dt:
        query = query.filter(LeadMeeting.scheduled_time <= end_dt)

    if lead_id:
        parsed_lead_id = _parse_uuid(lead_id)
        if parsed_lead_id:
            query = query.filter(LeadMeeting.lead_id == parsed_lead_id)

    if user_id:
        parsed_user_id = _parse_uuid(user_id)
        if parsed_user_id:
            query = query.filter(
                LeadMeeting.followup_tasks.any(FollowupTask.assigned_user_id == parsed_user_id)
            )

    meetings = query.order_by(LeadMeeting.scheduled_time.asc()).all()

    results: List[Dict[str, Any]] = []
    for meeting in meetings:
        # Fetch the latest lead_status from lead_events if available
        latest_event = (
            db.query(LeadEvent)
            .filter(LeadEvent.lead_id == meeting.lead_id)
            .order_by(LeadEvent.event_timestamp.desc())
            .first()
        )
        lead_status = latest_event.stage_name if latest_event else None

        tasks_list = [t.to_dict() for t in meeting.followup_tasks]

        results.append(
            {
                "id": str(meeting.id),
                "lead_id": str(meeting.lead_id),
                "scheduled_time": meeting.scheduled_time.isoformat() if meeting.scheduled_time else None,
                "prep_notes": meeting.prep_notes,
                "meeting_notes": meeting.meeting_notes,
                "created_on": meeting.created_on.isoformat() if meeting.created_on else None,
                "lead_status": lead_status,
                "followup_tasks": tasks_list,
            }
        )

    logger.info("Found %d calendar meetings matching filter parameters", len(results))
    return results


def submit_meeting_notes_and_tasks(
    db: Session,
    meeting_id: Union[uuid.UUID, str],
    meeting_notes: Optional[str] = None,
    prep_notes: Optional[str] = None,
    lead_status: Optional[str] = None,
    tasks: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Log meeting notes, transition lead status, and generate follow-up tasks [FR-010]."""
    meeting = get_meeting_by_id(db, meeting_id)

    if meeting_notes is not None:
        meeting.meeting_notes = meeting_notes

    if prep_notes is not None:
        meeting.prep_notes = prep_notes

    # Lead status transition logic
    updated_lead_status: Optional[str] = None
    if lead_status and lead_status.strip():
        clean_status = lead_status.strip()
        updated_lead_status = clean_status

        # Retrieve current latest lead event for context
        latest_event = (
            db.query(LeadEvent)
            .filter(LeadEvent.lead_id == meeting.lead_id)
            .order_by(LeadEvent.event_timestamp.desc())
            .first()
        )

        new_event = LeadEvent(
            id=uuid.uuid4(),
            lead_id=meeting.lead_id,
            stage_name=clean_status,
            channel_id=latest_event.channel_id if latest_event else None,
            campaign_id=latest_event.campaign_id if latest_event else None,
            territory_id=latest_event.territory_id if latest_event else None,
            postal_code=latest_event.postal_code if latest_event else None,
            quality_score=latest_event.quality_score if latest_event else 0,
            deal_value=latest_event.deal_value if latest_event else 0.0,
            revenue_amount=latest_event.revenue_amount if latest_event else 0.0,
            event_timestamp=datetime.now(timezone.utc),
        )
        db.add(new_event)
        logger.info(
            "Recorded lead status transition for lead %s to %s",
            meeting.lead_id,
            clean_status,
        )

    created_tasks: List[FollowupTask] = []
    if tasks:
        for idx, task_data in enumerate(tasks):
            description = (
                task_data.get("task_description") or task_data.get("description") or ""
            ).strip()
            if not description:
                raise InvalidNotePayloadError(f"Task at index {idx} requires a non-empty task description.")

            raw_user_id = task_data.get("assigned_user_id")
            assigned_user_id = _parse_uuid(raw_user_id)
            if not assigned_user_id:
                raise InvalidNotePayloadError(
                    f"Task at index {idx} requires a valid assigned_user_id UUID."
                )

            raw_due_date = task_data.get("due_date_val") or task_data.get("due_date")
            due_date_val = _parse_datetime(raw_due_date) if raw_due_date else None

            task = FollowupTask(
                id=uuid.uuid4(),
                meeting_id=meeting.id,
                lead_id=meeting.lead_id,
                assigned_user_id=assigned_user_id,
                due_date_val=due_date_val,
                is_completed=bool(task_data.get("is_completed", False)),
                task_description=description,
                created_on=datetime.now(timezone.utc),
            )
            db.add(task)
            created_tasks.append(task)

    db.commit()
    db.refresh(meeting)

    logger.info(
        "Updated meeting notes for meeting %s, created %d tasks",
        meeting.id,
        len(created_tasks),
    )

    return {
        "meeting": meeting.to_dict(),
        "created_tasks": [t.to_dict() for t in created_tasks],
        "lead_status": updated_lead_status,
        "lead_id": str(meeting.lead_id),
    }


def create_followup_task(
    db: Session,
    lead_id: Union[uuid.UUID, str],
    assigned_user_id: Union[uuid.UUID, str],
    task_description: str,
    meeting_id: Optional[Union[uuid.UUID, str]] = None,
    due_date_val: Optional[Union[datetime, str]] = None,
) -> FollowupTask:
    """Create a standalone follow-up task entry."""
    parsed_lead_id = _parse_uuid(lead_id)
    if not parsed_lead_id:
        raise InvalidNotePayloadError(f"Invalid lead_id: {lead_id!r}")

    parsed_user_id = _parse_uuid(assigned_user_id)
    if not parsed_user_id:
        raise InvalidNotePayloadError(f"Invalid assigned_user_id: {assigned_user_id!r}")

    clean_desc = task_description.strip() if task_description else ""
    if not clean_desc:
        raise InvalidNotePayloadError("task_description cannot be empty.")

    parsed_meeting_id = _parse_uuid(meeting_id) if meeting_id else None
    parsed_due_date = _parse_datetime(due_date_val) if due_date_val else None

    task = FollowupTask(
        id=uuid.uuid4(),
        meeting_id=parsed_meeting_id,
        lead_id=parsed_lead_id,
        assigned_user_id=parsed_user_id,
        due_date_val=parsed_due_date,
        is_completed=False,
        task_description=clean_desc,
        created_on=datetime.now(timezone.utc),
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    logger.info("Created followup task %s for assigned user %s", task.id, parsed_user_id)
    return task


def complete_followup_task(
    db: Session,
    task_id: Union[uuid.UUID, str],
    is_completed: bool = True,
) -> FollowupTask:
    """Update task completion status for a FollowupTask entity."""
    parsed_task_id = _parse_uuid(task_id)
    if not parsed_task_id:
        raise TaskNotFoundError(f"Invalid task ID format: {task_id!r}")

    task = db.query(FollowupTask).filter(FollowupTask.id == parsed_task_id).first()
    if not task:
        raise TaskNotFoundError(f"FollowupTask with ID {task_id} not found.")

    task.is_completed = is_completed
    db.commit()
    db.refresh(task)
    logger.info("Updated task %s completion status to %s", task.id, is_completed)
    return task
