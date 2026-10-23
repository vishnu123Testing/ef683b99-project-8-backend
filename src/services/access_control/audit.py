"""Non-blocking structured audit logging service.

Emits formatted JSON audit events with correlation metadata directly to stdout
and optionally persists events into the database via AuditLog model.
All operations are fail-safe and guaranteed not to raise exceptions to callers.
"""

from datetime import datetime, timezone
import json
import logging
import sys
from typing import Any, TextIO
import uuid

from sqlalchemy.orm import Session

from src.lib.logging import get_correlation_id, mask_sensitive_data
from src.models.audit import AuditLog

logger = logging.getLogger(__name__)


def _to_uuid(val: uuid.UUID | str | None) -> uuid.UUID | None:
    """Safely convert a value to a UUID object or None."""
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError):
        return None


def _format_datetime_utc(dt: datetime | None) -> tuple[datetime, str]:
    """Ensure datetime is timezone-aware UTC and format as ISO 8601 string ending with 'Z'."""
    if dt is None:
        dt = datetime.now(timezone.utc)
    elif dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)

    iso_str = dt.isoformat()
    if iso_str.endswith("+00:00"):
        iso_str = iso_str[:-6] + "Z"

    return dt, iso_str


class AuditService:
    """Service providing non-blocking, fail-safe structured audit logging."""

    def __init__(self, writer: TextIO | None = None) -> None:
        """Initialize AuditService with an optional output writer stream (defaults to sys.stdout)."""
        self.writer = writer

    def log_event(
        self,
        action: str,
        outcome_status: str,
        user_id: uuid.UUID | str | None = None,
        assigned_role: str | None = None,
        tenant_id: uuid.UUID | str | None = None,
        resource_id: str | None = None,
        client_ip: str | None = None,
        correlation_id: uuid.UUID | str | None = None,
        logged_at: datetime | None = None,
        db: Session | None = None,
        extra: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Emit a structured JSON audit event to stdout and optionally record to DB.

        Guaranteed fail-safe: traps all internal exceptions and never raises to caller.
        """
        try:
            event_uuid = uuid.uuid4()
            logged_at_dt, logged_at_str = _format_datetime_utc(logged_at)

            parsed_user_id = _to_uuid(user_id)
            parsed_tenant_id = _to_uuid(tenant_id)
            parsed_corr_id = _to_uuid(correlation_id)

            # Fallback to context correlation_id if not explicitly supplied
            if parsed_corr_id is None:
                ctx_cid = get_correlation_id()
                if ctx_cid:
                    parsed_corr_id = _to_uuid(ctx_cid)

            # Construct audit record dict matching required schema
            event_data: dict[str, Any] = {
                "event_id": str(event_uuid),
                "logged_at": logged_at_str,
                "user_id": str(parsed_user_id) if parsed_user_id else None,
                "assigned_role": assigned_role,
                "tenant_id": str(parsed_tenant_id) if parsed_tenant_id else None,
                "action": action,
                "resource_id": resource_id,
                "client_ip": client_ip,
                "outcome_status": outcome_status,
                "correlation_id": str(parsed_corr_id) if parsed_corr_id else None,
            }

            if extra:
                event_data["extra"] = mask_sensitive_data(extra)

            # Mask any credentials/tokens across the entire payload
            masked_event = mask_sensitive_data(event_data)

            # Write formatted JSON directly to output stream (non-blocking stdout)
            json_line = json.dumps(masked_event)
            out = self.writer if self.writer is not None else sys.stdout
            out.write(json_line + "\n")
            out.flush()

            # Optionally persist to database if Session provided
            if db is not None:
                try:
                    audit_entry = AuditLog(
                        audit_id=event_uuid,
                        logged_at=logged_at_dt,
                        user_id=parsed_user_id,
                        assigned_role=assigned_role,
                        tenant_id=parsed_tenant_id,
                        action=action,
                        resource_id=resource_id,
                        client_ip=client_ip,
                        outcome_status=outcome_status,
                        correlation_id=parsed_corr_id,
                    )
                    db.add(audit_entry)
                    db.commit()
                except Exception as db_exc:
                    try:
                        db.rollback()
                    except Exception:
                        pass
                    logger.error("Failed to persist audit log to database: %s", db_exc)

            return masked_event

        except Exception as exc:
            # Traps any failure (e.g. stdout I/O error, serialization issue) to prevent API requests from failing
            logger.error("Audit logging operation failed: %s", exc)
            return None

    @staticmethod
    def get_audit_logs(
        db: Session,
        tenant_id: uuid.UUID | str,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[AuditLog], int]:
        """Retrieve tenant-scoped audit log entries for administrative querying."""
        parsed_tenant_id = _to_uuid(tenant_id)
        if parsed_tenant_id is None:
            return [], 0

        query = db.query(AuditLog).filter(AuditLog.tenant_id == parsed_tenant_id)
        total_count = query.count()

        items = (
            query.order_by(AuditLog.logged_at.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )

        return items, total_count


# Module-level convenience functions
_default_audit_service = AuditService()


def log_audit_event(
    action: str,
    outcome_status: str,
    user_id: uuid.UUID | str | None = None,
    assigned_role: str | None = None,
    tenant_id: uuid.UUID | str | None = None,
    resource_id: str | None = None,
    client_ip: str | None = None,
    correlation_id: uuid.UUID | str | None = None,
    logged_at: datetime | None = None,
    db: Session | None = None,
    extra: dict[str, Any] | None = None,
    writer: TextIO | None = None,
) -> dict[str, Any] | None:
    """Convenience function to log a structured audit event."""
    service = _default_audit_service if writer is None else AuditService(writer=writer)
    return service.log_event(
        action=action,
        outcome_status=outcome_status,
        user_id=user_id,
        assigned_role=assigned_role,
        tenant_id=tenant_id,
        resource_id=resource_id,
        client_ip=client_ip,
        correlation_id=correlation_id,
        logged_at=logged_at,
        db=db,
        extra=extra,
    )
