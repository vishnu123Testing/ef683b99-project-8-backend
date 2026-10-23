"""Forecasting Service for AI completion timelines and confidence scores [FR-008, FR-009, FR-010, NFR-002, NFR-005].

Provides domain business logic for computing AI deal completion estimates, managing confidence scores,
handling fallback states when CRM data is sparse or unavailable, dispatching recalculations on deal state
modifications, and recording evaluation audit logs.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy.orm import Session

from src.lib.logging import get_correlation_id
from src.models.deal import Deal, DealForecast
from src.models.forecast import ForecastEvaluationLog
from src.services.deals.crm_port import CRMConnectorPort
from src.services.forecasting.forecast_port import (
    ForecastError,
    ForecastProviderPort,
    ForecastResult,
)

logger = logging.getLogger(__name__)


class ForecastingServiceError(Exception):
    """Base exception for forecasting service errors."""

    pass


class ForecastDealNotFoundError(ForecastingServiceError):
    """Raised when the specified deal for forecasting is not found in database."""

    pass


class ForecastComputationError(ForecastingServiceError):
    """Raised when AI forecast computation encounters an unrecoverable failure."""

    pass


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime object is timezone-aware and set to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class ForecastingService:
    """Domain service encapsulating AI completion timeline estimation and confidence scoring."""

    def __init__(
        self,
        db: Session,
        forecast_provider: Optional[ForecastProviderPort] = None,
        crm_provider: Optional[CRMConnectorPort] = None,
    ) -> None:
        """Initialize ForecastingService with a database session and optional explicit ports (NFR-5).

        Args:
            db: Synchronous SQLAlchemy database session.
            forecast_provider: Optional explicitly injected AI forecast engine port.
            crm_provider: Optional explicitly injected CRM data port.
        """
        self.db = db
        self._forecast_provider = forecast_provider
        self._crm_provider = crm_provider

    def _resolve_forecast_provider(
        self,
        forecast_provider_override: Optional[ForecastProviderPort] = None,
        crm_provider: Optional[CRMConnectorPort] = None,
    ) -> ForecastProviderPort:
        """Resolve effective forecast provider instance supporting explicit dependency injection (NFR-5)."""
        if forecast_provider_override is not None:
            return forecast_provider_override
        if self._forecast_provider is not None:
            return self._forecast_provider

        from src.connectors.forecast_connector import ForecastConnector

        effective_crm = self._resolve_crm_provider(crm_provider)
        return ForecastConnector(crm_provider=effective_crm)

    def _resolve_crm_provider(
        self,
        crm_provider_override: Optional[CRMConnectorPort] = None,
    ) -> Optional[CRMConnectorPort]:
        """Resolve effective CRM provider instance supporting explicit dependency injection (NFR-5)."""
        if crm_provider_override is not None:
            return crm_provider_override
        if self._crm_provider is not None:
            return self._crm_provider

        try:
            from src.connectors.crm_connector import CRMConnector

            return CRMConnector()
        except Exception:
            return None

    def get_forecast(self, deal_id: str) -> Dict[str, Any]:
        """Retrieve current AI completion forecast for a deal or compute initial baseline [FR-008, FR-009].

        Args:
            deal_id: Unique deal identifier.

        Returns:
            Dictionary payload representing the deal's forecast.

        Raises:
            ForecastDealNotFoundError: If deal does not exist in database.
        """
        deal = self.db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise ForecastDealNotFoundError(f"Deal '{deal_id}' not found.")

        forecast = (
            self.db.query(DealForecast)
            .filter(DealForecast.deal_id == deal_id)
            .first()
        )
        if forecast is None:
            return self.calculate_forecast(deal_id, trigger_event="INITIAL_FETCH")

        return forecast.to_dict()

    def calculate_forecast(
        self,
        deal_id: str,
        trigger_event: str = "CALCULATION",
        crm_provider: Optional[CRMConnectorPort] = None,
        forecast_provider: Optional[ForecastProviderPort] = None,
    ) -> Dict[str, Any]:
        """Compute AI completion timeline and confidence score for a deal with fallback handling [FR-008, FR-009, NFR-002, NFR-005].

        Args:
            deal_id: Unique deal identifier.
            trigger_event: Descriptive string for event triggering forecast calculation.
            crm_provider: Optional method-level explicit CRM data provider override.
            forecast_provider: Optional method-level explicit forecast provider override.

        Returns:
            Dictionary payload representing updated forecast record.

        Raises:
            ForecastDealNotFoundError: If deal does not exist in database.
        """
        deal = self.db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise ForecastDealNotFoundError(f"Deal '{deal_id}' not found.")

        effective_forecast_provider = self._resolve_forecast_provider(
            forecast_provider_override=forecast_provider,
            crm_provider=crm_provider,
        )
        effective_crm_provider = self._resolve_crm_provider(
            crm_provider_override=crm_provider,
        )

        correlation_id = get_correlation_id() or str(uuid.uuid4())

        result_obj: Optional[ForecastResult] = None
        error_msg: Optional[str] = None

        try:
            result_obj = effective_forecast_provider.calculate_forecast(
                deal_id=deal_id,
                crm_provider=effective_crm_provider,
            )
        except ForecastError as err:
            logger.warning(
                "AI forecast provider exception for deal '%s': %s",
                deal_id,
                err,
            )
            error_msg = str(err)
        except Exception as exc:
            logger.error(
                "Unexpected error computing AI forecast for deal '%s': %s",
                deal_id,
                exc,
            )
            error_msg = f"Unexpected calculation error: {str(exc)}"

        now_utc = datetime.now(timezone.utc)

        if result_obj is not None and result_obj.status_code == "READY":
            estimated_completion_date = _ensure_utc(
                result_obj.estimated_completion_date
            )
            confidence_score_pct = result_obj.confidence_score_pct
            status_code = "READY"
            model_metadata = result_obj.model_metadata
            error_detail = result_obj.error_detail
        else:
            # Fallback status handling when CRM data is sparse or provider encounters errors (FR-008, NFR-002)
            estimated_completion_date = None
            confidence_score_pct = None
            status_code = "INSUFFICIENT_DATA"
            model_metadata = result_obj.model_metadata if result_obj else None
            error_detail = (
                result_obj.error_detail
                if (result_obj and result_obj.error_detail)
                else (
                    error_msg
                    or "Insufficient CRM historical data or activities available for AI forecasting."
                )
            )

        # Upsert DealForecast entity in database
        forecast = (
            self.db.query(DealForecast)
            .filter(DealForecast.deal_id == deal_id)
            .first()
        )
        if forecast is None:
            forecast = DealForecast(
                id=str(uuid.uuid4()),
                deal_id=deal_id,
                estimated_completion_date=estimated_completion_date,
                confidence_score_pct=confidence_score_pct,
                status_code=status_code,
                calculated_on=now_utc,
            )
            self.db.add(forecast)
        else:
            forecast.estimated_completion_date = estimated_completion_date
            forecast.confidence_score_pct = confidence_score_pct
            forecast.status_code = status_code
            forecast.calculated_on = now_utc

        self.db.commit()
        self.db.refresh(forecast)

        # Record evaluation and recalculation log entry (FR-010, NFR-002)
        log_entry = ForecastEvaluationLog(
            id=str(uuid.uuid4()),
            deal_id=deal_id,
            forecast_id=forecast.id,
            estimated_completion_date=estimated_completion_date,
            confidence_score_pct=confidence_score_pct,
            status_code=status_code,
            trigger_event=trigger_event,
            correlation_id=correlation_id,
            model_metadata=model_metadata,
            error_detail=error_detail,
            calculated_on=now_utc,
        )
        self.db.add(log_entry)
        self.db.commit()

        return forecast.to_dict()

    def recalculate_forecast(
        self,
        deal_id: str,
        trigger_event: str = "RECALCULATION",
        crm_provider: Optional[CRMConnectorPort] = None,
        forecast_provider: Optional[ForecastProviderPort] = None,
    ) -> Dict[str, Any]:
        """Recalculate AI timeline completion forecast for a deal on stage, value, or activity changes [FR-010, NFR-002].

        Args:
            deal_id: Unique deal identifier.
            trigger_event: Event triggering recalculation (e.g. STAGE_UPDATE, VALUE_UPDATE, ACTIVITY_UPDATE).
            crm_provider: Optional explicit CRM data provider override.
            forecast_provider: Optional explicit forecast provider override.

        Returns:
            Dictionary payload representing updated forecast record.
        """
        return self.calculate_forecast(
            deal_id=deal_id,
            trigger_event=trigger_event,
            crm_provider=crm_provider,
            forecast_provider=forecast_provider,
        )

    def recalculate_batch_forecasts(
        self,
        deal_ids: List[str],
        trigger_event: str = "BATCH_RECALCULATION",
        crm_provider: Optional[CRMConnectorPort] = None,
        forecast_provider: Optional[ForecastProviderPort] = None,
    ) -> List[Dict[str, Any]]:
        """Recalculate AI completion forecasts for a batch of deal records [FR-010].

        Args:
            deal_ids: List of deal identifiers.
            trigger_event: Event triggering batch recalculation.
            crm_provider: Optional explicit CRM data provider override.
            forecast_provider: Optional explicit forecast provider override.

        Returns:
            List of forecast dictionary payloads.
        """
        results: List[Dict[str, Any]] = []
        for d_id in deal_ids:
            try:
                res = self.recalculate_forecast(
                    deal_id=d_id,
                    trigger_event=trigger_event,
                    crm_provider=crm_provider,
                    forecast_provider=forecast_provider,
                )
                results.append(res)
            except ForecastDealNotFoundError:
                now_utc = datetime.now(timezone.utc)
                results.append(
                    {
                        "id": None,
                        "deal_id": d_id,
                        "estimated_completion_date": None,
                        "confidence_score_pct": None,
                        "status_code": "INSUFFICIENT_DATA",
                        "calculated_on": now_utc.isoformat(),
                        "error_detail": f"Deal '{d_id}' not found in database.",
                    }
                )
            except Exception as err:
                now_utc = datetime.now(timezone.utc)
                results.append(
                    {
                        "id": None,
                        "deal_id": d_id,
                        "estimated_completion_date": None,
                        "confidence_score_pct": None,
                        "status_code": "INSUFFICIENT_DATA",
                        "calculated_on": now_utc.isoformat(),
                        "error_detail": f"Batch calculation error: {str(err)}",
                    }
                )
        return results

    def get_evaluation_logs(
        self,
        deal_id: str,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Retrieve historical AI forecast evaluation and recalculation log records for a deal [FR-010, NFR-002].

        Args:
            deal_id: Unique deal identifier.
            limit: Maximum number of log records to return.

        Returns:
            List of forecast evaluation log dictionaries.

        Raises:
            ForecastDealNotFoundError: If deal does not exist in database.
        """
        deal = self.db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise ForecastDealNotFoundError(f"Deal '{deal_id}' not found.")

        logs = (
            self.db.query(ForecastEvaluationLog)
            .filter(ForecastEvaluationLog.deal_id == deal_id)
            .order_by(ForecastEvaluationLog.calculated_on.desc())
            .limit(limit)
            .all()
        )
        return [log.to_dict() for log in logs]
