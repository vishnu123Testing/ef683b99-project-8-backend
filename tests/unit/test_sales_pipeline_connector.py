"""Unit tests for Sales Pipeline domain connector adapter."""

from unittest.mock import MagicMock, patch
import httpx
import pytest

from src.connectors.sales_pipeline import SalesPipelineConnector
from src.services.dashboard.domain_ports import (
    DomainStatus,
    MetricStatus,
    MetricUnit,
)


def test_sales_pipeline_connector_domain_id():
    """Verify that sales pipeline connector reports correct domain identifier."""
    connector = SalesPipelineConnector()
    assert connector.domain_id == "sales_pipeline"


def test_sales_pipeline_connector_fetch_metrics_default_30d():
    """Verify successful metric acquisition for 30d timeframe."""
    connector = SalesPipelineConnector()
    result = connector.fetch_metrics("30d")

    assert result.domain == "sales_pipeline"
    assert result.status == DomainStatus.COMPLETE
    assert result.error_detail is None
    assert len(result.metrics) == 2

    # Verify Pipeline Value metric
    pipeline_metric = next(m for m in result.metrics if m.metric_key == "pipeline_value")
    assert pipeline_metric.domain == "sales_pipeline"
    assert pipeline_metric.display_name == "Pipeline Value"
    assert pipeline_metric.raw_value == 450000.0
    assert pipeline_metric.baseline_value == 380000.0
    assert pipeline_metric.formatted_value == "$450,000"
    assert pipeline_metric.status == MetricStatus.VALID
    assert pipeline_metric.unit == MetricUnit.CURRENCY
    assert pipeline_metric.percentage_change == 18.4

    # Verify Win Rate metric
    win_rate_metric = next(m for m in result.metrics if m.metric_key == "win_rate")
    assert win_rate_metric.domain == "sales_pipeline"
    assert win_rate_metric.display_name == "Win Rate"
    assert win_rate_metric.raw_value == 28.5
    assert win_rate_metric.baseline_value == 25.0
    assert win_rate_metric.formatted_value == "28.5%"
    assert win_rate_metric.status == MetricStatus.VALID
    assert win_rate_metric.unit == MetricUnit.PERCENTAGE
    assert win_rate_metric.percentage_change == 14.0

    # Verify Stage Distribution Breakdown items
    assert len(result.breakdown_items) == 5
    proposal_item = next(b for b in result.breakdown_items if b.label == "Stage: Proposal")
    assert proposal_item.value == 112500.0


def test_sales_pipeline_connector_fetch_metrics_different_timeframes():
    """Verify metrics scaling for 7d and 90d timeframes."""
    connector = SalesPipelineConnector()

    res_7d = connector.fetch_metrics("7d")
    assert res_7d.status == DomainStatus.COMPLETE
    pipe_7d = next(m for m in res_7d.metrics if m.metric_key == "pipeline_value")
    assert pipe_7d.raw_value == 110000.0
    assert pipe_7d.formatted_value == "$110,000"

    res_90d = connector.fetch_metrics("90d")
    assert res_90d.status == DomainStatus.COMPLETE
    pipe_90d = next(m for m in res_90d.metrics if m.metric_key == "pipeline_value")
    assert pipe_90d.raw_value == 1350000.0
    assert pipe_90d.formatted_value == "$1,350,000"


def test_sales_pipeline_connector_invalid_timeframe_isolation():
    """Verify fault isolation when an invalid timeframe string is provided."""
    connector = SalesPipelineConnector()
    result = connector.fetch_metrics("invalid_timeframe")

    assert result.domain == "sales_pipeline"
    assert result.status == DomainStatus.UNAVAILABLE
    assert result.error_detail is not None
    assert "Invalid timeframe" in result.error_detail

    for metric in result.metrics:
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.formatted_value == "Unavailable"
        assert metric.raw_value is None


def test_sales_pipeline_connector_simulate_failure():
    """Verify fault isolation when connector encounters upstream runtime failure."""
    connector = SalesPipelineConnector(simulate_failure=True)
    result = connector.fetch_metrics("30d")

    assert result.domain == "sales_pipeline"
    assert result.status == DomainStatus.UNAVAILABLE
    assert result.error_detail is not None
    assert "HTTP 503" in result.error_detail

    for metric in result.metrics:
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.formatted_value == "Unavailable"


def test_sales_pipeline_connector_simulate_timeout():
    """Verify fault isolation and error reporting on timeout."""
    connector = SalesPipelineConnector(simulate_timeout=True, timeout=5.0)
    result = connector.fetch_metrics("30d")

    assert result.domain == "sales_pipeline"
    assert result.status == DomainStatus.UNAVAILABLE
    assert result.error_detail is not None
    assert "timed out after 5.0s" in result.error_detail

    for metric in result.metrics:
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.formatted_value == "Unavailable"


def test_sales_pipeline_connector_simulate_zero():
    """Verify correct ZERO status tagging for legitimate zero values."""
    connector = SalesPipelineConnector(simulate_zero=True)
    result = connector.fetch_metrics("30d")

    assert result.domain == "sales_pipeline"
    assert result.status == DomainStatus.COMPLETE

    pipe_metric = next(m for m in result.metrics if m.metric_key == "pipeline_value")
    assert pipe_metric.status == MetricStatus.ZERO
    assert pipe_metric.raw_value == 0.0
    assert pipe_metric.formatted_value == "$0"
    assert pipe_metric.percentage_change == -100.0

    win_metric = next(m for m in result.metrics if m.metric_key == "win_rate")
    assert win_metric.status == MetricStatus.ZERO
    assert win_metric.raw_value == 0.0
    assert win_metric.formatted_value == "0.0%"
    assert win_metric.percentage_change == -100.0


@patch("httpx.Client")
def test_sales_pipeline_connector_external_api_success(mock_client_cls):
    """Verify fetching and parsing from external upstream HTTP API."""
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "metrics": [
            {
                "metric_key": "pipeline_value",
                "display_name": "Pipeline Value",
                "raw_value": 500000.0,
                "baseline_value": 400000.0,
                "unit": "currency",
            },
            {
                "metric_key": "win_rate",
                "display_name": "Win Rate",
                "raw_value": 30.0,
                "baseline_value": 25.0,
                "unit": "percentage",
            },
        ],
        "breakdown_items": [
            {"label": "Stage: Proposal", "value": 200000.0},
        ],
    }
    mock_client.get.return_value = mock_response

    connector = SalesPipelineConnector(api_url="http://crm.service.internal", api_key="test-key")
    result = connector.fetch_metrics("30d")

    assert result.status == DomainStatus.COMPLETE
    assert len(result.metrics) == 2

    pipe_metric = next(m for m in result.metrics if m.metric_key == "pipeline_value")
    assert pipe_metric.raw_value == 500000.0
    assert pipe_metric.formatted_value == "$500,000"
    assert pipe_metric.percentage_change == 25.0

    proposal = next(b for b in result.breakdown_items if b.label == "Stage: Proposal")
    assert proposal.value == 200000.0

    mock_client.get.assert_called_once()


@patch("httpx.Client")
def test_sales_pipeline_connector_external_api_timeout_retries(mock_client_cls):
    """Verify timeout exception handling and retries for external HTTP API calls."""
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    mock_client.get.side_effect = httpx.TimeoutException("Connection timed out")

    connector = SalesPipelineConnector(
        api_url="http://crm.service.internal",
        timeout=1.0,
        retries=2,
    )
    result = connector.fetch_metrics("30d")

    assert result.status == DomainStatus.UNAVAILABLE
    assert "timed out after 1.0s" in result.error_detail
    # 1 initial attempt + 2 retries = 3 calls
    assert mock_client.get.call_count == 3
