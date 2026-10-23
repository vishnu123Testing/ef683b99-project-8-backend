"""Dashboard API request query and response schemas with strict boundary validation (FR-10, NFR-1)."""

from enum import Enum
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from src.connectors.financials import FinancialsConnector
from src.connectors.lead_gen import LeadGenConnector
from src.connectors.sales_pipeline import SalesPipelineConnector
from src.connectors.support_tickets import SupportTicketsConnector
from src.models.database import get_db
from src.services.dashboard.aggregator import (
    aggregate_domain_connectors,
    enrich_metric_with_baseline,
)
from src.services.dashboard.domain_ports import (
    AggregationStatus,
    DomainMetricsResult,
    DomainStatus,
    MetricStatus,
    MetricUnit,
)

router = APIRouter(prefix="/dashboard", tags=["Dashboard"])


class TimeframeEnum(str, Enum):
    """Allowed comparative period duration values for metric aggregation."""

    SEVEN_DAYS = "7d"
    THIRTY_DAYS = "30d"
    NINETY_DAYS = "90d"


class DomainEnum(str, Enum):
    """Supported operational domain identifiers."""

    LEAD_GEN = "lead_gen"
    SALES_PIPELINE = "sales_pipeline"
    SUPPORT = "support"
    FINANCIALS = "financials"


class DashboardSummaryQueryParams(BaseModel):
    """Pydantic schema validating request query parameters for executive dashboard summary."""

    timeframe: TimeframeEnum = Field(
        default=TimeframeEnum.THIRTY_DAYS,
        description="Comparative timeframe duration ('7d', '30d', '90d')",
    )

    @field_validator("timeframe", mode="before")
    @classmethod
    def validate_timeframe_string(cls, v: object) -> object:
        """Ensure timeframe is one of allowed values ('7d', '30d', '90d')."""
        if isinstance(v, str) and v not in {t.value for t in TimeframeEnum}:
            raise ValueError(
                f"Invalid timeframe parameter '{v}'. Allowed values: '7d', '30d', '90d'"
            )
        return v


class DomainDetailQueryParams(BaseModel):
    """Pydantic schema validating request parameters for domain drilldown quick-view."""

    domain_id: DomainEnum = Field(
        ...,
        description="Target domain identifier ('lead_gen', 'sales_pipeline', 'support', 'financials')",
    )
    timeframe: TimeframeEnum = Field(
        default=TimeframeEnum.THIRTY_DAYS,
        description="Comparative timeframe duration ('7d', '30d', '90d')",
    )

    @field_validator("domain_id", mode="before")
    @classmethod
    def validate_domain_string(cls, v: object) -> object:
        """Ensure domain_id matches a supported operational domain."""
        if isinstance(v, str) and v not in {d.value for d in DomainEnum}:
            raise ValueError(
                f"Unsupported domain_id '{v}'. Allowed values: 'lead_gen', 'sales_pipeline', 'support', 'financials'"
            )
        return v

    @field_validator("timeframe", mode="before")
    @classmethod
    def validate_timeframe_string(cls, v: object) -> object:
        """Ensure timeframe matches allowed timeframe options."""
        if isinstance(v, str) and v not in {t.value for t in TimeframeEnum}:
            raise ValueError(
                f"Invalid timeframe parameter '{v}'. Allowed values: '7d', '30d', '90d'"
            )
        return v


def validate_timeframe(timeframe: str = "30d") -> TimeframeEnum:
    """Sanitize and validate timeframe query parameter.

    Args:
        timeframe: Comparative duration parameter string (default '30d').

    Returns:
        TimeframeEnum: Validated timeframe enum instance.

    Raises:
        HTTPException: 400 Bad Request if timeframe is not in ('7d', '30d', '90d') (NFR-1).
    """
    try:
        return TimeframeEnum(timeframe)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid timeframe parameter '{timeframe}'. Allowed values: '7d', '30d', '90d'",
        )


def validate_domain_id(domain_id: str) -> DomainEnum:
    """Sanitize and validate domain_id path parameter.

    Args:
        domain_id: Domain identifier path parameter string.

    Returns:
        DomainEnum: Validated domain enum instance.

    Raises:
        HTTPException: 400 Bad Request if domain_id is unsupported (NFR-1).
    """
    try:
        return DomainEnum(domain_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Unsupported domain_id '{domain_id}'. "
                "Allowed values: 'lead_gen', 'sales_pipeline', 'support', 'financials'"
            ),
        )


class MetricSchema(BaseModel):
    """Pydantic schema representing a standardized operational KPI metric."""

    domain: str = Field(
        ...,
        description="Domain identifier (e.g., 'lead_gen', 'sales_pipeline', 'support', 'financials')",
    )
    metric_key: str = Field(
        ...,
        description="Unique metric identifier key (e.g., 'recurring_revenue', 'open_tickets')",
    )
    display_name: str = Field(
        ...,
        description="Human-readable title for UI card display",
    )
    raw_value: Optional[float] = Field(
        default=None,
        description="Raw current numerical value; null if unavailable",
    )
    baseline_value: Optional[float] = Field(
        default=None,
        description="Historical baseline numerical value for comparison",
    )
    formatted_value: Optional[str] = Field(
        default=None,
        description="Formatted string for UI display (e.g., '$12,500', '0', '$0', 'Unavailable')",
    )
    percentage_change: Optional[float] = Field(
        default=None,
        description="Calculated percentage change relative to baseline",
    )
    status: MetricStatus = Field(
        default=MetricStatus.UNAVAILABLE,
        description="Metric operational status code ('VALID', 'ZERO', 'UNAVAILABLE')",
    )
    unit: MetricUnit = Field(
        default=MetricUnit.COUNT,
        description="Measurement unit type ('currency', 'count', 'percentage')",
    )
    error_detail: Optional[str] = Field(
        default=None,
        description="Sanitized error message if metric acquisition failed",
    )


class BreakdownItemSchema(BaseModel):
    """Pydantic schema representing a domain distribution breakdown item."""

    label: str = Field(
        ...,
        description="Distribution segment label (e.g., 'Ticket Severity: P1')",
    )
    value: float = Field(
        ...,
        description="Segment numerical value",
    )


class DashboardSummaryResponse(BaseModel):
    """Response payload schema for consolidated executive dashboard summary."""

    status: AggregationStatus = Field(
        ...,
        description="Overall aggregation operational status ('COMPLETE', 'PARTIAL', 'FAILED')",
    )
    generated_at: str = Field(
        ...,
        description="ISO 8601 UTC timestamp of summary generation",
    )
    metrics: List[MetricSchema] = Field(
        default_factory=list,
        description="List of KPI metrics across all operational domains",
    )


class DomainDetailResponse(BaseModel):
    """Response payload schema for domain-specific quick-view panel drilldown."""

    domain: str = Field(
        ...,
        description="Domain identifier",
    )
    status: DomainStatus = Field(
        ...,
        description="Domain operational status ('COMPLETE', 'UNAVAILABLE')",
    )
    detailed_metrics: List[MetricSchema] = Field(
        default_factory=list,
        description="Array of KPI metrics matching summary structure",
    )
    breakdown_items: List[BreakdownItemSchema] = Field(
        default_factory=list,
        description="Array of distribution breakdown items",
    )
    error_detail: Optional[str] = Field(
        default=None,
        description="Captured exception or failure message if domain unavailable",
    )

    @property
    def metrics(self) -> List[MetricSchema]:
        """Convenience property mapping detailed_metrics to metrics."""
        return self.detailed_metrics


@router.get(
    "/summary",
    response_model=DashboardSummaryResponse,
    summary="Get consolidated executive dashboard summary metrics",
    description=(
        "Aggregates operational metrics across Lead Gen, Sales, Support, and Financials domains "
        "with fault isolation and baseline change indicators (FR-1, FR-2, FR-3, FR-4)."
    ),
)
def get_dashboard_summary(
    timeframe: str = "30d",
    db: Session = Depends(get_db),
) -> DashboardSummaryResponse:
    """Retrieve aggregated executive KPI summary metrics across all operational domains.

    Args:
        timeframe: Comparative duration parameter ('7d', '30d', '90d', default '30d').
        db: Synchronous SQLAlchemy database session.

    Returns:
        DashboardSummaryResponse: Consolidated summary object with overall status and metric list.
    """
    validated_tf = validate_timeframe(timeframe)
    connectors = [
        LeadGenConnector(),
        SalesPipelineConnector(),
        SupportTicketsConnector(),
        FinancialsConnector(),
    ]
    summary_result = aggregate_domain_connectors(
        connectors=connectors,
        timeframe=validated_tf.value,
        db=db,
    )
    metrics = [
        MetricSchema(
            domain=m.domain,
            metric_key=m.metric_key,
            display_name=m.display_name,
            raw_value=m.raw_value,
            baseline_value=m.baseline_value,
            formatted_value=m.formatted_value,
            percentage_change=m.percentage_change,
            status=m.status,
            unit=m.unit,
            error_detail=m.error_detail,
        )
        for m in summary_result.metrics
    ]
    return DashboardSummaryResponse(
        status=summary_result.status,
        generated_at=summary_result.generated_at,
        metrics=metrics,
    )


@router.get(
    "/domains/{domain_id}",
    response_model=DomainDetailResponse,
    summary="Get domain-specific quick-view panel drilldown metrics",
    description=(
        "Retrieves detailed KPI metrics and breakdown items for a specific operational domain "
        "(Lead Gen, Sales Pipeline, Support Ticketing, Financial Summaries) "
        "with fault isolation and timeframe filtering (FR-6, FR-7, FR-8, FR-9)."
    ),
)
def get_domain_detail(
    domain_id: str,
    timeframe: str = "30d",
    db: Session = Depends(get_db),
) -> DomainDetailResponse:
    """Retrieve detailed KPI metrics and breakdown items for a specific domain.

    Args:
        domain_id: Target domain identifier path parameter ('lead_gen', 'sales_pipeline', 'support', 'financials').
        timeframe: Comparative duration parameter ('7d', '30d', '90d', default '30d').
        db: Synchronous SQLAlchemy database session.

    Returns:
        DomainDetailResponse: Domain detail response containing status, metrics, and breakdown items.
    """
    validated_domain = validate_domain_id(domain_id)
    validated_tf = validate_timeframe(timeframe)

    connector_map = {
        DomainEnum.LEAD_GEN: LeadGenConnector,
        DomainEnum.SALES_PIPELINE: SalesPipelineConnector,
        DomainEnum.SUPPORT: SupportTicketsConnector,
        DomainEnum.FINANCIALS: FinancialsConnector,
    }

    connector_cls = connector_map[validated_domain]
    connector = connector_cls()

    try:
        domain_result = connector.fetch_metrics(timeframe=validated_tf.value)
    except Exception as exc:
        domain_result = DomainMetricsResult(
            domain=validated_domain.value,
            status=DomainStatus.UNAVAILABLE,
            metrics=[],
            breakdown_items=[],
            error_detail=f"Unhandled exception during fetch_metrics: {exc}",
        )

    detailed_metrics: List[MetricSchema] = []
    for m in domain_result.metrics:
        enriched = enrich_metric_with_baseline(metric=m, db=db)
        detailed_metrics.append(
            MetricSchema(
                domain=enriched.domain,
                metric_key=enriched.metric_key,
                display_name=enriched.display_name,
                raw_value=enriched.raw_value,
                baseline_value=enriched.baseline_value,
                formatted_value=enriched.formatted_value,
                percentage_change=enriched.percentage_change,
                status=enriched.status,
                unit=enriched.unit,
                error_detail=enriched.error_detail,
            )
        )

    breakdown_items = [
        BreakdownItemSchema(
            label=b.label,
            value=b.value,
        )
        for b in domain_result.breakdown_items
    ]

    return DomainDetailResponse(
        domain=domain_result.domain,
        status=domain_result.status,
        detailed_metrics=detailed_metrics,
        breakdown_items=breakdown_items,
        error_detail=domain_result.error_detail,
    )
