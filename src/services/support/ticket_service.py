"""Domain service for support ticket lifecycle, thread messaging, attachment binding, CSAT scoring, and analytics aggregation [FR-1, FR-2, FR-3, FR-7, FR-8, FR-9, NFR-2, NFR-3, NFR-4].

Provides business rules and persistence operations for:
- Ticket creation, retrieval, updates, filtering, and deletion
- Thread message creation and attachment storage binding
- CSAT rating collection upon ticket resolution
- Metrics aggregation (average response time, MTTR, breach rates, CSAT scores)
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.connectors.notification import NotificationConnector, NotificationPort
from src.connectors.storage import (
    BlobStorageAdapter,
    StorageError,
    StorageNotFoundError,
    StoragePort,
    StorageUploadError,
    StorageValidationError,
)
from src.lib.config import settings
from src.lib.logging import get_correlation_id
from src.models.ticket import (
    CSATRating,
    SLATracking,
    SupportTicket,
    TicketAttachment,
    TicketEscalation,
    TicketMessage,
)
from src.services.support.sla_service import (
    initialize_sla_tracking,
    recalculate_sla_targets,
)

logger = logging.getLogger(__name__)


class TicketServiceError(Exception):
    """Base exception for support ticket domain service operations."""

    pass


class TicketNotFoundError(TicketServiceError):
    """Raised when a requested support ticket is not found."""

    pass


class InvalidTicketError(TicketServiceError):
    """Raised when ticket parameters fail boundary validation rules."""

    pass


class InvalidMessageError(TicketServiceError):
    """Raised when a message payload or sender parameters are invalid."""

    pass


class InvalidCSATError(TicketServiceError):
    """Raised when a CSAT rating score or submission state is invalid."""

    pass


class CSATAlreadySubmittedError(TicketServiceError):
    """Raised when attempting to submit a CSAT rating for a ticket that already has one."""

    pass


class TicketAttachmentError(TicketServiceError):
    """Raised when retrieving or processing a ticket attachment fails."""

    pass


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime object is timezone-aware and set to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_uuid(val: Union[uuid.UUID, str], field_name: str = "id") -> uuid.UUID:
    """Safely parse UUID object or string representation."""
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, AttributeError) as exc:
        raise InvalidTicketError(f"Invalid {field_name} UUID format: '{val}'.") from exc


class TicketService:
    """Domain service managing support ticket lifecycle, messaging, attachments, CSAT ratings, and analytics."""

    def __init__(
        self,
        db: Session,
        storage_port: Optional[StoragePort] = None,
        notification_port: Optional[NotificationPort] = None,
    ) -> None:
        """Initialize TicketService with database session and optional ports [NFR-4].

        Args:
            db: Synchronous SQLAlchemy database session.
            storage_port: Optional BlobStorageAdapter / StoragePort implementation.
            notification_port: Optional NotificationConnector / NotificationPort implementation.
        """
        self.db = db
        self.storage_port = storage_port or BlobStorageAdapter()
        self.notification_port = notification_port or NotificationConnector()

    def create_ticket(
        self,
        priority: str,
        subject: str,
        description: str,
        client_id: Union[uuid.UUID, str],
        assigned_tier: Optional[str] = "tier1",
        assigned_agent_id: Optional[Union[uuid.UUID, str]] = None,
        create_initial_message: bool = True,
    ) -> SupportTicket:
        """Create a new support ticket and initialize SLA tracking [FR-1, FR-4, NFR-2, NFR-3].

        Args:
            priority: Ticket priority level ('P1', 'P2', 'P3').
            subject: Brief ticket summary header.
            description: Detailed problem description.
            client_id: Client UUID or string identifier.
            assigned_tier: Target support tier (defaults to 'tier1').
            assigned_agent_id: Optional assigned agent UUID or string identifier.
            create_initial_message: Toggle for creating the initial client message entry.

        Returns:
            Created SupportTicket ORM instance.

        Raises:
            InvalidTicketError: If inputs fail boundary validation checks.
        """
        if not priority or not str(priority).strip():
            raise InvalidTicketError("Ticket priority cannot be empty.")

        norm_priority = str(priority).upper().strip()
        if norm_priority not in ("P1", "P2", "P3"):
            raise InvalidTicketError(
                f"Invalid ticket priority: '{priority}'. Must be 'P1', 'P2', or 'P3'."
            )

        if not subject or not str(subject).strip():
            raise InvalidTicketError("Ticket subject cannot be empty.")

        if not description or not str(description).strip():
            raise InvalidTicketError("Ticket description cannot be empty.")

        parsed_client_id = _parse_uuid(client_id, field_name="client_id")

        parsed_agent_id: Optional[uuid.UUID] = None
        if assigned_agent_id is not None and str(assigned_agent_id).strip():
            parsed_agent_id = _parse_uuid(assigned_agent_id, field_name="assigned_agent_id")

        now = datetime.now(timezone.utc)
        ticket = SupportTicket(
            priority=norm_priority,
            status="open",
            subject=subject.strip(),
            description=description.strip(),
            client_id=parsed_client_id,
            assigned_tier=assigned_tier.strip() if assigned_tier else "tier1",
            assigned_agent_id=parsed_agent_id,
            created_at=now,
            updated_at=now,
        )

        self.db.add(ticket)
        self.db.flush()
        self.db.commit()

        # Initialize SLA countdown tracking records
        initialize_sla_tracking(self.db, ticket, current_time=now)

        # Create initial client thread message entry if requested
        if create_initial_message:
            initial_msg = TicketMessage(
                ticket_id=ticket.id,
                sender_id=parsed_client_id,
                sender_type="client",
                message_body=description.strip(),
                created_at=now,
            )
            self.db.add(initial_msg)
            self.db.flush()
            self.db.commit()

        logger.info(
            "Created support ticket record",
            extra={
                "correlation_id": get_correlation_id(),
                "ticket_id": str(ticket.id),
                "priority": ticket.priority,
                "client_id": str(ticket.client_id),
                "assigned_tier": ticket.assigned_tier,
            },
        )

        return ticket

    def get_ticket(
        self,
        ticket_id: Union[uuid.UUID, str],
    ) -> SupportTicket:
        """Retrieve a support ticket by UUID or string identifier [FR-1].

        Args:
            ticket_id: Ticket UUID or string identifier.

        Returns:
            SupportTicket ORM instance.

        Raises:
            TicketNotFoundError: If the specified ticket does not exist.
        """
        parsed_id = _parse_uuid(ticket_id, field_name="ticket_id")
        ticket = self.db.query(SupportTicket).filter(SupportTicket.id == parsed_id).first()

        if not ticket:
            raise TicketNotFoundError(f"Support ticket '{ticket_id}' not found.")

        return ticket

    def update_ticket(
        self,
        ticket_id: Union[uuid.UUID, str],
        priority: Optional[str] = None,
        status: Optional[str] = None,
        assigned_tier: Optional[str] = None,
        assigned_agent_id: Optional[Union[uuid.UUID, str]] = None,
        subject: Optional[str] = None,
        description: Optional[str] = None,
    ) -> SupportTicket:
        """Update support ticket fields, status transitions, and priority SLAs [FR-1, FR-4, FR-7, NFR-3].

        Args:
            ticket_id: Target ticket UUID or string identifier.
            priority: Optional new priority ('P1', 'P2', 'P3').
            status: Optional new status ('open', 'pending', 'resolved', 'closed').
            assigned_tier: Optional updated support tier.
            assigned_agent_id: Optional updated agent UUID or string identifier.
            subject: Optional updated subject.
            description: Optional updated description.

        Returns:
            Updated SupportTicket ORM instance.

        Raises:
            TicketNotFoundError: If ticket does not exist.
            InvalidTicketError: If updated field values fail validation.
        """
        ticket = self.get_ticket(ticket_id)
        now = datetime.now(timezone.utc)

        if priority is not None and str(priority).strip():
            norm_p = str(priority).upper().strip()
            if norm_p not in ("P1", "P2", "P3"):
                raise InvalidTicketError(
                    f"Invalid ticket priority: '{priority}'. Must be 'P1', 'P2', or 'P3'."
                )
            if norm_p != ticket.priority:
                ticket.priority = norm_p
                recalculate_sla_targets(self.db, ticket, norm_p, current_time=now)

        if status is not None and str(status).strip():
            norm_s = str(status).lower().strip()
            if norm_s not in ("open", "pending", "resolved", "closed"):
                raise InvalidTicketError(
                    f"Invalid ticket status: '{status}'. Allowed: open, pending, resolved, closed."
                )
            if norm_s != ticket.status:
                ticket.status = norm_s
                if norm_s in ("resolved", "closed") and ticket.resolved_at is None:
                    ticket.resolved_at = now

        if assigned_tier is not None and str(assigned_tier).strip():
            ticket.assigned_tier = str(assigned_tier).strip()

        if assigned_agent_id is not None:
            if str(assigned_agent_id).strip():
                ticket.assigned_agent_id = _parse_uuid(
                    assigned_agent_id, field_name="assigned_agent_id"
                )
            else:
                ticket.assigned_agent_id = None

        if subject is not None and str(subject).strip():
            ticket.subject = str(subject).strip()

        if description is not None and str(description).strip():
            ticket.description = str(description).strip()

        ticket.updated_at = now
        self.db.flush()
        self.db.commit()

        logger.info(
            "Updated support ticket record",
            extra={
                "correlation_id": get_correlation_id(),
                "ticket_id": str(ticket.id),
                "priority": ticket.priority,
                "status": ticket.status,
                "assigned_tier": ticket.assigned_tier,
                "assigned_agent_id": str(ticket.assigned_agent_id) if ticket.assigned_agent_id else None,
            },
        )

        return ticket

    def list_tickets(
        self,
        client_id: Optional[Union[uuid.UUID, str]] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None,
        assigned_tier: Optional[str] = None,
        assigned_agent_id: Optional[Union[uuid.UUID, str]] = None,
        limit: int = 50,
        offset: int = 0,
    ) -> List[SupportTicket]:
        """List and filter support tickets matching criteria [FR-1]."""
        query = self.db.query(SupportTicket)

        if client_id is not None and str(client_id).strip():
            parsed_client = _parse_uuid(client_id, field_name="client_id")
            query = query.filter(SupportTicket.client_id == parsed_client)

        if priority is not None and str(priority).strip():
            query = query.filter(SupportTicket.priority == str(priority).upper().strip())

        if status is not None and str(status).strip():
            query = query.filter(SupportTicket.status == str(status).lower().strip())

        if assigned_tier is not None and str(assigned_tier).strip():
            query = query.filter(SupportTicket.assigned_tier == str(assigned_tier).strip())

        if assigned_agent_id is not None and str(assigned_agent_id).strip():
            parsed_agent = _parse_uuid(assigned_agent_id, field_name="assigned_agent_id")
            query = query.filter(SupportTicket.assigned_agent_id == parsed_agent)

        query = query.order_by(SupportTicket.created_at.desc())
        return query.offset(offset).limit(limit).all()

    def count_tickets(
        self,
        client_id: Optional[Union[uuid.UUID, str]] = None,
        priority: Optional[str] = None,
        status: Optional[str] = None,
        assigned_tier: Optional[str] = None,
        assigned_agent_id: Optional[Union[uuid.UUID, str]] = None,
    ) -> int:
        """Count total support tickets matching filter parameters."""
        query = self.db.query(func.count(SupportTicket.id))

        if client_id is not None and str(client_id).strip():
            parsed_client = _parse_uuid(client_id, field_name="client_id")
            query = query.filter(SupportTicket.client_id == parsed_client)

        if priority is not None and str(priority).strip():
            query = query.filter(SupportTicket.priority == str(priority).upper().strip())

        if status is not None and str(status).strip():
            query = query.filter(SupportTicket.status == str(status).lower().strip())

        if assigned_tier is not None and str(assigned_tier).strip():
            query = query.filter(SupportTicket.assigned_tier == str(assigned_tier).strip())

        if assigned_agent_id is not None and str(assigned_agent_id).strip():
            parsed_agent = _parse_uuid(assigned_agent_id, field_name="assigned_agent_id")
            query = query.filter(SupportTicket.assigned_agent_id == parsed_agent)

        return query.scalar() or 0

    def delete_ticket(
        self,
        ticket_id: Union[uuid.UUID, str],
    ) -> bool:
        """Delete a support ticket and associated entities [FR-1]."""
        ticket = self.get_ticket(ticket_id)
        self.db.delete(ticket)
        self.db.flush()
        self.db.commit()

        logger.info(
            "Deleted support ticket record",
            extra={
                "correlation_id": get_correlation_id(),
                "ticket_id": str(ticket_id),
            },
        )
        return True

    def add_message(
        self,
        ticket_id: Union[uuid.UUID, str],
        sender_id: Union[uuid.UUID, str],
        sender_type: str,
        message_body: str,
        attachments: Optional[List[Union[Dict[str, Any], Tuple[Any, ...]]]] = None,
    ) -> TicketMessage:
        """Append a sequential message to a ticket thread with optional file attachment bindings [FR-2, FR-3, FR-7, NFR-2]."""
        ticket = self.get_ticket(ticket_id)

        if not message_body or not str(message_body).strip():
            raise InvalidMessageError("Message body text cannot be empty.")

        norm_type = str(sender_type).lower().strip()
        if norm_type not in ("client", "agent", "system"):
            raise InvalidMessageError(
                f"Invalid sender_type: '{sender_type}'. Must be 'client', 'agent', or 'system'."
            )

        parsed_sender = _parse_uuid(sender_id, field_name="sender_id")
        now = datetime.now(timezone.utc)

        # Log timestamp for first agent response if this is the first agent message
        if norm_type == "agent" and ticket.first_responded_at is None:
            ticket.first_responded_at = now

        msg = TicketMessage(
            ticket_id=ticket.id,
            sender_id=parsed_sender,
            sender_type=norm_type,
            message_body=message_body.strip(),
            created_at=now,
        )
        self.db.add(msg)
        self.db.flush()
        self.db.commit()

        # Handle attachment processing if provided
        if attachments:
            for att in attachments:
                filename: str = ""
                content: bytes = b""
                mime_type: Optional[str] = None

                if isinstance(att, dict):
                    filename = att.get("filename", "")
                    content = att.get("content", b"")
                    mime_type = att.get("mime_type")
                elif isinstance(att, (tuple, list)):
                    if len(att) >= 2:
                        filename = str(att[0])
                        content = att[1] if isinstance(att[1], bytes) else str(att[1]).encode()
                        if len(att) >= 3:
                            mime_type = str(att[2])
                else:
                    raise InvalidMessageError("Unsupported attachment format.")

                try:
                    res = self.storage_port.save_attachment(
                        ticket_id=ticket.id,
                        message_id=msg.id,
                        filename=filename,
                        content=content,
                        mime_type=mime_type,
                    )
                except StorageValidationError as exc:
                    raise InvalidMessageError(f"Attachment validation failed: {exc}") from exc
                except StorageError as exc:
                    raise TicketAttachmentError(f"Failed to store attachment: {exc}") from exc

                attachment_record = TicketAttachment(
                    id=uuid.uuid4(),
                    message_id=msg.id,
                    filename=res.filename,
                    file_size_bytes=res.file_size_bytes,
                    mime_type=res.mime_type,
                    storage_key=res.storage_key,
                    uploaded_at=res.stored_at,
                )
                self.db.add(attachment_record)

        ticket.updated_at = now
        self.db.flush()
        self.db.commit()

        logger.info(
            "Added message to support ticket thread",
            extra={
                "correlation_id": get_correlation_id(),
                "ticket_id": str(ticket.id),
                "message_id": str(msg.id),
                "sender_type": msg.sender_type,
                "has_attachments": bool(attachments),
            },
        )

        return msg

    def get_messages(
        self,
        ticket_id: Union[uuid.UUID, str],
    ) -> List[TicketMessage]:
        """Retrieve all sequential thread messages for a ticket ordered chronologically [FR-2]."""
        ticket = self.get_ticket(ticket_id)
        return (
            self.db.query(TicketMessage)
            .filter(TicketMessage.ticket_id == ticket.id)
            .order_by(TicketMessage.created_at.asc())
            .all()
        )

    def get_attachment_bytes(
        self,
        attachment_id: Union[uuid.UUID, str],
    ) -> Tuple[bytes, TicketAttachment]:
        """Retrieve binary attachment content and metadata given attachment ID [FR-2]."""
        parsed_id = _parse_uuid(attachment_id, field_name="attachment_id")
        att = (
            self.db.query(TicketAttachment)
            .filter(TicketAttachment.id == parsed_id)
            .first()
        )
        if not att:
            raise TicketAttachmentError(f"Attachment '{attachment_id}' not found.")

        try:
            content = self.storage_port.get_attachment(att.storage_key)
            return content, att
        except StorageError as exc:
            raise TicketAttachmentError(
                f"Failed to retrieve attachment content for key '{att.storage_key}': {exc}"
            ) from exc

    def submit_csat_rating(
        self,
        ticket_id: Union[uuid.UUID, str],
        score: int,
        feedback_text: Optional[str] = None,
    ) -> CSATRating:
        """Submit a client CSAT satisfaction rating upon ticket resolution [FR-9, NFR-2]."""
        ticket = self.get_ticket(ticket_id)

        if ticket.status not in ("resolved", "closed"):
            raise InvalidCSATError(
                f"Cannot submit CSAT rating for ticket in '{ticket.status}' status. "
                "Ticket must be resolved or closed."
            )

        # Check if CSAT rating already submitted
        existing_csat = (
            self.db.query(CSATRating)
            .filter(CSATRating.ticket_id == ticket.id)
            .first()
        )
        if existing_csat:
            raise CSATAlreadySubmittedError(
                f"CSAT rating already submitted for ticket '{ticket_id}'."
            )

        if not isinstance(score, int) or score < 1 or score > 5:
            raise InvalidCSATError(
                f"CSAT score must be an integer between 1 and 5. Received: {score}."
            )

        now = datetime.now(timezone.utc)
        clean_feedback = feedback_text.strip() if feedback_text and feedback_text.strip() else None

        csat = CSATRating(
            ticket_id=ticket.id,
            score=score,
            feedback_text=clean_feedback,
            submitted_at=now,
        )
        self.db.add(csat)
        self.db.flush()
        self.db.commit()

        logger.info(
            "Submitted CSAT rating for support ticket",
            extra={
                "correlation_id": get_correlation_id(),
                "ticket_id": str(ticket.id),
                "score": csat.score,
            },
        )

        return csat

    def get_csat_rating(
        self,
        ticket_id: Union[uuid.UUID, str],
    ) -> Optional[CSATRating]:
        """Retrieve CSAT rating for a ticket or None if not submitted [FR-9]."""
        parsed_id = _parse_uuid(ticket_id, field_name="ticket_id")
        return (
            self.db.query(CSATRating)
            .filter(CSATRating.ticket_id == parsed_id)
            .first()
        )

    def get_support_analytics(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        priority: Optional[str] = None,
        client_id: Optional[Union[uuid.UUID, str]] = None,
    ) -> Dict[str, Any]:
        """Aggregate operational support analytics and metrics over specified time window [FR-8]."""
        start_utc = _ensure_utc(start_time)
        end_utc = _ensure_utc(end_time)

        query = self.db.query(SupportTicket)

        if start_utc:
            query = query.filter(SupportTicket.created_at >= start_utc)

        if end_utc:
            query = query.filter(SupportTicket.created_at <= end_utc)

        if priority is not None and str(priority).strip():
            query = query.filter(SupportTicket.priority == str(priority).upper().strip())

        if client_id is not None and str(client_id).strip():
            parsed_client = _parse_uuid(client_id, field_name="client_id")
            query = query.filter(SupportTicket.client_id == parsed_client)

        tickets = query.all()
        ticket_ids = [t.id for t in tickets]

        total_tickets = len(tickets)
        open_count = sum(1 for t in tickets if t.status in ("open", "pending"))
        resolved_count = sum(1 for t in tickets if t.status in ("resolved", "closed"))

        # First response times
        resp_times_min = [
            (t.first_responded_at - t.created_at).total_seconds() / 60.0
            for t in tickets
            if t.first_responded_at is not None and t.created_at is not None
        ]
        avg_resp_min = (
            sum(resp_times_min) / len(resp_times_min) if resp_times_min else 0.0
        )

        # Mean time to resolution (MTTR)
        mttr_hours_list = [
            (t.resolved_at - t.created_at).total_seconds() / 3600.0
            for t in tickets
            if t.resolved_at is not None and t.created_at is not None
        ]
        avg_mttr_hours = (
            sum(mttr_hours_list) / len(mttr_hours_list) if mttr_hours_list else 0.0
        )

        # SLA breach rate calculations
        sla_breach_rate = 0.0
        if ticket_ids:
            breached_ticket_ids = set(
                r[0]
                for r in self.db.query(SLATracking.ticket_id)
                .filter(
                    SLATracking.ticket_id.in_(ticket_ids),
                    SLATracking.is_breached == True,
                )
                .distinct()
                .all()
            )
            sla_breach_rate = (
                (len(breached_ticket_ids) / total_tickets * 100.0)
                if total_tickets > 0
                else 0.0
            )

        # CSAT ratings
        csat_scores = []
        if ticket_ids:
            csat_records = (
                self.db.query(CSATRating)
                .filter(CSATRating.ticket_id.in_(ticket_ids))
                .all()
            )
            csat_scores = [c.score for c in csat_records]

        avg_csat = (sum(csat_scores) / len(csat_scores)) if csat_scores else 0.0
        csat_count = len(csat_scores)

        # Priority breakdown
        p1_count = sum(1 for t in tickets if t.priority == "P1")
        p2_count = sum(1 for t in tickets if t.priority == "P2")
        p3_count = sum(1 for t in tickets if t.priority == "P3")

        return {
            "time_window": {
                "start_time": start_utc.isoformat() if start_utc else None,
                "end_time": end_utc.isoformat() if end_utc else None,
            },
            "total_tickets": total_tickets,
            "open_tickets_count": open_count,
            "resolved_tickets_count": resolved_count,
            "average_response_time_minutes": round(avg_resp_min, 2),
            "mean_time_to_resolution_hours": round(avg_mttr_hours, 2),
            "sla_breach_rate_percent": round(sla_breach_rate, 2),
            "average_csat_score": round(avg_csat, 2),
            "csat_response_count": csat_count,
            "priority_breakdown": {
                "P1": p1_count,
                "P2": p2_count,
                "P3": p3_count,
            },
        }


# Standalone function wrappers for functional usage
def create_ticket(
    db: Session,
    priority: str,
    subject: str,
    description: str,
    client_id: Union[uuid.UUID, str],
    assigned_tier: Optional[str] = "tier1",
    assigned_agent_id: Optional[Union[uuid.UUID, str]] = None,
    storage_port: Optional[StoragePort] = None,
    notification_port: Optional[NotificationPort] = None,
) -> SupportTicket:
    """Standalone wrapper for creating a support ticket."""
    service = TicketService(db, storage_port=storage_port, notification_port=notification_port)
    return service.create_ticket(
        priority=priority,
        subject=subject,
        description=description,
        client_id=client_id,
        assigned_tier=assigned_tier,
        assigned_agent_id=assigned_agent_id,
    )


def get_ticket(
    db: Session,
    ticket_id: Union[uuid.UUID, str],
) -> SupportTicket:
    """Standalone wrapper for retrieving a support ticket."""
    service = TicketService(db)
    return service.get_ticket(ticket_id)


def update_ticket(
    db: Session,
    ticket_id: Union[uuid.UUID, str],
    priority: Optional[str] = None,
    status: Optional[str] = None,
    assigned_tier: Optional[str] = None,
    assigned_agent_id: Optional[Union[uuid.UUID, str]] = None,
    subject: Optional[str] = None,
    description: Optional[str] = None,
) -> SupportTicket:
    """Standalone wrapper for updating a support ticket."""
    service = TicketService(db)
    return service.update_ticket(
        ticket_id=ticket_id,
        priority=priority,
        status=status,
        assigned_tier=assigned_tier,
        assigned_agent_id=assigned_agent_id,
        subject=subject,
        description=description,
    )


def list_tickets(
    db: Session,
    client_id: Optional[Union[uuid.UUID, str]] = None,
    priority: Optional[str] = None,
    status: Optional[str] = None,
    assigned_tier: Optional[str] = None,
    assigned_agent_id: Optional[Union[uuid.UUID, str]] = None,
    limit: int = 50,
    offset: int = 0,
) -> List[SupportTicket]:
    """Standalone wrapper for listing and filtering support tickets."""
    service = TicketService(db)
    return service.list_tickets(
        client_id=client_id,
        priority=priority,
        status=status,
        assigned_tier=assigned_tier,
        assigned_agent_id=assigned_agent_id,
        limit=limit,
        offset=offset,
    )


def delete_ticket(
    db: Session,
    ticket_id: Union[uuid.UUID, str],
) -> bool:
    """Standalone wrapper for deleting a support ticket."""
    service = TicketService(db)
    return service.delete_ticket(ticket_id)


def add_message(
    db: Session,
    ticket_id: Union[uuid.UUID, str],
    sender_id: Union[uuid.UUID, str],
    sender_type: str,
    message_body: str,
    attachments: Optional[List[Union[Dict[str, Any], Tuple[Any, ...]]]] = None,
    storage_port: Optional[StoragePort] = None,
) -> TicketMessage:
    """Standalone wrapper for adding a message to a ticket thread."""
    service = TicketService(db, storage_port=storage_port)
    return service.add_message(
        ticket_id=ticket_id,
        sender_id=sender_id,
        sender_type=sender_type,
        message_body=message_body,
        attachments=attachments,
    )


def get_messages(
    db: Session,
    ticket_id: Union[uuid.UUID, str],
) -> List[TicketMessage]:
    """Standalone wrapper for retrieving messages for a ticket."""
    service = TicketService(db)
    return service.get_messages(ticket_id)


def submit_csat_rating(
    db: Session,
    ticket_id: Union[uuid.UUID, str],
    score: int,
    feedback_text: Optional[str] = None,
) -> CSATRating:
    """Standalone wrapper for submitting a CSAT rating."""
    service = TicketService(db)
    return service.submit_csat_rating(
        ticket_id=ticket_id,
        score=score,
        feedback_text=feedback_text,
    )


def get_support_analytics(
    db: Session,
    start_time: Optional[datetime] = None,
    end_time: Optional[datetime] = None,
    priority: Optional[str] = None,
    client_id: Optional[Union[uuid.UUID, str]] = None,
) -> Dict[str, Any]:
    """Standalone wrapper for aggregating support analytics."""
    service = TicketService(db)
    return service.get_support_analytics(
        start_time=start_time,
        end_time=end_time,
        priority=priority,
        client_id=client_id,
    )
