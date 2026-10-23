"""Unit tests for Lead Generation domain connector adapter."""

from unittest.mock import MagicMock, patch
import httpx
import pytest

from src.connectors.lead_gen import LeadGenConnector
from src.services.dashboard.domain_ports import (
    DomainStatus,
    MetricStatus,
    MetricUnit,
)


def test_lead_gen_connector_domain_id():
    """Verify that lead gen connector reports correct domain identifier."""
    connector = LeadGenConnector()
    assert connector.domain_id == "lead_gen"


def test_lead_gen_connector_fetch_metrics_default_30d():
    """Verify successful metric acquisition for 30d timeframe."""
    connector = LeadGenConnector()
    result = connector.fetch_metrics("30d")

    assert result.domain == "lead_gen"
    assert result.status == DomainStatus.COMPLETE
    assert result.error_detail is None
    assert len(result.metrics) == 2

    # Verify Lead Volume metric
    volume_metric = next(m for m in result.metrics if m.metric_key == "lead_volume")
    assert volume_metric.domain == "lead_gen"
    assert volume_metric.display_name == "Lead Volume"
    assert volume_metric.raw_value == 1450.0
    assert volume_metric.baseline_value == 1200.0
    assert volume_metric.formatted_value == "1,450"
    assert volume_metric.status == MetricStatus.VALID
    assert volume_metric.unit == MetricUnit.COUNT
    assert volume_metric.percentage_change == 20.8

    # Verify Conversion Rate metric
    conversion_metric = next(m for m in result.metrics if m.metric_key == "conversion_rate")
    assert conversion_metric.domain == "lead_gen"
    assert conversion_metric.display_name == "Conversion Rate"
    assert conversion_metric.raw_value == 4.2
    assert conversion_metric.baseline_value == 3.5
    assert conversion_metric.formatted_value == "4.2%"
    assert conversion_metric.status == MetricStatus.VALID
    assert conversion_metric.unit == MetricUnit.PERCENTAGE
    assert conversion_metric.percentage_change == 20.0

    # Verify Breakdown items
    assert len(result.breakdown_items) == 4
    organic_item = next(b for b in result.breakdown_items if b.label == "Lead Source: Organic")
    assert organic_item.value == 580.0


def test_lead_gen_connector_fetch_metrics_different_timeframes():
    """Verify metrics scaling for 7d and 90d timeframes."""
    connector = LeadGenConnector()

    res_7d = connector.fetch_metrics("7d")
    assert res_7d.status == DomainStatus.COMPLETE
    vol_7d = next(m for m in res_7d.metrics if m.metric_key == "lead_volume")
    assert vol_7d.raw_value == 340.0
    assert vol_7d.formatted_value == "340"

    res_90d = connector.fetch_metrics("90d")
    assert res_90d.status == DomainStatus.COMPLETE
    vol_90d = next(m for m in res_90d.metrics if m.metric_key == "lead_volume")
    assert vol_90d.raw_value == 4350.0
    assert vol_90d.formatted_value == "4,350"


def test_lead_gen_connector_invalid_timeframe_isolation():
    """Verify fault isolation when an invalid timeframe string is provided."""
    connector = LeadGenConnector()
    result = connector.fetch_metrics("invalid_timeframe")

    assert result.domain == "lead_gen"
    assert result.status == DomainStatus.UNAVAILABLE
    assert result.error_detail is not None
    assert "Invalid timeframe" in result.error_detail

    for metric in result.metrics:
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.formatted_value == "Unavailable"
        assert metric.raw_value is None


def test_lead_gen_connector_simulate_failure():
    """Verify fault isolation when connector encounters upstream runtime failure."""
    connector = LeadGenConnector(simulate_failure=True)
    result = connector.fetch_metrics("30d")

    assert result.domain == "lead_gen"
    assert result.status == DomainStatus.UNAVAILABLE
    assert result.error_detail is not None
    assert "HTTP 503" in result.error_detail

    for metric in result.metrics:
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.formatted_value == "Unavailable"


def test_lead_gen_connector_simulate_timeout():
    """Verify fault isolation and error reporting on timeout."""
    connector = LeadGenConnector(simulate_timeout=True, timeout=5.0)
    result = connector.fetch_metrics("30d")

    assert result.domain == "lead_gen"
    assert result.status == DomainStatus.UNAVAILABLE
    assert result.error_detail is not None
    assert "timed out after 5.0s" in result.error_detail

    for metric in result.metrics:
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.formatted_value == "Unavailable"


def test_lead_gen_connector_simulate_zero():
    """Verify correct ZERO status tagging for legitimate zero values."""
    connector = LeadGenConnector(simulate_zero=True)
    result = connector.fetch_metrics("30d")

    assert result.domain == "lead_gen"
    assert result.status == DomainStatus.COMPLETE

    vol_metric = next(m for m in result.metrics if m.metric_key == "lead_volume")
    assert vol_metric.status == MetricStatus.ZERO
    assert vol_metric.raw_value == 0.0
    assert vol_metric.formatted_value == "0"
    assert vol_metric.percentage_change == -100.0

    conv_metric = next(m for m in result.metrics if m.metric_key == "conversion_rate")
    assert conv_metric.status == MetricStatus.ZERO
    assert conv_metric.raw_value == 0.0
    assert conv_metric.formatted_value == "0.0%"
    assert conv_metric.percentage_change == -100.0


@patch("httpx.Client")
def test_lead_gen_connector_external_api_success(mock_client_cls):
    """Verify fetching and parsing from external upstream HTTP API."""
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "metrics": [
            {
                "metric_key": "lead_volume",
                "display_name": "Lead Volume",
                "raw_value": 2000.0,
                "baseline_value": 1500.0,
                "unit": "count",
            },
            {
                "metric_key": "conversion_rate",
                "display_name": "Conversion Rate",
                "raw_value": 5.0,
                "baseline_value": 4.0,
                "unit": "percentage",
            },
        ],
        "breakdown_items": [
            {"label": "Lead Source: Organic", "value": 1000.0},
        ],
    }
    mock_client.get.return_value = mock_response

    connector = LeadGenConnector(api_url="http://leadgen.service.internal", api_key="test-key")
    result = connector.fetch_metrics("30d")

    assert result.status == DomainStatus.COMPLETE
    assert len(result.metrics) == 2

    vol_metric = next(m for m in result.metrics if m.metric_key == "lead_volume")
    assert vol_metric.raw_value == 2000.0
    assert vol_metric.formatted_value == "2,000"
    assert vol_metric.percentage_change == 33.3

    organic = next(b for b in result.breakdown_items if b.label == "Lead Source: Organic")
    assert organic.value == 1000.0

    mock_client.get.assert_called_once()


@patch("httpx.Client")
def test_lead_gen_connector_external_api_timeout_retries(mock_client_cls):
    """Verify timeout exception handling and retries for external HTTP API calls."""
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    mock_client.get.side_effect = httpx.TimeoutException("Connection timed out")

    connector = LeadGenConnector(
        api_url="http://leadgen.service.internal",
        timeout=1.0,
        retries=2,
    )
    result = connector.fetch_metrics("30d")

    assert result.status == DomainStatus.UNAVAILABLE
    assert "timed out after 1.0s" in result.error_detail
    # 1 initial attempt + 2 retries = 3 calls
    assert mock_client.get.call_count == 3
