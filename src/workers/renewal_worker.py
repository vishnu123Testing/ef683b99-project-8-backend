"""Celery worker and scheduled task for contract renewal alert evaluation [FR-9].

Calculates UTC expiration thresholds at 90, 60, and 30 days prior to contract expiration
and dispatches renewal alerts using the NotificationPort interface.
Suppresses alert generation for terminated, expired, or renewed contracts.
"""

from datetime import date as date_type, datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from celery import Celery
from sqlalchemy.orm import Session

from src.connectors.notification_connector import NotificationConnector
from src.lib.config import settings
from src.models.contract import Contract
from src.models.database import SessionLocal
from src.services.contract.models import ContractStatus, RenewalThreshold
from src.services.contract.notification_port import (
    AlertSuppressedError,
    InvalidAlertThresholdError,
    NotificationDispatchError,
    NotificationPort,
    RenewalAlertPayload,
)

logger = logging.getLogger(__name__)

# Initialize Celery worker application
celery_app = Celery(
    "renewal_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
)


def process_renewal_alerts(
    db: Optional[Session] = None,
    reference_date: Optional[date_type] = None,
    notification_port: Optional[NotificationPort] = None,
    thresholds: Optional[List[int]] = None,
) -> Dict[str, Any]:
    """Evaluate contract expiration dates against UTC thresholds and dispatch renewal alerts [FR-9].

    Calculates remaining days to expiration relative to reference UTC date for active agreements.
    Dispatches alerts for 90, 60, and 30 day thresholds unless suppressed by contract status
    (e.g., TERMINATED, EXPIRED, RENEWED) or duplicate dispatch.

    Args:
        db: Optional SQLAlchemy Session instance. If None, SessionLocal() is created and closed.
        reference_date: Optional reference date (defaults to current UTC date).
        notification_port: Optional NotificationPort instance (defaults to NotificationConnector).
        thresholds: List of threshold day intervals to evaluate (defaults to settings.renewal_alert_thresholds_days).

    Returns:
        Summary dictionary with counts of processed contracts, dispatched alerts,
        suppressed alerts, and errors.
    """
    now_utc = datetime.now(timezone.utc)
    current_utc_date = reference_date if reference_date is not None else now_utc.date()

    eval_thresholds = (
        sorted(thresholds, reverse=True)
        if thresholds is not None
        else sorted(settings.renewal_alert_thresholds_days, reverse=True)
    )

    close_session = False
    if db is None:
        db = SessionLocal()
        close_session = True

    try:
        if notification_port is None:
            notification_port = NotificationConnector(db=db)

        # Query all contracts with non-null expires_on
        contracts = db.query(Contract).filter(Contract.expires_on.isnot(None)).all()

        dispatched_count = 0
        suppressed_count = 0
        error_count = 0
        dispatched_details: List[Dict[str, Any]] = []

        for contract in contracts:
            if not contract.expires_on:
                continue

            days_remaining = (contract.expires_on - current_utc_date).days

            for threshold in eval_thresholds:
                if days_remaining <= threshold:
                    payload = RenewalAlertPayload(
                        contract_id=contract.id,
                        threshold_days=threshold,
                        contract_title=contract.title,
                        counterparty=contract.counterparty,
                        expires_on=contract.expires_on,
                        contract_status=contract.status,
                    )

                    if notification_port.should_suppress_alert(
                        contract.status, contract.id, threshold
                    ):
                        suppressed_count += 1
                        continue

                    try:
                        result = notification_port.dispatch_renewal_alert(payload)
                        dispatched_count += 1
                        dispatched_details.append(result.to_dict())
                        logger.info(
                            f"Renewal alert dispatched for contract {contract.id} ({contract.title}) "
                            f"at {threshold}-day threshold. Days remaining: {days_remaining}."
                        )
                    except AlertSuppressedError as exc:
                        suppressed_count += 1
                        logger.debug(f"Alert suppressed for contract {contract.id}: {exc}")
                    except (InvalidAlertThresholdError, NotificationDispatchError) as exc:
                        error_count += 1
                        logger.error(f"Failed dispatching renewal alert for contract {contract.id}: {exc}")
                    except Exception as exc:
                        error_count += 1
                        logger.error(f"Unexpected error dispatching renewal alert for contract {contract.id}: {exc}")

        if close_session and hasattr(db, "commit"):
            db.commit()

        summary = {
            "processed_contracts": len(contracts),
            "dispatched_alerts": dispatched_count,
            "suppressed_alerts": suppressed_count,
            "errors": error_count,
            "reference_date": current_utc_date.isoformat(),
            "thresholds_evaluated": eval_thresholds,
            "dispatched_details": dispatched_details,
        }

        logger.info(
            f"Renewal worker evaluation complete for reference date {current_utc_date.isoformat()}: "
            f"processed={len(contracts)}, dispatched={dispatched_count}, "
            f"suppressed={suppressed_count}, errors={error_count}."
        )

        return summary

    finally:
        if close_session:
            db.close()


@celery_app.task(name="check_contract_renewals", bind=True)
def run_scheduled_renewal_check(
    self,
    reference_date_str: Optional[str] = None,
) -> Dict[str, Any]:
    """Scheduled Celery task executing contract renewal alert evaluation [FR-9].

    Args:
        reference_date_str: Optional ISO format string (YYYY-MM-DD) overriding current UTC date.

    Returns:
        Summary dictionary with execution results.
    """
    ref_date = date_type.fromisoformat(reference_date_str) if reference_date_str else None
    return process_renewal_alerts(reference_date=ref_date)
