"""Unit tests for External CRM connector adapter."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import httpx
import pytest

from src.connectors.crm_connector import CRMConnector
from src.services.deals.crm_port import (
    CRMActivityItem,
    CRMConnectionError,
    CRMDealRecord,
    CRMNotFoundError,
    CRMStageHistoryItem,
    CRMTimeoutError,
)


def test_crm_connector_get_simulated_deal_record():
    """Verify retrieving a full simulated deal record."""
    connector = CRMConnector()
    record = connector.get_deal_record("deal-101")

    assert record is not None
    assert record.deal_id == "deal-101"
    assert record.title == "Enterprise SaaS Expansion - Acme Corp"
    assert record.deal_value == 150000.0
    assert record.milestone_stage == "Negotiation"
    assert record.win_probability == 0.85
    assert len(record.stage_history) == 4
    assert len(record.activities) == 3


def test_crm_connector_get_deal_stage_history():
    """Verify retrieving chronological stage history items."""
    connector = CRMConnector()
    history = connector.get_deal_stage_history("deal-101")

    assert len(history) == 4
    assert history[0].milestone_stage == "Lead Qualification"
    assert history[0].win_probability == 0.20
    assert history[3].milestone_stage == "Negotiation"
    assert history[3].exited_at is None


def test_crm_connector_get_deal_activities():
    """Verify retrieving recent stakeholder activity records."""
    connector = CRMConnector()
    activities = connector.get_deal_activities("deal-101", limit=2)

    assert len(activities) == 2
    # Ensure reverse chronological order by occurred_on
    assert activities[0].occurred_on >= activities[1].occurred_on


def test_crm_connector_get_historical_deals_dataset():
    """Verify retrieving the historical dataset of deals."""
    connector = CRMConnector()
    dataset = connector.get_historical_deals_dataset()

    assert len(dataset) >= 3
    deal_ids = [d.deal_id for d in dataset]
    assert "deal-101" in deal_ids
    assert "deal-102" in deal_ids


def test_crm_connector_not_found_deal():
    """Verify error behavior when a deal is missing."""
    connector = CRMConnector()
    assert connector.get_deal_record("nonexistent-deal") is None

    with pytest.raises(CRMNotFoundError):
        connector.get_deal_stage_history("nonexistent-deal")

    with pytest.raises(CRMNotFoundError):
        connector.get_deal_activities("nonexistent-deal")


def test_crm_connector_simulate_timeout():
    """Verify simulation of execution timeout exception."""
    connector = CRMConnector(simulate_timeout=True)

    with pytest.raises(CRMTimeoutError):
        connector.get_deal_stage_history("deal-101")

    with pytest.raises(CRMTimeoutError):
        connector.get_deal_activities("deal-101")

    with pytest.raises(CRMTimeoutError):
        connector.get_deal_record("deal-101")


def test_crm_connector_simulate_failure():
    """Verify simulation of upstream connection failure."""
    connector = CRMConnector(simulate_failure=True)

    with pytest.raises(CRMConnectionError):
        connector.get_deal_stage_history("deal-101")

    with pytest.raises(CRMConnectionError):
        connector.get_deal_activities("deal-101")


def test_crm_connector_simulate_sparse_data():
    """Verify simulation of sparse CRM history data."""
    connector = CRMConnector(simulate_sparse_data=True)

    history = connector.get_deal_stage_history("deal-101")
    assert history == []

    activities = connector.get_deal_activities("deal-101")
    assert activities == []

    record = connector.get_deal_record("deal-101")
    assert record is not None
    assert record.stage_history == []
    assert record.activities == []


def test_crm_connector_add_and_clear_simulated_deals():
    """Verify dynamic addition and clearing of custom simulated deals."""
    connector = CRMConnector()
    now = datetime.now(timezone.utc)

    custom_deal = CRMDealRecord(
        deal_id="deal-custom-99",
        title="Custom Test Deal",
        deal_value=50000.0,
        milestone_stage="Discovery Call",
        win_probability=0.30,
        created_on=now,
        updated_on=now,
    )
    connector.add_simulated_deal(custom_deal)

    record = connector.get_deal_record("deal-custom-99")
    assert record is not None
    assert record.title == "Custom Test Deal"

    connector.clear_simulated_deals()
    assert connector.get_deal_record("deal-custom-99") is None
    assert connector.get_deal_record("deal-101") is None


@patch("httpx.Client")
def test_crm_connector_http_api_fetch_stage_history(mock_httpx_client):
    """Verify HTTP API integration for retrieving stage history."""
    mock_client_instance = MagicMock()
    mock_httpx_client.return_value.__enter__.return_value = mock_client_instance

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "items": [
            {
                "deal_id": "deal-api-1",
                "milestone_stage": "Lead Qualification",
                "entered_at": "2026-09-01T10:00:00+00:00",
                "exited_at": "2026-09-10T10:00:00+00:00",
                "duration_days": 9.0,
                "win_probability": 0.2,
                "deal_value": 100000.0,
            }
        ]
    }
    mock_client_instance.request.return_value = mock_response

    connector = CRMConnector(
        api_url="https://crm.example.com/api/v1", api_key="secret-token"
    )
    history = connector.get_deal_stage_history("deal-api-1")

    assert len(history) == 1
    assert history[0].deal_id == "deal-api-1"
    assert history[0].milestone_stage == "Lead Qualification"
    assert history[0].duration_days == 9.0


@patch("httpx.Client")
def test_crm_connector_http_api_404_error(mock_httpx_client):
    """Verify HTTP 404 response mapped to CRMNotFoundError."""
    mock_client_instance = MagicMock()
    mock_httpx_client.return_value.__enter__.return_value = mock_client_instance

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_client_instance.request.return_value = mock_response

    connector = CRMConnector(api_url="https://crm.example.com/api/v1")

    with pytest.raises(CRMNotFoundError):
        connector.get_deal_stage_history("missing-deal")


@patch("httpx.Client")
def test_crm_connector_http_api_timeout_error(mock_httpx_client):
    """Verify HTTP timeout mapped to CRMTimeoutError after retries."""
    mock_client_instance = MagicMock()
    mock_httpx_client.return_value.__enter__.return_value = mock_client_instance

    mock_client_instance.request.side_effect = httpx.TimeoutException("Connection timed out")

    connector = CRMConnector(api_url="https://crm.example.com/api/v1", retries=1)

    with pytest.raises(CRMTimeoutError):
        connector.get_deal_stage_history("deal-101")
