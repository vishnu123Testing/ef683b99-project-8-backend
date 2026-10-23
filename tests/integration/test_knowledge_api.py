"""Integration tests for Knowledge Repository REST API endpoints [FR-1, FR-2, FR-7, FR-8, FR-9, NFR-1, NFR-3]."""

import uuid
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.main import app
from src.models.database import Base, get_db
from src.models.knowledge import (
    CONTENT_TYPE_BEST_PRACTICE,
    CONTENT_TYPE_FAQ,
    CONTENT_TYPE_PLAYBOOK,
    CONTENT_TYPE_TRAINING_VIDEO,
    Attachment,
    Category,
    KnowledgeArticle,
    PlaybookStep,
    Tag,
    UserStepProgress,
)
from src.services.knowledge.article_service import (
    create_article,
    create_category,
    create_tag,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for integration testing."""
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
    """Fixture providing FastAPI TestClient wired to the test DB session."""
    def _get_test_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = _get_test_db
    test_client = TestClient(app)
    yield test_client
    app.dependency_overrides.clear()


def test_list_knowledge_articles_empty(client):
    """Test GET /api/v1/knowledge/articles returns empty list when no articles exist."""
    response = client.get("/api/v1/knowledge/articles")
    assert response.status_code == 200
    data = response.json()
    assert data["items"] == []
    assert data["total_count"] == 0
    assert data["page"] == 1
    assert data["limit"] == 20
    assert data["total_pages"] == 0


def test_list_knowledge_articles_search_and_filters(client, db_session):
    """Test searching and filtering knowledge articles via GET /api/v1/knowledge/articles [FR-1, FR-2, FR-7, FR-8, FR-9]."""
    # Seed categories and tags
    cat_faq = create_category(db_session, name="FAQs", description="Frequently Asked Questions")
    cat_playbook = create_category(db_session, name="Playbooks", description="Operational Playbooks")

    tag_sec = create_tag(db_session, name="Security")
    tag_billing = create_tag(db_session, name="Billing")

    # Global FAQ
    art1 = create_article(
        db_session,
        title="Password Reset Instructions",
        content_type=CONTENT_TYPE_FAQ,
        body="How to reset account credentials securely.",
        category_id=cat_faq.id,
        tag_ids=[tag_sec.id],
        tenant_id=None,
    )

    # Tenant-1 Playbook
    art2 = create_article(
        db_session,
        title="Quarterly Billing Reconciliation",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Steps for verifying billing invoices.",
        category_id=cat_playbook.id,
        tag_ids=[tag_billing.id],
        tenant_id="tenant-1",
    )

    # Tenant-2 Best Practice
    art3 = create_article(
        db_session,
        title="Cloud Deployment Guidelines",
        content_type=CONTENT_TYPE_BEST_PRACTICE,
        body="Security best practices for cloud infrastructure.",
        category_id=cat_faq.id,
        tag_ids=[tag_sec.id],
        tenant_id="tenant-2",
    )

    # 1. Tenant-1 search without extra filters (should return art1 and art2)
    resp = client.get("/api/v1/knowledge/articles", params={"tenant_id": "tenant-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 2
    returned_ids = {item["id"] for item in body["items"]}
    assert str(art1.id) in returned_ids
    assert str(art2.id) in returned_ids
    assert str(art3.id) not in returned_ids

    # 2. Keyword query search for 'billing'
    resp = client.get("/api/v1/knowledge/articles", params={"query": "billing", "tenant_id": "tenant-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 1
    assert body["items"][0]["id"] == str(art2.id)

    # 3. Filter by content_type='Playbook'
    resp = client.get("/api/v1/knowledge/articles", params={"content_type": "Playbook", "tenant_id": "tenant-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 1
    assert body["items"][0]["id"] == str(art2.id)

    # 4. Filter by category_id
    resp = client.get("/api/v1/knowledge/articles", params={"category_id": str(cat_faq.id), "tenant_id": "tenant-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 1
    assert body["items"][0]["id"] == str(art1.id)

    # 5. Filter by tag_ids (comma-separated or list)
    resp = client.get("/api/v1/knowledge/articles", params={"tag_ids": f"{tag_billing.id}", "tenant_id": "tenant-1"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["total_count"] == 1
    assert body["items"][0]["id"] == str(art2.id)


def test_list_knowledge_articles_validation_errors(client):
    """Test query parameter validation errors on GET /api/v1/knowledge/articles [FR-8, NFR-3]."""
    # 1. Invalid content_type
    resp = client.get("/api/v1/knowledge/articles", params={"content_type": "InvalidType"})
    assert resp.status_code == 422

    # 2. Invalid category_id UUID format
    resp = client.get("/api/v1/knowledge/articles", params={"category_id": "not-a-valid-uuid"})
    assert resp.status_code == 400
    assert "Invalid UUID format" in resp.json()["detail"]

    # 3. Invalid page index (< 1)
    resp = client.get("/api/v1/knowledge/articles", params={"page": 0})
    assert resp.status_code == 422

    # 4. Invalid limit (> 100)
    resp = client.get("/api/v1/knowledge/articles", params={"limit": 101})
    assert resp.status_code == 422


def test_get_knowledge_article_detail_success(client, db_session):
    """Test retrieving article detail by ID via GET /api/v1/knowledge/articles/{id} [FR-1, FR-3, FR-5, FR-6, FR-9]."""
    cat = create_category(db_session, name="Onboarding", description="Onboarding resources")
    tag = create_tag(db_session, name="Setup")

    playbook_steps = [
        {"step_number": 1, "title": "Initial Setup", "content": "Configure environment variables.", "checkpoint_instructions": "Verify .env exists."},
        {"step_number": 2, "title": "Run Migrations", "content": "Initialize schema tables.", "checkpoint_instructions": "Check DB tables."},
    ]
    attachments = [
        {"file_name": "guide.pdf", "file_url": "https://cdn.example.com/guide.pdf", "file_size_bytes": 1024576}
    ]

    art = create_article(
        db_session,
        title="Developer Onboarding Playbook",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Comprehensive guide to getting started.",
        category_id=cat.id,
        tag_ids=[tag.id],
        video_url="https://video.example.com/stream/123",
        tenant_id="tenant-1",
        playbook_steps=playbook_steps,
        attachments=attachments,
    )

    resp = client.get(f"/api/v1/knowledge/articles/{art.id}", params={"tenant_id": "tenant-1"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == str(art.id)
    assert data["title"] == "Developer Onboarding Playbook"
    assert data["content_type"] == CONTENT_TYPE_PLAYBOOK
    assert data["tenant_id"] == "tenant-1"
    assert data["video_url"] == "https://video.example.com/stream/123"
    assert data["category"]["id"] == str(cat.id)
    assert len(data["tags"]) == 1
    assert data["tags"][0]["name"] == "Setup"
    assert len(data["playbook_steps"]) == 2
    assert data["playbook_steps"][0]["step_number"] == 1
    assert data["playbook_steps"][0]["title"] == "Initial Setup"
    assert len(data["attachments"]) == 1
    assert data["attachments"][0]["file_name"] == "guide.pdf"


def test_get_knowledge_article_detail_multi_tenant_and_global(client, db_session):
    """Test multi-tenant isolation and global article access on GET /api/v1/knowledge/articles/{id} [FR-9]."""
    # Global article (tenant_id=None)
    art_global = create_article(
        db_session,
        title="Global FAQ",
        content_type=CONTENT_TYPE_FAQ,
        body="Global knowledge base item.",
        tenant_id=None,
    )

    # Tenant-2 private article
    art_t2 = create_article(
        db_session,
        title="Tenant 2 Private Strategy",
        content_type=CONTENT_TYPE_BEST_PRACTICE,
        body="Confidential internal document.",
        tenant_id="tenant-2",
    )

    # Tenant-1 accessing global article -> Should succeed
    resp = client.get(f"/api/v1/knowledge/articles/{art_global.id}", params={"tenant_id": "tenant-1"})
    assert resp.status_code == 200
    assert resp.json()["id"] == str(art_global.id)

    # Tenant-1 attempting to access Tenant-2 article -> Should fail with 404
    resp = client.get(f"/api/v1/knowledge/articles/{art_t2.id}", params={"tenant_id": "tenant-1"})
    assert resp.status_code == 404

    # Tenant-2 accessing its own article -> Should succeed
    resp = client.get(f"/api/v1/knowledge/articles/{art_t2.id}", params={"tenant_id": "tenant-2"})
    assert resp.status_code == 200


def test_get_knowledge_article_detail_not_found_and_invalid(client):
    """Test 404 Not Found and 400 Bad Request error handling for article detail endpoint [FR-8, NFR-3]."""
    # Non-existent article ID
    random_uuid = str(uuid.uuid4())
    resp = client.get(f"/api/v1/knowledge/articles/{random_uuid}")
    assert resp.status_code == 404

    # Invalid UUID format
    resp = client.get("/api/v1/knowledge/articles/invalid-uuid-format")
    assert resp.status_code == 400
    assert "Invalid UUID format" in resp.json()["detail"]


def test_toggle_playbook_step_progress_success(client, db_session):
    """Test POST /api/v1/knowledge/playbooks/{id}/steps/{step_id}/progress toggling step progress [FR-3, FR-4, FR-8, FR-9]."""
    cat = create_category(db_session, name="Operations Playbooks")
    playbook = create_article(
        db_session,
        title="Deployment Verification Playbook",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Steps to verify service deployments.",
        category_id=cat.id,
        tenant_id="tenant-prod",
        playbook_steps=[
            {
                "step_number": 1,
                "title": "Check Health Endpoint",
                "content": "Curl HTTP /healthz and verify status code 200.",
            },
            {
                "step_number": 2,
                "title": "Inspect Error Logs",
                "content": "Verify zero 500 error logs in stdout stream.",
            },
        ],
    )

    # Get article detail to fetch created playbook step IDs
    resp = client.get(f"/api/v1/knowledge/articles/{playbook.id}", params={"tenant_id": "tenant-prod"})
    assert resp.status_code == 200
    steps = resp.json()["playbook_steps"]
    assert len(steps) == 2
    step1_id = steps[0]["id"]
    step2_id = steps[1]["id"]

    # Toggle Step 1 to completed
    progress_url = f"/api/v1/knowledge/playbooks/{playbook.id}/steps/{step1_id}/progress"
    payload = {
        "is_completed": True,
        "user_id": "user-eng-1",
        "tenant_id": "tenant-prod",
    }
    resp = client.post(progress_url, json=payload)
    assert resp.status_code == 200
    data = resp.json()
    assert data["step_id"] == step1_id
    assert data["article_id"] == str(playbook.id)
    assert data["user_id"] == "user-eng-1"
    assert data["tenant_id"] == "tenant-prod"
    assert data["is_completed"] is True
    assert data["overall_playbook_progress_percentage"] == 50.0
    assert data["total_steps"] == 2
    assert data["completed_steps"] == 1
    assert data["completed_at"] is not None

    # Toggle Step 2 to completed
    progress_url_2 = f"/api/v1/knowledge/playbooks/{playbook.id}/steps/{step2_id}/progress"
    resp = client.post(progress_url_2, json=payload)
    assert resp.status_code == 200
    data2 = resp.json()
    assert data2["is_completed"] is True
    assert data2["overall_playbook_progress_percentage"] == 100.0
    assert data2["completed_steps"] == 2

    # Toggle Step 1 back to uncompleted
    payload_uncomplete = {
        "is_completed": False,
        "user_id": "user-eng-1",
        "tenant_id": "tenant-prod",
    }
    resp = client.post(progress_url, json=payload_uncomplete)
    assert resp.status_code == 200
    data_uncomplete = resp.json()
    assert data_uncomplete["is_completed"] is False
    assert data_uncomplete["overall_playbook_progress_percentage"] == 50.0
    assert data_uncomplete["completed_steps"] == 1


def test_toggle_playbook_step_progress_errors(client, db_session):
    """Test error handling for POST /api/v1/knowledge/playbooks/{id}/steps/{step_id}/progress [FR-8, NFR-3]."""
    cat = create_category(db_session, name="Support Playbooks")
    playbook_a = create_article(
        db_session,
        title="Playbook A",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Playbook A content.",
        category_id=cat.id,
        tenant_id="tenant-prod",
        playbook_steps=[{"step_number": 1, "title": "Step 1", "content": "Content 1"}],
    )

    playbook_b = create_article(
        db_session,
        title="Playbook B",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Playbook B content.",
        category_id=cat.id,
        tenant_id="tenant-prod",
        playbook_steps=[{"step_number": 1, "title": "Step B1", "content": "Content B1"}],
    )

    steps_a = client.get(f"/api/v1/knowledge/articles/{playbook_a.id}", params={"tenant_id": "tenant-prod"}).json()["playbook_steps"]
    steps_b = client.get(f"/api/v1/knowledge/articles/{playbook_b.id}", params={"tenant_id": "tenant-prod"}).json()["playbook_steps"]
    step_a_id = steps_a[0]["id"]
    step_b_id = steps_b[0]["id"]

    # 1. Non-existent playbook ID -> 404
    fake_playbook_id = str(uuid.uuid4())
    url_404 = f"/api/v1/knowledge/playbooks/{fake_playbook_id}/steps/{step_a_id}/progress"
    resp = client.post(url_404, json={"is_completed": True, "tenant_id": "tenant-prod"})
    assert resp.status_code == 404

    # 2. Step belonging to Playbook B requested on Playbook A -> 400 Bad Request
    url_bad_step = f"/api/v1/knowledge/playbooks/{playbook_a.id}/steps/{step_b_id}/progress"
    resp = client.post(url_bad_step, json={"is_completed": True, "tenant_id": "tenant-prod"})
    assert resp.status_code == 400
    assert "does not belong to playbook" in resp.json()["detail"]


def test_get_playbook_user_overall_progress_success(client, db_session):
    """Test GET /api/v1/knowledge/playbooks/{id}/progress returns overall progress breakdown [FR-3, FR-4, FR-9, NFR-3]."""
    cat = create_category(db_session, name="DevOps Playbooks")
    playbook = create_article(
        db_session,
        title="Deployment Verification Guide",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Steps to verify production deployment.",
        category_id=cat.id,
        tenant_id="tenant-alpha",
        playbook_steps=[
            {
                "step_number": 1,
                "title": "Check Health Endpoint",
                "content": "Verify GET /healthz returns 200 OK.",
            },
            {
                "step_number": 2,
                "title": "Run Smoke Tests",
                "content": "Execute automated smoke test suite.",
            },
        ],
    )

    progress_url = f"/api/v1/knowledge/playbooks/{playbook.id}/progress"
    params = {"user_id": "user-dev-1", "tenant_id": "tenant-alpha"}

    # Initial progress - 0% completed
    resp = client.get(progress_url, params=params)
    assert resp.status_code == 200
    data = resp.json()
    assert data["article_id"] == str(playbook.id)
    assert data["user_id"] == "user-dev-1"
    assert data["tenant_id"] == "tenant-alpha"
    assert data["total_steps"] == 2
    assert data["completed_steps"] == 0
    assert data["progress_percentage"] == 0.0
    assert data["completed_step_ids"] == []
    assert len(data["step_progress"]) == 2
    assert data["step_progress"][0]["is_completed"] is False
    assert data["step_progress"][1]["is_completed"] is False

    # Get step ID and toggle step 1 to completed
    article_detail = client.get(f"/api/v1/knowledge/articles/{playbook.id}", params={"tenant_id": "tenant-alpha"}).json()
    step1_id = article_detail["playbook_steps"][0]["id"]

    toggle_url = f"/api/v1/knowledge/playbooks/{playbook.id}/steps/{step1_id}/progress"
    toggle_resp = client.post(
        toggle_url,
        json={"is_completed": True, "user_id": "user-dev-1", "tenant_id": "tenant-alpha"},
    )
    assert toggle_resp.status_code == 200

    # Progress after completing step 1 - 50% completed
    resp2 = client.get(progress_url, params=params)
    assert resp2.status_code == 200
    data2 = resp2.json()
    assert data2["total_steps"] == 2
    assert data2["completed_steps"] == 1
    assert data2["progress_percentage"] == 50.0
    assert data2["completed_step_ids"] == [step1_id]
    assert data2["step_progress"][0]["is_completed"] is True
    assert data2["step_progress"][0]["completed_at"] is not None
    assert data2["step_progress"][1]["is_completed"] is False


def test_get_playbook_user_overall_progress_errors(client, db_session):
    """Test error conditions for GET /api/v1/knowledge/playbooks/{id}/progress [FR-8, FR-9, NFR-3]."""
    cat = create_category(db_session, name="Security Playbooks")
    playbook = create_article(
        db_session,
        title="Incident Response Plan",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Steps for security incident response.",
        category_id=cat.id,
        tenant_id="tenant-sec",
        playbook_steps=[{"step_number": 1, "title": "Isolate Node", "content": "Disconnect node from network."}],
    )

    # 1. Non-existent playbook ID -> 404
    fake_id = str(uuid.uuid4())
    resp = client.get(f"/api/v1/knowledge/playbooks/{fake_id}/progress", params={"tenant_id": "tenant-sec"})
    assert resp.status_code == 404

    # 2. Inaccessible tenant -> 404
    resp_tenant = client.get(f"/api/v1/knowledge/playbooks/{playbook.id}/progress", params={"tenant_id": "tenant-other"})
    assert resp_tenant.status_code == 404

    # 3. Invalid UUID format -> 400
    resp_bad_uuid = client.get("/api/v1/knowledge/playbooks/invalid-uuid-str/progress")
    assert resp_bad_uuid.status_code == 400
    assert "Invalid UUID format" in resp_bad_uuid.json()["detail"]


def test_delete_knowledge_article_success(client, db_session):
    """Test DELETE /api/v1/knowledge/articles/{id} successfully deletes article resource and returns 204 No Content [FR-1, FR-9]."""
    cat = create_category(db_session, name="Deprecation Guides")
    art = create_article(
        db_session,
        title="Legacy API Migration",
        content_type=CONTENT_TYPE_FAQ,
        body="Instructions for migrating away from v0 endpoints.",
        category_id=cat.id,
        tenant_id="tenant-del",
    )

    # Verify article exists
    get_resp = client.get(f"/api/v1/knowledge/articles/{art.id}", params={"tenant_id": "tenant-del"})
    assert get_resp.status_code == 200

    # Execute DELETE request
    del_resp = client.delete(f"/api/v1/knowledge/articles/{art.id}", params={"tenant_id": "tenant-del"})
    assert del_resp.status_code == 204
    assert del_resp.content == b""

    # Subsequent GET should return 404
    get_after_del = client.get(f"/api/v1/knowledge/articles/{art.id}", params={"tenant_id": "tenant-del"})
    assert get_after_del.status_code == 404


def test_delete_knowledge_article_errors_and_tenant_isolation(client, db_session):
    """Test error handling and tenant boundary checks on DELETE /api/v1/knowledge/articles/{id} [FR-9, NFR-3]."""
    cat = create_category(db_session, name="Confidential Docs")
    art_t1 = create_article(
        db_session,
        title="Tenant 1 Private Article",
        content_type=CONTENT_TYPE_BEST_PRACTICE,
        body="Confidential content for Tenant 1.",
        category_id=cat.id,
        tenant_id="tenant-1",
    )

    # 1. Attempting to delete Tenant 1 article as Tenant 2 -> 404 Not Found
    del_t2_resp = client.delete(f"/api/v1/knowledge/articles/{art_t1.id}", params={"tenant_id": "tenant-2"})
    assert del_t2_resp.status_code == 404

    # Verify article still exists for Tenant 1
    get_t1 = client.get(f"/api/v1/knowledge/articles/{art_t1.id}", params={"tenant_id": "tenant-1"})
    assert get_t1.status_code == 200

    # 2. Deleting non-existent UUID -> 404 Not Found
    fake_id = str(uuid.uuid4())
    del_fake_resp = client.delete(f"/api/v1/knowledge/articles/{fake_id}")
    assert del_fake_resp.status_code == 404

    # 3. Invalid UUID string format -> 400 Bad Request
    del_bad_uuid_resp = client.delete("/api/v1/knowledge/articles/not-a-valid-uuid")
    assert del_bad_uuid_resp.status_code == 400
    assert "Invalid UUID format" in del_bad_uuid_resp.json()["detail"]


def test_knowledge_api_logging_and_correlation_header(client):
    """Test correlation ID propagation and header inclusion across knowledge endpoints [NFR-4]."""
    custom_correlation_id = "test-corr-knowledge-999"
    headers = {"X-Correlation-ID": custom_correlation_id}

    response = client.get("/api/v1/knowledge/articles", headers=headers)
    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID") == custom_correlation_id


def test_knowledge_api_domain_service_errors(client, db_session):
    """Test domain exception handling resulting in HTTP 500 responses [NFR-4]."""
    from unittest.mock import patch
    from src.services.knowledge.article_service import ArticleServiceError
    from src.services.knowledge.playbook_service import PlaybookServiceError

    with patch("src.api.knowledge.search_articles", side_effect=ArticleServiceError("Database connectivity failed")):
        resp = client.get("/api/v1/knowledge/articles")
        assert resp.status_code == 500
        assert "searching knowledge articles" in resp.json()["detail"]

    with patch("src.api.knowledge.get_article_by_id", side_effect=ArticleServiceError("Detail retrieval failed")):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/knowledge/articles/{fake_id}")
        assert resp.status_code == 500
        assert "retrieving knowledge article detail" in resp.json()["detail"]

    with patch("src.api.knowledge.toggle_step_progress", side_effect=PlaybookServiceError("Step progress update failed")):
        fake_id = str(uuid.uuid4())
        fake_step_id = str(uuid.uuid4())
        resp = client.post(f"/api/v1/knowledge/playbooks/{fake_id}/steps/{fake_step_id}/progress", json={"is_completed": True})
        assert resp.status_code == 500
        assert "updating playbook step progress" in resp.json()["detail"]

    with patch("src.api.knowledge.get_playbook_progress", side_effect=PlaybookServiceError("Progress query failed")):
        fake_id = str(uuid.uuid4())
        resp = client.get(f"/api/v1/knowledge/playbooks/{fake_id}/progress")
        assert resp.status_code == 500
        assert "retrieving playbook progress" in resp.json()["detail"]

    with patch("src.api.knowledge.delete_article", side_effect=ArticleServiceError("Delete operation failed")):
        fake_id = str(uuid.uuid4())
        resp = client.delete(f"/api/v1/knowledge/articles/{fake_id}")
        assert resp.status_code == 500
        assert "deleting the knowledge article" in resp.json()["detail"]


def test_sql_persistence_step_progress_toggle_and_database_state(client, db_session):
    """Test SQL persistence in database tables when toggling playbook step progress [FR-3, FR-4, FR-9]."""
    cat = create_category(db_session, name="SQL Test Category")
    art = create_article(
        db_session,
        title="SQL Persistence Playbook",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Body content for SQL persistence test.",
        category_id=cat.id,
        tenant_id="tenant-sql-1",
        playbook_steps=[
            {"step_number": 1, "title": "Step 1 Title", "content": "Step 1 Content"},
            {"step_number": 2, "title": "Step 2 Title", "content": "Step 2 Content"},
        ],
    )

    # Fetch playbook step IDs via API
    detail_resp = client.get(f"/api/v1/knowledge/articles/{art.id}", params={"tenant_id": "tenant-sql-1"})
    assert detail_resp.status_code == 200
    steps = detail_resp.json()["playbook_steps"]
    step1_id = uuid.UUID(steps[0]["id"])
    step2_id = uuid.UUID(steps[1]["id"])

    # Toggle Step 1 to completed via API
    progress_url = f"/api/v1/knowledge/playbooks/{art.id}/steps/{step1_id}/progress"
    toggle_resp = client.post(
        progress_url,
        json={"is_completed": True, "user_id": "user-sql-1", "tenant_id": "tenant-sql-1"},
    )
    assert toggle_resp.status_code == 200

    # Query SQL database directly to verify record was persisted in user_step_progress table
    db_progress = (
        db_session.query(UserStepProgress)
        .filter_by(user_id="user-sql-1", step_id=step1_id)
        .first()
    )
    assert db_progress is not None
    assert db_progress.is_completed is True
    assert db_progress.tenant_id == "tenant-sql-1"
    assert db_progress.completed_at is not None

    # Step 2 should not exist in DB yet
    db_progress2 = (
        db_session.query(UserStepProgress)
        .filter_by(user_id="user-sql-1", step_id=step2_id)
        .first()
    )
    assert db_progress2 is None

    # Toggle Step 1 to uncompleted via API
    toggle_off_resp = client.post(
        progress_url,
        json={"is_completed": False, "user_id": "user-sql-1", "tenant_id": "tenant-sql-1"},
    )
    assert toggle_off_resp.status_code == 200

    # Query SQL database directly to verify updated state
    db_session.expire_all()
    db_progress_updated = (
        db_session.query(UserStepProgress)
        .filter_by(user_id="user-sql-1", step_id=step1_id)
        .first()
    )
    assert db_progress_updated is not None
    assert db_progress_updated.is_completed is False


def test_sql_persistence_article_deletion_cascade(client, db_session):
    """Test SQL persistence and ON DELETE CASCADE behavior when deleting an article [FR-1, FR-9]."""
    cat = create_category(db_session, name="Cascade Test Category")
    art = create_article(
        db_session,
        title="Cascade Article",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Content for cascade deletion test.",
        category_id=cat.id,
        tenant_id="tenant-del-cascade",
        playbook_steps=[{"step_number": 1, "title": "Step 1", "content": "Instruction 1"}],
        attachments=[{"file_name": "doc.pdf", "file_url": "https://example.com/doc.pdf", "file_size_bytes": 500}],
    )

    # Get step ID and mark step progress
    steps = client.get(f"/api/v1/knowledge/articles/{art.id}", params={"tenant_id": "tenant-del-cascade"}).json()["playbook_steps"]
    step_id = uuid.UUID(steps[0]["id"])

    client.post(
        f"/api/v1/knowledge/playbooks/{art.id}/steps/{step_id}/progress",
        json={"is_completed": True, "user_id": "user-cascade", "tenant_id": "tenant-del-cascade"},
    )

    # Verify records exist in SQL database
    assert db_session.query(KnowledgeArticle).filter_by(id=art.id).first() is not None
    assert db_session.query(PlaybookStep).filter_by(article_id=art.id).first() is not None
    assert db_session.query(Attachment).filter_by(article_id=art.id).first() is not None
    assert db_session.query(UserStepProgress).filter_by(step_id=step_id).first() is not None

    # Delete article via API
    del_resp = client.delete(f"/api/v1/knowledge/articles/{art.id}", params={"tenant_id": "tenant-del-cascade"})
    assert del_resp.status_code == 204

    # Verify SQL records are removed from database
    db_session.expire_all()
    assert db_session.query(KnowledgeArticle).filter_by(id=art.id).first() is None
    assert db_session.query(PlaybookStep).filter_by(article_id=art.id).all() == []
    assert db_session.query(Attachment).filter_by(article_id=art.id).all() == []
    assert db_session.query(UserStepProgress).filter_by(step_id=step_id).all() == []


def test_all_content_types_and_media_persistence(client, db_session):
    """Test creating, querying, and persisting all 4 content types (FAQ, Playbook, BestPractice, TrainingVideo) [FR-1, FR-5, FR-6, FR-7]."""
    cat = create_category(db_session, name="All Types Category")
    tag = create_tag(db_session, name="MediaTag")

    # FAQ
    art_faq = create_article(
        db_session,
        title="FAQ Item",
        content_type=CONTENT_TYPE_FAQ,
        body="FAQ body text",
        category_id=cat.id,
        tag_ids=[tag.id],
    )

    # Playbook
    art_playbook = create_article(
        db_session,
        title="Playbook Item",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Playbook body text",
        category_id=cat.id,
        tag_ids=[tag.id],
        playbook_steps=[{"step_number": 1, "title": "Playbook Step 1", "content": "Playbook Step Content"}],
    )

    # Best Practice
    art_bp = create_article(
        db_session,
        title="Best Practice Item",
        content_type=CONTENT_TYPE_BEST_PRACTICE,
        body="Best practice body text",
        category_id=cat.id,
        tag_ids=[tag.id],
    )

    # Training Video
    art_video = create_article(
        db_session,
        title="Training Video Module",
        content_type=CONTENT_TYPE_TRAINING_VIDEO,
        body="Video overview body text",
        category_id=cat.id,
        tag_ids=[tag.id],
        video_url="https://video.example.com/embed/999",
        attachments=[{"file_name": "slides.pdf", "file_url": "https://example.com/slides.pdf", "file_size_bytes": 2048}],
    )

    # Verify query for TrainingVideo via API
    resp_v = client.get("/api/v1/knowledge/articles", params={"content_type": "TrainingVideo"})
    assert resp_v.status_code == 200
    data_v = resp_v.json()
    assert data_v["total_count"] == 1
    assert data_v["items"][0]["id"] == str(art_video.id)
    assert data_v["items"][0]["video_url"] == "https://video.example.com/embed/999"

    # Verify query for detail of TrainingVideo
    detail_v = client.get(f"/api/v1/knowledge/articles/{art_video.id}").json()
    assert detail_v["video_url"] == "https://video.example.com/embed/999"
    assert len(detail_v["attachments"]) == 1
    assert detail_v["attachments"][0]["file_name"] == "slides.pdf"

    # Direct SQL assertion for all articles
    assert art_faq.id is not None
    assert art_playbook.id is not None
    assert art_bp.id is not None
    db_articles = db_session.query(KnowledgeArticle).all()
    types_in_db = {a.content_type for a in db_articles}
    assert types_in_db == {CONTENT_TYPE_FAQ, CONTENT_TYPE_PLAYBOOK, CONTENT_TYPE_BEST_PRACTICE, CONTENT_TYPE_TRAINING_VIDEO}


def test_multi_tenant_isolation_unauthenticated_and_scoped_search(client, db_session):
    """Test multi-tenant isolation rules during search and detail queries [FR-9]."""
    cat = create_category(db_session, name="Tenant Test Category")

    # Global resource (tenant_id=None)
    g_art = create_article(db_session, title="Global Resource", content_type=CONTENT_TYPE_FAQ, body="Global content", category_id=cat.id, tenant_id=None)
    # Tenant Alpha resource
    a_art = create_article(db_session, title="Alpha Private Resource", content_type=CONTENT_TYPE_FAQ, body="Alpha content", category_id=cat.id, tenant_id="tenant-alpha")
    # Tenant Beta resource
    b_art = create_article(db_session, title="Beta Private Resource", content_type=CONTENT_TYPE_FAQ, body="Beta content", category_id=cat.id, tenant_id="tenant-beta")

    # 1. Search without tenant_id parameter -> returns ONLY global resources
    resp_no_tenant = client.get("/api/v1/knowledge/articles")
    assert resp_no_tenant.status_code == 200
    ids_no_tenant = {item["id"] for item in resp_no_tenant.json()["items"]}
    assert str(g_art.id) in ids_no_tenant
    assert str(a_art.id) not in ids_no_tenant
    assert str(b_art.id) not in ids_no_tenant

    # 2. Search as Tenant Alpha -> returns Global + Tenant Alpha resources
    resp_alpha = client.get("/api/v1/knowledge/articles", params={"tenant_id": "tenant-alpha"})
    assert resp_alpha.status_code == 200
    ids_alpha = {item["id"] for item in resp_alpha.json()["items"]}
    assert str(g_art.id) in ids_alpha
    assert str(a_art.id) in ids_alpha
    assert str(b_art.id) not in ids_alpha

    # 3. Search as Tenant Beta -> returns Global + Tenant Beta resources
    resp_beta = client.get("/api/v1/knowledge/articles", params={"tenant_id": "tenant-beta"})
    assert resp_beta.status_code == 200
    ids_beta = {item["id"] for item in resp_beta.json()["items"]}
    assert str(g_art.id) in ids_beta
    assert str(a_art.id) not in ids_beta
    assert str(b_art.id) in ids_beta


