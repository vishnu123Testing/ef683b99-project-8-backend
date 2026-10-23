"""Unit tests for conversion funnel analytics domain service [FR-004, FR-005]."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.analytics import AcquisitionChannel, Campaign, LeadEvent
from src.models.database import Base
from src.services.analytics.funnel import (
    DEFAULT_FUNNEL_STAGES,
    FunnelServiceError,
    InvalidFunnelDateRangeError,
    analyze_conversion_funnel,
    delete_funnel_threshold,
    get_funnel_thresholds,
    set_funnel_threshold,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for testing funnel service."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_get_set_delete_funnel_thresholds(db_session):
    """Test set_funnel_threshold, get_funnel_thresholds, and delete_funnel_threshold."""
    # Set threshold for MQL
    rec1 = set_funnel_threshold(db_session, "MQL", 25.0)
    assert rec1.stage_name == "MQL"
    assert rec1.max_dropoff_percent == 25.0

    # Set threshold for SQL
    rec2 = set_funnel_threshold(db_session, "SQL", 35.5)
    assert rec2.stage_name == "SQL"
    assert rec2.max_dropoff_percent == 35.5

    thresholds = get_funnel_thresholds(db_session)
    assert thresholds["MQL"] == 25.0
    assert thresholds["SQL"] == 35.5

    # Update MQL threshold
    rec1_updated = set_funnel_threshold(db_session, "MQL", 20.0)
    assert rec1_updated.max_dropoff_percent == 20.0
    assert get_funnel_thresholds(db_session)["MQL"] == 20.0

    # Delete threshold
    deleted = delete_funnel_threshold(db_session, "MQL")
    assert deleted is True
    assert "MQL" not in get_funnel_thresholds(db_session)

    # Delete non-existent threshold
    deleted_again = delete_funnel_threshold(db_session, "NON_EXISTENT")
    assert deleted_again is False


def test_set_funnel_threshold_invalid_stage_name_raises(db_session):
    """Test set_funnel_threshold with empty stage name raises FunnelServiceError."""
    with pytest.raises(FunnelServiceError, match="stage_name cannot be empty"):
        set_funnel_threshold(db_session, "", 30.0)

    with pytest.raises(FunnelServiceError, match="stage_name cannot be empty"):
        set_funnel_threshold(db_session, "   ", 30.0)


def test_analyze_conversion_funnel_empty_events(db_session):
    """Test funnel analysis when no lead events exist in database."""
    results = analyze_conversion_funnel(db_session)

    assert len(results) == len(DEFAULT_FUNNEL_STAGES)
    for idx, stage_data in enumerate(results):
        assert stage_data["stage_name"] == DEFAULT_FUNNEL_STAGES[idx]
        assert stage_data["stage_count"] == 0
        assert stage_data["conversion_rate"] == 0.0
        assert stage_data["dropoff_rate"] == 0.0
        assert stage_data["is_bottleneck"] is False


def test_analyze_conversion_funnel_stage_progression_and_bottlenecks(db_session):
    """Test stage progression counts, conversion rates, and bottleneck detection [FR-004, FR-005]."""
    now = datetime.now(timezone.utc)

    # Insert lead events across funnel stages
    # LEAD: 100 distinct leads
    for i in range(100):
        db_session.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="LEAD", event_timestamp=now))

    # MQL: 60 distinct leads
    for i in range(60):
        db_session.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="MQL", event_timestamp=now))

    # SQL: 20 distinct leads
    for i in range(20):
        db_session.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="SQL", event_timestamp=now))

    # OPPORTUNITY: 10 distinct leads
    for i in range(10):
        db_session.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="OPPORTUNITY", event_timestamp=now))

    # CLOSED_WON: 8 distinct leads
    for i in range(8):
        db_session.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="CLOSED_WON", event_timestamp=now))

    db_session.commit()

    results = analyze_conversion_funnel(db_session, default_threshold_percent=30.0)

    # Stage 0: LEAD
    lead_stage = results[0]
    assert lead_stage["stage_name"] == "LEAD"
    assert lead_stage["stage_count"] == 100
    assert lead_stage["conversion_rate"] == 100.0
    assert lead_stage["dropoff_rate"] == 0.0
    assert lead_stage["is_bottleneck"] is False

    # Stage 1: MQL (100 -> 60 = 40% drop-off > 30% threshold => bottleneck)
    mql_stage = results[1]
    assert mql_stage["stage_name"] == "MQL"
    assert mql_stage["stage_count"] == 60
    assert mql_stage["conversion_rate"] == 60.0
    assert mql_stage["dropoff_rate"] == 40.0
    assert mql_stage["is_bottleneck"] is True

    # Stage 2: SQL (60 -> 20 = 66.67% drop-off > 30% threshold => bottleneck)
    sql_stage = results[2]
    assert sql_stage["stage_name"] == "SQL"
    assert sql_stage["stage_count"] == 20
    assert sql_stage["conversion_rate"] == 33.33
    assert sql_stage["dropoff_rate"] == 66.67
    assert sql_stage["is_bottleneck"] is True

    # Stage 3: OPPORTUNITY (20 -> 10 = 50% drop-off > 30% threshold => bottleneck)
    opp_stage = results[3]
    assert opp_stage["stage_name"] == "OPPORTUNITY"
    assert opp_stage["stage_count"] == 10
    assert opp_stage["conversion_rate"] == 50.0
    assert opp_stage["dropoff_rate"] == 50.0
    assert opp_stage["is_bottleneck"] is True

    # Stage 4: CLOSED_WON (10 -> 8 = 20% drop-off <= 30% threshold => NOT bottleneck)
    won_stage = results[4]
    assert won_stage["stage_name"] == "CLOSED_WON"
    assert won_stage["stage_count"] == 8
    assert won_stage["conversion_rate"] == 80.0
    assert won_stage["dropoff_rate"] == 20.0
    assert won_stage["is_bottleneck"] is False


def test_analyze_conversion_funnel_custom_threshold_override(db_session):
    """Test custom FunnelThreshold overrides default bottleneck flagging."""
    now = datetime.now(timezone.utc)

    for i in range(100):
        db_session.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="LEAD", event_timestamp=now))
    for i in range(60):
        db_session.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="MQL", event_timestamp=now))
    db_session.commit()

    # Set custom max drop-off for MQL to 50%
    set_funnel_threshold(db_session, "MQL", 50.0)

    # 100 -> 60 is 40% drop-off, which is <= 50% threshold
    results = analyze_conversion_funnel(db_session, default_threshold_percent=30.0)
    mql_stage = results[1]
    assert mql_stage["stage_name"] == "MQL"
    assert mql_stage["dropoff_rate"] == 40.0
    assert mql_stage["threshold_percent"] == 50.0
    assert mql_stage["is_bottleneck"] is False


def test_analyze_conversion_funnel_date_range_filters(db_session):
    """Test filtering funnel events by start_date_val and end_date_val."""
    now = datetime.now(timezone.utc)
    old_time = now - timedelta(days=10)
    recent_time = now - timedelta(days=2)

    # Insert old events
    for i in range(50):
        db_session.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="LEAD", event_timestamp=old_time))

    # Insert recent events
    for i in range(20):
        db_session.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="LEAD", event_timestamp=recent_time))

    db_session.commit()

    # Filter only recent events (last 5 days)
    start_filter = now - timedelta(days=5)
    results = analyze_conversion_funnel(db_session, start_date_val=start_filter)
    lead_stage = results[0]
    assert lead_stage["stage_count"] == 20


def test_invalid_date_range_raises_error(db_session):
    """Test start_date_val > end_date_val raises InvalidFunnelDateRangeError."""
    start = datetime(2026, 5, 10, tzinfo=timezone.utc)
    end = datetime(2026, 5, 1, tzinfo=timezone.utc)

    with pytest.raises(InvalidFunnelDateRangeError, match="start_date_val cannot be after end_date_val"):
        analyze_conversion_funnel(db_session, start_date_val=start, end_date_val=end)


def test_analyze_conversion_funnel_channel_campaign_filters(db_session):
    """Test filtering funnel lead events by channel_id and campaign_id."""
    channel_a = uuid.uuid4()
    channel_b = uuid.uuid4()
    campaign_x = uuid.uuid4()

    now = datetime.now(timezone.utc)

    # Channel A events
    for i in range(30):
        db_session.add(
            LeadEvent(
                lead_id=uuid.uuid4(),
                stage_name="LEAD",
                channel_id=channel_a,
                campaign_id=campaign_x,
                event_timestamp=now,
            )
        )

    # Channel B events
    for i in range(15):
        db_session.add(
            LeadEvent(
                lead_id=uuid.uuid4(),
                stage_name="LEAD",
                channel_id=channel_b,
                event_timestamp=now,
            )
        )

    db_session.commit()

    # Filter by channel_a
    results_a = analyze_conversion_funnel(db_session, channel_id=channel_a)
    assert results_a[0]["stage_count"] == 30

    # Filter by campaign_x
    results_cmp = analyze_conversion_funnel(db_session, campaign_id=campaign_x)
    assert results_cmp[0]["stage_count"] == 30


def test_analyze_conversion_funnel_custom_stage_order(db_session):
    """Test custom stage_order parameter."""
    now = datetime.now(timezone.utc)

    db_session.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="MQL", event_timestamp=now))
    db_session.add(LeadEvent(lead_id=uuid.uuid4(), stage_name="SQL", event_timestamp=now))
    db_session.commit()

    custom_order = ["MQL", "SQL", "CLOSED_WON"]
    results = analyze_conversion_funnel(db_session, stage_order=custom_order)

    assert [r["stage_name"] for r in results] == custom_order


def test_invalid_uuid_raises_funnel_service_error(db_session):
    """Test passing invalid UUID string raises FunnelServiceError."""
    with pytest.raises(FunnelServiceError, match="Invalid UUID value"):
        analyze_conversion_funnel(db_session, channel_id="not-a-uuid")
