"""Unit tests for ReportingService, ROI calculations, and lead quality score computation."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.analytics import AcquisitionChannel, Campaign, LeadEvent
from src.models.database import Base
from src.services.analytics.reporting import (
    CampaignLimitExceededError,
    CampaignNotFoundError,
    ChannelNotFoundError,
    InvalidDateRangeError,
    ReportingService,
    calculate_campaign_trends,
    calculate_channel_roi,
    compute_lead_quality_score,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for reporting service tests."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_calculate_channel_roi_zero_spend_safety():
    """Test ROI calculation returns ('N/A', None) for zero or negative spend [FR-003, AC-3]."""
    roi_val, roi_str = calculate_channel_roi(0.0, 5000.0)
    assert roi_val is None
    assert roi_str == "N/A"

    roi_val, roi_str = calculate_channel_roi(None, 1000.0)
    assert roi_val is None
    assert roi_str == "N/A"

    roi_val, roi_str = calculate_channel_roi(-100.0, 500.0)
    assert roi_val is None
    assert roi_str == "N/A"


def test_calculate_channel_roi_valid_spend():
    """Test ROI calculation percentage and formatting for valid spend values [FR-003]."""
    # 150% ROI
    roi_val, roi_str = calculate_channel_roi(1000.0, 2500.0)
    assert roi_val == 150.0
    assert roi_str == "150.00%"

    # -100% ROI (zero revenue)
    roi_val, roi_str = calculate_channel_roi(500.0, 0.0)
    assert roi_val == -100.0
    assert roi_str == "-100.00%"

    # 0% ROI (break even)
    roi_val, roi_str = calculate_channel_roi(1000.0, 1000.0)
    assert roi_val == 0.0
    assert roi_str == "0.00%"


def test_compute_lead_quality_score():
    """Test rule-based lead quality score computation and raw score clamping [FR-002]."""
    # Raw score provided
    assert compute_lead_quality_score("MQL", raw_score=85) == 85
    assert compute_lead_quality_score("MQL", raw_score=150) == 100
    assert compute_lead_quality_score("MQL", raw_score=-20) == 0

    # Stage-based score calculation
    won_score = compute_lead_quality_score("Closed Won", deal_value=15000.0, revenue_amount=15000.0)
    assert won_score == 100  # 50 base + 30 stage + 10 deal_val + 10 revenue

    mql_score = compute_lead_quality_score("MQL", deal_value=500.0)
    assert mql_score == 60  # 50 base + 10 stage


def test_get_channel_acquisition_report_aggregation_and_zero_spend(db_session):
    """Test channel acquisition aggregation, ROI calculation, and zero spend safety [FR-001, FR-003, AC-1, AC-3]."""
    # Channel 1: Paid Search (Spend $2000, Revenue $5000)
    ch1 = AcquisitionChannel(channel_name="Paid Search", spend_amount=2000.0)
    # Channel 2: Organic Referral (Spend $0, Revenue $3000)
    ch2 = AcquisitionChannel(channel_name="Organic Referral", spend_amount=0.0)
    db_session.add_all([ch1, ch2])
    db_session.commit()

    now = datetime.now(timezone.utc)
    lead1 = uuid.uuid4()
    lead2 = uuid.uuid4()
    lead3 = uuid.uuid4()

    # Lead events for Paid Search
    e1 = LeadEvent(lead_id=lead1, channel_id=ch1.id, stage_name="SQL", quality_score=80, revenue_amount=2000.0, event_timestamp=now)
    e2 = LeadEvent(lead_id=lead2, channel_id=ch1.id, stage_name="Closed Won", quality_score=90, revenue_amount=3000.0, event_timestamp=now)

    # Lead event for Organic Referral
    e3 = LeadEvent(lead_id=lead3, channel_id=ch2.id, stage_name="Closed Won", quality_score=95, revenue_amount=3000.0, event_timestamp=now)

    db_session.add_all([e1, e2, e3])
    db_session.commit()

    service = ReportingService(db_session)
    report = service.get_channel_acquisition_report(sort_by="channel_name", sort_order="asc")

    assert len(report) == 2

    # Organic Referral
    org = next(r for r in report if r["channel_name"] == "Organic Referral")
    assert org["lead_volume"] == 1
    assert org["quality_score"] == 95.0
    assert org["spend"] == 0.0
    assert org["revenue"] == 3000.0
    assert org["roi"] is None
    assert org["roi_str"] == "N/A"

    # Paid Search
    paid = next(r for r in report if r["channel_name"] == "Paid Search")
    assert paid["lead_volume"] == 2
    assert paid["quality_score"] == 85.0
    assert paid["spend"] == 2000.0
    assert paid["revenue"] == 5000.0
    assert paid["roi"] == 150.0
    assert paid["roi_str"] == "150.00%"


def test_get_channel_acquisition_report_sorting(db_session):
    """Test channel acquisition report sorting by quality_score descending [FR-002, AC-2]."""
    ch1 = AcquisitionChannel(channel_name="Email Marketing", spend_amount=500.0)
    ch2 = AcquisitionChannel(channel_name="Social Ads", spend_amount=1000.0)
    ch3 = AcquisitionChannel(channel_name="SEO", spend_amount=300.0)
    db_session.add_all([ch1, ch2, ch3])
    db_session.commit()

    now = datetime.now(timezone.utc)
    # High quality for SEO (90)
    e1 = LeadEvent(lead_id=uuid.uuid4(), channel_id=ch3.id, stage_name="SQL", quality_score=90, event_timestamp=now)
    # Medium quality for Email (70)
    e2 = LeadEvent(lead_id=uuid.uuid4(), channel_id=ch1.id, stage_name="MQL", quality_score=70, event_timestamp=now)
    # Low quality for Social (50)
    e3 = LeadEvent(lead_id=uuid.uuid4(), channel_id=ch2.id, stage_name="Lead", quality_score=50, event_timestamp=now)

    db_session.add_all([e1, e2, e3])
    db_session.commit()

    service = ReportingService(db_session)
    report = service.get_channel_acquisition_report(sort_by="quality_score", sort_order="desc")

    channel_names = [r["channel_name"] for r in report]
    assert channel_names == ["SEO", "Email Marketing", "Social Ads"]


def test_get_channel_acquisition_report_filters(db_session):
    """Test date filtering and channel_id filtering."""
    ch1 = AcquisitionChannel(channel_name="Events", spend_amount=1000.0)
    ch2 = AcquisitionChannel(channel_name="Webinars", spend_amount=800.0)
    db_session.add_all([ch1, ch2])
    db_session.commit()

    base_time = datetime(2026, 1, 15, 12, 0, tzinfo=timezone.utc)
    old_time = datetime(2025, 12, 1, 12, 0, tzinfo=timezone.utc)

    e1 = LeadEvent(lead_id=uuid.uuid4(), channel_id=ch1.id, stage_name="MQL", quality_score=75, event_timestamp=base_time)
    e2 = LeadEvent(lead_id=uuid.uuid4(), channel_id=ch1.id, stage_name="MQL", quality_score=60, event_timestamp=old_time)

    db_session.add_all([e1, e2])
    db_session.commit()

    service = ReportingService(db_session)

    # Filter by date range (only 2026 event)
    report = service.get_channel_acquisition_report(
        start_date_val=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_date_val=datetime(2026, 1, 31, tzinfo=timezone.utc),
    )
    ch1_report = next(r for r in report if r["channel_id"] == str(ch1.id))
    assert ch1_report["lead_volume"] == 1
    assert ch1_report["quality_score"] == 75.0

    # Filter by specific channel_id
    single_report = service.get_channel_acquisition_report(channel_id=str(ch2.id))
    assert len(single_report) == 1
    assert single_report[0]["channel_name"] == "Webinars"


def test_get_channel_acquisition_report_errors(db_session):
    """Test exception raising for invalid date range and missing channel_id."""
    service = ReportingService(db_session)

    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)

    # Invalid date range (start > end)
    with pytest.raises(InvalidDateRangeError):
        service.get_channel_acquisition_report(start_date_val=now, end_date_val=yesterday)

    # Missing channel ID
    fake_id = str(uuid.uuid4())
    with pytest.raises(ChannelNotFoundError):
        service.get_channel_acquisition_report(channel_id=fake_id)

    # Invalid UUID format
    with pytest.raises(ChannelNotFoundError):
        service.get_channel_acquisition_report(channel_id="invalid-uuid")


def test_get_campaign_comparison_valid_campaigns(db_session):
    """Test comparative campaign trend calculation for up to 5 campaigns [FR-006]."""
    # Create 3 campaigns
    c1 = Campaign(campaign_name="Spring Promo", budget=1000.0)
    c2 = Campaign(campaign_name="Summer Sale", budget=2000.0)
    c3 = Campaign(campaign_name="Free Trial", budget=0.0)
    db_session.add_all([c1, c2, c3])
    db_session.commit()

    d1 = datetime(2026, 3, 1, 10, 0, tzinfo=timezone.utc)
    d2 = datetime(2026, 3, 2, 10, 0, tzinfo=timezone.utc)

    l1, l2, l3, l4 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

    # Events for Campaign 1 (c1)
    e1 = LeadEvent(lead_id=l1, campaign_id=c1.id, stage_name="SQL", quality_score=80, revenue_amount=500.0, event_timestamp=d1)
    e2 = LeadEvent(lead_id=l2, campaign_id=c1.id, stage_name="Closed Won", quality_score=90, revenue_amount=1000.0, event_timestamp=d2)

    # Events for Campaign 2 (c2)
    e3 = LeadEvent(lead_id=l3, campaign_id=c2.id, stage_name="MQL", quality_score=40, revenue_amount=0.0, event_timestamp=d1)
    e4 = LeadEvent(lead_id=l4, campaign_id=c2.id, stage_name="SQL", quality_score=60, revenue_amount=300.0, event_timestamp=d2)

    db_session.add_all([e1, e2, e3, e4])
    db_session.commit()

    service = ReportingService(db_session)
    results = service.get_campaign_comparison([str(c1.id), str(c2.id), str(c3.id)])

    assert len(results) == 3

    # Campaign 1 assertions
    c1_res = next(r for r in results if r["campaign_id"] == str(c1.id))
    assert c1_res["campaign_name"] == "Spring Promo"
    assert c1_res["lead_volume"] == 2
    assert c1_res["budget"] == 1000.0
    assert c1_res["cost_per_lead"] == 500.0  # 1000 / 2
    assert c1_res["cost_per_lead_str"] == "$500.00"
    assert c1_res["total_revenue"] == 1500.0
    assert c1_res["engagement_rate"] == 100.0  # 2 of 2 quality_score >= 50
    assert len(c1_res["time_series"]) == 2

    # Campaign 2 assertions
    c2_res = next(r for r in results if r["campaign_id"] == str(c2.id))
    assert c2_res["lead_volume"] == 2
    assert c2_res["cost_per_lead"] == 1000.0  # 2000 / 2
    assert c2_res["engagement_rate"] == 50.0  # 1 of 2 quality_score >= 50

    # Campaign 3 assertions (zero leads, zero budget)
    c3_res = next(r for r in results if r["campaign_id"] == str(c3.id))
    assert c3_res["lead_volume"] == 0
    assert c3_res["cost_per_lead"] is None
    assert c3_res["cost_per_lead_str"] == "N/A"
    assert c3_res["engagement_rate"] == 0.0
    assert c3_res["time_series"] == []


def test_get_campaign_comparison_max_limit_exceeded(db_session):
    """Test that attempting to compare >5 campaigns raises CampaignLimitExceededError [FR-006]."""
    service = ReportingService(db_session)
    fake_ids = [str(uuid.uuid4()) for _ in range(6)]

    with pytest.raises(CampaignLimitExceededError):
        service.get_campaign_comparison(fake_ids)


def test_get_campaign_comparison_errors(db_session):
    """Test CampaignNotFoundError and InvalidDateRangeError handling [FR-006]."""
    c1 = Campaign(campaign_name="Active Campaign", budget=500.0)
    db_session.add(c1)
    db_session.commit()

    service = ReportingService(db_session)

    # Missing campaign ID
    fake_id = str(uuid.uuid4())
    with pytest.raises(CampaignNotFoundError):
        service.get_campaign_comparison([str(c1.id), fake_id])

    # Invalid UUID string
    with pytest.raises(CampaignNotFoundError):
        service.get_campaign_comparison(["not-a-valid-uuid"])

    # Invalid date range
    now = datetime.now(timezone.utc)
    yesterday = now - timedelta(days=1)
    with pytest.raises(InvalidDateRangeError):
        service.get_campaign_comparison([str(c1.id)], start_date_val=now, end_date_val=yesterday)

