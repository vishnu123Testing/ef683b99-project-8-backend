"""SQLAlchemy ORM models for Lead Acquisition & Marketing Analytics.

Defines database entities for acquisition_channels, lead_events, campaigns,
funnel_thresholds, and report_audit_logs.
"""

from datetime import datetime, timezone
from typing import List, Optional
import uuid

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base


class AcquisitionChannel(Base):
    """AcquisitionChannel ORM model for marketing channels and spend data."""

    __tablename__ = "acquisition_channels"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    channel_name: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    spend_amount: Mapped[float] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=0.0,
    )
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    lead_events: Mapped[List["LeadEvent"]] = relationship(
        "LeadEvent",
        back_populates="channel",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict:
        """Convert AcquisitionChannel instance into a dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "channel_name": self.channel_name,
            "spend_amount": float(self.spend_amount) if self.spend_amount is not None else 0.0,
            "created_on": self.created_on.isoformat() if self.created_on else None,
        }

    def __repr__(self) -> str:
        return f"<AcquisitionChannel(id={self.id}, channel_name={self.channel_name!r}, spend_amount={self.spend_amount})>"


class Campaign(Base):
    """Campaign ORM model for marketing campaign metadata and budgets."""

    __tablename__ = "campaigns"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    campaign_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    start_date_val: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    end_date_val: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    budget: Mapped[float] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=0.0,
    )
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    lead_events: Mapped[List["LeadEvent"]] = relationship(
        "LeadEvent",
        back_populates="campaign",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict:
        """Convert Campaign instance into a dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "campaign_name": self.campaign_name,
            "start_date_val": self.start_date_val.isoformat() if self.start_date_val else None,
            "end_date_val": self.end_date_val.isoformat() if self.end_date_val else None,
            "budget": float(self.budget) if self.budget is not None else 0.0,
            "created_on": self.created_on.isoformat() if self.created_on else None,
        }

    def __repr__(self) -> str:
        return f"<Campaign(id={self.id}, campaign_name={self.campaign_name!r}, budget={self.budget})>"


class LeadEvent(Base):
    """LeadEvent ORM model for tracking individual lead progressions and revenue events."""

    __tablename__ = "lead_events"

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
    channel_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("acquisition_channels.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    campaign_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("campaigns.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    territory_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("territories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    stage_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        index=True,
    )
    quality_score: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )
    deal_value: Mapped[float] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=0.0,
    )
    revenue_amount: Mapped[float] = mapped_column(
        Numeric(12, 2),
        nullable=False,
        default=0.0,
    )
    postal_code: Mapped[Optional[str]] = mapped_column(
        String(20),
        nullable=True,
        index=True,
    )
    event_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    channel: Mapped[Optional["AcquisitionChannel"]] = relationship(
        "AcquisitionChannel",
        back_populates="lead_events",
    )
    campaign: Mapped[Optional["Campaign"]] = relationship(
        "Campaign",
        back_populates="lead_events",
    )

    def to_dict(self) -> dict:
        """Convert LeadEvent instance into a dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "lead_id": str(self.lead_id) if self.lead_id else None,
            "channel_id": str(self.channel_id) if self.channel_id else None,
            "campaign_id": str(self.campaign_id) if self.campaign_id else None,
            "territory_id": str(self.territory_id) if self.territory_id else None,
            "stage_name": self.stage_name,
            "quality_score": self.quality_score,
            "deal_value": float(self.deal_value) if self.deal_value is not None else 0.0,
            "revenue_amount": float(self.revenue_amount) if self.revenue_amount is not None else 0.0,
            "postal_code": self.postal_code,
            "event_timestamp": self.event_timestamp.isoformat() if self.event_timestamp else None,
        }

    def __repr__(self) -> str:
        return f"<LeadEvent(id={self.id}, lead_id={self.lead_id}, stage_name={self.stage_name!r})>"


class FunnelThreshold(Base):
    """FunnelThreshold ORM model for bottleneck detection thresholds per funnel stage."""

    __tablename__ = "funnel_thresholds"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    stage_name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )
    max_dropoff_percent: Mapped[float] = mapped_column(
        Numeric(5, 2),
        nullable=False,
        default=0.0,
    )
    updated_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        """Convert FunnelThreshold instance into a dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "stage_name": self.stage_name,
            "max_dropoff_percent": float(self.max_dropoff_percent) if self.max_dropoff_percent is not None else 0.0,
            "updated_on": self.updated_on.isoformat() if self.updated_on else None,
        }

    def __repr__(self) -> str:
        return f"<FunnelThreshold(id={self.id}, stage_name={self.stage_name!r}, max_dropoff_percent={self.max_dropoff_percent})>"


class ReportAuditLog(Base):
    """ReportAuditLog ORM model capturing report configuration changes."""

    __tablename__ = "report_audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    action_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    config_delta: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
        default=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        """Convert ReportAuditLog instance into a dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "action_type": self.action_type,
            "config_delta": self.config_delta,
            "created_on": self.created_on.isoformat() if self.created_on else None,
        }

    def __repr__(self) -> str:
        return f"<ReportAuditLog(id={self.id}, user_id={self.user_id}, action_type={self.action_type!r})>"
