"""Abstract domain connector port contracts and standardized metric DTOs.

Enforces strict typing and explicit interface boundaries to decouple data fetchers
from domain aggregation logic and visual display components (NFR-3).
"""

from abc import ABC, abstractmethod
from enum import Enum
from typing import List, Optional
from pydantic import BaseModel, Field


class MetricStatus(str, Enum):
    """Operational status code for individual metrics."""

    VALID = "VALID"
    ZERO = "ZERO"
    UNAVAILABLE = "UNAVAILABLE"


class DomainStatus(str, Enum):
    """Operational status code for domain data fetch results."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    UNAVAILABLE = "UNAVAILABLE"


class AggregationStatus(str, Enum):
    """Overall operational status code for multi-domain metric aggregation."""

    COMPLETE = "COMPLETE"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"


class MetricUnit(str, Enum):
    """Standardized metric measurement unit types."""

    CURRENCY = "currency"
    COUNT = "count"
    PERCENTAGE = "percentage"


class MetricResult(BaseModel):
    """Standardized DTO representing an individual operational metric data item."""

    domain: str = Field(
        ...,
        description="Domain identifier (e.g., 'lead_gen', 'sales_pipeline', 'support', 'financials')",
    )
    metric_key: str = Field(
        ...,
        description="Unique key for the metric (e.g., 'recurring_revenue', 'open_tickets')",
    )
    display_name: str = Field(
        ...,
        description="Human-readable title for UI card display",
    )
    raw_value: Optional[float] = Field(
        default=None,
        description="Raw numerical current value; None if unavailable",
    )
    baseline_value: Optional[float] = Field(
        default=None,
        description="Historical baseline numerical value for comparison",
    )
    formatted_value: Optional[str] = Field(
        default=None,
        description="Formatted string for UI display (e.g., '$12,500', '0', 'Unavailable')",
    )
    percentage_change: Optional[float] = Field(
        default=None,
        description="Calculated percentage change relative to baseline",
    )
    status: MetricStatus = Field(
        default=MetricStatus.UNAVAILABLE,
        description="Metric operational status code",
    )
    unit: MetricUnit = Field(
        default=MetricUnit.COUNT,
        description="Measurement unit type",
    )
    error_detail: Optional[str] = Field(
        default=None,
        description="Error detail if metric acquisition failed",
    )


class BreakdownItem(BaseModel):
    """Standardized DTO representing a distribution breakdown entry within a domain."""

    label: str = Field(
        ...,
        description="Breakdown segment label (e.g., 'Ticket Severity: P1')",
    )
    value: float = Field(
        ...,
        description="Segment numerical value",
    )


class DomainMetricsResult(BaseModel):
    """Standardized DTO container returned by domain connector ports containing operational metrics."""

    domain: str = Field(
        ...,
        description="Domain identifier",
    )
    status: DomainStatus = Field(
        default=DomainStatus.UNAVAILABLE,
        description="Domain collection operational status",
    )
    metrics: List[MetricResult] = Field(
        default_factory=list,
        description="List of metric results for the domain",
    )
    breakdown_items: List[BreakdownItem] = Field(
        default_factory=list,
        description="Optional distribution breakdown items",
    )
    error_detail: Optional[str] = Field(
        default=None,
        description="Captured exception or failure message if unavailable",
    )


class AggregatedSummaryResult(BaseModel):
    """Standardized DTO representing multi-domain aggregated dashboard summary."""

    status: AggregationStatus = Field(
        default=AggregationStatus.FAILED,
        description="Overall aggregation operational status (COMPLETE, PARTIAL, FAILED)",
    )
    generated_at: str = Field(
        ...,
        description="ISO 8601 UTC timestamp of summary generation",
    )
    metrics: List[MetricResult] = Field(
        default_factory=list,
        description="Flattened list of metric results across all operational domains",
    )


class DomainConnectorPort(ABC):
    """Abstract base class defining the contract for domain connector adapters.

    All downstream domain connector adapters (lead gen, sales pipeline, support tickets, financials)
    must implement this interface to decouple external API communication from aggregation logic.
    """

    @property
    @abstractmethod
    def domain_id(self) -> str:
        """Return the unique domain identifier string for this connector port (e.g., 'lead_gen')."""
        pass

    @abstractmethod
    def fetch_metrics(self, timeframe: str = "30d") -> DomainMetricsResult:
        """Retrieve operational metrics and distribution items for the specified timeframe.

        Must execute within configured timeout boundaries and return a DomainMetricsResult
        with appropriate status flags (COMPLETE or UNAVAILABLE) and error handling.
        """
        pass
