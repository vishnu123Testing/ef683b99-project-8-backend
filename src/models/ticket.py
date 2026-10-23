"""SQLAlchemy ORM models for client support operations and SLA management.

Defines database entities for tickets, thread messages, attachments, SLA tracking,
escalation events, and CSAT ratings.
"""

from datetime import datetime, timezone
from typing import List, Optional
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base


class SupportTicket(Base):
    """Support ticket ORM model representing client service and bug requests."""

    __tablename__ = "support_tickets"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    priority: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
        index=True,
    )  # Values: P1, P2, P3
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        index=True,
        default="open",
    )  # Values: open, pending, resolved, closed
    subject: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    assigned_tier: Mapped[Optional[str]] = mapped_column(
        String(50),
        nullable=True,
        default="tier1",
    )
    assigned_agent_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )
    first_responded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    resolved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    # Relationships
    messages: Mapped[List["TicketMessage"]] = relationship(
        "TicketMessage",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketMessage.created_at",
    )
    sla_trackings: Mapped[List["SLATracking"]] = relationship(
        "SLATracking",
        back_populates="ticket",
        cascade="all, delete-orphan",
    )
    escalations: Mapped[List["TicketEscalation"]] = relationship(
        "TicketEscalation",
        back_populates="ticket",
        cascade="all, delete-orphan",
        order_by="TicketEscalation.escalated_at",
    )
    csat_rating: Mapped[Optional["CSATRating"]] = relationship(
        "CSATRating",
        back_populates="ticket",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<SupportTicket(id={self.id}, priority='{self.priority}', status='{self.status}', subject='{self.subject}')>"

    def to_dict(self) -> dict:
        """Serialize support ticket instance to dictionary format."""
        return {
            "id": str(self.id),
            "priority": self.priority,
            "status": self.status,
            "subject": self.subject,
            "description": self.description,
            "client_id": str(self.client_id) if self.client_id else None,
            "assigned_tier": self.assigned_tier,
            "assigned_agent_id": str(self.assigned_agent_id) if self.assigned_agent_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "first_responded_at": self.first_responded_at.isoformat() if self.first_responded_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }


class TicketMessage(Base):
    """Sequential thread message log ORM model for tickets."""

    __tablename__ = "ticket_messages"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sender_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    sender_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # Values: client, agent, system
    message_body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    ticket: Mapped["SupportTicket"] = relationship(
        "SupportTicket",
        back_populates="messages",
    )
    attachments: Mapped[List["TicketAttachment"]] = relationship(
        "TicketAttachment",
        back_populates="message",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<TicketMessage(id={self.id}, ticket_id={self.ticket_id}, sender_type='{self.sender_type}')>"

    def to_dict(self) -> dict:
        """Serialize message instance to dictionary format."""
        return {
            "id": str(self.id),
            "ticket_id": str(self.ticket_id),
            "sender_id": str(self.sender_id),
            "sender_type": self.sender_type,
            "message_body": self.message_body,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class TicketAttachment(Base):
    """Attachment metadata ORM model associated with ticket thread messages."""

    __tablename__ = "ticket_attachments"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    message_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("ticket_messages.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    filename: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    file_size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    mime_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    storage_key: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    message: Mapped["TicketMessage"] = relationship(
        "TicketMessage",
        back_populates="attachments",
    )

    def __repr__(self) -> str:
        return f"<TicketAttachment(id={self.id}, filename='{self.filename}', file_size_bytes={self.file_size_bytes})>"

    def to_dict(self) -> dict:
        """Serialize attachment metadata instance to dictionary format."""
        return {
            "id": str(self.id),
            "message_id": str(self.message_id),
            "filename": self.filename,
            "file_size_bytes": self.file_size_bytes,
            "mime_type": self.mime_type,
            "storage_key": self.storage_key,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
        }


class SLATracking(Base):
    """SLA countdown state tracking ORM model for first response and resolution targets."""

    __tablename__ = "sla_tracking"
    __table_args__ = (
        UniqueConstraint("ticket_id", "target_type", name="uq_sla_tracking_ticket_target"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    target_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # Values: first_response, resolution
    target_due_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    warning_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    breached_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_breached: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )

    # Relationships
    ticket: Mapped["SupportTicket"] = relationship(
        "SupportTicket",
        back_populates="sla_trackings",
    )

    def __repr__(self) -> str:
        return f"<SLATracking(id={self.id}, ticket_id={self.ticket_id}, target_type='{self.target_type}', is_breached={self.is_breached})>"

    def to_dict(self) -> dict:
        """Serialize SLA tracking instance to dictionary format."""
        return {
            "id": str(self.id),
            "ticket_id": str(self.ticket_id),
            "target_type": self.target_type,
            "target_due_at": self.target_due_at.isoformat() if self.target_due_at else None,
            "warning_sent_at": self.warning_sent_at.isoformat() if self.warning_sent_at else None,
            "breached_at": self.breached_at.isoformat() if self.breached_at else None,
            "is_breached": self.is_breached,
        }


class TicketEscalation(Base):
    """Support tier escalation re-assignment audit log ORM model."""

    __tablename__ = "ticket_escalations"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_tier: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    target_tier: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    escalated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    reason: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    # Relationships
    ticket: Mapped["SupportTicket"] = relationship(
        "SupportTicket",
        back_populates="escalations",
    )

    def __repr__(self) -> str:
        return f"<TicketEscalation(id={self.id}, ticket_id={self.ticket_id}, {self.source_tier} -> {self.target_tier})>"

    def to_dict(self) -> dict:
        """Serialize escalation record to dictionary format."""
        return {
            "id": str(self.id),
            "ticket_id": str(self.ticket_id),
            "source_tier": self.source_tier,
            "target_tier": self.target_tier,
            "escalated_at": self.escalated_at.isoformat() if self.escalated_at else None,
            "reason": self.reason,
        }


class CSATRating(Base):
    """CSAT client feedback rating ORM model."""

    __tablename__ = "csat_ratings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    ticket_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("support_tickets.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )  # Values: 1 to 5
    feedback_text: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    ticket: Mapped["SupportTicket"] = relationship(
        "SupportTicket",
        back_populates="csat_rating",
    )

    def __repr__(self) -> str:
        return f"<CSATRating(id={self.id}, ticket_id={self.ticket_id}, score={self.score})>"

    def to_dict(self) -> dict:
        """Serialize CSAT rating instance to dictionary format."""
        return {
            "id": str(self.id),
            "ticket_id": str(self.ticket_id),
            "score": self.score,
            "feedback_text": self.feedback_text,
            "submitted_at": self.submitted_at.isoformat() if self.submitted_at else None,
        }
