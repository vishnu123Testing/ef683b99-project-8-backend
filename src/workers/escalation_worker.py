"""Celery worker and scheduled task for SLA breach scanning, alert suppression, and JSON logging [FR-005, NFR-004].

Scans open action items for SLA threshold breaches using ActionItemService, updates escalation status,
suppresses duplicate alerts for already escalated items, emits structured JSON logs with correlation IDs,
and executes periodic scans on Celery worker queues.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional
import uuid

from celery import Celery
from sqlalchemy.orm import Session

from src.lib.config import settings
from src.lib.logging import get_correlation_id, set_correlation_id
from src.models.database import SessionLocal
from src.services.action_items.service import ActionItemService

logger = logging.getLogger(__name__)

# Initialize Celery worker application
celery_app = Celery(
    "escalation_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_default_queue=settings.escalation_worker_queue,
)


def process_sla_breach_scans(
    db: Optional[Session] = None,
    current_time: Optional[datetime] = None,
    notification_port: Optional[Any] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute SLA breach scan and alert processing across open action items [FR-005, NFR-004].

    Scans open action items against target due dates and SLA thresholds.
    Flags breached items as escalated, suppresses duplicate notifications for already escalated tasks,
    and logs structured JSON events containing correlation IDs.

    Args:
        db: Optional SQLAlchemy Session instance. If None, SessionLocal() is initialized and closed.
        current_time: Optional evaluation timestamp (defaults to current UTC time).
        notification_port: Optional notification port interface for dispatching alerts.
        correlation_id: Optional correlation ID string. Generated if not present in context.

    Returns:
        Summary dictionary containing scan execution statistics and list of escalated items.
    """
    # Establish or verify correlation ID
    cid = correlation_id or get_correlation_id() or str(uuid.uuid4())
    token = set_correlation_id(cid)

    eval_time = current_time or datetime.now(timezone.utc)
    if eval_time.tzinfo is None:
        eval_time = eval_time.replace(tzinfo=timezone.utc)
    else:
        eval_time = eval_time.astimezone(timezone.utc)

    close_session = False
    if db is None:
        db = SessionLocal()
        close_session = True

    try:
        service = ActionItemService(db=db, notification_port=notification_port)
        scan_result = service.scan_and_escalate_sla_breaches(current_time=eval_time)

        summary = {
            "status": "success",
            "scanned_count": scan_result.get("scanned_count", 0),
            "escalated_count": scan_result.get("escalated_count", 0),
            "suppressed_count": scan_result.get("suppressed_count", 0),
            "escalated_items": scan_result.get("escalated_items", []),
            "evaluation_time": eval_time.isoformat(),
            "correlation_id": cid,
        }

        logger.info(
            f"SLA breach scan completed successfully for timestamp {eval_time.isoformat()}: "
            f"scanned={summary['scanned_count']}, escalated={summary['escalated_count']}, "
            f"suppressed={summary['suppressed_count']}.",
            extra={
                "correlation_id": cid,
                "event_type": "sla_breach_scan_completed",
                "scanned_count": summary["scanned_count"],
                "escalated_count": summary["escalated_count"],
                "suppressed_count": summary["suppressed_count"],
                "evaluation_time": summary["evaluation_time"],
            },
        )

        return summary

    except Exception as exc:
        logger.error(
            f"Failed to execute SLA breach scan: {exc}",
            extra={
                "correlation_id": cid,
                "event_type": "sla_breach_scan_failed",
                "error": str(exc),
            },
        )
        raise

    finally:
        if close_session:
            db.close()


@celery_app.task(name="scan_sla_breaches", bind=True)
def run_scheduled_sla_scan(
    self,
    current_time_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Scheduled Celery task executing periodic SLA breach scans and escalation processing [FR-005, NFR-004].

    Args:
        current_time_iso: Optional ISO format timestamp string overriding evaluation time.

    Returns:
        Summary dictionary containing scan outcomes and escalation counts.
    """
    eval_time = datetime.fromisoformat(current_time_iso) if current_time_iso else None
    return process_sla_breach_scans(current_time=eval_time)
