"""Unit tests for Support Ticketing domain connector adapter."""

from unittest.mock import MagicMock, patch
import httpx
import pytest

from src.connectors.support_tickets import SupportTicketsConnector
from src.services.dashboard.domain_ports import (
    DomainStatus,
    MetricStatus,
    MetricUnit,
)


def test_support_tickets_connector_domain_id():
    """Verify that support tickets connector reports correct domain identifier."""
    connector = SupportTicketsConnector()
    assert connector.domain_id == "support"


def test_support_tickets_connector_fetch_metrics_default_30d():
    """Verify successful metric acquisition for 30d timeframe."""
    connector = SupportTicketsConnector()
    result = connector.fetch_metrics("30d")

    assert result.domain == "support"
    assert result.status == DomainStatus.COMPLETE
    assert result.error_detail is None
    assert len(result.metrics) == 3

    # Verify Open Tickets metric
    open_tickets_metric = next(m for m in result.metrics if m.metric_key == "open_tickets")
    assert open_tickets_metric.domain == "support"
    assert open_tickets_metric.display_name == "Open Tickets"
    assert open_tickets_metric.raw_value == 42.0
    assert open_tickets_metric.baseline_value == 50.0
    assert open_tickets_metric.formatted_value == "42"
    assert open_tickets_metric.status == MetricStatus.VALID
    assert open_tickets_metric.unit == MetricUnit.COUNT
    assert open_tickets_metric.percentage_change == -16.0

    # Verify Avg Resolution Time metric
    res_time_metric = next(m for m in result.metrics if m.metric_key == "avg_resolution_time")
    assert res_time_metric.domain == "support"
    assert res_time_metric.display_name == "Avg Resolution Time"
    assert res_time_metric.raw_value == 2.4
    assert res_time_metric.baseline_value == 3.0
    assert res_time_metric.formatted_value == "2.4h"
    assert res_time_metric.status == MetricStatus.VALID
    assert res_time_metric.unit == MetricUnit.COUNT
    assert res_time_metric.percentage_change == -20.0

    # Verify SLA Compliance metric
    sla_metric = next(m for m in result.metrics if m.metric_key == "sla_compliance")
    assert sla_metric.domain == "support"
    assert sla_metric.display_name == "SLA Compliance"
    assert sla_metric.raw_value == 96.5
    assert sla_metric.baseline_value == 94.0
    assert sla_metric.formatted_value == "96.5%"
    assert sla_metric.status == MetricStatus.VALID
    assert sla_metric.unit == MetricUnit.PERCENTAGE
    assert sla_metric.percentage_change == 2.7

    # Verify Severity Distribution Breakdown items
    assert len(result.breakdown_items) == 4
    p1_item = next(b for b in result.breakdown_items if b.label == "Ticket Severity: P1")
    assert p1_item.value == 2.0


def test_support_tickets_connector_fetch_metrics_different_timeframes():
    """Verify metrics scaling for 7d and 90d timeframes."""
    connector = SupportTicketsConnector()

    res_7d = connector.fetch_metrics("7d")
    assert res_7d.status == DomainStatus.COMPLETE
    open_7d = next(m for m in res_7d.metrics if m.metric_key == "open_tickets")
    assert open_7d.raw_value == 12.0
    assert open_7d.formatted_value == "12"

    res_90d = connector.fetch_metrics("90d")
    assert res_90d.status == DomainStatus.COMPLETE
    open_90d = next(m for m in res_90d.metrics if m.metric_key == "open_tickets")
    assert open_90d.raw_value == 135.0
    assert open_90d.formatted_value == "135"


def test_support_tickets_connector_invalid_timeframe_isolation():
    """Verify fault isolation when an invalid timeframe string is provided."""
    connector = SupportTicketsConnector()
    result = connector.fetch_metrics("invalid_timeframe")

    assert result.domain == "support"
    assert result.status == DomainStatus.UNAVAILABLE
    assert result.error_detail is not None
    assert "Invalid timeframe" in result.error_detail

    for metric in result.metrics:
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.formatted_value == "Unavailable"
        assert metric.raw_value is None


def test_support_tickets_connector_simulate_failure():
    """Verify fault isolation when connector encounters upstream runtime failure."""
    connector = SupportTicketsConnector(simulate_failure=True)
    result = connector.fetch_metrics("30d")

    assert result.domain == "support"
    assert result.status == DomainStatus.UNAVAILABLE
    assert result.error_detail is not None
    assert "HTTP 503" in result.error_detail

    for metric in result.metrics:
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.formatted_value == "Unavailable"


def test_support_tickets_connector_simulate_timeout():
    """Verify fault isolation and error reporting on timeout."""
    connector = SupportTicketsConnector(simulate_timeout=True, timeout=5.0)
    result = connector.fetch_metrics("30d")

    assert result.domain == "support"
    assert result.status == DomainStatus.UNAVAILABLE
    assert result.error_detail is not None
    assert "timed out after 5.0s" in result.error_detail

    for metric in result.metrics:
        assert metric.status == MetricStatus.UNAVAILABLE
        assert metric.formatted_value == "Unavailable"


def test_support_tickets_connector_simulate_zero():
    """Verify correct ZERO status tagging for legitimate zero values."""
    connector = SupportTicketsConnector(simulate_zero=True)
    result = connector.fetch_metrics("30d")

    assert result.domain == "support"
    assert result.status == DomainStatus.COMPLETE

    open_metric = next(m for m in result.metrics if m.metric_key == "open_tickets")
    assert open_metric.status == MetricStatus.ZERO
    assert open_metric.raw_value == 0.0
    assert open_metric.formatted_value == "0"
    assert open_metric.percentage_change == -100.0

    res_metric = next(m for m in result.metrics if m.metric_key == "avg_resolution_time")
    assert res_metric.status == MetricStatus.ZERO
    assert res_metric.raw_value == 0.0
    assert res_metric.formatted_value == "0.0h"
    assert res_metric.percentage_change == -100.0

    sla_metric = next(m for m in result.metrics if m.metric_key == "sla_compliance")
    assert sla_metric.status == MetricStatus.ZERO
    assert sla_metric.raw_value == 0.0
    assert sla_metric.formatted_value == "0.0%"
    assert sla_metric.percentage_change == -100.0


@patch("httpx.Client")
def test_support_tickets_connector_external_api_success(mock_client_cls):
    """Verify fetching and parsing from external upstream HTTP API."""
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client

    mock_response = MagicMock()
    mock_response.raise_for_status.return_value = None
    mock_response.json.return_value = {
        "metrics": [
            {
                "metric_key": "open_tickets",
                "display_name": "Open Tickets",
                "raw_value": 30.0,
                "baseline_value": 40.0,
                "unit": "count",
            },
            {
                "metric_key": "avg_resolution_time",
                "display_name": "Avg Resolution Time",
                "raw_value": 2.0,
                "baseline_value": 2.5,
                "unit": "count",
            },
            {
                "metric_key": "sla_compliance",
                "display_name": "SLA Compliance",
                "raw_value": 98.0,
                "baseline_value": 95.0,
                "unit": "percentage",
            },
        ],
        "breakdown_items": [
            {"label": "Ticket Severity: P1", "value": 1.0},
        ],
    }
    mock_client.get.return_value = mock_response

    connector = SupportTicketsConnector(api_url="http://support.service.internal", api_key="test-key")
    result = connector.fetch_metrics("30d")

    assert result.status == DomainStatus.COMPLETE
    assert len(result.metrics) == 3

    open_metric = next(m for m in result.metrics if m.metric_key == "open_tickets")
    assert open_metric.raw_value == 30.0
    assert open_metric.formatted_value == "30"
    assert open_metric.percentage_change == -25.0

    p1_item = next(b for b in result.breakdown_items if b.label == "Ticket Severity: P1")
    assert p1_item.value == 1.0

    mock_client.get.assert_called_once()


@patch("httpx.Client")
def test_support_tickets_connector_external_api_timeout_retries(mock_client_cls):
    """Verify timeout exception handling and retries for external HTTP API calls."""
    mock_client = MagicMock()
    mock_client_cls.return_value.__enter__.return_value = mock_client
    mock_client.get.side_effect = httpx.TimeoutException("Connection timed out")

    connector = SupportTicketsConnector(
        api_url="http://support.service.internal",
        timeout=1.0,
        retries=2,
    )
    result = connector.fetch_metrics("30d")

    assert result.status == DomainStatus.UNAVAILABLE
    assert "timed out after 1.0s" in result.error_detail
    # 1 initial attempt + 2 retries = 3 calls
    assert mock_client.get.call_count == 3
