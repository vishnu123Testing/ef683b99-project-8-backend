"""Contract compliance verification test for committed OpenAPI schema in contracts/openapi.json."""

import json
from pathlib import Path

from src.api.main import app

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = PROJECT_ROOT / "contracts" / "openapi.json"


def test_openapi_contract_file_exists():
    """Verify that contracts/openapi.json exists on disk."""
    assert CONTRACT_PATH.exists(), f"OpenAPI contract specification file missing at {CONTRACT_PATH}"


def test_openapi_contract_matches_app_schema():
    """Verify that committed openapi.json matches FastAPI runtime app.openapi() schema."""
    assert CONTRACT_PATH.exists(), "Contract file does not exist"
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        committed_schema = json.load(f)

    runtime_schema = app.openapi()
    assert committed_schema == runtime_schema, "Committed contracts/openapi.json does not match FastAPI app.openapi() schema"


def test_dashboard_endpoints_defined_in_contract():
    """Verify that executive dashboard routes and schemas are declared in OpenAPI contract."""
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        contract = json.load(f)

    paths = contract.get("paths", {})
    assert "/api/v1/dashboard/summary" in paths
    assert "/api/v1/dashboard/domains/{domain_id}" in paths

    summary_op = paths["/api/v1/dashboard/summary"].get("get")
    assert summary_op is not None
    assert summary_op["summary"] == "Get consolidated executive dashboard summary metrics"

    domain_op = paths["/api/v1/dashboard/domains/{domain_id}"].get("get")
    assert domain_op is not None
    assert domain_op["summary"] == "Get domain-specific quick-view panel drilldown metrics"

    components = contract.get("components", {}).get("schemas", {})
    assert "DashboardSummaryResponse" in components
    assert "DomainDetailResponse" in components
    assert "MetricSchema" in components
    assert "BreakdownItemSchema" in components
    assert "AggregationStatus" in components
    assert "DomainStatus" in components
    assert "MetricStatus" in components
    assert "MetricUnit" in components
