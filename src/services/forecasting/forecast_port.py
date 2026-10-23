"""Abstract port interface and data models for AI completion forecasting providers.

Defines the explicit boundary for AI completion timeline and confidence score estimation engines,
decoupling forecasting services from specific AI providers or ambient external state (FR-8, FR-9, NFR-5).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, date as date_type
from typing import TYPE_CHECKING, Dict, List, Optional, Any

if TYPE_CHECKING:
    from src.services.deals.crm_port import CRMConnectorPort, CRMDealRecord


class ForecastError(Exception):
    """Base exception for AI completion forecasting engine errors."""

    pass


class InsufficientDataError(ForecastError):
    """Raised when CRM historical records or activities are insufficient for forecasting."""

    pass


class ForecastProviderError(ForecastError):
    """Raised when external AI forecasting provider encounters an internal error or connection failure."""

    pass


class ForecastTimeoutError(ForecastError):
    """Raised when an external AI forecasting API call exceeds execution timeout boundaries."""

    pass


class ForecastConnectionError(ForecastError):
    """Raised when communication with external AI forecasting provider endpoint fails."""

    pass


@dataclass
class ForecastResult:
    """Dataclass holding AI completion date estimate and confidence score output."""

    deal_id: str
    estimated_completion_date: Optional[datetime] = None
    confidence_score_pct: Optional[float] = None
    status_code: str = "READY"  # "READY", "INSUFFICIENT_DATA", "COMPUTING"
    calculated_on: Optional[datetime] = None
    model_metadata: Optional[str] = None
    error_detail: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert ForecastResult instance into a dictionary payload."""
        return {
            "deal_id": self.deal_id,
            "estimated_completion_date": (
                self.estimated_completion_date.isoformat()
                if self.estimated_completion_date
                else None
            ),
            "confidence_score_pct": self.confidence_score_pct,
            "status_code": self.status_code,
            "calculated_on": (
                self.calculated_on.isoformat()
                if self.calculated_on
                else None
            ),
            "model_metadata": self.model_metadata,
            "error_detail": self.error_detail,
        }


class ForecastProviderPort(ABC):
    """Abstract port defining contracts for AI deal completion timeline and confidence forecasting."""

    @abstractmethod
    def calculate_forecast(
        self,
        deal_id: str,
        crm_provider: Optional["CRMConnectorPort"] = None,
        deal_record: Optional["CRMDealRecord"] = None,
    ) -> ForecastResult:
        """Calculate estimated completion date and confidence score for a deal using injected CRM data."""
        pass

    @abstractmethod
    def evaluate_batch_forecasts(
        self,
        deal_ids: List[str],
        crm_provider: Optional["CRMConnectorPort"] = None,
    ) -> List[ForecastResult]:
        """Calculate estimated completion dates and confidence scores for a batch of deal records."""
        pass
