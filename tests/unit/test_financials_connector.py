"""Unit tests for Financial Summaries domain connector adapter."""

from unittest.mock import MagicMock, patch
import httpx
import pytest

from src.connectors.financials import FinancialsConnector
from src.services.dashboard.domain_ports import (
    DomainStatus,
    MetricStatus,
    MetricUnit,
)


def test_financials_connector_domain_id():
    """Verify that financials connector reports correct domain identifier."""
    connector = FinancialsConnector()
    assert connector.domain_id == "financials"


def test_financials_connector_fetch_metrics_default_30d():
    """Verify successful metric acquisition for 30d timeframe."""
    connector = FinancialsConnector()
    result = connector.fetch_metrics("30d")

    assert result.domain == "financials"
    assert result.status == DomainStatus.COMPLETE
    assert result.error_detail is None
    assert len(result.metrics) == 3

    # Verify Recurring Revenue metric
    mrr_metric = next(m for m in result.metrics if m.metric_key == "recurring_revenue")
    assert mrr_metric.domain == "financials"
    assert mrr_metric.display_name == "Recurring Revenue"
    assert mrr_metric.raw_value == 12500.0
    assert mrr_metric.baseline_value == 10000.0
    assert mrr_metric.formatted_value == "$12,500"
    assert mrr_metric.status == MetricStatus.VALID
    assert mrr_metric.unit == MetricUnit.CURRENCY
    assert mrr_metric.percentage_change == 25.0

    # Verify Outstanding Invoices metric
    invoices_metric = next(m for m in result.metrics if m.metric_key == "outstanding_invoices")
    assert invoices_metric.domain == "financials"
    assert invoices_metric.display_name == "Outstanding Invoices"
    assert invoices_metric.raw_value == 4200.0
    assert invoices_metric.baseline_value == 5000.0
    assert invoices_metric.formatted_value == "$4,200"
    assert invoices_metric.status == MetricStatus.VALID
    assert invoices_metric.unit == MetricUnit.CURRENCY
    assert invoices_metric.percentage_change == -16.0

    # Verify Net Profit Margin metric
    margin_metric = next(m for m in result.metrics if m.metric_key == "net_profit_margin")
    assert margin_metric.domain == "financials"
    assert margin_metric.display_name == "Net Profit Margin"
    assert margin_metric.raw_value == 22.5
    assert margin_metric.baseline_value == 20.0
    assert margin_metric.formatted_value == "22.5%"
    assert margin_metric.status == MetricStatus.VALID
    assert margin_metric.unit == MetricUnit.PERCENTAGE
    assert margin_metric.percentage_change == 12.5

    # Verify Breakdown items
    assert len(result.breakdown_items) == 3
    sub_item = next(b for b in result.breakdown_items if b.label == "Category: Subscriptions")
    assert sub_item.value == 8500.0


def test_financials_connector_fetch_metrics_different_timeframes():
    """Verify metrics scaling for 7d and 90d timeframes."""
    connector = FinancialsConnector()

    res_7d = connector.fetch_metrics("7d")
    assert res_7d.status == DomainStatus.COMPLETE
    mrr_7d = next(m for m in res_7d.metrics if m.metric_key == "recurring_revenue")
    assert mrr_7d.raw_value == 3200.0
    assert mrr_7d.formatted_value == "$3,200"

    res_90d = connector.fetch_metrics("90d")
    assert res_90d.status == DomainStatus.COMPLETE
    mrr_90d = next(m for m in res_90d.metrics if m.metric_key == "recurring_revenue")
    assert mrr_90d.raw_value == 38000.0
    assert mrr_90d.formatted_value == "$38,000"


def test_financials_connector_invalid_timeframe_isolation():
    """Verify fault isolation when an invalid timeframe string is provided."""
    connector = FinancialsConnector()
    result = connector.fetch_metrics("invalid_timeframe")

    assert result.domain == "financials"
    assert result.status == DomainStatus.UNAVAILABLE
    assert result.error_detail is not None
    assert "Invalid timeframe" in result.error_detail

    for metric in result.metrics:
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.formatted_value == "Unavailable"
        assert metric.raw_value is None


def test_financials_connector_simulate_failure():
    """Verify fault isolation when connector encounters upstream runtime failure."""
    connector = FinancialsConnector(simulate_failure=True)
    result = connector.fetch_metrics("30d")

    assert result.domain == "financials"
    assert result.status == DomainStatus.UNAVAILABLE
    assert result.error_detail is not None
    assert "HTTP 503" in result.error_detail

    for metric in result.metrics:
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.formatted_value == "Unavailable"


def test_financials_connector_simulate_timeout():
    """Verify fault isolation and error reporting on timeout."""
    connector = FinancialsConnector(simulate_timeout=True, timeout=5.0)
    result = connector.fetch_metrics("30d")

    assert result.domain == "financials"
    assert result.status == DomainStatus.UNAVAILABLE
    assert result.error_detail is not None
    assert "timed out after 5.0s" in result.error_detail

    for metric in result.metrics:
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.formatted_value == "Unavailable"


def test_financials_connector_simulate_zero():
    """Verify correct ZERO status tagging for legitimate zero values."""
    connector = FinancialsConnector(simulate_zero=True)
    result = connector.fetch_metrics("30d")

    assert result.domain == "financials"
    assert result.status == DomainStatus.COMPLETE

    mrr_metric = next(m for m in result.metrics if m.metric_key == "recurring_revenue")
    assert mrr_metric.status == MetricStatus.ZERO
    assert mrr_metric.raw_value == 0.0
    assert mrr_metric.formatted_value == "$0"
    assert mrr_metric.percentage_change == -100.0

    margin_metric = next(m for m in result.metrics if m.metric_key == "net_profit_margin")
    assert margin_metric.status == MetricStatus.ZERO
    assert margin_metric.raw_value == 0.0
    assert margin_metric.formatted_value == "0.0%"


def test_financials_connector_external_api_success():
    """Verify external HTTP API response fetching and parsing."""
    mock_data = {
        "metrics": [
            {
                "metric_key": "recurring_revenue",
                "display_name": "Recurring Revenue",
                "raw_value": 15000.0,
                "baseline_value": 12000.0,
                "unit": "currency",
            }
        ],
        "breakdown_items": [
            {"label": "Category: Subscriptions", "value": 10000.0}
        ],
    }

    mock_response = MagicMock()
    mock_response.json.return_value = mock_data
    mock_response.raise_for_status.return_value = None

    with patch("httpx.Client.get", return_value=mock_response):
        connector = FinancialsConnector(api_url="https://api.financials.example.com", api_key="secret")
        result = connector.fetch_metrics("30d")

        assert result.domain == "financials"
        assert result.status == DomainStatus.COMPLETE
        assert len(result.metrics) == 1
        mrr = result.metrics[0]
        assert mrr.metric_key == "recurring_revenue"
        assert mrr.raw_value == 15000.0
        assert mrr.formatted_value == "$15,000"
        assert mrr.percentage_change == 25.0


def test_financials_connector_external_api_timeout_retry():
    """Verify retry behavior and timeout handling when external API fails repeatedly."""
    with patch("httpx.Client.get", side_effect=httpx.TimeoutException("Connection timed out")):
        connector = FinancialsConnector(
            api_url="https://api.financials.example.com",
            retries=1,
            timeout=2.0,
        )
        result = connector.fetch_metrics("30d")

        assert result.domain == "financials"
        assert result.status == DomainStatus.UNAVAILABLE
        assert "timed out after 2.0s" in result.error_detail
