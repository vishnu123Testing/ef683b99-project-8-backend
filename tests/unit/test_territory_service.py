"""Unit tests for Territory Analytics and Access Scoping Service."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.analytics import LeadEvent
from src.models.database import Base
from src.models.marketing import Territory
from src.services.marketing.territory import (
    InvalidFilterError,
    TerritoryAccessDeniedError,
    filter_territories_by_access,
    find_matching_territory,
    get_territory_heatmap_data,
    match_postal_code_pattern,
)


@pytest.fixture
def db_session():
    """In-memory SQLite database session fixture."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_match_postal_code_pattern():
    """Test postal code pattern matching logic."""
    assert match_postal_code_pattern("90210", "90210") is True
    assert match_postal_code_pattern("90210", "90*") is True
    assert match_postal_code_pattern("90210", "9021?") is True
    assert match_postal_code_pattern(" ny100 ", "10001, NY*, 902*") is True
    assert match_postal_code_pattern("30301", "10001, NY*, 902*") is False
    assert match_postal_code_pattern("", "90*") is False
    assert match_postal_code_pattern("90210", "") is False


def test_find_matching_territory():
    """Test finding a matching territory object for a given postal code."""
    t1 = Territory(
        territory_code="US-WEST",
        territory_name="US West Coast",
        postal_code_patterns="90*, 91*, 92*",
    )
    t2 = Territory(
        territory_code="US-EAST",
        territory_name="US East Coast",
        postal_code_patterns="100*, 101*, NY*",
    )
    territories = [t1, t2]

    assert find_matching_territory("90210", territories) == t1
    assert find_matching_territory("NY100", territories) == t2
    assert find_matching_territory("30301", territories) is None
    assert find_matching_territory(None, territories) is None
    assert find_matching_territory("", territories) is None


def test_filter_territories_by_access():
    """Test filtering territories based on user territory access scope."""
    id1 = uuid.uuid4()
    id2 = uuid.uuid4()
    t1 = Territory(id=id1, territory_code="T1", territory_name="Territory 1")
    t2 = Territory(id=id2, territory_code="T2", territory_name="Territory 2")
    territories = [t1, t2]

    # Unrestricted
    assert filter_territories_by_access(territories, None) == [t1, t2]

    # Restricted to t1
    res = filter_territories_by_access(territories, [str(id1)])
    assert res == [t1]

    # Empty access list
    assert filter_territories_by_access(territories, []) == []


def test_get_territory_heatmap_data_aggregation(db_session):
    """Test full heatmap data aggregation across mapped territories and unassigned drawer."""
    t_west = Territory(
        id=uuid.uuid4(),
        territory_code="US-WEST",
        territory_name="US West",
        postal_code_patterns="90*, 91*",
    )
    t_east = Territory(
        id=uuid.uuid4(),
        territory_code="US-EAST",
        territory_name="US East",
        postal_code_patterns="100*, 101*",
    )
    db_session.add_all([t_west, t_east])
    db_session.commit()

    now = datetime.now(timezone.utc)

    # Events
    e1 = LeadEvent(
        lead_id=uuid.uuid4(),
        territory_id=t_west.id,
        stage_name="LEAD",
        deal_value=10000.0,
        revenue_amount=8000.0,
        event_timestamp=now,
    )
    e2 = LeadEvent(
        lead_id=uuid.uuid4(),
        postal_code="90210",  # Matches US-WEST
        stage_name="LEAD",
        deal_value=20000.0,
        revenue_amount=16000.0,
        event_timestamp=now,
    )
    e3 = LeadEvent(
        lead_id=uuid.uuid4(),
        territory_id=t_east.id,
        stage_name="CLOSED_WON",
        deal_value=15000.0,
        revenue_amount=15000.0,
        event_timestamp=now,
    )
    e4 = LeadEvent(
        lead_id=uuid.uuid4(),
        postal_code="99999",  # Unassigned
        stage_name="LEAD",
        deal_value=5000.0,
        revenue_amount=4000.0,
        event_timestamp=now,
    )
    e5 = LeadEvent(
        lead_id=uuid.uuid4(),
        postal_code=None,  # Unassigned
        stage_name="LEAD",
        deal_value=3000.0,
        revenue_amount=2000.0,
        event_timestamp=now,
    )
    db_session.add_all([e1, e2, e3, e4, e5])
    db_session.commit()

    result = get_territory_heatmap_data(db_session)

    assert len(result["territories"]) == 2
    west_res = next(t for t in result["territories"] if t["territory_code"] == "US-WEST")
    east_res = next(t for t in result["territories"] if t["territory_code"] == "US-EAST")

    assert west_res["lead_count"] == 2
    assert west_res["total_deal_value"] == 30000.0
    assert west_res["avg_deal_value"] == 15000.0

    assert east_res["lead_count"] == 1
    assert east_res["total_deal_value"] == 15000.0

    drawer = result["unassigned_drawer"]
    assert drawer["lead_count"] == 2
    assert drawer["total_deal_value"] == 8000.0
    assert drawer["total_revenue"] == 6000.0
    assert len(drawer["unassigned_events"]) == 2

    summary = result["summary"]
    assert summary["total_mapped_leads"] == 3
    assert summary["total_unassigned_leads"] == 2
    assert summary["total_leads"] == 5
    assert summary["total_deal_value"] == 53000.0


def test_get_territory_heatmap_data_filters(db_session):
    """Test filtering by lead_status and deal value thresholds."""
    t_west = Territory(
        id=uuid.uuid4(),
        territory_code="US-WEST",
        territory_name="US West",
        postal_code_patterns="90*",
    )
    db_session.add(t_west)
    db_session.commit()

    now = datetime.now(timezone.utc)
    e1 = LeadEvent(
        lead_id=uuid.uuid4(),
        territory_id=t_west.id,
        stage_name="LEAD",
        deal_value=5000.0,
        event_timestamp=now,
    )
    e2 = LeadEvent(
        lead_id=uuid.uuid4(),
        territory_id=t_west.id,
        stage_name="CLOSED_WON",
        deal_value=25000.0,
        event_timestamp=now,
    )
    db_session.add_all([e1, e2])
    db_session.commit()

    # Filter by stage_name="LEAD"
    res_lead = get_territory_heatmap_data(db_session, lead_status="LEAD")
    west = res_lead["territories"][0]
    assert west["lead_count"] == 1
    assert west["total_deal_value"] == 5000.0

    # Filter by min_deal_value=10000
    res_val = get_territory_heatmap_data(db_session, min_deal_value=10000.0)
    west_val = res_val["territories"][0]
    assert west_val["lead_count"] == 1
    assert west_val["total_deal_value"] == 25000.0


def test_get_territory_heatmap_data_invalid_filter_errors(db_session):
    """Test raising InvalidFilterError for malformed filters."""
    with pytest.raises(InvalidFilterError, match="min_deal_value cannot be greater than max_deal_value"):
        get_territory_heatmap_data(db_session, min_deal_value=50000.0, max_deal_value=10000.0)

    now = datetime.now(timezone.utc)
    later = datetime(2027, 1, 1, tzinfo=timezone.utc)
    with pytest.raises(InvalidFilterError, match="start_date_val cannot be after end_date_val"):
        get_territory_heatmap_data(db_session, start_date_val=later, end_date_val=now)


def test_get_territory_heatmap_data_access_scoping(db_session):
    """Test multi-region user access scoping (NFR-002) and authorization enforcement."""
    t1_id = uuid.uuid4()
    t2_id = uuid.uuid4()
    t1 = Territory(id=t1_id, territory_code="T1", territory_name="Territory 1")
    t2 = Territory(id=t2_id, territory_code="T2", territory_name="Territory 2")
    db_session.add_all([t1, t2])
    db_session.commit()

    # Scoped to t1 only
    res = get_territory_heatmap_data(db_session, user_territory_ids=[t1_id])
    assert len(res["territories"]) == 1
    assert res["territories"][0]["territory_id"] == str(t1_id)

    # Attempting to access t2 when authorized only for t1 raises TerritoryAccessDeniedError
    with pytest.raises(TerritoryAccessDeniedError):
        get_territory_heatmap_data(
            db_session,
            territory_id=t2_id,
            user_territory_ids=[t1_id],
        )
