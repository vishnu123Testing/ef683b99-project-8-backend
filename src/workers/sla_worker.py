"""Celery periodic background task for SLA countdown evaluation and escalation execution [FR-4, FR-5, FR-6, NFR-1, NFR-3, NFR-4].

Scans active support tickets for SLA countdown status, evaluates warning thresholds,
detects breach conditions, triggers support tier escalations, and logs structured
JSON events with correlation metadata.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, Optional
import uuid

from celery import Celery
from sqlalchemy.orm import Session

from src.connectors.notification import NotificationConnector
from src.lib.config import settings
from src.lib.logging import get_correlation_id, set_correlation_id
from src.models.database import SessionLocal
from src.services.support.sla_service import evaluate_all_active_slas

logger = logging.getLogger(__name__)

# Initialize Celery worker application
celery_app = Celery(
    "sla_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_default_queue=settings.sla_worker_queue,
)


def process_sla_evaluations(
    db: Optional[Session] = None,
    current_time: Optional[datetime] = None,
    notification_port: Optional[Any] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute periodic SLA countdown evaluation and escalation execution across active tickets [FR-4, FR-5, FR-6, NFR-1, NFR-3].

    Args:
        db: Optional SQLAlchemy Session. If None, SessionLocal() is initialized and closed.
        current_time: Optional evaluation timestamp (defaults to current UTC time).
        notification_port: Optional notification connector override.
        correlation_id: Optional correlation ID string.

    Returns:
        Summary dictionary containing evaluation counts, warnings, breaches, and escalations.
    """
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
        if notification_port is None:
            notification_port = NotificationConnector()

        eval_result = evaluate_all_active_slas(
            db=db,
            notification_connector=notification_port,
            current_time=eval_time,
        )

        summary = {
            "status": "success",
            "evaluated_count": eval_result.get("evaluated_count", 0),
            "warnings_count": eval_result.get("warnings_count", 0),
            "breaches_count": eval_result.get("breaches_count", 0),
            "escalations_count": eval_result.get("escalations_count", 0),
            "evaluation_time": eval_time.isoformat(),
            "correlation_id": cid,
        }

        logger.info(
            f"Periodic SLA countdown and escalation evaluation completed for timestamp {eval_time.isoformat()}: "
            f"evaluated={summary['evaluated_count']}, warnings={summary['warnings_count']}, "
            f"breaches={summary['breaches_count']}, escalations={summary['escalations_count']}.",
            extra={
                "correlation_id": cid,
                "event_type": "sla_evaluation_completed",
                "evaluated_count": summary["evaluated_count"],
                "warnings_count": summary["warnings_count"],
                "breaches_count": summary["breaches_count"],
                "escalations_count": summary["escalations_count"],
                "evaluation_time": summary["evaluation_time"],
            },
        )

        return summary

    except Exception as exc:
        logger.error(
            f"Failed to execute SLA countdown evaluation scan: {exc}",
            extra={
                "correlation_id": cid,
                "event_type": "sla_evaluation_failed",
                "error": str(exc),
            },
        )
        raise

    finally:
        if close_session and db is not None:
            db.close()


# Alias for backward compatibility / alternative imports
process_sla_countdown_evaluations = process_sla_evaluations


@celery_app.task(name="evaluate_sla_countdowns", bind=True)
def run_scheduled_sla_evaluation(
    self,
    current_time_iso: Optional[str] = None,
) -> Dict[str, Any]:
    """Scheduled Celery task executing periodic SLA countdown evaluation and escalation execution [FR-4, FR-5, FR-6].

    Args:
        current_time_iso: Optional ISO format timestamp string overriding evaluation time.

    Returns:
        Summary dictionary containing evaluation outcomes and escalation counts.
    """
    eval_time = datetime.fromisoformat(current_time_iso) if current_time_iso else None
    return process_sla_evaluations(current_time=eval_time)


# Alias task function name for task runner convenience
evaluate_active_slas = run_scheduled_sla_evaluation
