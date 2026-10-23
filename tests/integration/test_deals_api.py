"""Integration tests for FastAPI deal pipeline and stage transition endpoints in backend/src/api/deals.py [FR-001, FR-002, FR-003, NFR-003]."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.main import app
from src.models.database import Base, get_db

# Set up in-memory SQLite database for testing API endpoints
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Dependency override providing isolated SQLite test session."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_database():
    """Fixture ensuring fresh database tables for each test case."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


client = TestClient(app)


def test_get_pipeline_deals_empty():
    """Verify GET /api/v1/deals/pipeline returns empty array when no deals exist."""
    response = client.get("/api/v1/deals/pipeline")
    assert response.status_code == 200
    assert response.json() == []


def test_create_deal_and_get_pipeline():
    """Verify POST /api/v1/deals creates a deal and GET /api/v1/deals/pipeline retrieves it [FR-001, FR-002, NFR-003]."""
    payload = {
        "title": "  Enterprise License Renewal  ",
        "deal_value": 120000.0,
        "milestone_stage": "  Solution Proposal  ",
        "win_probability": 0.60,
    }
    response = client.post("/api/v1/deals", json=payload)
    assert response.status_code == 201

    data = response.json()
    assert data["id"] is not None
    assert data["title"] == "Enterprise License Renewal"  # Sanitized whitespace
    assert data["deal_value"] == 120000.0
    assert data["milestone_stage"] == "Solution Proposal"  # Sanitized whitespace
    assert data["win_probability"] == 0.60
    assert "age_in_days" in data
    assert data["age_in_days"] == 0

    deal_id = data["id"]

    # Retrieve pipeline
    pipeline_res = client.get("/api/v1/deals/pipeline")
    assert pipeline_res.status_code == 200
    pipeline_data = pipeline_res.json()
    assert len(pipeline_data) == 1
    assert pipeline_data[0]["id"] == deal_id
    assert pipeline_data[0]["title"] == "Enterprise License Renewal"


def test_create_deal_validation_errors():
    """Verify POST /api/v1/deals returns 422 for invalid payloads [NFR-003]."""
    # Empty title
    response1 = client.post(
        "/api/v1/deals",
        json={
            "title": "   ",
            "deal_value": 50000.0,
            "milestone_stage": "Discovery",
        },
    )
    assert response1.status_code == 422

    # Negative deal value
    response2 = client.post(
        "/api/v1/deals",
        json={
            "title": "Valid Title",
            "deal_value": -500.0,
            "milestone_stage": "Discovery",
        },
    )
    assert response2.status_code == 422

    # Invalid win probability (> 1.0)
    response3 = client.post(
        "/api/v1/deals",
        json={
            "title": "Valid Title",
            "deal_value": 50000.0,
            "milestone_stage": "Discovery",
            "win_probability": 1.5,
        },
    )
    assert response3.status_code == 422


def test_filter_deals_by_stage():
    """Verify GET /api/v1/deals/pipeline supports filtering by milestone stage [FR-001]."""
    client.post(
        "/api/v1/deals",
        json={"title": "Deal 1", "deal_value": 10000.0, "milestone_stage": "Discovery"},
    )
    client.post(
        "/api/v1/deals",
        json={"title": "Deal 2", "deal_value": 20000.0, "milestone_stage": "Negotiation"},
    )

    # Filter for Discovery
    res1 = client.get("/api/v1/deals/pipeline?stage=Discovery")
    assert res1.status_code == 200
    data1 = res1.json()
    assert len(data1) == 1
    assert data1[0]["title"] == "Deal 1"

    # Filter for Negotiation
    res2 = client.get("/api/v1/deals/pipeline?stage=Negotiation")
    assert res2.status_code == 200
    data2 = res2.json()
    assert len(data2) == 1
    assert data2[0]["title"] == "Deal 2"


def test_get_deal_summary_success_and_not_found():
    """Verify GET /api/v1/deals/{deal_id} returns deal details or 404 Not Found [FR-001, FR-002]."""
    # Non-existent deal
    res_not_found = client.get("/api/v1/deals/non-existent-uuid")
    assert res_not_found.status_code == 404
    assert "not found" in res_not_found.json()["detail"].lower()

    # Existing deal
    create_res = client.post(
        "/api/v1/deals",
        json={"title": "Summary Test Deal", "deal_value": 30000.0, "milestone_stage": "Proposal"},
    )
    deal_id = create_res.json()["id"]

    summary_res = client.get(f"/api/v1/deals/{deal_id}")
    assert summary_res.status_code == 200
    summary_data = summary_res.json()
    assert summary_data["id"] == deal_id
    assert summary_data["title"] == "Summary Test Deal"
    assert "action_items_count" in summary_data
    assert "stakeholders_count" in summary_data


def test_update_deal_stage_success():
    """Verify PATCH /api/v1/deals/{deal_id}/stage updates stage progression [FR-003, NFR-003]."""
    create_res = client.post(
        "/api/v1/deals",
        json={"title": "Stage Update Test", "deal_value": 50000.0, "milestone_stage": "Discovery"},
    )
    deal_id = create_res.json()["id"]

    # Update stage to Negotiation
    patch_res = client.patch(
        f"/api/v1/deals/{deal_id}/stage",
        json={"milestone_stage": "Contract Negotiation"},
    )
    assert patch_res.status_code == 200
    updated_data = patch_res.json()
    assert updated_data["id"] == deal_id
    assert updated_data["milestone_stage"] == "Contract Negotiation"
    assert updated_data["win_probability"] == 0.80  # Calculated probability for Contract Negotiation


def test_update_deal_stage_errors():
    """Verify PATCH /api/v1/deals/{deal_id}/stage handles 404 and 422 error cases [FR-003, NFR-003]."""
    # 404 for missing deal
    patch_404 = client.patch(
        "/api/v1/deals/missing-id/stage",
        json={"milestone_stage": "Negotiation"},
    )
    assert patch_404.status_code == 404

    # 422 for blank milestone stage
    create_res = client.post(
        "/api/v1/deals",
        json={"title": "Stage Error Test", "deal_value": 50000.0, "milestone_stage": "Discovery"},
    )
    deal_id = create_res.json()["id"]

    patch_422 = client.patch(
        f"/api/v1/deals/{deal_id}/stage",
        json={"milestone_stage": "    "},
    )
    assert patch_422.status_code == 422


def test_action_item_crud_endpoints():
    """Verify action item creation, listing, completion, and deletion endpoints [FR-004, FR-005, NFR-003]."""
    # 1. Create a deal first
    deal_res = client.post(
        "/api/v1/deals",
        json={"title": "Action Item Deal", "deal_value": 75000.0, "milestone_stage": "Discovery"},
    )
    assert deal_res.status_code == 201
    deal_id = deal_res.json()["id"]

    # 2. Try creating action item for missing deal -> 404
    create_404 = client.post(
        "/api/v1/deals/missing-deal-id/action-items",
        json={
            "title": "Prepare Proposal",
            "assigned_owner_id": "owner_123",
            "target_due_date": "2026-11-01T12:00:00Z",
        },
    )
    assert create_404.status_code == 404

    # 3. Create action item with invalid empty title -> 422
    create_422 = client.post(
        f"/api/v1/deals/{deal_id}/action-items",
        json={
            "title": "   ",
            "assigned_owner_id": "owner_123",
            "target_due_date": "2026-11-01T12:00:00Z",
        },
    )
    assert create_422.status_code == 422

    # 4. Create action item successfully -> 201
    create_res = client.post(
        f"/api/v1/deals/{deal_id}/action-items",
        json={
            "title": "  Schedule Technical Discovery  ",
            "assigned_owner_id": "sales_rep_1",
            "target_due_date": "2026-10-15T10:00:00Z",
            "sla_threshold_hours": 24,
        },
    )
    assert create_res.status_code == 201
    item_data = create_res.json()
    assert item_data["id"] is not None
    assert item_data["deal_id"] == deal_id
    assert item_data["title"] == "Schedule Technical Discovery"  # Whitespace trimmed
    assert item_data["assigned_owner_id"] == "sales_rep_1"
    assert item_data["is_completed"] is False
    assert item_data["is_escalated"] is False
    item_id = item_data["id"]

    # 5. List action items for deal -> 200
    list_res = client.get(f"/api/v1/deals/{deal_id}/action-items")
    assert list_res.status_code == 200
    items_list = list_res.json()
    assert len(items_list) == 1
    assert items_list[0]["id"] == item_id

    # 6. Complete action item -> 200
    complete_res = client.patch(
        f"/api/v1/deals/{deal_id}/action-items/{item_id}/complete",
        json={},
    )
    assert complete_res.status_code == 200
    completed_data = complete_res.json()
    assert completed_data["id"] == item_id
    assert completed_data["is_completed"] is True
    assert completed_data["is_escalated"] is False
    assert completed_data["completed_on"] is not None

    # 7. Complete non-existent action item -> 404
    complete_404 = client.patch(
        f"/api/v1/deals/{deal_id}/action-items/missing-item-id/complete",
        json={},
    )
    assert complete_404.status_code == 404

    # 8. Delete action item -> 204 No Content
    delete_res = client.delete(f"/api/v1/deals/{deal_id}/action-items/{item_id}")
    assert delete_res.status_code == 204
    assert delete_res.text == ""  # Empty body

    # 9. List action items after deletion -> 200 (empty list)
    list_after_res = client.get(f"/api/v1/deals/{deal_id}/action-items")
    assert list_after_res.status_code == 200
    assert len(list_after_res.json()) == 0

    # 10. Delete non-existent action item -> 404
    delete_404 = client.delete(f"/api/v1/deals/{deal_id}/action-items/{item_id}")
    assert delete_404.status_code == 404


def test_stakeholder_management_and_activity_feed_endpoints():
    """Verify stakeholder creation, list/feed retrieval, and activity logging endpoints [FR-006, FR-007, NFR-003]."""
    # 1. Create a deal first
    deal_res = client.post(
        "/api/v1/deals",
        json={"title": "Stakeholder Test Deal", "deal_value": 150000.0, "milestone_stage": "Proposal"},
    )
    assert deal_res.status_code == 201
    deal_id = deal_res.json()["id"]

    # 2. Get stakeholders for missing deal -> 404
    get_404 = client.get("/api/v1/deals/missing-deal-id/stakeholders")
    assert get_404.status_code == 404

    # 3. Create stakeholder for missing deal -> 404
    post_404 = client.post(
        "/api/v1/deals/missing-deal-id/stakeholders",
        json={
            "full_name": "Jane Doe",
            "email": "jane.doe@acme.com",
            "org_role": "VP Engineering",
            "influence_level": "High",
        },
    )
    assert post_404.status_code == 404

    # 4. Create stakeholder with invalid payload -> 422
    invalid_res = client.post(
        f"/api/v1/deals/{deal_id}/stakeholders",
        json={
            "full_name": "   ",
            "email": "not-an-email",
            "org_role": "VP",
            "influence_level": "High",
        },
    )
    assert invalid_res.status_code == 422

    # 5. Create stakeholder successfully -> 201
    create_stk_res = client.post(
        f"/api/v1/deals/{deal_id}/stakeholders",
        json={
            "full_name": "  Jane Doe  ",
            "email": "  JANE.DOE@ACME.COM  ",
            "org_role": "  VP of Engineering  ",
            "influence_level": "  High  ",
        },
    )
    assert create_stk_res.status_code == 201
    stk_data = create_stk_res.json()
    assert stk_data["id"] is not None
    assert stk_data["deal_id"] == deal_id
    assert stk_data["full_name"] == "Jane Doe"  # Sanitized whitespace
    assert stk_data["email"] == "jane.doe@acme.com"  # Lowercased and sanitized
    assert stk_data["org_role"] == "VP of Engineering"
    assert stk_data["influence_level"] == "High"
    stk_id = stk_data["id"]

    # 6. Retrieve stakeholders and feed -> 200 (1 stakeholder, 0 activities)
    feed_res1 = client.get(f"/api/v1/deals/{deal_id}/stakeholders")
    assert feed_res1.status_code == 200
    feed_data1 = feed_res1.json()
    assert "stakeholders" in feed_data1
    assert "recent_activities" in feed_data1
    assert len(feed_data1["stakeholders"]) == 1
    assert feed_data1["stakeholders"][0]["id"] == stk_id
    assert len(feed_data1["recent_activities"]) == 0

    # 7. Log activity for stakeholder -> 201
    activity_res = client.post(
        f"/api/v1/deals/{deal_id}/stakeholders/{stk_id}/activities",
        json={
            "activity_type": "Meeting",
            "summary": "Initial technical architecture review with engineering team.",
        },
    )
    assert activity_res.status_code == 201
    act_data = activity_res.json()
    assert act_data["id"] is not None
    assert act_data["stakeholder_id"] == stk_id
    assert act_data["deal_id"] == deal_id
    assert act_data["activity_type"] == "Meeting"
    assert act_data["summary"] == "Initial technical architecture review with engineering team."
    assert act_data["stakeholder_name"] == "Jane Doe"

    # 8. Retrieve stakeholders and feed -> 200 (1 stakeholder, 1 activity)
    feed_res2 = client.get(f"/api/v1/deals/{deal_id}/stakeholders")
    assert feed_res2.status_code == 200
    feed_data2 = feed_res2.json()
    assert len(feed_data2["recent_activities"]) == 1
    act_feed_item = feed_data2["recent_activities"][0]
    assert act_feed_item["id"] == act_data["id"]
    assert act_feed_item["activity_type"] == "Meeting"
    assert act_feed_item["stakeholder_name"] == "Jane Doe"


def test_ai_forecast_endpoints():
    """Verify AI completion forecast, recalculate, and evaluation log endpoints [FR-008, FR-009, NFR-003]."""
    # 1. Try forecast for missing deal -> 404
    get_404 = client.get("/api/v1/deals/missing-deal-id/forecast")
    assert get_404.status_code == 404

    recalc_404 = client.post("/api/v1/deals/missing-deal-id/forecast/recalculate")
    assert recalc_404.status_code == 404

    logs_404 = client.get("/api/v1/deals/missing-deal-id/forecast/logs")
    assert logs_404.status_code == 404

    # 2. Create a deal
    deal_res = client.post(
        "/api/v1/deals",
        json={"title": "Forecast Test Deal", "deal_value": 250000.0, "milestone_stage": "Negotiation"},
    )
    assert deal_res.status_code == 201
    deal_id = deal_res.json()["id"]

    # 3. GET forecast for existing deal (triggers initial baseline calculation) -> 200
    forecast_res = client.get(f"/api/v1/deals/{deal_id}/forecast")
    assert forecast_res.status_code == 200
    forecast_data = forecast_res.json()
    assert forecast_data["deal_id"] == deal_id
    assert "status_code" in forecast_data
    assert "confidence_score_pct" in forecast_data
    assert "estimated_completion_date" in forecast_data
    assert "calculated_on" in forecast_data

    # 4. Recalculate forecast via POST /recalculate -> 200
    recalc_res = client.post(f"/api/v1/deals/{deal_id}/forecast/recalculate")
    assert recalc_res.status_code == 200
    recalc_data = recalc_res.json()
    assert recalc_data["deal_id"] == deal_id
    assert "status_code" in recalc_data

    # 5. Recalculate forecast via POST alias -> 200
    post_alias_res = client.post(f"/api/v1/deals/{deal_id}/forecast")
    assert post_alias_res.status_code == 200
    assert post_alias_res.json()["deal_id"] == deal_id

    # 6. GET forecast evaluation logs -> 200
    logs_res = client.get(f"/api/v1/deals/{deal_id}/forecast/logs")
    assert logs_res.status_code == 200
    logs_data = logs_res.json()
    assert isinstance(logs_data, list)
    assert len(logs_data) >= 1
    assert logs_data[0]["deal_id"] == deal_id


