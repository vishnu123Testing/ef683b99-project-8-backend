"""FastAPI routes for Marketing & Calendar Management [FR-009, NFR-003, NFR-004]."""

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.models.database import get_db
from src.services.marketing.calendar import (
    CalendarMeetingNotFoundError,
    CalendarServiceError,
    InvalidCalendarFilterError,
    InvalidNotePayloadError,
    query_calendar_meetings,
    submit_meeting_notes_and_tasks,
)

logger = logging.getLogger(__name__)

marketing_router = APIRouter(prefix="/marketing", tags=["Marketing"])
router = marketing_router


class FollowupTaskSchema(BaseModel):
    """Pydantic schema for post-meeting follow-up task objects [FR-009]."""

    id: Optional[str] = Field(None, description="UUID string of follow-up task")
    meeting_id: Optional[str] = Field(None, description="UUID string of parent meeting")
    lead_id: str = Field(..., description="UUID string of associated lead")
    assigned_user_id: str = Field(..., description="UUID string of assigned user")
    due_date_val: Optional[str] = Field(None, description="ISO timestamp for task due date")
    is_completed: bool = Field(False, description="Completion status flag")
    task_description: str = Field(..., description="Detailed description of task")
    created_on: Optional[str] = Field(None, description="ISO timestamp of task creation")


class LeadMeetingResponseSchema(BaseModel):
    """Pydantic schema for calendar lead meeting, prep briefing, and tasks [FR-009]."""

    id: str = Field(..., description="UUID string of lead meeting")
    lead_id: str = Field(..., description="UUID string of lead")
    scheduled_time: Optional[str] = Field(None, description="ISO timestamp of scheduled meeting time")
    prep_notes: Optional[str] = Field(None, description="Preparation briefing notes")
    meeting_notes: Optional[str] = Field(None, description="Post-meeting summary notes")
    created_on: Optional[str] = Field(None, description="ISO timestamp of meeting creation")
    lead_status: Optional[str] = Field(None, description="Latest status/stage name of lead")
    followup_tasks: List[FollowupTaskSchema] = Field(
        default_factory=list, description="List of linked follow-up tasks"
    )


class TaskCreateSchema(BaseModel):
    """Pydantic schema for creating a follow-up task during meeting note logging [FR-010, NFR-004]."""

    task_description: Optional[str] = Field(None, description="Detailed description of task")
    description: Optional[str] = Field(None, description="Alternative field for task description")
    assigned_user_id: str = Field(..., description="UUID string of assigned user")
    due_date_val: Optional[str] = Field(None, description="ISO timestamp for task due date")
    due_date: Optional[str] = Field(None, description="Alternative field for task due date")
    is_completed: bool = Field(False, description="Completion status flag")


class SubmitMeetingNotesRequestSchema(BaseModel):
    """Pydantic schema for logging meeting notes, lead status updates, and creating tasks [FR-010, NFR-004]."""

    meeting_notes: Optional[str] = Field(None, description="Post-meeting summary notes")
    prep_notes: Optional[str] = Field(None, description="Preparation briefing notes")
    lead_status: Optional[str] = Field(None, description="Updated lead status / stage name")
    tasks: Optional[List[TaskCreateSchema]] = Field(
        None, description="List of follow-up tasks to create"
    )


class SubmitNotesResponseSchema(BaseModel):
    """Pydantic schema for response from logging meeting notes [FR-010]."""

    meeting: Dict[str, Any] = Field(..., description="Updated meeting entity dictionary")
    created_tasks: List[FollowupTaskSchema] = Field(
        default_factory=list, description="List of created follow-up task objects"
    )
    lead_status: Optional[str] = Field(None, description="Updated lead status / stage name")
    lead_id: str = Field(..., description="UUID string of associated lead")


@marketing_router.get(
    "/calendar/meetings",
    response_model=List[LeadMeetingResponseSchema],
    status_code=status.HTTP_200_OK,
    summary="Get calendar meetings, prep briefings, and follow-up tasks",
    description="Retrieve scheduled meetings, linked preparation briefings, and post-meeting tasks [FR-009].",
)
def get_calendar_meetings(
    user_id: Optional[str] = Query(None, description="Filter meetings by assigned user UUID"),
    lead_id: Optional[str] = Query(None, description="Filter meetings by lead UUID"),
    start_date_val: Optional[datetime] = Query(None, description="Filter meetings starting from datetime (UTC)"),
    end_date_val: Optional[datetime] = Query(None, description="Filter meetings up to datetime (UTC)"),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Retrieve scheduled lead meetings, prep briefings, and follow-up tasks [FR-009, NFR-003, NFR-004]."""
    clean_user_id = user_id.strip() if isinstance(user_id, str) and user_id.strip() else None
    clean_lead_id = lead_id.strip() if isinstance(lead_id, str) and lead_id.strip() else None

    logger.info(
        "Fetching calendar meetings with filters: user_id=%s, lead_id=%s, start_date_val=%s, end_date_val=%s",
        clean_user_id,
        clean_lead_id,
        start_date_val,
        end_date_val,
    )

    try:
        results = query_calendar_meetings(
            db=db,
            user_id=clean_user_id,
            lead_id=clean_lead_id,
            start_date_val=start_date_val,
            end_date_val=end_date_val,
        )
        return results
    except InvalidCalendarFilterError as exc:
        logger.warning("Invalid calendar filter parameters: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except CalendarServiceError as exc:
        logger.error("Calendar service error during meeting query: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Calendar query failed: {exc}",
        ) from exc


@marketing_router.post(
    "/calendar/meetings/{id}/notes",
    response_model=SubmitNotesResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Log meeting notes, update lead status, and create follow-up tasks",
    description="Log post-meeting notes, update lead status stage, and create assigned follow-up tasks [FR-010, NFR-004].",
)
def submit_meeting_notes(
    id: str,
    payload: SubmitMeetingNotesRequestSchema,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Log meeting notes, update lead status, and generate follow-up tasks [FR-010, NFR-004]."""
    clean_id = id.strip() if isinstance(id, str) else id

    logger.info("Submitting meeting notes for meeting ID: %s", clean_id)

    tasks_data = None
    if payload.tasks is not None:
        tasks_data = [t.model_dump() for t in payload.tasks]

    clean_meeting_notes = (
        payload.meeting_notes.strip() if isinstance(payload.meeting_notes, str) else payload.meeting_notes
    )
    clean_prep_notes = (
        payload.prep_notes.strip() if isinstance(payload.prep_notes, str) else payload.prep_notes
    )
    clean_lead_status = (
        payload.lead_status.strip() if isinstance(payload.lead_status, str) else payload.lead_status
    )

    try:
        result = submit_meeting_notes_and_tasks(
            db=db,
            meeting_id=clean_id,
            meeting_notes=clean_meeting_notes,
            prep_notes=clean_prep_notes,
            lead_status=clean_lead_status,
            tasks=tasks_data,
        )
        return result
    except CalendarMeetingNotFoundError as exc:
        logger.warning("Meeting not found for notes submission: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
    except InvalidNotePayloadError as exc:
        logger.warning("Invalid notes or task payload: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except CalendarServiceError as exc:
        logger.error("Error submitting meeting notes: %s", exc)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to submit meeting notes: {exc}",
        ) from exc
