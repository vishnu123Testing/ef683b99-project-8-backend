"""Contract compliance verification test for contract lifecycle endpoints in contracts/openapi.json."""

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


def test_contract_endpoints_defined_in_contract():
    """Verify that contract lifecycle routes and schemas are declared in OpenAPI contract."""
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        contract = json.load(f)

    paths = contract.get("paths", {})
    assert "/api/v1/contracts" in paths
    assert "/api/v1/contracts/{id}" in paths
    assert "/api/v1/contracts/{id}/documents" in paths
    assert "/api/v1/contracts/{id}/documents/{version_id}" in paths
    assert "/api/v1/contracts/{id}/amendments" in paths
    assert "/api/v1/signatures/pending" in paths
    assert "/api/v1/signatures/{id}/execute" in paths

    # Verify GET /api/v1/contracts
    contracts_get_op = paths["/api/v1/contracts"].get("get")
    assert contracts_get_op is not None

    # Verify POST /api/v1/contracts
    contracts_post_op = paths["/api/v1/contracts"].get("post")
    assert contracts_post_op is not None

    # Verify DELETE /api/v1/contracts/{id} returns 204
    contracts_delete_op = paths["/api/v1/contracts/{id}"].get("delete")
    assert contracts_delete_op is not None
    assert "204" in contracts_delete_op["responses"]

    # Verify components schemas
    schemas = contract.get("components", {}).get("schemas", {})
    assert "ContractCategory" in schemas
    assert "ContractStatus" in schemas
    assert "ContractCreateRequest" in schemas
    assert "ContractDetailResponse" in schemas
    assert "ContractListResponse" in schemas
    assert "DocumentVersionResponse" in schemas
    assert "AmendmentDetailResponse" in schemas
    assert "SignatureExecuteRequest" in schemas
    assert "SignatureExecuteResponse" in schemas


def test_support_ticket_endpoints_defined_in_contract():
    """Verify that support ticket operations and schemas are declared in OpenAPI contract [FR-1 - FR-9]."""
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        contract = json.load(f)

    paths = contract.get("paths", {})
    assert "/api/v1/tickets" in paths
    assert "/api/v1/tickets/{ticket_id}" in paths
    assert "/api/v1/tickets/{ticket_id}/messages" in paths
    assert "/api/v1/tickets/{ticket_id}/attachments" in paths
    assert "/api/v1/tickets/{ticket_id}/csat" in paths
    assert "/api/v1/analytics/support" in paths
    assert "/api/v1/support/analytics" in paths

    # Verify DELETE /api/v1/tickets/{ticket_id} returns 204 No Content
    tickets_delete_op = paths["/api/v1/tickets/{ticket_id}"].get("delete")
    assert tickets_delete_op is not None
    assert "204" in tickets_delete_op["responses"]

    # Verify schemas
    schemas = contract.get("components", {}).get("schemas", {})
    assert "CreateTicketSchema" in schemas
    assert "UpdateTicketSchema" in schemas
    assert "TicketResponseSchema" in schemas
    assert "TicketListResponseSchema" in schemas
    assert "CreateMessageSchema" in schemas
    assert "TicketMessageResponseSchema" in schemas
    assert "TicketAttachmentResponseSchema" in schemas
    assert "CreateCSATRatingSchema" in schemas
    assert "CSATRatingResponseSchema" in schemas
    assert "SupportAnalyticsResponseSchema" in schemas


def test_financial_and_alerting_endpoints_defined_in_contract():
    """Verify that financial metrics endpoints and notification hub management routes and schemas are declared in OpenAPI contract [FR-1..FR-12]."""
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        contract = json.load(f)

    paths = contract.get("paths", {})
    assert "/api/v1/financial/quotes" in paths
    assert "/api/v1/financial/invoices" in paths
    assert "/api/v1/financial/payments" in paths
    assert "/api/v1/financial/ar-aging" in paths
    assert "/api/v1/notifications/in-app" in paths
    assert "/api/v1/notifications/triggers" in paths
    assert "/api/v1/notifications/triggers/{trigger_id}" in paths
    assert "/api/v1/notifications/preferences" in paths

    # Verify GET /api/v1/financial/quotes
    quotes_get_op = paths["/api/v1/financial/quotes"].get("get")
    assert quotes_get_op is not None

    # Verify GET /api/v1/financial/invoices
    invoices_get_op = paths["/api/v1/financial/invoices"].get("get")
    assert invoices_get_op is not None

    # Verify GET /api/v1/financial/payments
    payments_get_op = paths["/api/v1/financial/payments"].get("get")
    assert payments_get_op is not None

    # Verify GET /api/v1/financial/ar-aging
    ar_aging_get_op = paths["/api/v1/financial/ar-aging"].get("get")
    assert ar_aging_get_op is not None

    # Verify DELETE /api/v1/notifications/triggers/{trigger_id} returns 204 No Content
    triggers_delete_op = paths["/api/v1/notifications/triggers/{trigger_id}"].get("delete")
    assert triggers_delete_op is not None
    assert "204" in triggers_delete_op["responses"]

    # Verify PUT /api/v1/notifications/preferences
    pref_put_op = paths["/api/v1/notifications/preferences"].get("put")
    assert pref_put_op is not None

    # Verify components schemas
    schemas = contract.get("components", {}).get("schemas", {})
    assert "QuoteResponseSchema" in schemas
    assert "InvoiceResponseSchema" in schemas
    assert "PaymentResponseSchema" in schemas
    assert "ARAgingBucketSchema" in schemas
    assert "ARAgingMetricsResponseSchema" in schemas
    assert "TriggerCreateSchema" in schemas
    assert "TriggerResponseSchema" in schemas
    assert "PreferenceUpdateSchema" in schemas
    assert "PreferenceResponseSchema" in schemas
    assert "InAppNotificationResponseSchema" in schemas


def test_knowledge_endpoints_defined_in_contract():
    """Verify that knowledge repository endpoints and schemas are declared in OpenAPI contract [FR-1 - FR-9]."""
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        contract = json.load(f)

    paths = contract.get("paths", {})
    assert "/api/v1/knowledge/articles" in paths
    assert "/api/v1/knowledge/articles/{id}" in paths
    assert "/api/v1/knowledge/playbooks/{id}/steps/{step_id}/progress" in paths
    assert "/api/v1/knowledge/playbooks/{id}/progress" in paths

    # Verify GET /api/v1/knowledge/articles
    articles_get_op = paths["/api/v1/knowledge/articles"].get("get")
    assert articles_get_op is not None

    # Verify GET /api/v1/knowledge/articles/{id}
    article_detail_op = paths["/api/v1/knowledge/articles/{id}"].get("get")
    assert article_detail_op is not None

    # Verify DELETE /api/v1/knowledge/articles/{id} returns 204 No Content
    article_delete_op = paths["/api/v1/knowledge/articles/{id}"].get("delete")
    assert article_delete_op is not None
    assert "204" in article_delete_op["responses"]

    # Verify POST /api/v1/knowledge/playbooks/{id}/steps/{step_id}/progress
    step_progress_post_op = paths["/api/v1/knowledge/playbooks/{id}/steps/{step_id}/progress"].get("post")
    assert step_progress_post_op is not None

    # Verify GET /api/v1/knowledge/playbooks/{id}/progress
    playbook_progress_get_op = paths["/api/v1/knowledge/playbooks/{id}/progress"].get("get")
    assert playbook_progress_get_op is not None

    # Verify components schemas
    schemas = contract.get("components", {}).get("schemas", {})
    assert "CategoryResponseSchema" in schemas
    assert "TagResponseSchema" in schemas
    assert "ArticleCardSchema" in schemas
    assert "ArticleListResponseSchema" in schemas
    assert "AttachmentResponseSchema" in schemas
    assert "PlaybookStepResponseSchema" in schemas
    assert "ArticleDetailResponseSchema" in schemas
    assert "StepProgressToggleRequestSchema" in schemas
    assert "StepProgressResponseSchema" in schemas
    assert "StepProgressItemSchema" in schemas
    assert "PlaybookProgressResponseSchema" in schemas


