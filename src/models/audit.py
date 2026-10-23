"""AuditLog ORM model capturing security and entity events."""

from datetime import datetime, timezone
import uuid

from sqlalchemy import DateTime, Index, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from src.models.database import Base


class AuditLog(Base):
    """AuditLog ORM model for recording security, access, and entity lifecycle events."""

    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_tenant_id_logged_at", "tenant_id", "logged_at"),
    )

    audit_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    logged_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    assigned_role: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
    )
    tenant_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        nullable=True,
    )
    action: Mapped[str] = mapped_column(
        String(128),
        nullable=False,
    )
    resource_id: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    client_ip: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )
    outcome_status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
    )
    correlation_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        index=True,
        nullable=True,
    )

    def to_dict(self) -> dict:
        """Convert AuditLog model instance into a dictionary representation."""
        return {
            "event_id": str(self.audit_id) if self.audit_id else None,
            "logged_at": self.logged_at.isoformat() if self.logged_at else None,
            "user_id": str(self.user_id) if self.user_id else None,
            "assigned_role": self.assigned_role,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "action": self.action,
            "resource_id": self.resource_id,
            "client_ip": self.client_ip,
            "outcome_status": self.outcome_status,
            "correlation_id": str(self.correlation_id) if self.correlation_id else None,
        }

    def __repr__(self) -> str:
        return (
            f"<AuditLog(audit_id={self.audit_id}, "
            f"action={self.action!r}, "
            f"outcome_status={self.outcome_status!r}, "
            f"logged_at={self.logged_at})>"
        )
