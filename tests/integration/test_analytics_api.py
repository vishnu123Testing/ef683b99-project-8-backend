"""Integration tests for FastAPI channel acquisition analytics API endpoint [FR-001, FR-002, FR-003, NFR-003, NFR-004]."""

from datetime import datetime, timedelta, timezone
import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.main import app
from src.models.analytics import (
    AcquisitionChannel,
    Campaign,
    FunnelThreshold,
    LeadEvent,
    ReportAuditLog,
)
from src.models.database import Base, get_db
from src.models.marketing import Territory

# Isolated in-memory SQLite database setup for API integration testing
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
    """Fixture creating fresh database schema for every test."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


client = TestClient(app)


def test_get_channel_acquisition_report_empty():
    """Verify GET /api/v1/analytics/acquisition returns empty array when no channels exist."""
    response = client.get("/api/v1/analytics/acquisition")
    assert response.status_code == 200
    assert response.json() == []


def test_get_channel_acquisition_report_success():
    """Verify GET /api/v1/analytics/acquisition aggregates metrics and calculates zero-spend ROI [FR-001, FR-002, FR-003]."""
    db = TestingSessionLocal()

    # Create Paid Search (spend $1000, revenue $2500 -> 150% ROI)
    ch1 = AcquisitionChannel(channel_name="Paid Search", spend_amount=1000.0)
    # Create Organic Search (spend $0, revenue $1200 -> ROI 'N/A')
    ch2 = AcquisitionChannel(channel_name="Organic Search", spend_amount=0.0)
    db.add_all([ch1, ch2])
    db.commit()

    now = datetime.now(timezone.utc)
    lead1, lead2, lead3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    # Lead events for Paid Search
    e1 = LeadEvent(
        lead_id=lead1,
        channel_id=ch1.id,
        stage_name="Closed Won",
        quality_score=90,
        deal_value=2500.0,
        revenue_amount=2500.0,
        event_timestamp=now,
    )
    # Lead events for Organic Search
    e2 = LeadEvent(
        lead_id=lead2,
        channel_id=ch2.id,
        stage_name="MQL",
        quality_score=60,
        deal_value=600.0,
        revenue_amount=600.0,
        event_timestamp=now,
    )
    e3 = LeadEvent(
        lead_id=lead3,
        channel_id=ch2.id,
        stage_name="SQL",
        quality_score=80,
        deal_value=600.0,
        revenue_amount=600.0,
        event_timestamp=now,
    )
    db.add_all([e1, e2, e3])
    db.commit()
    db.close()

    response = client.get("/api/v1/analytics/acquisition?sort_by=channel_name&sort_order=asc")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 2

    # Organic Search
    organic = data[0]
    assert organic["channel_name"] == "Organic Search"
    assert organic["lead_volume"] == 2
    assert organic["quality_score"] == 70.0
    assert organic["spend"] == 0.0
    assert organic["revenue"] == 1200.0
    assert organic["roi"] is None
    assert organic["roi_str"] == "N/A"

    # Paid Search
    paid = data[1]
    assert paid["channel_name"] == "Paid Search"
    assert paid["lead_volume"] == 1
    assert paid["quality_score"] == 90.0
    assert paid["spend"] == 1000.0
    assert paid["revenue"] == 2500.0
    assert paid["roi"] == 150.0
    assert paid["roi_str"] == "150.00%"


def test_get_channel_acquisition_report_sorting():
    """Verify channels can be sorted by quality_score descending and ascending [FR-002]."""
    db = TestingSessionLocal()
    ch1 = AcquisitionChannel(channel_name="Email Marketing", spend_amount=500.0)
    ch2 = AcquisitionChannel(channel_name="Social Ads", spend_amount=1000.0)
    ch3 = AcquisitionChannel(channel_name="SEO", spend_amount=200.0)
    db.add_all([ch1, ch2, ch3])
    db.commit()

    now = datetime.now(timezone.utc)
    e1 = LeadEvent(lead_id=uuid.uuid4(), channel_id=ch1.id, stage_name="SQL", quality_score=70, event_timestamp=now)
    e2 = LeadEvent(lead_id=uuid.uuid4(), channel_id=ch2.id, stage_name="MQL", quality_score=50, event_timestamp=now)
    e3 = LeadEvent(lead_id=uuid.uuid4(), channel_id=ch3.id, stage_name="Closed Won", quality_score=95, event_timestamp=now)
    db.add_all([e1, e2, e3])
    db.commit()
    db.close()

    # Sort descending
    res_desc = client.get("/api/v1/analytics/acquisition?sort_by=quality_score&sort_order=desc")
    assert res_desc.status_code == 200
    names_desc = [item["channel_name"] for item in res_desc.json()]
    assert names_desc == ["SEO", "Email Marketing", "Social Ads"]

    # Sort ascending
    res_asc = client.get("/api/v1/analytics/acquisition?sort_by=quality_score&sort_order=asc")
    assert res_asc.status_code == 200
    names_asc = [item["channel_name"] for item in res_asc.json()]
    assert names_asc == ["Social Ads", "Email Marketing", "SEO"]


def test_get_channel_acquisition_report_input_sanitization():
    """Verify input parameter whitespace stripping and case normalization [NFR-004]."""
    db = TestingSessionLocal()
    ch1 = AcquisitionChannel(channel_name="Display Ads", spend_amount=800.0)
    db.add(ch1)
    db.commit()
    ch_id = str(ch1.id)
    db.close()

    # Pass padded whitespace parameters
    url = f"/api/v1/analytics/acquisition?channel_id=  {ch_id}  &sort_by=  spend  &sort_order=  DESC  "
    response = client.get(url)
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["channel_id"] == ch_id
    assert data[0]["channel_name"] == "Display Ads"


def test_get_channel_acquisition_report_filtering():
    """Verify filtering by specific channel_id and date boundaries."""
    db = TestingSessionLocal()
    ch1 = AcquisitionChannel(channel_name="Events", spend_amount=1500.0)
    ch2 = AcquisitionChannel(channel_name="Webinars", spend_amount=900.0)
    db.add_all([ch1, ch2])
    db.commit()

    time_in_range = datetime(2026, 2, 10, 12, 0, tzinfo=timezone.utc)
    time_out_of_range = datetime(2025, 1, 10, 12, 0, tzinfo=timezone.utc)

    e1 = LeadEvent(lead_id=uuid.uuid4(), channel_id=ch1.id, stage_name="SQL", quality_score=80, event_timestamp=time_in_range)
    e2 = LeadEvent(lead_id=uuid.uuid4(), channel_id=ch1.id, stage_name="SQL", quality_score=40, event_timestamp=time_out_of_range)
    db.add_all([e1, e2])
    db.commit()
    ch1_id = str(ch1.id)
    ch2_id = str(ch2.id)
    db.close()

    # Filter by date range (only time_in_range event)
    start_str = "2026-02-01T00:00:00Z"
    end_str = "2026-02-28T23:59:59Z"
    res_date = client.get(f"/api/v1/analytics/acquisition?start_date_val={start_str}&end_date_val={end_str}")
    assert res_date.status_code == 200
    ch1_item = next(item for item in res_date.json() if item["channel_id"] == ch1_id)
    assert ch1_item["lead_volume"] == 1
    assert ch1_item["quality_score"] == 80.0

    # Filter by specific channel_id
    res_ch2 = client.get(f"/api/v1/analytics/acquisition?channel_id={ch2_id}")
    assert res_ch2.status_code == 200
    data_ch2 = res_ch2.json()
    assert len(data_ch2) == 1
    assert data_ch2[0]["channel_name"] == "Webinars"


def test_get_channel_acquisition_report_invalid_sort_field():
    """Verify passing invalid sort_by parameter yields HTTP 400 Bad Request [NFR-004]."""
    response = client.get("/api/v1/analytics/acquisition?sort_by=invalid_field_name")
    assert response.status_code == 400
    assert "Invalid sort_by field" in response.json()["detail"]


def test_get_channel_acquisition_report_invalid_sort_order():
    """Verify passing invalid sort_order parameter yields HTTP 400 Bad Request [NFR-004]."""
    response = client.get("/api/v1/analytics/acquisition?sort_order=sideways")
    assert response.status_code == 400
    assert "Invalid sort_order" in response.json()["detail"]


def test_get_channel_acquisition_report_invalid_date_range():
    """Verify passing start_date_val > end_date_val yields HTTP 400 Bad Request [NFR-004]."""
    start_str = "2026-05-10T00:00:00Z"
    end_str = "2026-05-01T00:00:00Z"
    response = client.get(f"/api/v1/analytics/acquisition?start_date_val={start_str}&end_date_val={end_str}")
    assert response.status_code == 400
    assert "start_date_val must be before" in response.json()["detail"]


def test_get_channel_acquisition_report_channel_not_found():
    """Verify passing non-existent or invalid channel_id yields HTTP 404 Not Found."""
    fake_id = str(uuid.uuid4())
    response = client.get(f"/api/v1/analytics/acquisition?channel_id={fake_id}")
    assert response.status_code == 404
    assert "not found" in response.json()["detail"].lower()


def test_get_channel_acquisition_report_correlation_id_header():
    """Verify endpoint propagates correlation ID in HTTP response headers [NFR-003]."""
    custom_cid = "test-correlation-id-999"
    response = client.get(
        "/api/v1/analytics/acquisition",
        headers={"X-Correlation-ID": custom_cid},
    )
    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID") == custom_cid


def test_get_conversion_funnel_report_default_empty():
    """Verify GET /api/v1/analytics/funnel returns default stages when no events exist [FR-004]."""
    response = client.get("/api/v1/analytics/funnel")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) == 5
    stage_names = [item["stage_name"] for item in data]
    assert stage_names == ["LEAD", "MQL", "SQL", "OPPORTUNITY", "CLOSED_WON"]
    for item in data:
        assert item["stage_count"] == 0
        assert item["conversion_rate"] == 0.0
        assert item["dropoff_rate"] == 0.0
        assert item["is_bottleneck"] is False


def test_get_conversion_funnel_report_success_and_bottleneck():
    """Verify GET /api/v1/analytics/funnel aggregates stage counts and flags drop-off bottlenecks [FR-004, FR-005]."""
    from src.models.analytics import FunnelThreshold

    db = TestingSessionLocal()
    # Set threshold of 25.0% for MQL stage
    threshold = FunnelThreshold(stage_name="MQL", max_dropoff_percent=25.0)
    db.add(threshold)
    db.commit()

    now = datetime.now(timezone.utc)
    # 100 LEAD events
    for _ in range(100):
        db.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="LEAD", event_timestamp=now))
    # 50 MQL events (50% dropoff, exceeds 25% threshold -> bottleneck!)
    for _ in range(50):
        db.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="MQL", event_timestamp=now))
    db.commit()
    db.close()

    response = client.get("/api/v1/analytics/funnel")
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 5

    lead_stage = next(s for s in data if s["stage_name"] == "LEAD")
    assert lead_stage["stage_count"] == 100
    assert lead_stage["conversion_rate"] == 100.0
    assert lead_stage["dropoff_rate"] == 0.0
    assert lead_stage["is_bottleneck"] is False

    mql_stage = next(s for s in data if s["stage_name"] == "MQL")
    assert mql_stage["stage_count"] == 50
    assert mql_stage["conversion_rate"] == 50.0
    assert mql_stage["dropoff_rate"] == 50.0
    assert mql_stage["is_bottleneck"] is True
    assert mql_stage["threshold_percent"] == 25.0


def test_get_conversion_funnel_report_sanitization_and_filters():
    """Verify whitespace sanitization and parameter filters for funnel report [NFR-004]."""
    db = TestingSessionLocal()
    ch1 = AcquisitionChannel(channel_name="Search Ads", spend_amount=500.0)
    db.add(ch1)
    db.commit()
    ch_id = str(ch1.id)

    now = datetime.now(timezone.utc)
    db.add(LeadEvent(lead_id=uuid.uuid4(), channel_id=ch1.id, stage_name="LEAD", event_timestamp=now))
    db.commit()
    db.close()

    # Pass whitespace-padded channel_id
    res = client.get(f"/api/v1/analytics/funnel?channel_id=   {ch_id}   ")
    assert res.status_code == 200
    data = res.json()
    lead_stage = next(s for s in data if s["stage_name"] == "LEAD")
    assert lead_stage["stage_count"] == 1


def test_get_conversion_funnel_report_invalid_date_range():
    """Verify start_date_val after end_date_val returns HTTP 400 Bad Request [NFR-004]."""
    start_str = "2026-05-10T00:00:00Z"
    end_str = "2026-05-01T00:00:00Z"
    res = client.get(f"/api/v1/analytics/funnel?start_date_val={start_str}&end_date_val={end_str}")
    assert res.status_code == 400
    assert "start_date_val cannot be after end_date_val" in res.json()["detail"]


def test_get_conversion_funnel_report_invalid_uuid():
    """Verify passing invalid UUID string returns HTTP 400 Bad Request [NFR-004]."""
    res = client.get("/api/v1/analytics/funnel?channel_id=invalid-uuid-string")
    assert res.status_code == 400
    assert "Invalid UUID value" in res.json()["detail"]


def test_get_campaign_comparison_empty():
    """Verify GET /api/v1/analytics/campaigns/compare returns empty list when no campaign_ids provided [FR-006]."""
    res = client.get("/api/v1/analytics/campaigns/compare")
    assert res.status_code == 200
    assert res.json() == []


def test_get_campaign_comparison_success():
    """Verify GET /api/v1/analytics/campaigns/compare returns comparative metrics and time series for up to 5 campaigns [FR-006]."""
    db = TestingSessionLocal()
    c1 = Campaign(campaign_name="Alpha Campaign", budget=1000.0)
    c2 = Campaign(campaign_name="Beta Campaign", budget=2000.0)
    db.add_all([c1, c2])
    db.commit()

    c1_id = str(c1.id)
    c2_id = str(c2.id)

    d1 = datetime(2026, 4, 1, 10, 0, tzinfo=timezone.utc)
    d2 = datetime(2026, 4, 2, 10, 0, tzinfo=timezone.utc)

    l1, l2, l3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    # Events for Campaign 1 (Alpha)
    e1 = LeadEvent(lead_id=l1, campaign_id=c1.id, stage_name="SQL", quality_score=80, revenue_amount=500.0, event_timestamp=d1)
    e2 = LeadEvent(lead_id=l2, campaign_id=c1.id, stage_name="Closed Won", quality_score=90, revenue_amount=1000.0, event_timestamp=d2)

    # Event for Campaign 2 (Beta)
    e3 = LeadEvent(lead_id=l3, campaign_id=c2.id, stage_name="MQL", quality_score=40, revenue_amount=0.0, event_timestamp=d1)

    db.add_all([e1, e2, e3])
    db.commit()
    db.close()

    res = client.get(f"/api/v1/analytics/campaigns/compare?campaign_ids={c1_id},{c2_id}")
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2

    alpha = next(item for item in data if item["campaign_id"] == c1_id)
    assert alpha["campaign_name"] == "Alpha Campaign"
    assert alpha["budget"] == 1000.0
    assert alpha["lead_volume"] == 2
    assert alpha["relative_volume_pct"] == 66.67  # 2 of 3 total leads
    assert alpha["cost_per_lead"] == 500.0
    assert alpha["cost_per_lead_str"] == "$500.00"
    assert alpha["total_revenue"] == 1500.0
    assert alpha["engagement_rate"] == 100.0
    assert len(alpha["time_series"]) == 2
    assert len(alpha["trend_points"]) == 2

    beta = next(item for item in data if item["campaign_id"] == c2_id)
    assert beta["campaign_name"] == "Beta Campaign"
    assert beta["budget"] == 2000.0
    assert beta["lead_volume"] == 1
    assert beta["relative_volume_pct"] == 33.33  # 1 of 3 total leads
    assert beta["cost_per_lead"] == 2000.0
    assert beta["cost_per_lead_str"] == "$2000.00"
    assert beta["engagement_rate"] == 0.0


def test_get_campaign_comparison_max_limit_exceeded():
    """Verify requesting >5 campaigns returns HTTP 400 Bad Request error [FR-006]."""
    fake_ids = [str(uuid.uuid4()) for _ in range(6)]
    csv_ids = ",".join(fake_ids)
    res = client.get(f"/api/v1/analytics/campaigns/compare?campaign_ids={csv_ids}")
    assert res.status_code == 400
    assert "Maximum 5 campaigns can be compared simultaneously" in res.json()["detail"]


def test_get_campaign_comparison_not_found():
    """Verify specifying a non-existent campaign_id returns HTTP 404 Not Found [FR-006]."""
    fake_id = str(uuid.uuid4())
    res = client.get(f"/api/v1/analytics/campaigns/compare?campaign_ids={fake_id}")
    assert res.status_code == 404
    assert f"Campaign with id '{fake_id}' not found" in res.json()["detail"]


def test_get_campaign_comparison_invalid_date_range():
    """Verify start_date_val after end_date_val returns HTTP 400 Bad Request [NFR-004]."""
    db = TestingSessionLocal()
    c1 = Campaign(campaign_name="Test Campaign", budget=500.0)
    db.add(c1)
    db.commit()
    c1_id = str(c1.id)
    db.close()

    start_str = "2026-05-10T00:00:00Z"
    end_str = "2026-05-01T00:00:00Z"
    res = client.get(f"/api/v1/analytics/campaigns/compare?campaign_ids={c1_id}&start_date_val={start_str}&end_date_val={end_str}")
    assert res.status_code == 400
    assert "start_date_val must be before or equal to end_date_val" in res.json()["detail"]


def test_get_campaign_comparison_input_sanitization():
    """Verify campaign_ids query parameter whitespace trimming and CSV parsing [NFR-004]."""
    db = TestingSessionLocal()
    c1 = Campaign(campaign_name="Padded Campaign 1", budget=300.0)
    c2 = Campaign(campaign_name="Padded Campaign 2", budget=400.0)
    db.add_all([c1, c2])
    db.commit()
    c1_id = str(c1.id)
    c2_id = str(c2.id)
    db.close()

    # Whitespace padding in CSV values
    url = f"/api/v1/analytics/campaigns/compare?campaign_ids=  {c1_id}  ,  {c2_id}  "
    res = client.get(url)
    assert res.status_code == 200
    data = res.json()
    assert len(data) == 2
    ids_returned = {item["campaign_id"] for item in data}
    assert ids_returned == {c1_id, c2_id}


def test_get_territory_heatmap_success():
    """Verify GET /api/v1/analytics/territory/heatmap aggregates density metrics and unassigned drawer [FR-007, FR-008]."""
    db = TestingSessionLocal()
    t_west = Territory(
        id=uuid.uuid4(),
        territory_code="US-WEST",
        territory_name="US West Region",
        postal_code_patterns="90*, 91*",
    )
    t_east = Territory(
        id=uuid.uuid4(),
        territory_code="US-EAST",
        territory_name="US East Region",
        postal_code_patterns="100*, 101*",
    )
    db.add_all([t_west, t_east])
    db.commit()

    now = datetime.now(timezone.utc)
    t_west_id = str(t_west.id)
    t_east_id = str(t_east.id)

    # Event mapped directly by territory_id
    e1 = LeadEvent(
        lead_id=uuid.uuid4(),
        territory_id=t_west.id,
        stage_name="SQL",
        deal_value=12000.0,
        revenue_amount=10000.0,
        event_timestamp=now,
    )
    # Event mapped by postal code pattern
    e2 = LeadEvent(
        lead_id=uuid.uuid4(),
        postal_code="90210",
        stage_name="Closed Won",
        deal_value=18000.0,
        revenue_amount=18000.0,
        event_timestamp=now,
    )
    # Unassigned event
    e3 = LeadEvent(
        lead_id=uuid.uuid4(),
        postal_code="30301",
        stage_name="MQL",
        deal_value=5000.0,
        revenue_amount=3000.0,
        event_timestamp=now,
    )
    db.add_all([e1, e2, e3])
    db.commit()
    db.close()

    res = client.get("/api/v1/analytics/territory/heatmap")
    assert res.status_code == 200
    data = res.json()

    assert "territories" in data
    assert "unassigned_drawer" in data
    assert "summary" in data

    territories = data["territories"]
    assert len(territories) == 2

    west = next(t for t in territories if t["territory_id"] == t_west_id)
    assert west["territory_code"] == "US-WEST"
    assert west["lead_count"] == 2
    assert west["total_deal_value"] == 30000.0
    assert west["avg_deal_value"] == 15000.0

    drawer = data["unassigned_drawer"]
    assert drawer["lead_count"] == 1
    assert drawer["total_deal_value"] == 5000.0
    assert drawer["total_revenue"] == 3000.0
    assert len(drawer["unassigned_events"]) == 1
    assert drawer["unassigned_events"][0]["postal_code"] == "30301"

    summary = data["summary"]
    assert summary["total_mapped_leads"] == 2
    assert summary["total_unassigned_leads"] == 1
    assert summary["total_leads"] == 3
    assert summary["total_deal_value"] == 35000.0


def test_get_territory_heatmap_filters_and_access_scoping():
    """Verify territory filters, min/max deal value, and multi-region access scoping [FR-008, NFR-002]."""
    db = TestingSessionLocal()
    t1 = Territory(id=uuid.uuid4(), territory_code="T1", territory_name="Territory 1", postal_code_patterns="10*")
    t2 = Territory(id=uuid.uuid4(), territory_code="T2", territory_name="Territory 2", postal_code_patterns="20*")
    db.add_all([t1, t2])
    db.commit()
    t1_id = str(t1.id)
    t2_id = str(t2.id)

    now = datetime.now(timezone.utc)
    e1 = LeadEvent(lead_id=uuid.uuid4(), territory_id=t1.id, stage_name="SQL", deal_value=5000.0, event_timestamp=now)
    e2 = LeadEvent(lead_id=uuid.uuid4(), territory_id=t2.id, stage_name="SQL", deal_value=15000.0, event_timestamp=now)
    db.add_all([e1, e2])
    db.commit()
    db.close()

    # Scope access to t1 only
    res_scoped = client.get(f"/api/v1/analytics/territory/heatmap?user_territory_ids={t1_id}")
    assert res_scoped.status_code == 200
    data_scoped = res_scoped.json()
    assert len(data_scoped["territories"]) == 1
    assert data_scoped["territories"][0]["territory_id"] == t1_id

    # Request unauthorized territory_id -> HTTP 403 Forbidden
    res_forbidden = client.get(f"/api/v1/analytics/territory/heatmap?territory_id={t2_id}&user_territory_ids={t1_id}")
    assert res_forbidden.status_code == 403
    assert "User is not authorized to access territory" in res_forbidden.json()["detail"]


def test_get_territory_heatmap_invalid_filters_and_sanitization():
    """Verify min_deal_value > max_deal_value returns HTTP 400 and parameter sanitization [NFR-004]."""
    db = TestingSessionLocal()
    t1 = Territory(id=uuid.uuid4(), territory_code="T1", territory_name="Territory 1")
    db.add(t1)
    db.commit()
    t1_id = str(t1.id)
    db.close()

    # Invalid min/max deal value range
    res_invalid_val = client.get("/api/v1/analytics/territory/heatmap?min_deal_value=1000&max_deal_value=500")
    assert res_invalid_val.status_code == 400
    assert "min_deal_value cannot be greater than max_deal_value" in res_invalid_val.json()["detail"]

    # Parameter sanitization with padded spaces
    url = f"/api/v1/analytics/territory/heatmap?lead_status=  SQL  &territory_id=  {t1_id}  "
    res_sanitized = client.get(url)
    assert res_sanitized.status_code == 200


def test_post_config_audit_success():
    """Verify POST /api/v1/analytics/config/audit creates immutable audit record [FR-011, NFR-003]."""
    test_user_id = str(uuid.uuid4())
    payload = {
        "user_id": test_user_id,
        "action_type": "UPDATE_THRESHOLD",
        "config_delta": '{"stage": "SQL", "old_dropoff": 25.0, "new_dropoff": 30.0}',
    }
    response = client.post("/api/v1/analytics/config/audit", json=payload)
    assert response.status_code == 200
    data = response.json()
    assert "id" in data
    assert data["user_id"] == test_user_id
    assert data["action_type"] == "UPDATE_THRESHOLD"
    assert "SQL" in data["config_delta"]
    assert "created_on" in data

    # Verify persisted in database
    db = TestingSessionLocal()
    record = db.query(ReportAuditLog).filter(ReportAuditLog.id == uuid.UUID(data["id"])).first()
    assert record is not None
    assert str(record.user_id) == test_user_id
    assert record.action_type == "UPDATE_THRESHOLD"
    db.close()


def test_post_config_audit_omitted_user_id_and_validation_errors():
    """Verify POST /api/v1/analytics/config/audit generates default user_id if omitted and validates input [FR-011, NFR-004]."""
    # Omitted user_id
    payload_no_user = {
        "action_type": "CREATE_REPORT_CONFIG",
        "config_delta": "New custom report configuration",
    }
    res = client.post("/api/v1/analytics/config/audit", json=payload_no_user)
    assert res.status_code == 200
    data = res.json()
    assert data["action_type"] == "CREATE_REPORT_CONFIG"
    assert data["user_id"] is not None

    # Invalid user_id UUID format
    res_bad_user = client.post("/api/v1/analytics/config/audit", json={"user_id": "not-a-uuid", "action_type": "TEST"})
    assert res_bad_user.status_code == 400
    assert "Invalid user_id UUID format" in res_bad_user.json()["detail"]

    # Empty action_type
    res_bad_action = client.post("/api/v1/analytics/config/audit", json={"action_type": "   "})
    assert res_bad_action.status_code in (400, 422)


def test_delete_config_funnel_threshold_success():
    """Verify DELETE /api/v1/analytics/config/{id} deletes funnel threshold and returns 204 No Content with empty body [FR-011, NFR-003]."""
    db = TestingSessionLocal()
    threshold = FunnelThreshold(
        id=uuid.uuid4(),
        stage_name="MQL",
        max_dropoff_percent=20.0,
    )
    db.add(threshold)
    db.commit()
    thresh_id = str(threshold.id)
    db.close()

    response = client.delete(f"/api/v1/analytics/config/{thresh_id}")
    assert response.status_code == 204
    assert response.content == b""

    # Verify deleted from DB
    db = TestingSessionLocal()
    check = db.query(FunnelThreshold).filter(FunnelThreshold.id == uuid.UUID(thresh_id)).first()
    assert check is None

    # Verify deletion audit log record was created
    audit = db.query(ReportAuditLog).filter(ReportAuditLog.action_type == "DELETE_CONFIG").first()
    assert audit is not None
    assert thresh_id in audit.config_delta
    db.close()


def test_delete_config_audit_log_success():
    """Verify DELETE /api/v1/analytics/config/{id} deletes audit log record and returns 204 No Content [FR-011]."""
    db = TestingSessionLocal()
    audit_log = ReportAuditLog(
        id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        action_type="TEMP_CONFIG",
        config_delta="Temporary config change",
    )
    db.add(audit_log)
    db.commit()
    audit_id = str(audit_log.id)
    db.close()

    response = client.delete(f"/api/v1/analytics/config/{audit_id}")
    assert response.status_code == 204
    assert response.content == b""

    db = TestingSessionLocal()
    check = db.query(ReportAuditLog).filter(ReportAuditLog.id == uuid.UUID(audit_id)).first()
    assert check is None
    db.close()


def test_delete_config_not_found_and_invalid_uuid():
    """Verify DELETE /api/v1/analytics/config/{id} returns 404 for missing ID and 400 for invalid UUID [NFR-004]."""
    random_id = str(uuid.uuid4())
    res_not_found = client.delete(f"/api/v1/analytics/config/{random_id}")
    assert res_not_found.status_code == 404
    assert f"Configuration record with ID '{random_id}' not found" in res_not_found.json()["detail"]

    res_invalid_uuid = client.delete("/api/v1/analytics/config/not-a-valid-uuid")
    assert res_invalid_uuid.status_code == 400
    assert "Invalid configuration ID UUID format" in res_invalid_uuid.json()["detail"]


def test_post_analytics_exports_success_acquisition_csv():
    """Verify POST /api/v1/analytics/exports submits async task and returns 202 Accepted with task_id [FR-012, NFR-004]."""
    payload = {
        "report_type": "acquisition",
        "export_format": "csv",
        "date_filters": {"channel_id": str(uuid.uuid4())},
    }
    response = client.post("/api/v1/analytics/exports", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert len(data["task_id"]) > 0
    # Verify task_id is a valid UUID
    parsed_uuid = uuid.UUID(data["task_id"])
    assert parsed_uuid is not None


def test_post_analytics_exports_success_funnel_pdf_sanitization():
    """Verify POST /api/v1/analytics/exports sanitizes parameters and handles PDF format [FR-012, NFR-004]."""
    payload = {
        "report_type": "  FUNNEL  ",
        "export_format": " PDF ",
        "date_filters": {"start_date_val": "2026-01-01T00:00:00Z"},
    }
    response = client.post("/api/v1/analytics/exports", json=payload)
    assert response.status_code == 202
    data = response.json()
    assert "task_id" in data
    assert uuid.UUID(data["task_id"]) is not None


def test_post_analytics_exports_validation_errors():
    """Verify POST /api/v1/analytics/exports validates report_type and export_format [NFR-004]."""
    # Invalid report_type
    res_bad_type = client.post(
        "/api/v1/analytics/exports",
        json={"report_type": "unsupported_report", "export_format": "csv"},
    )
    assert res_bad_type.status_code == 422

    # Invalid export_format
    res_bad_fmt = client.post(
        "/api/v1/analytics/exports",
        json={"report_type": "acquisition", "export_format": "excel"},
    )
    assert res_bad_fmt.status_code == 422


# Integration tests for operational support analytics endpoint [FR-8, NFR-2, NFR-3, NFR-4]


def test_get_support_analytics_empty():
    """Verify GET /api/v1/support/analytics returns zeroed metrics when no tickets exist [FR-8]."""
    res1 = client.get("/api/v1/support/analytics")
    assert res1.status_code == 200
    data1 = res1.json()
    assert data1["total_tickets"] == 0
    assert data1["open_tickets_count"] == 0
    assert data1["resolved_tickets_count"] == 0
    assert data1["average_response_time_minutes"] == 0.0
    assert data1["mean_time_to_resolution_hours"] == 0.0
    assert data1["sla_breach_rate_percent"] == 0.0
    assert data1["average_csat_score"] == 0.0
    assert data1["csat_response_count"] == 0
    assert data1["priority_breakdown"] == {"P1": 0, "P2": 0, "P3": 0}

    # Verify alias path /api/v1/analytics/support
    res2 = client.get("/api/v1/analytics/support")
    assert res2.status_code == 200
    assert res2.json() == data1


def test_get_support_analytics_with_data():
    """Verify GET /api/v1/support/analytics calculates operational metrics accurately [FR-8]."""
    from src.models.ticket import CSATRating, SLATracking, SupportTicket

    db = TestingSessionLocal()
    now = datetime.now(timezone.utc)
    client_id = uuid.uuid4()

    # Ticket 1: P1 open ticket
    t1 = SupportTicket(
        priority="P1",
        status="open",
        subject="P1 Issue",
        description="Desc P1",
        client_id=client_id,
        created_at=now - timedelta(hours=2),
    )
    # Ticket 2: P2 resolved ticket with first response in 30 mins and resolution in 2 hours
    t2 = SupportTicket(
        priority="P2",
        status="resolved",
        subject="P2 Issue",
        description="Desc P2",
        client_id=client_id,
        created_at=now - timedelta(hours=5),
        first_responded_at=now - timedelta(hours=4, minutes=30),
        resolved_at=now - timedelta(hours=3),
    )
    db.add_all([t1, t2])
    db.commit()

    # CSAT for Ticket 2
    csat = CSATRating(
        ticket_id=t2.id,
        score=5,
        feedback_text="Excellent",
        submitted_at=now,
    )
    # Breached SLA tracking for Ticket 1
    sla = SLATracking(
        ticket_id=t1.id,
        target_type="first_response",
        target_due_at=now - timedelta(hours=1),
        is_breached=True,
        breached_at=now - timedelta(hours=1),
    )
    db.add_all([csat, sla])
    db.commit()
    db.close()

    res = client.get(f"/api/v1/support/analytics?client_id={client_id}")
    assert res.status_code == 200
    data = res.json()

    assert data["total_tickets"] == 2
    assert data["open_tickets_count"] == 1
    assert data["resolved_tickets_count"] == 1
    assert data["average_response_time_minutes"] == 30.0  # 30 mins for t2
    assert data["mean_time_to_resolution_hours"] == 2.0  # 2 hours for t2
    assert data["sla_breach_rate_percent"] == 50.0  # 1 out of 2 breached
    assert data["average_csat_score"] == 5.0
    assert data["csat_response_count"] == 1
    assert data["priority_breakdown"] == {"P1": 1, "P2": 1, "P3": 0}


def test_get_support_analytics_validation_errors():
    """Verify validation errors for priority, client_id, and invalid time window bounds [NFR-2]."""
    # Invalid priority -> 400 Bad Request
    res_bad_prio = client.get("/api/v1/support/analytics?priority=INVALID")
    assert res_bad_prio.status_code == 400
    assert "Invalid priority filter" in res_bad_prio.json()["detail"]

    # Invalid client_id UUID -> 400 Bad Request
    res_bad_client = client.get("/api/v1/support/analytics?client_id=not-a-uuid")
    assert res_bad_client.status_code == 400
    assert "Invalid client_id UUID format" in res_bad_client.json()["detail"]

    # start_time > end_time -> 400 Bad Request
    res_bad_range = client.get(
        "/api/v1/support/analytics?start_time=2026-02-01T00:00:00Z&end_time=2026-01-01T00:00:00Z"
    )
    assert res_bad_range.status_code == 400
    assert "start_time cannot be after end_time" in res_bad_range.json()["detail"]
