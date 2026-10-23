"""Celery worker and tasks for asynchronous AI forecast recalculations [FR-010, NFR-002, NFR-004].

Executes background AI completion timeline and confidence score recalculation tasks when deal stage,
value, or stakeholder activity changes, emits structured JSON logs with correlation IDs, and updates
forecast records via ForecastingService.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from celery import Celery
from sqlalchemy.orm import Session

from src.lib.config import settings
from src.lib.logging import get_correlation_id, set_correlation_id
from src.models.database import SessionLocal
from src.services.forecasting.service import ForecastDealNotFoundError, ForecastingService

logger = logging.getLogger(__name__)

# Initialize Celery worker application
celery_app = Celery(
    "forecast_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_default_queue=settings.forecast_recalculation_queue,
)


def process_forecast_recalculation(
    deal_id: str,
    trigger_event: str = "DEAL_UPDATE",
    db: Optional[Session] = None,
    crm_provider: Optional[Any] = None,
    forecast_provider: Optional[Any] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute AI forecast recalculation for a deal and log structured events [FR-010, NFR-002, NFR-004].

    Args:
        deal_id: Unique identifier for target deal.
        trigger_event: Descriptive event trigger (e.g., STAGE_UPDATE, VALUE_UPDATE, ACTIVITY_UPDATE).
        db: Optional SQLAlchemy Session. If None, SessionLocal() is initialized and closed.
        crm_provider: Optional explicit CRM connector port override.
        forecast_provider: Optional explicit forecast engine port override.
        correlation_id: Optional correlation ID string. Generated if not present in context.

    Returns:
        Summary dictionary containing recalculation outcome and updated forecast details.
    """
    cid = correlation_id or get_correlation_id() or str(uuid.uuid4())
    token = set_correlation_id(cid)

    close_session = False
    if db is None:
        db = SessionLocal()
        close_session = True

    try:
        service = ForecastingService(
            db=db,
            forecast_provider=forecast_provider,
            crm_provider=crm_provider,
        )

        forecast_dict = service.recalculate_forecast(
            deal_id=deal_id,
            trigger_event=trigger_event,
            crm_provider=crm_provider,
            forecast_provider=forecast_provider,
        )

        logger.info(
            f"AI forecast recalculation completed for deal '{deal_id}' (trigger: {trigger_event}): "
            f"status={forecast_dict.get('status_code')}, completion_date={forecast_dict.get('estimated_completion_date')}, "
            f"confidence={forecast_dict.get('confidence_score_pct')}%.",
            extra={
                "correlation_id": cid,
                "event_type": "forecast_recalculation_completed",
                "deal_id": deal_id,
                "trigger_event": trigger_event,
                "status_code": forecast_dict.get("status_code"),
                "confidence_score_pct": forecast_dict.get("confidence_score_pct"),
                "estimated_completion_date": forecast_dict.get("estimated_completion_date"),
            },
        )

        return {
            "status": "success",
            "deal_id": deal_id,
            "trigger_event": trigger_event,
            "forecast": forecast_dict,
            "correlation_id": cid,
        }

    except ForecastDealNotFoundError as exc:
        logger.warning(
            f"AI forecast recalculation skipped for deal '{deal_id}': deal not found in database.",
            extra={
                "correlation_id": cid,
                "event_type": "forecast_recalculation_deal_not_found",
                "deal_id": deal_id,
                "trigger_event": trigger_event,
                "error": str(exc),
            },
        )
        return {
            "status": "error",
            "deal_id": deal_id,
            "trigger_event": trigger_event,
            "error": str(exc),
            "correlation_id": cid,
        }

    except Exception as exc:
        logger.error(
            f"Failed to execute AI forecast recalculation for deal '{deal_id}': {exc}",
            extra={
                "correlation_id": cid,
                "event_type": "forecast_recalculation_failed",
                "deal_id": deal_id,
                "trigger_event": trigger_event,
                "error": str(exc),
            },
        )
        raise

    finally:
        if close_session:
            db.close()


def process_batch_forecast_recalculations(
    deal_ids: List[str],
    trigger_event: str = "BATCH_DEAL_UPDATE",
    db: Optional[Session] = None,
    crm_provider: Optional[Any] = None,
    forecast_provider: Optional[Any] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute AI forecast recalculations for a list of deals and log structured events [FR-010, NFR-002, NFR-004].

    Args:
        deal_ids: List of deal unique identifiers.
        trigger_event: Descriptive event trigger.
        db: Optional SQLAlchemy Session. If None, SessionLocal() is initialized and closed.
        crm_provider: Optional explicit CRM connector port override.
        forecast_provider: Optional explicit forecast engine port override.
        correlation_id: Optional correlation ID string. Generated if not present in context.

    Returns:
        Summary dictionary containing batch execution outcomes and forecast payloads.
    """
    cid = correlation_id or get_correlation_id() or str(uuid.uuid4())
    token = set_correlation_id(cid)

    close_session = False
    if db is None:
        db = SessionLocal()
        close_session = True

    try:
        service = ForecastingService(
            db=db,
            forecast_provider=forecast_provider,
            crm_provider=crm_provider,
        )

        results = service.recalculate_batch_forecasts(
            deal_ids=deal_ids,
            trigger_event=trigger_event,
            crm_provider=crm_provider,
            forecast_provider=forecast_provider,
        )

        logger.info(
            f"Batch AI forecast recalculation completed for {len(deal_ids)} deals (trigger: {trigger_event}).",
            extra={
                "correlation_id": cid,
                "event_type": "batch_forecast_recalculation_completed",
                "deal_count": len(deal_ids),
                "trigger_event": trigger_event,
            },
        )

        return {
            "status": "success",
            "deal_ids": deal_ids,
            "processed_count": len(results),
            "trigger_event": trigger_event,
            "forecasts": results,
            "correlation_id": cid,
        }

    except Exception as exc:
        logger.error(
            f"Failed to execute batch AI forecast recalculations: {exc}",
            extra={
                "correlation_id": cid,
                "event_type": "batch_forecast_recalculation_failed",
                "deal_ids": deal_ids,
                "trigger_event": trigger_event,
                "error": str(exc),
            },
        )
        raise

    finally:
        if close_session:
            db.close()


@celery_app.task(name="recalculate_deal_forecast", bind=True)
def recalculate_deal_forecast_task(
    self,
    deal_id: str,
    trigger_event: str = "DEAL_UPDATE",
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Scheduled/Event Celery background task for asynchronous single deal forecast recalculations [FR-010, NFR-002, NFR-004].

    Args:
        deal_id: Target deal identifier.
        trigger_event: Trigger event description.
        correlation_id: Optional correlation ID passed from API caller.

    Returns:
        Summary dictionary containing calculation result.
    """
    return process_forecast_recalculation(
        deal_id=deal_id,
        trigger_event=trigger_event,
        correlation_id=correlation_id,
    )


@celery_app.task(name="recalculate_batch_forecasts", bind=True)
def recalculate_batch_forecasts_task(
    self,
    deal_ids: List[str],
    trigger_event: str = "BATCH_DEAL_UPDATE",
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Scheduled/Event Celery background task for asynchronous batch deal forecast recalculations [FR-010, NFR-002, NFR-004].

    Args:
        deal_ids: List of target deal identifiers.
        trigger_event: Trigger event description.
        correlation_id: Optional correlation ID passed from API caller.

    Returns:
        Summary dictionary containing batch calculation results.
    """
    return process_batch_forecast_recalculations(
        deal_ids=deal_ids,
        trigger_event=trigger_event,
        correlation_id=correlation_id,
    )
