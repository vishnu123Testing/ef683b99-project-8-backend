"""SQLAlchemy ORM models for Visual Deal Pipeline & Sales Intelligence.

Defines database entities for Deal, ActionItem, Stakeholder, StakeholderActivity,
and DealForecast.
"""

from datetime import datetime, timezone
from typing import List, Optional
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base


class Deal(Base):
    """Deal ORM entity representing a sales opportunity in the visual pipeline."""

    __tablename__ = "deals"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    deal_value: Mapped[float] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=0.0,
    )
    milestone_stage: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    win_probability: Mapped[float] = mapped_column(
        Float,
        nullable=False,
        default=0.0,
    )
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    action_items: Mapped[List["ActionItem"]] = relationship(
        "ActionItem",
        back_populates="deal",
        cascade="all, delete-orphan",
    )
    stakeholders: Mapped[List["Stakeholder"]] = relationship(
        "Stakeholder",
        back_populates="deal",
        cascade="all, delete-orphan",
    )
    stakeholder_activities: Mapped[List["StakeholderActivity"]] = relationship(
        "StakeholderActivity",
        back_populates="deal",
        cascade="all, delete-orphan",
    )
    forecast: Mapped[Optional["DealForecast"]] = relationship(
        "DealForecast",
        back_populates="deal",
        uselist=False,
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict:
        """Convert Deal instance into a dictionary representation."""
        return {
            "id": self.id,
            "title": self.title,
            "deal_value": float(self.deal_value) if self.deal_value is not None else 0.0,
            "milestone_stage": self.milestone_stage,
            "win_probability": self.win_probability,
            "created_on": self.created_on.isoformat() if self.created_on else None,
            "updated_on": self.updated_on.isoformat() if self.updated_on else None,
        }

    def __repr__(self) -> str:
        return (
            f"<Deal(id={self.id!r}, "
            f"title={self.title!r}, "
            f"milestone_stage={self.milestone_stage!r}, "
            f"deal_value={self.deal_value})>"
        )


class ActionItem(Base):
    """ActionItem ORM entity representing a task/action item assigned to a deal."""

    __tablename__ = "action_items"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    deal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    assigned_owner_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    target_due_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    sla_threshold_hours: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=24,
    )
    is_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    completed_on: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    is_escalated: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
        index=True,
    )
    escalated_on: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    deal: Mapped["Deal"] = relationship(
        "Deal",
        back_populates="action_items",
    )

    def to_dict(self) -> dict:
        """Convert ActionItem instance into a dictionary representation."""
        return {
            "id": self.id,
            "deal_id": self.deal_id,
            "title": self.title,
            "assigned_owner_id": self.assigned_owner_id,
            "target_due_date": self.target_due_date.isoformat() if self.target_due_date else None,
            "sla_threshold_hours": self.sla_threshold_hours,
            "is_completed": self.is_completed,
            "completed_on": self.completed_on.isoformat() if self.completed_on else None,
            "is_escalated": self.is_escalated,
            "escalated_on": self.escalated_on.isoformat() if self.escalated_on else None,
            "created_on": self.created_on.isoformat() if self.created_on else None,
        }

    def __repr__(self) -> str:
        return (
            f"<ActionItem(id={self.id!r}, "
            f"deal_id={self.deal_id!r}, "
            f"title={self.title!r}, "
            f"is_completed={self.is_completed}, "
            f"is_escalated={self.is_escalated})>"
        )


class Stakeholder(Base):
    """Stakeholder ORM entity representing a mapped contact for a deal."""

    __tablename__ = "stakeholders"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    deal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    full_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    org_role: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    influence_level: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    deal: Mapped["Deal"] = relationship(
        "Deal",
        back_populates="stakeholders",
    )
    activities: Mapped[List["StakeholderActivity"]] = relationship(
        "StakeholderActivity",
        back_populates="stakeholder",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict:
        """Convert Stakeholder instance into a dictionary representation."""
        return {
            "id": self.id,
            "deal_id": self.deal_id,
            "full_name": self.full_name,
            "email": self.email,
            "org_role": self.org_role,
            "influence_level": self.influence_level,
            "is_active": self.is_active,
            "created_on": self.created_on.isoformat() if self.created_on else None,
        }

    def __repr__(self) -> str:
        return (
            f"<Stakeholder(id={self.id!r}, "
            f"deal_id={self.deal_id!r}, "
            f"full_name={self.full_name!r}, "
            f"org_role={self.org_role!r}, "
            f"influence_level={self.influence_level!r})>"
        )


class StakeholderActivity(Base):
    """StakeholderActivity ORM entity representing an interaction with a stakeholder."""

    __tablename__ = "stakeholder_activities"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    stakeholder_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("stakeholders.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    deal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    activity_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    occurred_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    stakeholder: Mapped["Stakeholder"] = relationship(
        "Stakeholder",
        back_populates="activities",
    )
    deal: Mapped["Deal"] = relationship(
        "Deal",
        back_populates="stakeholder_activities",
    )

    def to_dict(self) -> dict:
        """Convert StakeholderActivity instance into a dictionary representation."""
        return {
            "id": self.id,
            "stakeholder_id": self.stakeholder_id,
            "deal_id": self.deal_id,
            "activity_type": self.activity_type,
            "summary": self.summary,
            "occurred_on": self.occurred_on.isoformat() if self.occurred_on else None,
            "created_on": self.created_on.isoformat() if self.created_on else None,
        }

    def __repr__(self) -> str:
        return (
            f"<StakeholderActivity(id={self.id!r}, "
            f"stakeholder_id={self.stakeholder_id!r}, "
            f"deal_id={self.deal_id!r}, "
            f"activity_type={self.activity_type!r})>"
        )


class DealForecast(Base):
    """DealForecast ORM entity representing AI-predicted deal completion date and confidence."""

    __tablename__ = "deal_forecasts"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    deal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    estimated_completion_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    confidence_score_pct: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
    )
    status_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="COMPUTING",
    )
    calculated_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    deal: Mapped["Deal"] = relationship(
        "Deal",
        back_populates="forecast",
    )

    def to_dict(self) -> dict:
        """Convert DealForecast instance into a dictionary representation."""
        return {
            "id": self.id,
            "deal_id": self.deal_id,
            "estimated_completion_date": (
                self.estimated_completion_date.isoformat()
                if self.estimated_completion_date
                else None
            ),
            "confidence_score_pct": self.confidence_score_pct,
            "status_code": self.status_code,
            "calculated_on": self.calculated_on.isoformat() if self.calculated_on else None,
        }

    def __repr__(self) -> str:
        return (
            f"<DealForecast(id={self.id!r}, "
            f"deal_id={self.deal_id!r}, "
            f"status_code={self.status_code!r}, "
            f"confidence_score_pct={self.confidence_score_pct})>"
        )
