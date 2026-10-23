"""SQLAlchemy ORM models for multi-channel alerting and notification hub.

Defines database entities for AlertTrigger, NotificationPreference,
NotificationLog, and AlertSuppression with explicit indexes and relationships.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
import uuid

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    JSON,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base


class AlertTrigger(Base):
    """AlertTrigger ORM model representing custom financial trigger rules."""

    __tablename__ = "alert_triggers"

    trigger_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    rule_name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )
    financial_criteria: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    threshold_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    urgency: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # Values: Low, Medium, High
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
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

    # Relationships
    notification_logs: Mapped[List["NotificationLog"]] = relationship(
        "NotificationLog",
        back_populates="trigger",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<AlertTrigger(trigger_id={self.trigger_id}, rule_name={self.rule_name!r}, "
            f"financial_criteria={self.financial_criteria!r}, threshold_amount={self.threshold_amount}, "
            f"urgency={self.urgency!r}, is_active={self.is_active})>"
        )

    def to_dict(self) -> dict:
        """Convert AlertTrigger instance into a dictionary representation."""
        return {
            "trigger_id": str(self.trigger_id) if self.trigger_id else None,
            "rule_name": self.rule_name,
            "financial_criteria": self.financial_criteria,
            "threshold_amount": (
                float(self.threshold_amount)
                if self.threshold_amount is not None
                else 0.0
            ),
            "urgency": self.urgency,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class NotificationPreference(Base):
    """NotificationPreference ORM model representing role-based notification channel mappings."""

    __tablename__ = "notification_preferences"

    preference_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    role_name: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    event_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )
    urgency: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # Values: Low, Medium, High
    channels_json: Mapped[list] = mapped_column(
        JSON,
        nullable=False,
    )  # e.g. ["InApp", "Email", "SMS"]
    version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
    )  # Used for optimistic locking
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

    def __repr__(self) -> str:
        return (
            f"<NotificationPreference(preference_id={self.preference_id}, role_name={self.role_name!r}, "
            f"event_type={self.event_type!r}, urgency={self.urgency!r}, version={self.version})>"
        )

    def to_dict(self) -> dict:
        """Convert NotificationPreference instance into a dictionary representation."""
        return {
            "preference_id": (
                str(self.preference_id) if self.preference_id else None
            ),
            "role_name": self.role_name,
            "event_type": self.event_type,
            "urgency": self.urgency,
            "channels_json": self.channels_json,
            "version": self.version,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class NotificationLog(Base):
    """NotificationLog ORM model representing structured audit logs of notification dispatches."""

    __tablename__ = "notification_logs"

    notification_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    recipient_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    trigger_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("alert_triggers.trigger_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    urgency: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # Values: Low, Medium, High
    channel: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # Values: InApp, Email, SMS
    delivery_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )  # Values: Sent, Failed, Fallback
    dispatched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    correlation_id: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        index=True,
    )

    # Relationships
    trigger: Mapped[Optional[AlertTrigger]] = relationship(
        "AlertTrigger",
        back_populates="notification_logs",
    )

    def __repr__(self) -> str:
        return (
            f"<NotificationLog(notification_id={self.notification_id}, recipient_id={self.recipient_id}, "
            f"urgency={self.urgency!r}, channel={self.channel!r}, delivery_status={self.delivery_status!r}, "
            f"correlation_id={self.correlation_id!r})>"
        )

    def to_dict(self) -> dict:
        """Convert NotificationLog instance into a dictionary representation."""
        return {
            "notification_id": (
                str(self.notification_id) if self.notification_id else None
            ),
            "recipient_id": str(self.recipient_id) if self.recipient_id else None,
            "trigger_id": str(self.trigger_id) if self.trigger_id else None,
            "urgency": self.urgency,
            "channel": self.channel,
            "delivery_status": self.delivery_status,
            "dispatched_at": (
                self.dispatched_at.isoformat() if self.dispatched_at else None
            ),
            "correlation_id": self.correlation_id,
        }


class AlertSuppression(Base):
    """AlertSuppression ORM model for tracking deduplication hashes during active suppression windows."""

    __tablename__ = "alert_suppressions"

    suppression_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    dedup_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    last_triggered_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )

    def __repr__(self) -> str:
        return (
            f"<AlertSuppression(suppression_id={self.suppression_id}, dedup_hash={self.dedup_hash!r}, "
            f"last_triggered_at={self.last_triggered_at}, expires_at={self.expires_at})>"
        )

    def to_dict(self) -> dict:
        """Convert AlertSuppression instance into a dictionary representation."""
        return {
            "suppression_id": (
                str(self.suppression_id) if self.suppression_id else None
            ),
            "dedup_hash": self.dedup_hash,
            "last_triggered_at": (
                self.last_triggered_at.isoformat()
                if self.last_triggered_at
                else None
            ),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
        }
