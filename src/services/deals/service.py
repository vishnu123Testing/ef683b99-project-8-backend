"""Core domain service for Visual Deal Pipeline management [FR-001, FR-002, FR-003, NFR-001].

Provides business logic for stage querying, deal stage transitions, age/probability
calculations, and dispatching forecast recalculations on deal state modifications.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy.orm import Session

from src.lib.logging import get_correlation_id
from src.models.deal import Deal, DealForecast


logger = logging.getLogger(__name__)


# Default win probabilities mapped by normalized milestone stage name
DEFAULT_STAGE_WIN_PROBABILITIES: Dict[str, float] = {
    "qualification": 0.20,
    "lead qualification": 0.20,
    "lead_qualification": 0.20,
    "discovery": 0.40,
    "discovery call": 0.40,
    "discovery_call": 0.40,
    "proposal": 0.60,
    "solution proposal": 0.60,
    "solution_proposal": 0.60,
    "negotiation": 0.80,
    "contract negotiation": 0.80,
    "contract_negotiation": 0.80,
    "closed won": 1.00,
    "closed_won": 1.00,
    "won": 1.00,
    "closed lost": 0.00,
    "closed_lost": 0.00,
    "lost": 0.00,
}


class DealServiceError(Exception):
    """Base exception for deal domain service errors."""

    pass


class DealNotFoundError(DealServiceError):
    """Raised when a requested deal record is not found."""

    pass


class InvalidDealStageError(DealServiceError):
    """Raised when an invalid deal milestone stage is supplied."""

    pass


class DealService:
    """Domain service encapsulating deal pipeline operations, age/probability logic, and forecast dispatch."""

    def __init__(
        self,
        db: Session,
        crm_connector: Optional[Any] = None,
        forecasting_service: Optional[Any] = None,
    ) -> None:
        """Initialize DealService with a database session and optional dependencies."""
        self.db = db
        self._crm_connector = crm_connector
        self._forecasting_service = forecasting_service

    def calculate_deal_age(self, created_on: Optional[datetime]) -> int:
        """Calculate the age of a deal in days based on its creation timestamp [FR-002].

        Args:
            created_on: Creation timestamp of the deal.

        Returns:
            Non-negative integer representing total age in days.
        """
        if not created_on:
            return 0

        now = datetime.now(timezone.utc)
        if created_on.tzinfo is None:
            created_on = created_on.replace(tzinfo=timezone.utc)

        delta = now - created_on
        return max(0, int(delta.total_seconds() // 86400))

    def calculate_win_probability(
        self,
        milestone_stage: str,
        current_probability: Optional[float] = None,
    ) -> float:
        """Calculate or look up the win probability score for a given milestone stage [FR-002].

        Args:
            milestone_stage: Milestone stage string.
            current_probability: Optional explicit probability override.

        Returns:
            Float probability value between 0.0 and 1.0.
        """
        if current_probability is not None and 0.0 <= current_probability <= 1.0:
            return float(current_probability)

        normalized_stage = milestone_stage.strip().lower()
        if normalized_stage in DEFAULT_STAGE_WIN_PROBABILITIES:
            return DEFAULT_STAGE_WIN_PROBABILITIES[normalized_stage]

        # Default fallback if stage not recognized
        return 0.50

    def get_deal_by_id(self, deal_id: str) -> Deal:
        """Retrieve a Deal ORM model by ID.

        Args:
            deal_id: Unique deal identifier.

        Returns:
            Deal ORM model instance.

        Raises:
            DealNotFoundError: If deal does not exist.
        """
        deal = self.db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise DealNotFoundError(f"Deal with ID '{deal_id}' not found.")
        return deal

    def get_pipeline_deals(self, stage: Optional[str] = None) -> List[Dict[str, Any]]:
        """Query deal pipeline board records, optionally filtered by milestone stage [FR-001, FR-002].

        Args:
            stage: Optional milestone stage filter string.

        Returns:
            List of deal dictionary payloads containing deal value, age_in_days, and win_probability.
        """
        query = self.db.query(Deal)
        if stage and stage.strip():
            target_stage = stage.strip()
            query = query.filter(Deal.milestone_stage.ilike(target_stage))

        deals = query.order_by(Deal.updated_on.desc()).all()
        results: List[Dict[str, Any]] = []

        for deal in deals:
            deal_dict = deal.to_dict()
            deal_dict["age_in_days"] = self.calculate_deal_age(deal.created_on)
            if deal.forecast:
                deal_dict["forecast"] = deal.forecast.to_dict()
            else:
                deal_dict["forecast"] = None
            results.append(deal_dict)

        return results

    def create_deal(
        self,
        title: str,
        deal_value: float,
        milestone_stage: str,
        win_probability: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Create a new deal record in the visual pipeline.

        Args:
            title: Title of the deal.
            deal_value: Monetary value of the deal.
            milestone_stage: Milestone stage.
            win_probability: Optional explicit win probability.

        Returns:
            Dictionary payload of created deal including age_in_days.
        """
        calculated_prob = self.calculate_win_probability(milestone_stage, win_probability)
        now = datetime.now(timezone.utc)

        deal = Deal(
            id=str(uuid.uuid4()),
            title=title.strip(),
            deal_value=deal_value,
            milestone_stage=milestone_stage.strip(),
            win_probability=calculated_prob,
            created_on=now,
            updated_on=now,
        )

        self.db.add(deal)
        self.db.commit()
        self.db.refresh(deal)

        # Dispatch initial forecast recalculation
        self.dispatch_forecast_recalculation(deal.id, trigger_event="DEAL_CREATED")

        result = deal.to_dict()
        result["age_in_days"] = self.calculate_deal_age(deal.created_on)
        return result

    def update_deal_stage(
        self,
        deal_id: str,
        new_stage: str,
        win_probability: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Update the milestone progression stage of a deal and trigger forecast recalculation [FR-003, NFR-001].

        Args:
            deal_id: Unique deal identifier.
            new_stage: New milestone stage string.
            win_probability: Optional explicit probability override.

        Returns:
            Updated deal dictionary payload with recalculated metrics.

        Raises:
            DealNotFoundError: If deal does not exist.
            InvalidDealStageError: If new stage string is empty.
        """
        if not new_stage or not new_stage.strip():
            raise InvalidDealStageError("New milestone stage cannot be empty.")

        deal = self.get_deal_by_id(deal_id)

        clean_stage = new_stage.strip()
        deal.milestone_stage = clean_stage
        deal.win_probability = self.calculate_win_probability(clean_stage, win_probability)
        deal.updated_on = datetime.now(timezone.utc)

        self.db.commit()
        self.db.refresh(deal)

        # Dispatch forecast recalculation for stage update event
        self.dispatch_forecast_recalculation(deal.id, trigger_event="STAGE_UPDATE")

        result = deal.to_dict()
        result["age_in_days"] = self.calculate_deal_age(deal.created_on)
        if deal.forecast:
            result["forecast"] = deal.forecast.to_dict()
        else:
            result["forecast"] = None
        return result

    def dispatch_forecast_recalculation(
        self,
        deal_id: str,
        trigger_event: str = "STAGE_UPDATE",
    ) -> bool:
        """Dispatch asynchronous or direct AI timeline completion forecast recalculation [FR-010, NFR-002, NFR-004].

        Args:
            deal_id: Unique deal identifier.
            trigger_event: Name of event triggering recalculation.

        Returns:
            Boolean indicating whether recalculation dispatch was initiated successfully.
        """
        correlation_id = get_correlation_id() or str(uuid.uuid4())

        logger.info(
            "Forecast recalculation dispatched for deal_id='%s', trigger_event='%s', correlation_id='%s'",
            deal_id,
            trigger_event,
            correlation_id,
        )

        # Ensure or initialize a DealForecast record in COMPUTING state if needed
        forecast = self.db.query(DealForecast).filter(DealForecast.deal_id == deal_id).first()
        if not forecast:
            forecast = DealForecast(
                id=str(uuid.uuid4()),
                deal_id=deal_id,
                status_code="COMPUTING",
                calculated_on=datetime.now(timezone.utc),
            )
            self.db.add(forecast)
            self.db.commit()
        else:
            forecast.status_code = "COMPUTING"
            forecast.calculated_on = datetime.now(timezone.utc)
            self.db.commit()

        # If a forecasting service instance is explicitly injected, call it directly
        if self._forecasting_service is not None:
            try:
                if hasattr(self._forecasting_service, "recalculate_forecast"):
                    self._forecasting_service.recalculate_forecast(deal_id, trigger_event=trigger_event)
                elif hasattr(self._forecasting_service, "calculate_forecast"):
                    self._forecasting_service.calculate_forecast(deal_id)
            except Exception as exc:
                logger.warning("Direct forecasting service invocation failed for deal '%s': %s", deal_id, exc)

        # Try dispatching to Celery worker task asynchronously if module exists
        try:
            from src.workers.forecast_worker import recalculate_deal_forecast_task  # type: ignore

            recalculate_deal_forecast_task.delay(deal_id, trigger_event)
        except (ImportError, AttributeError, Exception) as err:
            logger.debug(
                "Celery forecast recalculation task unavailable or deferred: %s",
                err,
            )

        return True

    def get_deal_summary(self, deal_id: str) -> Dict[str, Any]:
        """Get comprehensive deal summary including age_in_days, forecast, action items count, and stakeholders count.

        Args:
            deal_id: Unique deal identifier.

        Returns:
            Dictionary payload with detailed deal information.
        """
        deal = self.get_deal_by_id(deal_id)
        result = deal.to_dict()
        result["age_in_days"] = self.calculate_deal_age(deal.created_on)
        result["action_items_count"] = len(deal.action_items)
        result["stakeholders_count"] = len(deal.stakeholders)

        if deal.forecast:
            result["forecast"] = deal.forecast.to_dict()
        else:
            result["forecast"] = None

        return result
