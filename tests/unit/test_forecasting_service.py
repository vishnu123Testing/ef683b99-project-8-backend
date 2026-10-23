"""Unit tests for ForecastingService in backend/src/services/forecasting/service.py (FR-008, FR-009, FR-010, NFR-002, NFR-005)."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.connectors.crm_connector import CRMConnector
from src.connectors.forecast_connector import ForecastConnector
from src.models.database import Base
from src.models.deal import Deal, DealForecast
from src.models.forecast import ForecastEvaluationLog
from src.services.deals.crm_port import CRMDealRecord, CRMStageHistoryItem, CRMActivityItem
from src.services.forecasting.forecast_port import (
    ForecastError,
    ForecastProviderError,
    ForecastProviderPort,
    ForecastResult,
    ForecastTimeoutError,
    InsufficientDataError,
)
from src.services.forecasting.service import (
    ForecastComputationError,
    ForecastDealNotFoundError,
    ForecastingService,
    ForecastingServiceError,
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


def test_get_forecast_missing_deal(db_session: Session):
    """Verify get_forecast raises ForecastDealNotFoundError when deal does not exist."""
    service = ForecastingService(db=db_session)
    missing_id = str(uuid.uuid4())

    with pytest.raises(ForecastDealNotFoundError) as exc_info:
        service.get_forecast(missing_id)

    assert missing_id in str(exc_info.value)


def test_calculate_forecast_success(db_session: Session):
    """Verify calculating AI completion forecast for a deal with valid CRM history (FR-008, FR-009)."""
    # 1. Create deal record in database
    deal = Deal(
        title="Enterprise Software License",
        deal_value=150000.0,
        milestone_stage="negotiation",
        win_probability=0.8,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)

    crm = CRMConnector()
    forecast_connector = ForecastConnector(crm_provider=crm)
    service = ForecastingService(
        db=db_session,
        forecast_provider=forecast_connector,
        crm_provider=crm,
    )

    result = service.calculate_forecast(deal.id, trigger_event="STAGE_UPDATE")

    assert result is not None
    assert result["deal_id"] == deal.id
    assert result["status_code"] == "READY"
    assert result["confidence_score_pct"] is not None
    assert result["confidence_score_pct"] >= 50.0
    assert result["estimated_completion_date"] is not None

    # Verify DealForecast entity persisted in DB
    forecast_in_db = db_session.query(DealForecast).filter(DealForecast.deal_id == deal.id).first()
    assert forecast_in_db is not None
    assert forecast_in_db.status_code == "READY"
    assert forecast_in_db.confidence_score_pct == result["confidence_score_pct"]

    # Verify ForecastEvaluationLog entity persisted in DB (FR-010)
    log_in_db = (
        db_session.query(ForecastEvaluationLog)
        .filter(ForecastEvaluationLog.deal_id == deal.id)
        .first()
    )
    assert log_in_db is not None
    assert log_in_db.trigger_event == "STAGE_UPDATE"
    assert log_in_db.status_code == "READY"
    assert log_in_db.forecast_id == forecast_in_db.id


def test_calculate_forecast_fallback_insufficient_data(db_session: Session):
    """Verify fallback handling when CRM history is sparse or insufficient (FR-008, NFR-002)."""
    deal = Deal(
        title="Sparse Lead Deal",
        deal_value=5000.0,
        milestone_stage="qualification",
        win_probability=0.2,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)

    # Use simulated insufficient data forecast connector
    forecast_connector = ForecastConnector(simulate_insufficient_data=True)
    service = ForecastingService(
        db=db_session,
        forecast_provider=forecast_connector,
    )

    result = service.calculate_forecast(deal.id)

    assert result["deal_id"] == deal.id
    assert result["status_code"] == "INSUFFICIENT_DATA"
    assert result["estimated_completion_date"] is None
    assert result["confidence_score_pct"] is None

    # Verify persisted fallback state in DB
    forecast_in_db = db_session.query(DealForecast).filter(DealForecast.deal_id == deal.id).first()
    assert forecast_in_db is not None
    assert forecast_in_db.status_code == "INSUFFICIENT_DATA"
    assert forecast_in_db.estimated_completion_date is None

    # Verify evaluation log
    logs = service.get_evaluation_logs(deal.id)
    assert len(logs) == 1
    assert logs[0]["status_code"] == "INSUFFICIENT_DATA"
    assert "insufficient" in logs[0]["error_detail"].lower()


def test_calculate_forecast_exception_handling(db_session: Session):
    """Verify handling when forecast provider raises ForecastTimeoutError or ForecastProviderError."""
    deal = Deal(
        title="Timeout Test Deal",
        deal_value=25000.0,
        milestone_stage="proposal",
        win_probability=0.6,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)

    timeout_connector = ForecastConnector(simulate_timeout=True)
    service = ForecastingService(db=db_session, forecast_provider=timeout_connector)

    result = service.calculate_forecast(deal.id, trigger_event="MANUAL_RECALCULATE")

    assert result["status_code"] == "INSUFFICIENT_DATA"
    assert result["estimated_completion_date"] is None
    assert result["confidence_score_pct"] is None

    logs = service.get_evaluation_logs(deal.id)
    assert len(logs) == 1
    assert "timeout" in logs[0]["error_detail"].lower()


def test_explicit_dependency_injection_override(db_session: Session):
    """Verify method-level explicit dependency injection of custom ports (NFR-5)."""
    deal = Deal(
        title="DI Test Deal",
        deal_value=90000.0,
        milestone_stage="negotiation",
        win_probability=0.8,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)

    service = ForecastingService(db=db_session)  # Default ports in constructor

    mock_provider = MagicMock(spec=ForecastProviderPort)
    expected_date = datetime.now(timezone.utc)
    mock_provider.calculate_forecast.return_value = ForecastResult(
        deal_id=deal.id,
        estimated_completion_date=expected_date,
        confidence_score_pct=92.5,
        status_code="READY",
        model_metadata='{"model": "mock_v1"}',
    )

    result = service.calculate_forecast(
        deal.id,
        forecast_provider=mock_provider,
    )

    assert result["status_code"] == "READY"
    assert result["confidence_score_pct"] == 92.5
    assert mock_provider.calculate_forecast.called


def test_recalculate_forecast_events(db_session: Session):
    """Verify recalculate_forecast records stage, value, and activity trigger events (FR-010)."""
    deal = Deal(
        title="Recalculation Event Deal",
        deal_value=50000.0,
        milestone_stage="discovery",
        win_probability=0.4,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)

    crm = CRMConnector()
    forecast_connector = ForecastConnector(crm_provider=crm)
    service = ForecastingService(
        db=db_session,
        forecast_provider=forecast_connector,
        crm_provider=crm,
    )

    service.recalculate_forecast(deal.id, trigger_event="STAGE_UPDATE")
    service.recalculate_forecast(deal.id, trigger_event="VALUE_UPDATE")
    service.recalculate_forecast(deal.id, trigger_event="ACTIVITY_UPDATE")

    logs = service.get_evaluation_logs(deal.id)
    assert len(logs) == 3

    events = [log["trigger_event"] for log in logs]
    assert "ACTIVITY_UPDATE" in events
    assert "VALUE_UPDATE" in events
    assert "STAGE_UPDATE" in events


def test_recalculate_batch_forecasts(db_session: Session):
    """Verify batch recalculation across multiple deal IDs (FR-010)."""
    deal1 = Deal(title="Batch Deal 1", deal_value=10000.0, milestone_stage="proposal")
    deal2 = Deal(title="Batch Deal 2", deal_value=20000.0, milestone_stage="negotiation")
    db_session.add_all([deal1, deal2])
    db_session.commit()
    db_session.refresh(deal1)
    db_session.refresh(deal2)

    crm = CRMConnector()
    forecast_connector = ForecastConnector(crm_provider=crm)
    service = ForecastingService(
        db=db_session,
        forecast_provider=forecast_connector,
        crm_provider=crm,
    )

    results = service.recalculate_batch_forecasts(
        [deal1.id, deal2.id, "missing-deal-id"],
        trigger_event="BATCH_JOB",
    )

    assert len(results) == 3
    assert results[0]["deal_id"] == deal1.id
    assert results[0]["status_code"] in ("READY", "INSUFFICIENT_DATA")
    assert results[1]["deal_id"] == deal2.id
    assert results[2]["deal_id"] == "missing-deal-id"
    assert results[2]["status_code"] == "INSUFFICIENT_DATA"


def test_get_forecast_existing_and_initial(db_session: Session):
    """Verify get_forecast returns existing forecast or computes initial one."""
    deal = Deal(title="Get Forecast Deal", deal_value=30000.0, milestone_stage="proposal")
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)

    crm = CRMConnector()
    forecast_connector = ForecastConnector(crm_provider=crm)
    service = ForecastingService(
        db=db_session,
        forecast_provider=forecast_connector,
        crm_provider=crm,
    )

    # Initial fetch triggers calculation
    res1 = service.get_forecast(deal.id)
    assert res1["deal_id"] == deal.id
    assert res1["status_code"] == "READY"

    # Subsequent fetch returns existing record without recalculating
    res2 = service.get_forecast(deal.id)
    assert res2["id"] == res1["id"]
