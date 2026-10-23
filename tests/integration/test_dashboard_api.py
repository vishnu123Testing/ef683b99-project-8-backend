"""Integration tests for GET /api/v1/dashboard/summary route handler (FR-1, FR-2, FR-3, FR-4)."""

from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from src.api.main import app
from src.services.dashboard.domain_ports import (
    AggregationStatus,
    DomainMetricsResult,
    DomainStatus,
    MetricResult,
    MetricStatus,
    MetricUnit,
)

client = TestClient(app)


def test_get_dashboard_summary_default_timeframe():
    """Verify GET /api/v1/dashboard/summary returns 200 OK with default 30d timeframe (FR-1, FR-2)."""
    response = client.get("/api/v1/dashboard/summary")
    assert response.status_code == 200

    data = response.json()
    assert "status" in data
    assert "generated_at" in data
    assert "metrics" in data
    assert isinstance(data["metrics"], list)
    assert len(data["metrics"]) > 0

    # Ensure all four domains are represented
    domains = {m["domain"] for m in data["metrics"]}
    assert {"lead_gen", "sales_pipeline", "support", "financials"}.issubset(domains)

    # Check metric schema structure
    first_metric = data["metrics"][0]
    assert "domain" in first_metric
    assert "metric_key" in first_metric
    assert "display_name" in first_metric
    assert "raw_value" in first_metric
    assert "formatted_value" in first_metric
    assert "status" in first_metric
    assert "unit" in first_metric


def test_get_dashboard_summary_valid_timeframes():
    """Verify GET /api/v1/dashboard/summary handles valid timeframe query params ('7d', '90d')."""
    for tf in ["7d", "30d", "90d"]:
        response = client.get(f"/api/v1/dashboard/summary?timeframe={tf}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in [s.value for s in AggregationStatus]


def test_get_dashboard_summary_invalid_timeframe_raises_400():
    """Verify GET /api/v1/dashboard/summary returns HTTP 400 for invalid timeframe parameter."""
    response = client.get("/api/v1/dashboard/summary?timeframe=14d")
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "Invalid timeframe parameter" in data["detail"]


def test_get_dashboard_summary_partial_outage_handling():
    """Verify GET /api/v1/dashboard/summary handles connector failure gracefully as PARTIAL status (FR-3)."""
    # Mock LeadGenConnector.fetch_metrics to raise an exception simulating connector failure
    with patch(
        "src.connectors.lead_gen.LeadGenConnector.fetch_metrics",
        side_effect=Exception("Upstream LeadGen service unavailable"),
    ):
        response = client.get("/api/v1/dashboard/summary")
        assert response.status_code == 200
        data = response.json()

        assert data["status"] == AggregationStatus.PARTIAL.value

        # Check that LeadGen metrics render as UNAVAILABLE with "Unavailable" formatted_value
        lead_gen_metrics = [m for m in data["metrics"] if m["domain"] == "lead_gen"]
        for m in lead_gen_metrics:
            assert m["status"] == MetricStatus.UNAVAILABLE.value
            assert m["formatted_value"] == "Unavailable"


def test_get_dashboard_summary_zero_value_formatting():
    """Verify GET /api/v1/dashboard/summary renders legitimate zero metrics as '0' or '$0' (FR-4)."""
    # Mock SupportTicketsConnector to return 0 open tickets
    zero_support_result = DomainMetricsResult(
        domain="support",
        status=DomainStatus.COMPLETE,
        metrics=[
            MetricResult(
                domain="support",
                metric_key="open_tickets",
                display_name="Open Support Tickets",
                raw_value=0.0,
                baseline_value=10.0,
                formatted_value="0",
                percentage_change=-100.0,
                status=MetricStatus.ZERO,
                unit=MetricUnit.COUNT,
            )
        ],
    )

    with patch(
        "src.connectors.support_tickets.SupportTicketsConnector.fetch_metrics",
        return_value=zero_support_result,
    ):
        response = client.get("/api/v1/dashboard/summary")
        assert response.status_code == 200
        data = response.json()

        support_open = next(
            (m for m in data["metrics"] if m["domain"] == "support" and m["metric_key"] == "open_tickets"),
            None,
        )
        assert support_open is not None
        assert support_open["status"] == MetricStatus.ZERO.value
        assert support_open["formatted_value"] == "0"
        assert support_open["raw_value"] == 0.0


def test_get_domain_detail_lead_gen():
    """Verify GET /api/v1/dashboard/domains/lead_gen returns detailed Lead Gen metrics (FR-6)."""
    response = client.get("/api/v1/dashboard/domains/lead_gen")
    assert response.status_code == 200

    data = response.json()
    assert data["domain"] == "lead_gen"
    assert data["status"] == DomainStatus.COMPLETE.value
    assert "detailed_metrics" in data
    assert isinstance(data["detailed_metrics"], list)
    assert len(data["detailed_metrics"]) >= 2

    keys = {m["metric_key"] for m in data["detailed_metrics"]}
    assert "lead_volume" in keys
    assert "conversion_rate" in keys

    assert "breakdown_items" in data
    assert isinstance(data["breakdown_items"], list)
    assert len(data["breakdown_items"]) > 0


def test_get_domain_detail_sales_pipeline():
    """Verify GET /api/v1/dashboard/domains/sales_pipeline returns detailed Sales Pipeline metrics (FR-7)."""
    response = client.get("/api/v1/dashboard/domains/sales_pipeline")
    assert response.status_code == 200

    data = response.json()
    assert data["domain"] == "sales_pipeline"
    assert data["status"] == DomainStatus.COMPLETE.value
    assert len(data["detailed_metrics"]) >= 2

    keys = {m["metric_key"] for m in data["detailed_metrics"]}
    assert "pipeline_value" in keys
    assert "win_rate" in keys

    assert len(data["breakdown_items"]) > 0


def test_get_domain_detail_support():
    """Verify GET /api/v1/dashboard/domains/support returns detailed Support Ticketing metrics (FR-8)."""
    response = client.get("/api/v1/dashboard/domains/support")
    assert response.status_code == 200

    data = response.json()
    assert data["domain"] == "support"
    assert data["status"] == DomainStatus.COMPLETE.value
    assert len(data["detailed_metrics"]) >= 2

    keys = {m["metric_key"] for m in data["detailed_metrics"]}
    assert "open_tickets" in keys

    assert len(data["breakdown_items"]) > 0


def test_get_domain_detail_financials():
    """Verify GET /api/v1/dashboard/domains/financials returns detailed Financial Summary metrics (FR-9)."""
    response = client.get("/api/v1/dashboard/domains/financials")
    assert response.status_code == 200

    data = response.json()
    assert data["domain"] == "financials"
    assert data["status"] == DomainStatus.COMPLETE.value
    assert len(data["detailed_metrics"]) >= 2

    keys = {m["metric_key"] for m in data["detailed_metrics"]}
    assert "recurring_revenue" in keys

    assert len(data["breakdown_items"]) > 0


def test_get_domain_detail_valid_timeframes():
    """Verify GET /api/v1/dashboard/domains/{domain_id} accepts valid timeframes ('7d', '90d')."""
    for tf in ["7d", "30d", "90d"]:
        response = client.get(f"/api/v1/dashboard/domains/support?timeframe={tf}")
        assert response.status_code == 200
        data = response.json()
        assert data["domain"] == "support"


def test_get_domain_detail_invalid_domain_raises_400():
    """Verify GET /api/v1/dashboard/domains/{domain_id} returns HTTP 400 for unsupported domain_id."""
    response = client.get("/api/v1/dashboard/domains/unknown_domain")
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "Unsupported domain_id" in data["detail"]


def test_get_domain_detail_invalid_timeframe_raises_400():
    """Verify GET /api/v1/dashboard/domains/{domain_id} returns HTTP 400 for invalid timeframe."""
    response = client.get("/api/v1/dashboard/domains/lead_gen?timeframe=14d")
    assert response.status_code == 400
    data = response.json()
    assert "detail" in data
    assert "Invalid timeframe parameter" in data["detail"]


def test_get_domain_detail_connector_failure_handling():
    """Verify GET /api/v1/dashboard/domains/{domain_id} handles connector failure gracefully."""
    with patch(
        "src.connectors.lead_gen.LeadGenConnector.fetch_metrics",
        side_effect=Exception("Upstream LeadGen service offline"),
    ):
        response = client.get("/api/v1/dashboard/domains/lead_gen")
        assert response.status_code == 200
        data = response.json()

        assert data["domain"] == "lead_gen"
        assert data["status"] == DomainStatus.UNAVAILABLE.value
        assert len(data["detailed_metrics"]) == 0
        assert data["error_detail"] is not None
        assert "Upstream LeadGen service offline" in data["error_detail"]
