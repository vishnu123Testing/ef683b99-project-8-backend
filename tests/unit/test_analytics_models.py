"""Unit tests for AcquisitionChannel, Campaign, LeadEvent, FunnelThreshold, and ReportAuditLog ORM models."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.models.analytics import (
    AcquisitionChannel,
    Campaign,
    FunnelThreshold,
    LeadEvent,
    ReportAuditLog,
)
from src.models.database import Base


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for testing models."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_acquisition_channel_model_to_dict_and_repr():
    """Test AcquisitionChannel initialization, repr, and to_dict method."""
    now = datetime.now(timezone.utc)
    channel = AcquisitionChannel(
        channel_name="Organic Search",
        spend_amount=1500.50,
        created_on=now,
    )

    assert channel.channel_name == "Organic Search"
    assert channel.spend_amount == 1500.50
    assert channel.created_on == now

    repr_str = repr(channel)
    assert "Organic Search" in repr_str
    assert "1500.5" in repr_str

    d = channel.to_dict()
    assert d["channel_name"] == "Organic Search"
    assert d["spend_amount"] == 1500.50
    assert d["created_on"] == now.isoformat()


def test_campaign_model_to_dict_and_repr():
    """Test Campaign initialization, repr, and to_dict method."""
    start_dt = datetime(2026, 1, 1, 0, 0, tzinfo=timezone.utc)
    end_dt = datetime(2026, 3, 31, 23, 59, tzinfo=timezone.utc)
    campaign = Campaign(
        campaign_name="Q1 Product Launch",
        start_date_val=start_dt,
        end_date_val=end_dt,
        budget=25000.0,
    )

    assert campaign.campaign_name == "Q1 Product Launch"
    assert campaign.start_date_val == start_dt
    assert campaign.end_date_val == end_dt
    assert campaign.budget == 25000.0

    repr_str = repr(campaign)
    assert "Q1 Product Launch" in repr_str
    assert "25000.0" in repr_str

    d = campaign.to_dict()
    assert d["campaign_name"] == "Q1 Product Launch"
    assert d["start_date_val"] == start_dt.isoformat()
    assert d["end_date_val"] == end_dt.isoformat()
    assert d["budget"] == 25000.0


def test_lead_event_model_to_dict_and_repr():
    """Test LeadEvent initialization, repr, and to_dict method."""
    now = datetime.now(timezone.utc)
    lead_id = uuid.uuid4()
    event = LeadEvent(
        lead_id=lead_id,
        stage_name="MQL",
        quality_score=85,
        deal_value=5000.0,
        revenue_amount=0.0,
        postal_code="90210",
        event_timestamp=now,
    )

    assert event.lead_id == lead_id
    assert event.stage_name == "MQL"
    assert event.quality_score == 85
    assert event.deal_value == 5000.0
    assert event.postal_code == "90210"

    repr_str = repr(event)
    assert "MQL" in repr_str
    assert str(lead_id) in repr_str

    d = event.to_dict()
    assert d["lead_id"] == str(lead_id)
    assert d["stage_name"] == "MQL"
    assert d["quality_score"] == 85
    assert d["deal_value"] == 5000.0
    assert d["postal_code"] == "90210"
    assert d["event_timestamp"] == now.isoformat()


def test_funnel_threshold_model_to_dict_and_repr():
    """Test FunnelThreshold initialization, repr, and to_dict method."""
    now = datetime.now(timezone.utc)
    threshold = FunnelThreshold(
        stage_name="Lead to MQL",
        max_dropoff_percent=35.5,
        updated_on=now,
    )

    assert threshold.stage_name == "Lead to MQL"
    assert threshold.max_dropoff_percent == 35.5
    assert threshold.updated_on == now

    repr_str = repr(threshold)
    assert "Lead to MQL" in repr_str
    assert "35.5" in repr_str

    d = threshold.to_dict()
    assert d["stage_name"] == "Lead to MQL"
    assert d["max_dropoff_percent"] == 35.5
    assert d["updated_on"] == now.isoformat()


def test_report_audit_log_model_to_dict_and_repr():
    """Test ReportAuditLog initialization, repr, and to_dict method."""
    user_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    audit = ReportAuditLog(
        user_id=user_id,
        action_type="UPDATE_THRESHOLD",
        config_delta='{"stage": "MQL", "max_dropoff_percent": 30.0}',
        created_on=now,
    )

    assert audit.user_id == user_id
    assert audit.action_type == "UPDATE_THRESHOLD"
    assert audit.config_delta == '{"stage": "MQL", "max_dropoff_percent": 30.0}'

    repr_str = repr(audit)
    assert "UPDATE_THRESHOLD" in repr_str
    assert str(user_id) in repr_str

    d = audit.to_dict()
    assert d["user_id"] == str(user_id)
    assert d["action_type"] == "UPDATE_THRESHOLD"
    assert d["config_delta"] == '{"stage": "MQL", "max_dropoff_percent": 30.0}'
    assert d["created_on"] == now.isoformat()


def test_analytics_models_persistence_and_relationships(db_session: Session):
    """Test saving analytics models to SQLite and verifying relationships and constraints."""
    channel = AcquisitionChannel(
        channel_name="Google Ads",
        spend_amount=5000.0,
    )
    campaign = Campaign(
        campaign_name="Spring Promo 2026",
        budget=10000.0,
    )
    db_session.add(channel)
    db_session.add(campaign)
    db_session.commit()
    db_session.refresh(channel)
    db_session.refresh(campaign)

    assert channel.id is not None
    assert campaign.id is not None

    lead_id = uuid.uuid4()
    lead_event = LeadEvent(
        lead_id=lead_id,
        channel_id=channel.id,
        campaign_id=campaign.id,
        stage_name="SQL",
        quality_score=90,
        deal_value=12000.0,
        revenue_amount=12000.0,
        postal_code="10001",
    )
    db_session.add(lead_event)
    db_session.commit()
    db_session.refresh(lead_event)

    assert lead_event.id is not None
    assert lead_event.channel_id == channel.id
    assert lead_event.campaign_id == campaign.id

    # Verify relationships
    assert len(channel.lead_events) == 1
    assert channel.lead_events[0].id == lead_event.id
    assert lead_event.channel.channel_name == "Google Ads"

    assert len(campaign.lead_events) == 1
    assert campaign.lead_events[0].id == lead_event.id
    assert lead_event.campaign.campaign_name == "Spring Promo 2026"

    # Test FunnelThreshold unique constraint on stage_name
    threshold1 = FunnelThreshold(
        stage_name="SQL to Opportunity",
        max_dropoff_percent=20.0,
    )
    db_session.add(threshold1)
    db_session.commit()

    threshold2 = FunnelThreshold(
        stage_name="SQL to Opportunity",
        max_dropoff_percent=25.0,
    )
    db_session.add(threshold2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    # Test ReportAuditLog persistence
    user_id = uuid.uuid4()
    audit_log = ReportAuditLog(
        user_id=user_id,
        action_type="CREATE_METRIC_RULE",
        config_delta='{"rule": "custom_roi"}',
    )
    db_session.add(audit_log)
    db_session.commit()
    db_session.refresh(audit_log)

    assert audit_log.id is not None
    assert audit_log.user_id == user_id
    assert audit_log.action_type == "CREATE_METRIC_RULE"
