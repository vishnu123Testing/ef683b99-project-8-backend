"""Abstract port interface for retrieving CRM deal stage history, activities, and deal records.

Defines the explicit boundary for CRM data providers to ensure forecasting and deal services
are decoupled from external state and provider SDKs (FR-8, NFR-5).
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Optional


class CRMConnectorError(Exception):
    """Base exception for CRM connector errors."""

    pass


class CRMNotFoundError(CRMConnectorError):
    """Raised when a requested deal or stakeholder is not found in the CRM system."""

    pass


class CRMTimeoutError(CRMConnectorError):
    """Raised when an external CRM API call exceeds the timeout limit."""

    pass


class CRMConnectionError(CRMConnectorError):
    """Raised when communication with external CRM endpoint fails."""

    pass


@dataclass
class CRMStageHistoryItem:
    """Historical record of a deal stage transition."""

    deal_id: str
    milestone_stage: str
    entered_at: datetime
    exited_at: Optional[datetime] = None
    duration_days: float = 0.0
    win_probability: float = 0.0
    deal_value: float = 0.0


@dataclass
class CRMActivityItem:
    """Record of an interaction associated with a deal or stakeholder."""

    activity_id: str
    deal_id: str
    activity_type: str  # "Email", "Meeting", "Call", "Note"
    summary: str
    occurred_on: datetime
    stakeholder_id: Optional[str] = None
    stakeholder_email: Optional[str] = None


@dataclass
class CRMDealRecord:
    """Comprehensive CRM deal entity including stage history and activities."""

    deal_id: str
    title: str
    deal_value: float
    milestone_stage: str
    win_probability: float
    created_on: datetime
    updated_on: datetime
    stage_history: List[CRMStageHistoryItem] = field(default_factory=list)
    activities: List[CRMActivityItem] = field(default_factory=list)


class CRMConnectorPort(ABC):
    """Abstract port defining contracts for external CRM data retrieval."""

    @abstractmethod
    def get_deal_stage_history(self, deal_id: str) -> List[CRMStageHistoryItem]:
        """Retrieve chronological stage transition history for a specific deal."""
        pass

    @abstractmethod
    def get_deal_activities(self, deal_id: str, limit: int = 50) -> List[CRMActivityItem]:
        """Retrieve recent interaction activity records for a specific deal."""
        pass

    @abstractmethod
    def get_deal_record(self, deal_id: str) -> Optional[CRMDealRecord]:
        """Retrieve full CRM deal record including history and activity feed."""
        pass

    @abstractmethod
    def get_historical_deals_dataset(self, min_records: int = 1) -> List[CRMDealRecord]:
        """Retrieve dataset of historical deals for AI completion model forecasting baselines."""
        pass
