"""Unit tests for dashboard Pydantic request query and response schemas (FR-10, NFR-1)."""

import pytest
from fastapi import HTTPException

from src.api.dashboard import (
    BreakdownItemSchema,
    DashboardSummaryQueryParams,
    DashboardSummaryResponse,
    DomainDetailQueryParams,
    DomainDetailResponse,
    DomainEnum,
    MetricSchema,
    TimeframeEnum,
    validate_domain_id,
    validate_timeframe,
)
from src.services.dashboard.domain_ports import (
    AggregationStatus,
    DomainStatus,
    MetricStatus,
    MetricUnit,
)


def test_timeframe_enum_values():
    """Verify supported TimeframeEnum values ('7d', '30d', '90d')."""
    assert TimeframeEnum.SEVEN_DAYS == "7d"
    assert TimeframeEnum.THIRTY_DAYS == "30d"
    assert TimeframeEnum.NINETY_DAYS == "90d"


def test_domain_enum_values():
    """Verify supported DomainEnum values ('lead_gen', 'sales_pipeline', 'support', 'financials')."""
    assert DomainEnum.LEAD_GEN == "lead_gen"
    assert DomainEnum.SALES_PIPELINE == "sales_pipeline"
    assert DomainEnum.SUPPORT == "support"
    assert DomainEnum.FINANCIALS == "financials"


def test_validate_timeframe_valid():
    """Verify validate_timeframe returns correct TimeframeEnum for valid inputs."""
    assert validate_timeframe("7d") == TimeframeEnum.SEVEN_DAYS
    assert validate_timeframe("30d") == TimeframeEnum.THIRTY_DAYS
    assert validate_timeframe("90d") == TimeframeEnum.NINETY_DAYS


def test_validate_timeframe_invalid_raises_400():
    """Verify validate_timeframe raises HTTP 400 Bad Request for invalid timeframe (NFR-1)."""
    with pytest.raises(HTTPException) as exc_info:
        validate_timeframe("14d")
    assert exc_info.value.status_code == 400
    assert "Invalid timeframe parameter" in exc_info.value.detail


def test_validate_domain_id_valid():
    """Verify validate_domain_id returns correct DomainEnum for valid inputs."""
    assert validate_domain_id("lead_gen") == DomainEnum.LEAD_GEN
    assert validate_domain_id("sales_pipeline") == DomainEnum.SALES_PIPELINE
    assert validate_domain_id("support") == DomainEnum.SUPPORT
    assert validate_domain_id("financials") == DomainEnum.FINANCIALS


def test_validate_domain_id_invalid_raises_400():
    """Verify validate_domain_id raises HTTP 400 Bad Request for unsupported domain_id (NFR-1)."""
    with pytest.raises(HTTPException) as exc_info:
        validate_domain_id("marketing")
    assert exc_info.value.status_code == 400
    assert "Unsupported domain_id" in exc_info.value.detail


def test_dashboard_summary_query_params_valid():
    """Verify DashboardSummaryQueryParams Pydantic schema validation."""
    params = DashboardSummaryQueryParams(timeframe="90d")
    assert params.timeframe == TimeframeEnum.NINETY_DAYS

    default_params = DashboardSummaryQueryParams()
    assert default_params.timeframe == TimeframeEnum.THIRTY_DAYS


def test_dashboard_summary_query_params_invalid():
    """Verify DashboardSummaryQueryParams raises ValueError on invalid timeframe."""
    with pytest.raises(ValueError):
        DashboardSummaryQueryParams(timeframe="invalid_tf")


def test_domain_detail_query_params_valid():
    """Verify DomainDetailQueryParams Pydantic schema validation."""
    params = DomainDetailQueryParams(domain_id="support", timeframe="7d")
    assert params.domain_id == DomainEnum.SUPPORT
    assert params.timeframe == TimeframeEnum.SEVEN_DAYS


def test_domain_detail_query_params_invalid():
    """Verify DomainDetailQueryParams raises ValueError on invalid domain_id."""
    with pytest.raises(ValueError):
        DomainDetailQueryParams(domain_id="unknown_domain", timeframe="30d")


def test_metric_schema_serialization():
    """Verify MetricSchema construction, defaults, and dictionary export (FR-10)."""
    metric = MetricSchema(
        domain="financials",
        metric_key="recurring_revenue",
        display_name="Recurring Revenue",
        raw_value=12500.0,
        baseline_value=10000.0,
        formatted_value="$12,500",
        percentage_change=25.0,
        status=MetricStatus.VALID,
        unit=MetricUnit.CURRENCY,
    )

    data = metric.model_dump()
    assert data["domain"] == "financials"
    assert data["metric_key"] == "recurring_revenue"
    assert data["raw_value"] == 12500.0
    assert data["formatted_value"] == "$12,500"
    assert data["status"] == MetricStatus.VALID
    assert data["unit"] == MetricUnit.CURRENCY


def test_dashboard_summary_response():
    """Verify DashboardSummaryResponse structure and JSON dump (FR-10)."""
    metric = MetricSchema(
        domain="support",
        metric_key="open_tickets",
        display_name="Open Tickets",
        raw_value=0.0,
        baseline_value=5.0,
        formatted_value="0",
        percentage_change=-100.0,
        status=MetricStatus.ZERO,
        unit=MetricUnit.COUNT,
    )

    response = DashboardSummaryResponse(
        status=AggregationStatus.COMPLETE,
        generated_at="2026-10-06T08:00:00Z",
        metrics=[metric],
    )

    dump = response.model_dump()
    assert dump["status"] == AggregationStatus.COMPLETE
    assert dump["generated_at"] == "2026-10-06T08:00:00Z"
    assert len(dump["metrics"]) == 1
    assert dump["metrics"][0]["status"] == MetricStatus.ZERO


def test_domain_detail_response():
    """Verify DomainDetailResponse structure, detailed_metrics, and breakdown items (FR-10)."""
    breakdown = BreakdownItemSchema(label="Ticket Severity: P1", value=2.0)
    metric = MetricSchema(
        domain="support",
        metric_key="open_tickets",
        display_name="Open Tickets",
        raw_value=2.0,
        baseline_value=3.0,
        formatted_value="2",
        percentage_change=-33.3,
        status=MetricStatus.VALID,
        unit=MetricUnit.COUNT,
    )

    response = DomainDetailResponse(
        domain="support",
        status=DomainStatus.COMPLETE,
        detailed_metrics=[metric],
        breakdown_items=[breakdown],
    )

    assert response.metrics == response.detailed_metrics
    dump = response.model_dump()
    assert dump["domain"] == "support"
    assert dump["status"] == DomainStatus.COMPLETE
    assert len(dump["detailed_metrics"]) == 1
    assert len(dump["breakdown_items"]) == 1
    assert dump["breakdown_items"][0]["label"] == "Ticket Severity: P1"
