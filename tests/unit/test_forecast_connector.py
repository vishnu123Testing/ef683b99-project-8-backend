"""Unit tests for Forecast Provider Connector Adapter and Port (FR-8, FR-9, NFR-5)."""

from datetime import datetime, timezone
import json
from unittest.mock import MagicMock, patch
import httpx
import pytest

from src.connectors.crm_connector import CRMConnector
from src.connectors.forecast_connector import ForecastConnector, ForecastProviderConnector
from src.services.deals.crm_port import CRMDealRecord, CRMStageHistoryItem, CRMActivityItem
from src.services.forecasting.forecast_port import (
    ForecastConnectionError,
    ForecastError,
    ForecastProviderError,
    ForecastProviderPort,
    ForecastResult,
    ForecastTimeoutError,
    InsufficientDataError,
)


def test_forecast_connector_calculate_forecast_success():
    """Verify calculating AI completion forecast for a deal with CRM history."""
    crm = CRMConnector()
    connector = ForecastConnector(crm_provider=crm)

    result = connector.calculate_forecast("deal-101")

    assert result is not None
    assert result.deal_id == "deal-101"
    assert result.status_code == "READY"
    assert isinstance(result.estimated_completion_date, datetime)
    assert result.estimated_completion_date > datetime.now(timezone.utc)
    assert isinstance(result.confidence_score_pct, float)
    assert result.confidence_score_pct >= 50.0
    assert result.model_metadata is not None
    metadata = json.loads(result.model_metadata)
    assert metadata["milestone_stage"] == "Negotiation"


def test_forecast_connector_explicit_crm_injection():
    """Verify explicitly injecting CRM provider into calculate_forecast (NFR-5)."""
    connector = ForecastConnector()  # No default CRM provider
    crm = CRMConnector()

    result = connector.calculate_forecast("deal-101", crm_provider=crm)

    assert result.status_code == "READY"
    assert result.confidence_score_pct is not None


def test_forecast_connector_insufficient_crm_data():
    """Verify fallback to INSUFFICIENT_DATA status when CRM history is sparse."""
    connector = ForecastConnector(simulate_insufficient_data=True)

    result = connector.calculate_forecast("deal-101")

    assert result.status_code == "INSUFFICIENT_DATA"
    assert result.estimated_completion_date is None
    assert result.confidence_score_pct is None
    assert "insufficient" in result.error_detail.lower()


def test_forecast_connector_sparse_record_data_points():
    """Verify handling deal record with data points below min_history_records threshold."""
    crm = CRMConnector()
    # Create a sparse deal record with 0 stage history and 0 activities
    sparse_deal = CRMDealRecord(
        deal_id="sparse-deal-1",
        title="Sparse Deal Test",
        deal_value=1000.0,
        milestone_stage="Lead Qualification",
        win_probability=0.1,
        created_on=datetime.now(timezone.utc),
        updated_on=datetime.now(timezone.utc),
        stage_history=[],
        activities=[],
    )
    crm.add_simulated_deal(sparse_deal)

    connector = ForecastConnector(crm_provider=crm, min_history_records=3)
    result = connector.calculate_forecast("sparse-deal-1")

    assert result.status_code == "INSUFFICIENT_DATA"
    assert result.estimated_completion_date is None
    assert result.confidence_score_pct is None


def test_forecast_connector_missing_crm_deal():
    """Verify response when requested deal is missing from CRM provider."""
    crm = CRMConnector()
    connector = ForecastConnector(crm_provider=crm)

    result = connector.calculate_forecast("missing-deal-999")

    assert result.status_code == "INSUFFICIENT_DATA"
    assert result.estimated_completion_date is None
    assert result.confidence_score_pct is None


def test_forecast_connector_low_confidence_fallback():
    """Verify fallback when calculated confidence is below threshold."""
    crm = CRMConnector()
    connector = ForecastConnector(
        crm_provider=crm,
        confidence_threshold_pct=95.0,  # High threshold
        simulate_low_confidence=True,
    )

    result = connector.calculate_forecast("deal-101")

    assert result.status_code == "INSUFFICIENT_DATA"
    assert result.estimated_completion_date is None
    assert result.confidence_score_pct is not None


def test_forecast_connector_simulate_timeout():
    """Verify simulation of forecast provider execution timeout boundary."""
    connector = ForecastConnector(simulate_timeout=True)

    with pytest.raises(ForecastTimeoutError):
        connector.calculate_forecast("deal-101")


def test_forecast_connector_simulate_failure():
    """Verify simulation of forecast provider connection failure."""
    connector = ForecastConnector(simulate_failure=True)

    with pytest.raises(ForecastProviderError):
        connector.calculate_forecast("deal-101")


def test_forecast_connector_evaluate_batch_forecasts():
    """Verify evaluate_batch_forecasts for multiple deal IDs."""
    crm = CRMConnector()
    connector = ForecastConnector(crm_provider=crm)

    results = connector.evaluate_batch_forecasts(["deal-101", "deal-102", "nonexistent"])

    assert len(results) == 3
    assert results[0].deal_id == "deal-101"
    assert results[0].status_code == "READY"
    assert results[1].deal_id == "deal-102"
    assert results[1].status_code == "READY"
    assert results[2].deal_id == "nonexistent"
    assert results[2].status_code == "INSUFFICIENT_DATA"


def test_forecast_result_to_dict():
    """Verify to_dict conversion on ForecastResult dataclass."""
    now_val = datetime.now(timezone.utc)
    res = ForecastResult(
        deal_id="test-deal",
        estimated_completion_date=now_val,
        confidence_score_pct=78.5,
        status_code="READY",
        calculated_on=now_val,
        model_metadata='{"test": True}',
    )
    d = res.to_dict()

    assert d["deal_id"] == "test-deal"
    assert d["status_code"] == "READY"
    assert d["confidence_score_pct"] == 78.5
    assert d["estimated_completion_date"] == now_val.isoformat()
    assert d["calculated_on"] == now_val.isoformat()
