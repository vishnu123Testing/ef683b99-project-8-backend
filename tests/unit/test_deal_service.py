"""Unit tests for DealService in backend/src/services/deals/service.py."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.deal import ActionItem, Deal, DealForecast, Stakeholder
from src.services.deals.service import (
    DealNotFoundError,
    DealService,
    InvalidDealStageError,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_calculate_deal_age(db_session: Session):
    """Test calculation of deal age in days [FR-002]."""
    service = DealService(db=db_session)

    assert service.calculate_deal_age(None) == 0

    now = datetime.now(timezone.utc)
    ten_days_ago = now - timedelta(days=10)
    assert service.calculate_deal_age(ten_days_ago) == 10

    # Naive datetime handling
    naive_dt = datetime.now() - timedelta(days=3)
    assert service.calculate_deal_age(naive_dt) in (2, 3)


def test_calculate_win_probability(db_session: Session):
    """Test calculation and lookup of win probability score by stage [FR-002]."""
    service = DealService(db=db_session)

    # Standard stages
    assert service.calculate_win_probability("lead qualification") == 0.20
    assert service.calculate_win_probability("Discovery Call") == 0.40
    assert service.calculate_win_probability("Solution Proposal") == 0.60
    assert service.calculate_win_probability("Contract Negotiation") == 0.80
    assert service.calculate_win_probability("Closed Won") == 1.00
    assert service.calculate_win_probability("Closed Lost") == 0.00

    # Unknown stage fallback
    assert service.calculate_win_probability("Custom Stage") == 0.50

    # Explicit override
    assert service.calculate_win_probability("lead qualification", current_probability=0.95) == 0.95


def test_create_deal(db_session: Session):
    """Test creating a deal record through DealService [FR-001, FR-002]."""
    service = DealService(db=db_session)

    result = service.create_deal(
        title="Acme SaaS Expansion",
        deal_value=75000.0,
        milestone_stage="Solution Proposal",
    )

    assert result["id"] is not None
    assert result["title"] == "Acme SaaS Expansion"
    assert result["deal_value"] == 75000.0
    assert result["milestone_stage"] == "Solution Proposal"
    assert result["win_probability"] == 0.60
    assert result["age_in_days"] == 0

    # Verify persisted in database
    deal_in_db = db_session.query(Deal).filter(Deal.id == result["id"]).first()
    assert deal_in_db is not None
    assert deal_in_db.title == "Acme SaaS Expansion"

    # Verify forecast record initialized in COMPUTING status
    forecast_in_db = db_session.query(DealForecast).filter(DealForecast.deal_id == result["id"]).first()
    assert forecast_in_db is not None
    assert forecast_in_db.status_code == "COMPUTING"


def test_get_deal_by_id_and_not_found(db_session: Session):
    """Test retrieving a deal by ID and handling missing deal exception."""
    service = DealService(db=db_session)

    created = service.create_deal(
        title="Test Deal",
        deal_value=10000.0,
        milestone_stage="qualification",
    )

    deal = service.get_deal_by_id(created["id"])
    assert deal.id == created["id"]
    assert deal.title == "Test Deal"

    with pytest.raises(DealNotFoundError):
        service.get_deal_by_id("non-existent-uuid")


def test_get_pipeline_deals_with_and_without_filter(db_session: Session):
    """Test querying pipeline deals with stage filtering [FR-001, FR-002]."""
    service = DealService(db=db_session)

    service.create_deal("Deal A", 50000.0, "qualification")
    service.create_deal("Deal B", 100000.0, "proposal")
    service.create_deal("Deal C", 200000.0, "proposal")

    all_deals = service.get_pipeline_deals()
    assert len(all_deals) == 3

    proposal_deals = service.get_pipeline_deals(stage="proposal")
    assert len(proposal_deals) == 2
    for d in proposal_deals:
        assert d["milestone_stage"] == "proposal"
        assert "age_in_days" in d

    qual_deals = service.get_pipeline_deals(stage="qualification")
    assert len(qual_deals) == 1
    assert qual_deals[0]["title"] == "Deal A"


def test_update_deal_stage_and_forecast_dispatch(db_session: Session):
    """Test updating deal milestone stage and dispatching forecast recalculation [FR-003, NFR-001]."""
    mock_forecasting = MagicMock()
    service = DealService(db=db_session, forecasting_service=mock_forecasting)

    deal_payload = service.create_deal("TechCorp Renewal", 120000.0, "qualification")
    deal_id = deal_payload["id"]

    updated = service.update_deal_stage(deal_id, new_stage="Contract Negotiation")

    assert updated["milestone_stage"] == "Contract Negotiation"
    assert updated["win_probability"] == 0.80

    # Verify forecast status set to COMPUTING
    forecast = db_session.query(DealForecast).filter(DealForecast.deal_id == deal_id).first()
    assert forecast is not None
    assert forecast.status_code == "COMPUTING"

    # Verify mock forecasting service invocation
    mock_forecasting.recalculate_forecast.assert_called_with(deal_id, trigger_event="STAGE_UPDATE")


def test_update_deal_stage_validation_errors(db_session: Session):
    """Test stage update input validation and missing deal error handling."""
    service = DealService(db=db_session)

    created = service.create_deal("Validation Deal", 15000.0, "discovery")

    with pytest.raises(InvalidDealStageError):
        service.update_deal_stage(created["id"], new_stage="")

    with pytest.raises(InvalidDealStageError):
        service.update_deal_stage(created["id"], new_stage="   ")

    with pytest.raises(DealNotFoundError):
        service.update_deal_stage("invalid-id", new_stage="proposal")


def test_get_deal_summary(db_session: Session):
    """Test deal summary aggregation including action item and stakeholder counts."""
    service = DealService(db=db_session)

    deal = Deal(
        title="Big Enterprise Deal",
        deal_value=500000.0,
        milestone_stage="proposal",
        win_probability=0.60,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)

    action1 = ActionItem(
        deal_id=deal.id,
        title="Schedule demo",
        assigned_owner_id="usr_1",
        target_due_date=datetime.now(timezone.utc),
    )
    stakeholder1 = Stakeholder(
        deal_id=deal.id,
        full_name="Alice Smith",
        email="alice@example.com",
        org_role="CTO",
        influence_level="High",
    )
    db_session.add_all([action1, stakeholder1])
    db_session.commit()

    summary = service.get_deal_summary(deal.id)

    assert summary["id"] == deal.id
    assert summary["title"] == "Big Enterprise Deal"
    assert summary["action_items_count"] == 1
    assert summary["stakeholders_count"] == 1
    assert "age_in_days" in summary
