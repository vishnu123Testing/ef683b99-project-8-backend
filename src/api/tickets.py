"""FastAPI REST endpoints for support ticket lifecycle management, thread messaging, attachments, and CSAT ratings [FR-1, FR-2, FR-3, FR-7, FR-9, NFR-2, NFR-3]."""

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional
import uuid

from fastapi import (
    APIRouter,
    Body,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from src.lib.logging import get_correlation_id
from src.models.database import get_db
from src.services.support.ticket_service import (
    CSATAlreadySubmittedError,
    InvalidCSATError,
    InvalidMessageError,
    InvalidTicketError,
    TicketAttachmentError,
    TicketNotFoundError,
    TicketService,
    TicketServiceError,
)

logger = logging.getLogger(__name__)

tickets_router = APIRouter(prefix="/tickets", tags=["Tickets"])
router = tickets_router


# Pydantic Schemas for Request & Response validation [NFR-2]


class CreateTicketSchema(BaseModel):
    """Schema for creating a new support ticket [FR-1, NFR-2]."""

    priority: str = Field(..., description="Ticket priority: 'P1', 'P2', or 'P3'")
    subject: str = Field(..., min_length=1, max_length=255, description="Brief summary header")
    description: str = Field(..., min_length=1, description="Detailed problem description")
    client_id: str = Field(..., description="Client UUID or string identifier")
    assigned_tier: Optional[str] = Field("tier1", description="Assigned support tier (defaults to 'tier1')")
    assigned_agent_id: Optional[str] = Field(None, description="Optional assigned agent UUID or string")

    @field_validator("priority", mode="before")
    @classmethod
    def sanitize_priority(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_clean = v.upper().strip()
            if v_clean not in ("P1", "P2", "P3"):
                raise ValueError("Priority must be 'P1', 'P2', or 'P3'.")
            return v_clean
        return v

    @field_validator("subject", "description", "client_id", mode="before")
    @classmethod
    def sanitize_non_empty_strings(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_clean = v.strip()
            if not v_clean:
                raise ValueError("String fields cannot be empty or blank.")
            return v_clean
        return v


class UpdateTicketSchema(BaseModel):
    """Schema for updating support ticket attributes [FR-1, NFR-2]."""

    priority: Optional[str] = Field(None, description="Updated priority ('P1', 'P2', 'P3')")
    status: Optional[str] = Field(None, description="Updated status ('open', 'pending', 'resolved', 'closed')")
    assigned_tier: Optional[str] = Field(None, description="Updated support tier")
    assigned_agent_id: Optional[str] = Field(None, description="Updated assigned agent UUID string")
    subject: Optional[str] = Field(None, description="Updated summary header")
    description: Optional[str] = Field(None, description="Updated problem description")

    @field_validator("priority", mode="before")
    @classmethod
    def sanitize_priority(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip():
            v_clean = v.upper().strip()
            if v_clean not in ("P1", "P2", "P3"):
                raise ValueError("Priority must be 'P1', 'P2', or 'P3'.")
            return v_clean
        return v

    @field_validator("status", mode="before")
    @classmethod
    def sanitize_status(cls, v: Any) -> Any:
        if isinstance(v, str) and v.strip():
            v_clean = v.lower().strip()
            if v_clean not in ("open", "pending", "resolved", "closed"):
                raise ValueError("Status must be one of: 'open', 'pending', 'resolved', 'closed'.")
            return v_clean
        return v


class SLATrackingResponseSchema(BaseModel):
    """Schema for SLA countdown state details [FR-4]."""

    id: str
    ticket_id: str
    target_type: str
    target_due_at: Optional[str] = None
    warning_sent_at: Optional[str] = None
    breached_at: Optional[str] = None
    is_breached: bool


class TicketResponseSchema(BaseModel):
    """Schema for returning support ticket details [FR-1, FR-4, FR-7]."""

    id: str
    priority: str
    status: str
    subject: str
    description: str
    client_id: str
    assigned_tier: Optional[str] = None
    assigned_agent_id: Optional[str] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    first_responded_at: Optional[str] = None
    resolved_at: Optional[str] = None
    sla_trackings: Optional[List[SLATrackingResponseSchema]] = None


class TicketListResponseSchema(BaseModel):
    """Schema for paginated ticket search results [FR-1]."""

    items: List[TicketResponseSchema]
    total_count: int
    limit: int
    offset: int


class CreateMessageSchema(BaseModel):
    """Schema for appending a message to a ticket thread [FR-2, NFR-2]."""

    sender_id: str = Field(..., description="Sender UUID or string identifier")
    sender_type: str = Field(..., description="Sender type: 'client', 'agent', or 'system'")
    message_body: str = Field(..., min_length=1, description="Thread message body text")

    @field_validator("sender_type", mode="before")
    @classmethod
    def sanitize_sender_type(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_clean = v.lower().strip()
            if v_clean not in ("client", "agent", "system"):
                raise ValueError("sender_type must be 'client', 'agent', or 'system'.")
            return v_clean
        return v

    @field_validator("sender_id", "message_body", mode="before")
    @classmethod
    def sanitize_non_empty_strings(cls, v: Any) -> Any:
        if isinstance(v, str):
            v_clean = v.strip()
            if not v_clean:
                raise ValueError("Field cannot be blank or empty.")
            return v_clean
        return v


class TicketAttachmentResponseSchema(BaseModel):
    """Schema for ticket attachment file metadata [FR-2, FR-3]."""

    id: str
    message_id: str
    filename: str
    file_size_bytes: int
    mime_type: str
    storage_key: str
    uploaded_at: Optional[str] = None


class TicketMessageResponseSchema(BaseModel):
    """Schema for sequential thread message details [FR-2]."""

    id: str
    ticket_id: str
    sender_id: str
    sender_type: str
    message_body: str
    created_at: Optional[str] = None
    attachments: Optional[List[TicketAttachmentResponseSchema]] = None


class CreateCSATRatingSchema(BaseModel):
    """Schema for submitting CSAT rating feedback [FR-9, NFR-2]."""

    score: int = Field(..., ge=1, le=5, description="Satisfaction rating score from 1 to 5")
    feedback_text: Optional[str] = Field(None, description="Optional text feedback comment")


class CSATRatingResponseSchema(BaseModel):
    """Schema for CSAT rating details [FR-9]."""

    id: str
    ticket_id: str
    score: int
    feedback_text: Optional[str] = None
    submitted_at: Optional[str] = None


# Helper function to serialize ticket ORM model to response schema
def _build_ticket_response(ticket: Any) -> TicketResponseSchema:
    ticket_dict = ticket.to_dict()
    sla_list = []
    if hasattr(ticket, "sla_trackings") and ticket.sla_trackings:
        for sla in ticket.sla_trackings:
            sla_list.append(SLATrackingResponseSchema(**sla.to_dict()))
    ticket_dict["sla_trackings"] = sla_list
    return TicketResponseSchema(**ticket_dict)


# API Route Handlers


@tickets_router.post(
    "",
    response_model=TicketResponseSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new support ticket",
)
def create_ticket_endpoint(
    payload: CreateTicketSchema,
    db: Session = Depends(get_db),
) -> TicketResponseSchema:
    """Create a new support ticket, initialize SLA countdown tracking, and post initial message [FR-1, FR-4, FR-7]."""
    try:
        service = TicketService(db)
        ticket = service.create_ticket(
            priority=payload.priority,
            subject=payload.subject,
            description=payload.description,
            client_id=payload.client_id,
            assigned_tier=payload.assigned_tier,
            assigned_agent_id=payload.assigned_agent_id,
        )
        return _build_ticket_response(ticket)
    except InvalidTicketError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    except TicketServiceError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@tickets_router.get(
    "",
    response_model=TicketListResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="List and filter support tickets",
)
def list_tickets_endpoint(
    client_id: Optional[str] = Query(None, description="Filter by client UUID"),
    priority: Optional[str] = Query(None, description="Filter by priority level ('P1', 'P2', 'P3')"),
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by status ('open', 'pending', 'resolved', 'closed')"),
    assigned_tier: Optional[str] = Query(None, description="Filter by assigned tier"),
    assigned_agent_id: Optional[str] = Query(None, description="Filter by assigned agent UUID"),
    limit: int = Query(50, ge=1, le=100, description="Maximum items per page"),
    offset: int = Query(0, ge=0, description="Offset for pagination"),
    db: Session = Depends(get_db),
) -> TicketListResponseSchema:
    """Filter and list support tickets matching specified query criteria [FR-1]."""
    try:
        service = TicketService(db)
        tickets = service.list_tickets(
            client_id=client_id,
            priority=priority,
            status=status_filter,
            assigned_tier=assigned_tier,
            assigned_agent_id=assigned_agent_id,
            limit=limit,
            offset=offset,
        )
        total_count = service.count_tickets(
            client_id=client_id,
            priority=priority,
            status=status_filter,
            assigned_tier=assigned_tier,
            assigned_agent_id=assigned_agent_id,
        )
        items = [_build_ticket_response(t) for t in tickets]
        return TicketListResponseSchema(
            items=items,
            total_count=total_count,
            limit=limit,
            offset=offset,
        )
    except InvalidTicketError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@tickets_router.get(
    "/{ticket_id}",
    response_model=TicketResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Retrieve support ticket details",
)
def get_ticket_endpoint(
    ticket_id: str,
    db: Session = Depends(get_db),
) -> TicketResponseSchema:
    """Retrieve full support ticket details including active SLA tracking states [FR-1, FR-4]."""
    try:
        service = TicketService(db)
        ticket = service.get_ticket(ticket_id)
        return _build_ticket_response(ticket)
    except TicketNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidTicketError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@tickets_router.put(
    "/{ticket_id}",
    response_model=TicketResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Update support ticket attributes",
)
@tickets_router.patch(
    "/{ticket_id}",
    response_model=TicketResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Patch support ticket attributes",
)
def update_ticket_endpoint(
    ticket_id: str,
    payload: UpdateTicketSchema,
    db: Session = Depends(get_db),
) -> TicketResponseSchema:
    """Update support ticket fields, status transitions, or priority levels [FR-1, FR-4, FR-7]."""
    try:
        service = TicketService(db)
        updated_ticket = service.update_ticket(
            ticket_id=ticket_id,
            priority=payload.priority,
            status=payload.status,
            assigned_tier=payload.assigned_tier,
            assigned_agent_id=payload.assigned_agent_id,
            subject=payload.subject,
            description=payload.description,
        )
        return _build_ticket_response(updated_ticket)
    except TicketNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidTicketError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@tickets_router.delete(
    "/{ticket_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a support ticket",
)
def delete_ticket_endpoint(
    ticket_id: str,
    db: Session = Depends(get_db),
) -> Response:
    """Delete a support ticket and return 204 No Content with empty body [FR-1, ARCHITECTURE.md §5]."""
    try:
        service = TicketService(db)
        service.delete_ticket(ticket_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except TicketNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidTicketError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@tickets_router.post(
    "/{ticket_id}/messages",
    response_model=TicketMessageResponseSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Post a message to ticket thread with optional attachment",
)
async def create_message_endpoint(
    ticket_id: str,
    payload: Optional[CreateMessageSchema] = Body(None),
    sender_id: Optional[str] = Form(None),
    sender_type: Optional[str] = Form(None),
    message_body: Optional[str] = Form(None),
    file: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
) -> TicketMessageResponseSchema:
    """Append a sequential message to a ticket thread with optional file attachment upload [FR-2, FR-3, FR-7, NFR-2]."""
    final_sender_id = None
    final_sender_type = None
    final_message_body = None

    if payload is not None:
        final_sender_id = payload.sender_id
        final_sender_type = payload.sender_type
        final_message_body = payload.message_body
    else:
        if not sender_id or not sender_type or not message_body:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Missing required message fields: sender_id, sender_type, and message_body are required.",
            )
        try:
            validated_schema = CreateMessageSchema(
                sender_id=sender_id,
                sender_type=sender_type,
                message_body=message_body,
            )
            final_sender_id = validated_schema.sender_id
            final_sender_type = validated_schema.sender_type
            final_message_body = validated_schema.message_body
        except Exception as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            )

    attachments_data = None
    if file is not None:
        file_content = await file.read()
        filename = file.filename or "attachment"
        mime_type = file.content_type or "application/octet-stream"
        attachments_data = [
            {
                "filename": filename,
                "content": file_content,
                "mime_type": mime_type,
            }
        ]

    try:
        service = TicketService(db)
        msg = service.add_message(
            ticket_id=ticket_id,
            sender_id=final_sender_id,
            sender_type=final_sender_type,
            message_body=final_message_body,
            attachments=attachments_data,
        )

        msg_dict = msg.to_dict()
        att_list = []
        if hasattr(msg, "attachments") and msg.attachments:
            for att in msg.attachments:
                att_list.append(TicketAttachmentResponseSchema(**att.to_dict()))
        msg_dict["attachments"] = att_list

        logger.info(
            "Posted message to ticket thread",
            extra={
                "correlation_id": get_correlation_id(),
                "ticket_id": ticket_id,
                "message_id": str(msg.id),
                "sender_type": msg.sender_type,
                "has_attachment": file is not None,
            },
        )

        return TicketMessageResponseSchema(**msg_dict)
    except TicketNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except (InvalidMessageError, InvalidTicketError, TicketAttachmentError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@tickets_router.post(
    "/{ticket_id}/attachments",
    response_model=TicketAttachmentResponseSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Upload file attachment to a support ticket",
)
async def upload_attachment_endpoint(
    ticket_id: str,
    file: UploadFile = File(...),
    sender_id: str = Form(...),
    sender_type: str = Form("client"),
    message_body: Optional[str] = Form(None),
    db: Session = Depends(get_db),
) -> TicketAttachmentResponseSchema:
    """Upload a file attachment to a support ticket with boundary payload sanitization and validation [FR-2, FR-3, NFR-2]."""
    if not sender_id or not sender_id.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="sender_id is required for uploading file attachments.",
        )

    default_body = f"Uploaded file attachment: {file.filename or 'attachment'}"
    try:
        validated_schema = CreateMessageSchema(
            sender_id=sender_id,
            sender_type=sender_type or "client",
            message_body=message_body or default_body,
        )
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )

    file_content = await file.read()
    filename = file.filename or "attachment"
    mime_type = file.content_type or "application/octet-stream"

    attachments_data = [
        {
            "filename": filename,
            "content": file_content,
            "mime_type": mime_type,
        }
    ]

    try:
        service = TicketService(db)
        msg = service.add_message(
            ticket_id=ticket_id,
            sender_id=validated_schema.sender_id,
            sender_type=validated_schema.sender_type,
            message_body=validated_schema.message_body,
            attachments=attachments_data,
        )

        if not hasattr(msg, "attachments") or not msg.attachments:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Failed to attach file to ticket message.",
            )

        attachment = msg.attachments[0]
        logger.info(
            "Uploaded file attachment to ticket",
            extra={
                "correlation_id": get_correlation_id(),
                "ticket_id": ticket_id,
                "attachment_id": str(attachment.id),
                "attachment_filename": attachment.filename,
                "file_size_bytes": attachment.file_size_bytes,
            },
        )
        return TicketAttachmentResponseSchema(**attachment.to_dict())
    except TicketNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except (InvalidMessageError, InvalidTicketError, TicketAttachmentError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@tickets_router.get(
    "/{ticket_id}/messages",
    response_model=List[TicketMessageResponseSchema],
    status_code=status.HTTP_200_OK,
    summary="Retrieve ticket thread messages",
)
def get_messages_endpoint(
    ticket_id: str,
    db: Session = Depends(get_db),
) -> List[TicketMessageResponseSchema]:
    """Retrieve all chronological messages and attachment metadata for a ticket thread [FR-2]."""
    try:
        service = TicketService(db)
        messages = service.get_messages(ticket_id)
        result = []
        for msg in messages:
            msg_dict = msg.to_dict()
            att_list = []
            if hasattr(msg, "attachments") and msg.attachments:
                for att in msg.attachments:
                    att_list.append(TicketAttachmentResponseSchema(**att.to_dict()))
            msg_dict["attachments"] = att_list
            result.append(TicketMessageResponseSchema(**msg_dict))
        return result
    except TicketNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidTicketError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@tickets_router.get(
    "/attachments/{attachment_id}",
    status_code=status.HTTP_200_OK,
    summary="Download ticket attachment file",
)
def download_attachment_endpoint(
    attachment_id: str,
    db: Session = Depends(get_db),
) -> Response:
    """Retrieve and stream binary content for a ticket attachment [FR-2]."""
    try:
        service = TicketService(db)
        content, att = service.get_attachment_bytes(attachment_id)
        headers = {
            "Content-Disposition": f'attachment; filename="{att.filename}"',
        }
        return Response(content=content, media_type=att.mime_type, headers=headers)
    except TicketAttachmentError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidTicketError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@tickets_router.post(
    "/{ticket_id}/csat",
    response_model=CSATRatingResponseSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Submit CSAT rating for a resolved ticket",
)
def submit_csat_endpoint(
    ticket_id: str,
    payload: CreateCSATRatingSchema,
    db: Session = Depends(get_db),
) -> CSATRatingResponseSchema:
    """Submit client CSAT satisfaction score (1 to 5) for a resolved support ticket [FR-9, NFR-2]."""
    try:
        service = TicketService(db)
        csat = service.submit_csat_rating(
            ticket_id=ticket_id,
            score=payload.score,
            feedback_text=payload.feedback_text,
        )
        logger.info(
            "Submitted CSAT rating via API endpoint",
            extra={
                "correlation_id": get_correlation_id(),
                "ticket_id": ticket_id,
                "csat_id": str(csat.id),
                "score": csat.score,
            },
        )
        return CSATRatingResponseSchema(**csat.to_dict())
    except TicketNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except CSATAlreadySubmittedError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))
    except (InvalidCSATError, InvalidTicketError) as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))


@tickets_router.get(
    "/{ticket_id}/csat",
    response_model=CSATRatingResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Get CSAT rating for a ticket",
)
def get_csat_endpoint(
    ticket_id: str,
    db: Session = Depends(get_db),
) -> CSATRatingResponseSchema:
    """Retrieve CSAT rating score and feedback for a support ticket [FR-9]."""
    try:
        service = TicketService(db)
        csat = service.get_csat_rating(ticket_id)
        if not csat:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"CSAT rating for ticket '{ticket_id}' not found or not yet submitted.",
            )
        return CSATRatingResponseSchema(**csat.to_dict())
    except TicketNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc))
    except InvalidTicketError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
