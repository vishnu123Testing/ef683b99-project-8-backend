"""Unit tests for dashboard domain connector ports and standardized metric DTOs."""

import pytest
from src.services.dashboard.domain_ports import (
    AggregatedSummaryResult,
    AggregationStatus,
    BreakdownItem,
    DomainConnectorPort,
    DomainMetricsResult,
    DomainStatus,
    MetricResult,
    MetricStatus,
    MetricUnit,
)


def test_metric_status_enums():
    """Verify enum string values for metric operational status codes."""
    assert MetricStatus.VALID.value == "VALID"
    assert MetricStatus.ZERO.value == "ZERO"
    assert MetricStatus.UNAVAILABLE.value == "UNAVAILABLE"


def test_domain_status_enums():
    """Verify enum string values for domain status codes."""
    assert DomainStatus.COMPLETE.value == "COMPLETE"
    assert DomainStatus.PARTIAL.value == "PARTIAL"
    assert DomainStatus.UNAVAILABLE.value == "UNAVAILABLE"


def test_aggregation_status_enums():
    """Verify enum string values for overall aggregation status codes."""
    assert AggregationStatus.COMPLETE.value == "COMPLETE"
    assert AggregationStatus.PARTIAL.value == "PARTIAL"
    assert AggregationStatus.FAILED.value == "FAILED"


def test_metric_unit_enums():
    """Verify enum string values for metric unit types."""
    assert MetricUnit.CURRENCY.value == "currency"
    assert MetricUnit.COUNT.value == "count"
    assert MetricUnit.PERCENTAGE.value == "percentage"


def test_metric_result_defaults_and_serialization():
    """Test MetricResult DTO instantiation with defaults and model_dump."""
    result = MetricResult(
        domain="financials",
        metric_key="recurring_revenue",
        display_name="Recurring Revenue",
    )
    assert result.domain == "financials"
    assert result.metric_key == "recurring_revenue"
    assert result.display_name == "Recurring Revenue"
    assert result.raw_value is None
    assert result.baseline_value is None
    assert result.formatted_value is None
    assert result.percentage_change is None
    assert result.status == MetricStatus.UNAVAILABLE
    assert result.unit == MetricUnit.COUNT
    assert result.error_detail is None

    dump = result.model_dump()
    assert dump["domain"] == "financials"
    assert dump["status"] == "UNAVAILABLE"
    assert dump["unit"] == "count"


def test_metric_result_valid_values():
    """Test MetricResult DTO with valid numerical and formatted metric data."""
    result = MetricResult(
        domain="sales_pipeline",
        metric_key="deal_volume",
        display_name="Deal Volume",
        raw_value=1250000.0,
        baseline_value=1000000.0,
        formatted_value="$1,250,000",
        percentage_change=25.0,
        status=MetricStatus.VALID,
        unit=MetricUnit.CURRENCY,
    )
    assert result.raw_value == 1250000.0
    assert result.status == MetricStatus.VALID
    assert result.unit == MetricUnit.CURRENCY
    assert result.percentage_change == 25.0


def test_breakdown_item():
    """Test BreakdownItem DTO instantiation and validation."""
    item = BreakdownItem(label="Ticket Severity: P1", value=4.0)
    assert item.label == "Ticket Severity: P1"
    assert item.value == 4.0


def test_domain_metrics_result():
    """Test DomainMetricsResult container with nested MetricResult and BreakdownItem objects."""
    metric = MetricResult(
        domain="support",
        metric_key="open_tickets",
        display_name="Open Tickets",
        raw_value=0.0,
        formatted_value="0",
        status=MetricStatus.ZERO,
        unit=MetricUnit.COUNT,
    )
    breakdown = BreakdownItem(label="Category: Billing", value=12.0)

    domain_res = DomainMetricsResult(
        domain="support",
        status=DomainStatus.COMPLETE,
        metrics=[metric],
        breakdown_items=[breakdown],
    )

    assert domain_res.domain == "support"
    assert domain_res.status == DomainStatus.COMPLETE
    assert len(domain_res.metrics) == 1
    assert domain_res.metrics[0].status == MetricStatus.ZERO
    assert len(domain_res.breakdown_items) == 1
    assert domain_res.breakdown_items[0].label == "Category: Billing"


def test_domain_connector_port_abstract_instantiation():
    """Verify that DomainConnectorPort cannot be instantiated directly without implementing abstract methods."""

    class IncompleteConnector(DomainConnectorPort):
        pass

    with pytest.raises(TypeError):
        IncompleteConnector()


def test_domain_connector_port_concrete_implementation():
    """Verify concrete subclass implementation of DomainConnectorPort interface."""

    class MockLeadGenConnector(DomainConnectorPort):
        @property
        def domain_id(self) -> str:
            return "lead_gen"

        def fetch_metrics(self, timeframe: str = "30d") -> DomainMetricsResult:
            return DomainMetricsResult(
                domain=self.domain_id,
                status=DomainStatus.COMPLETE,
                metrics=[],
            )

    connector = MockLeadGenConnector()
    assert connector.domain_id == "lead_gen"
    res = connector.fetch_metrics("7d")
    assert res.domain == "lead_gen"
    assert res.status == DomainStatus.COMPLETE


def test_aggregated_summary_result():
    """Test AggregatedSummaryResult DTO instantiation and serialization."""
    metric = MetricResult(
        domain="lead_gen",
        metric_key="lead_volume",
        display_name="Lead Volume",
        raw_value=500.0,
        formatted_value="500",
        status=MetricStatus.VALID,
        unit=MetricUnit.COUNT,
    )
    summary = AggregatedSummaryResult(
        status=AggregationStatus.COMPLETE,
        generated_at="2026-10-06T08:00:00Z",
        metrics=[metric],
    )
    assert summary.status == AggregationStatus.COMPLETE
    assert summary.generated_at == "2026-10-06T08:00:00Z"
    assert len(summary.metrics) == 1
    assert summary.metrics[0].domain == "lead_gen"
