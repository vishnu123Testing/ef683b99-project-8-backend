"""SQLAlchemy ORM models for Marketing & Territory Analytics.

Defines database entities for territories, lead_meetings, and followup_tasks.
"""

from datetime import datetime, timezone
from typing import List, Optional
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base


class Territory(Base):
    """Territory ORM model representing geographic territory definitions."""

    __tablename__ = "territories"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    territory_code: Mapped[str] = mapped_column(
        String(50),
        unique=True,
        nullable=False,
        index=True,
    )
    territory_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    postal_code_patterns: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        """Convert Territory instance into a dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "territory_code": self.territory_code,
            "territory_name": self.territory_name,
            "postal_code_patterns": self.postal_code_patterns,
            "created_on": self.created_on.isoformat() if self.created_on else None,
        }

    def __repr__(self) -> str:
        return (
            f"<Territory(id={self.id}, territory_code={self.territory_code!r}, "
            f"territory_name={self.territory_name!r})>"
        )


class LeadMeeting(Base):
    """LeadMeeting ORM model for calendar meeting tracking and prep notes."""

    __tablename__ = "lead_meetings"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    scheduled_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    prep_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    meeting_notes: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    followup_tasks: Mapped[List["FollowupTask"]] = relationship(
        "FollowupTask",
        back_populates="meeting",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict:
        """Convert LeadMeeting instance into a dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "lead_id": str(self.lead_id) if self.lead_id else None,
            "scheduled_time": self.scheduled_time.isoformat() if self.scheduled_time else None,
            "prep_notes": self.prep_notes,
            "meeting_notes": self.meeting_notes,
            "created_on": self.created_on.isoformat() if self.created_on else None,
            "followup_tasks": (
                [task.to_dict() for task in self.followup_tasks] if self.followup_tasks else []
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<LeadMeeting(id={self.id}, lead_id={self.lead_id}, "
            f"scheduled_time={self.scheduled_time})>"
        )


class FollowupTask(Base):
    """FollowupTask ORM model for post-meeting assigned action items."""

    __tablename__ = "followup_tasks"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    meeting_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("lead_meetings.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    lead_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    assigned_user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    due_date_val: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    task_description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    meeting: Mapped[Optional["LeadMeeting"]] = relationship(
        "LeadMeeting",
        back_populates="followup_tasks",
    )

    def to_dict(self) -> dict:
        """Convert FollowupTask instance into a dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "meeting_id": str(self.meeting_id) if self.meeting_id else None,
            "lead_id": str(self.lead_id) if self.lead_id else None,
            "assigned_user_id": str(self.assigned_user_id) if self.assigned_user_id else None,
            "due_date_val": self.due_date_val.isoformat() if self.due_date_val else None,
            "is_completed": self.is_completed,
            "task_description": self.task_description,
            "created_on": self.created_on.isoformat() if self.created_on else None,
        }

    def __repr__(self) -> str:
        return (
            f"<FollowupTask(id={self.id}, meeting_id={self.meeting_id}, "
            f"assigned_user_id={self.assigned_user_id}, is_completed={self.is_completed})>"
        )
