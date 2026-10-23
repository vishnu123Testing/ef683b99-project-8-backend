"""Contract compliance verification test for knowledge repository endpoints against OpenAPI definitions in contracts/openapi.json [FR-1 - FR-9, NFR-1, NFR-3]."""

import json
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.main import app
from src.models.database import Base, get_db
from src.models.knowledge import CONTENT_TYPE_PLAYBOOK
from src.services.knowledge.article_service import (
    create_article,
    create_category,
    create_tag,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_PATH = PROJECT_ROOT / "contracts" / "openapi.json"


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for contract verification testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def client(db_session):
    """Fixture providing FastAPI TestClient wired to the in-memory test DB session."""
    def _get_test_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _get_test_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def test_openapi_contract_file_exists():
    """Verify that contracts/openapi.json specification file exists on disk."""
    assert CONTRACT_PATH.exists(), f"OpenAPI contract specification file missing at {CONTRACT_PATH}"


def test_openapi_contract_matches_app_schema():
    """Verify that committed contracts/openapi.json matches FastAPI runtime app.openapi() schema."""
    assert CONTRACT_PATH.exists(), "Contract file does not exist"
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        committed_schema = json.load(f)

    runtime_schema = app.openapi()
    assert committed_schema == runtime_schema, (
        "Committed contracts/openapi.json does not match FastAPI app.openapi() schema"
    )


def test_knowledge_endpoints_declared_in_contract():
    """Verify that all knowledge repository endpoint routes and HTTP operations are declared in OpenAPI contract [FR-1 - FR-9]."""
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        contract = json.load(f)

    paths = contract.get("paths", {})

    # Check path declarations
    assert "/api/v1/knowledge/articles" in paths
    assert "/api/v1/knowledge/articles/{id}" in paths
    assert "/api/v1/knowledge/playbooks/{id}/steps/{step_id}/progress" in paths
    assert "/api/v1/knowledge/playbooks/{id}/progress" in paths

    # Verify GET /api/v1/knowledge/articles
    articles_get_op = paths["/api/v1/knowledge/articles"].get("get")
    assert articles_get_op is not None
    assert "summary" in articles_get_op

    # Verify GET /api/v1/knowledge/articles/{id}
    article_detail_get_op = paths["/api/v1/knowledge/articles/{id}"].get("get")
    assert article_detail_get_op is not None

    # Verify DELETE /api/v1/knowledge/articles/{id}
    article_delete_op = paths["/api/v1/knowledge/articles/{id}"].get("delete")
    assert article_delete_op is not None
    assert "204" in article_delete_op["responses"]

    # Verify POST /api/v1/knowledge/playbooks/{id}/steps/{step_id}/progress
    step_progress_post_op = paths["/api/v1/knowledge/playbooks/{id}/steps/{step_id}/progress"].get("post")
    assert step_progress_post_op is not None

    # Verify GET /api/v1/knowledge/playbooks/{id}/progress
    playbook_progress_get_op = paths["/api/v1/knowledge/playbooks/{id}/progress"].get("get")
    assert playbook_progress_get_op is not None


def test_knowledge_articles_list_contract_schema():
    """Verify query parameters and 200/422 response schemas for GET /api/v1/knowledge/articles [FR-1, FR-2, FR-7, FR-8]."""
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        contract = json.load(f)

    op = contract["paths"]["/api/v1/knowledge/articles"]["get"]

    # Parameters
    param_names = [p["name"] for p in op.get("parameters", [])]
    expected_params = ["query", "category_id", "tag_ids", "content_type", "tenant_id", "is_published", "page", "limit"]
    for param in expected_params:
        assert param in param_names, f"Missing parameter '{param}' in GET /api/v1/knowledge/articles contract"

    # Response 200 schema reference
    resp_200 = op["responses"]["200"]
    content_schema = resp_200["content"]["application/json"]["schema"]
    assert content_schema.get("$ref") == "#/components/schemas/ArticleListResponseSchema"

    # Response 422 for validation error
    assert "422" in op["responses"]


def test_knowledge_article_detail_contract_schema():
    """Verify parameters and 200/400/404 response schemas for GET /api/v1/knowledge/articles/{id} [FR-1, FR-3, FR-5, FR-6]."""
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        contract = json.load(f)

    op = contract["paths"]["/api/v1/knowledge/articles/{id}"]["get"]

    # Parameters
    param_names = [p["name"] for p in op.get("parameters", [])]
    assert "id" in param_names
    assert "tenant_id" in param_names

    # Response 200 schema reference
    resp_200 = op["responses"]["200"]
    content_schema = resp_200["content"]["application/json"]["schema"]
    assert content_schema.get("$ref") == "#/components/schemas/ArticleDetailResponseSchema"

    # Error response status codes
    assert "400" in op["responses"]
    assert "404" in op["responses"]


def test_knowledge_article_delete_contract_schema():
    """Verify 204 No Content response schema for DELETE /api/v1/knowledge/articles/{id} [FR-1, FR-9]."""
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        contract = json.load(f)

    op = contract["paths"]["/api/v1/knowledge/articles/{id}"]["delete"]

    # Parameters
    param_names = [p["name"] for p in op.get("parameters", [])]
    assert "id" in param_names
    assert "tenant_id" in param_names

    # Response 204 status code (no content body)
    assert "204" in op["responses"]
    resp_204 = op["responses"]["204"]
    assert "content" not in resp_204 or resp_204.get("content") == {}

    # Error response status codes
    assert "400" in op["responses"]
    assert "404" in op["responses"]


def test_knowledge_playbook_step_progress_contract_schema():
    """Verify request body and 200 response schema for POST /api/v1/knowledge/playbooks/{id}/steps/{step_id}/progress [FR-3, FR-4]."""
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        contract = json.load(f)

    op = contract["paths"]["/api/v1/knowledge/playbooks/{id}/steps/{step_id}/progress"]["post"]

    # Parameters
    param_names = [p["name"] for p in op.get("parameters", [])]
    assert "id" in param_names
    assert "step_id" in param_names

    # Request body
    req_body_schema = op["requestBody"]["content"]["application/json"]["schema"]
    assert req_body_schema.get("$ref") == "#/components/schemas/StepProgressToggleRequestSchema"

    # Response 200 schema reference
    resp_200 = op["responses"]["200"]
    content_schema = resp_200["content"]["application/json"]["schema"]
    assert content_schema.get("$ref") == "#/components/schemas/StepProgressResponseSchema"


def test_knowledge_playbook_overall_progress_contract_schema():
    """Verify parameters and 200 response schema for GET /api/v1/knowledge/playbooks/{id}/progress [FR-3, FR-4]."""
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        contract = json.load(f)

    op = contract["paths"]["/api/v1/knowledge/playbooks/{id}/progress"]["get"]

    # Parameters
    param_names = [p["name"] for p in op.get("parameters", [])]
    assert "id" in param_names
    assert "user_id" in param_names
    assert "tenant_id" in param_names

    # Response 200 schema reference
    resp_200 = op["responses"]["200"]
    content_schema = resp_200["content"]["application/json"]["schema"]
    assert content_schema.get("$ref") == "#/components/schemas/PlaybookProgressResponseSchema"


def test_knowledge_component_schemas_defined_in_contract():
    """Verify that all knowledge schemas are defined in OpenAPI contract components [FR-1 - FR-9, NFR-3]."""
    with open(CONTRACT_PATH, "r", encoding="utf-8") as f:
        contract = json.load(f)

    schemas = contract.get("components", {}).get("schemas", {})

    expected_schemas = [
        "CategoryResponseSchema",
        "TagResponseSchema",
        "ArticleCardSchema",
        "ArticleListResponseSchema",
        "AttachmentResponseSchema",
        "PlaybookStepResponseSchema",
        "ArticleDetailResponseSchema",
        "StepProgressToggleRequestSchema",
        "StepProgressResponseSchema",
        "StepProgressItemSchema",
        "PlaybookProgressResponseSchema",
    ]

    for schema_name in expected_schemas:
        assert schema_name in schemas, f"Missing component schema '{schema_name}' in OpenAPI contract"

    # Verify ArticleListResponseSchema properties
    list_props = schemas["ArticleListResponseSchema"]["properties"]
    assert "items" in list_props
    assert "total_count" in list_props
    assert "page" in list_props
    assert "limit" in list_props
    assert "total_pages" in list_props

    # Verify ArticleCardSchema properties
    card_props = schemas["ArticleCardSchema"]["properties"]
    assert "id" in card_props
    assert "title" in card_props
    assert "content_type" in card_props

    # Verify ArticleDetailResponseSchema properties
    detail_props = schemas["ArticleDetailResponseSchema"]["properties"]
    assert "attachments" in detail_props
    assert "playbook_steps" in detail_props

    # Verify StepProgressResponseSchema properties
    step_resp_props = schemas["StepProgressResponseSchema"]["properties"]
    assert "step_id" in step_resp_props
    assert "overall_playbook_progress_percentage" in step_resp_props
    assert "total_steps" in step_resp_props
    assert "completed_steps" in step_resp_props

    # Verify PlaybookProgressResponseSchema properties
    playbook_resp_props = schemas["PlaybookProgressResponseSchema"]["properties"]
    assert "article_id" in playbook_resp_props
    assert "total_steps" in playbook_resp_props
    assert "completed_steps" in playbook_resp_props
    assert "progress_percentage" in playbook_resp_props
    assert "step_progress" in playbook_resp_props


def test_knowledge_api_runtime_responses_conform_to_contract(client, db_session):
    """Verify runtime HTTP API responses conform to OpenAPI contract definitions [FR-1 - FR-9, NFR-3]."""
    # 1. Setup seed data
    cat = create_category(db_session, name="Contracts Guide", description="Guide for contracts")
    tag = create_tag(db_session, name="Legal")

    playbook_steps = [
        {"step_number": 1, "title": "Review terms", "content": "Read clause 1.", "checkpoint_instructions": "Check clause 1."},
        {"step_number": 2, "title": "Sign agreement", "content": "Execute signature.", "checkpoint_instructions": "Signature recorded."},
    ]
    attachments = [
        {"file_name": "sample_contract.pdf", "file_url": "https://cdn.example.com/contract.pdf", "file_size_bytes": 2048576}
    ]

    art = create_article(
        db_session,
        title="Contract Execution Playbook",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Detailed playbook for signing agreements.",
        category_id=cat.id,
        tag_ids=[tag.id],
        video_url="https://video.example.com/stream/999",
        tenant_id="contract-tenant",
        playbook_steps=playbook_steps,
        attachments=attachments,
    )

    # 2. Test GET /api/v1/knowledge/articles response shape
    resp = client.get("/api/v1/knowledge/articles", params={"tenant_id": "contract-tenant"})
    assert resp.status_code == 200
    data = resp.json()
    assert "items" in data
    assert "total_count" in data
    assert "page" in data
    assert "limit" in data
    assert "total_pages" in data
    assert data["total_count"] == 1
    assert data["items"][0]["id"] == str(art.id)

    # 3. Test GET /api/v1/knowledge/articles/{id} response shape
    resp = client.get(f"/api/v1/knowledge/articles/{art.id}", params={"tenant_id": "contract-tenant"})
    assert resp.status_code == 200
    detail = resp.json()
    assert detail["id"] == str(art.id)
    assert detail["title"] == "Contract Execution Playbook"
    assert len(detail["playbook_steps"]) == 2
    assert len(detail["attachments"]) == 1

    step_1_id = detail["playbook_steps"][0]["id"]

    # 4. Test POST /api/v1/knowledge/playbooks/{id}/steps/{step_id}/progress response shape
    progress_payload = {"is_completed": True, "user_id": "user-789", "tenant_id": "contract-tenant"}
    resp = client.post(
        f"/api/v1/knowledge/playbooks/{art.id}/steps/{step_1_id}/progress",
        json=progress_payload,
    )
    assert resp.status_code == 200
    step_resp = resp.json()
    assert step_resp["step_id"] == step_1_id
    assert step_resp["is_completed"] is True
    assert step_resp["completed_steps"] == 1
    assert step_resp["total_steps"] == 2
    assert step_resp["overall_playbook_progress_percentage"] == 50.0

    # 5. Test GET /api/v1/knowledge/playbooks/{id}/progress response shape
    resp = client.get(
        f"/api/v1/knowledge/playbooks/{art.id}/progress",
        params={"user_id": "user-789", "tenant_id": "contract-tenant"},
    )
    assert resp.status_code == 200
    playbook_resp = resp.json()
    assert playbook_resp["article_id"] == str(art.id)
    assert playbook_resp["total_steps"] == 2
    assert playbook_resp["completed_steps"] == 1
    assert playbook_resp["progress_percentage"] == 50.0
    assert len(playbook_resp["step_progress"]) == 2

    # 6. Test DELETE /api/v1/knowledge/articles/{id} 204 No Content shape
    resp = client.delete(f"/api/v1/knowledge/articles/{art.id}", params={"tenant_id": "contract-tenant"})
    assert resp.status_code == 204
    assert resp.content == b""
