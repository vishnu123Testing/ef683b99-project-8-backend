"""Unit tests for ForecastEvaluationLog ORM model in backend/src/models/forecast.py."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.deal import Deal, DealForecast
from src.models.forecast import (
    ForecastEvaluationLog,
    ForecastLog,
    ForecastRecalculationLog,
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


def test_forecast_evaluation_log_defaults_and_repr():
    """Test ForecastEvaluationLog instantiation defaults, repr string, and to_dict method."""
    deal_id = str(uuid.uuid4())
    log_entry = ForecastEvaluationLog(deal_id=deal_id)

    assert log_entry.deal_id == deal_id
    assert log_entry.status_code == "COMPUTING"
    assert log_entry.trigger_event == "RECALCULATION"
    assert log_entry.forecast_id is None
    assert log_entry.estimated_completion_date is None
    assert log_entry.confidence_score_pct is None
    assert log_entry.correlation_id is None

    repr_str = repr(log_entry)
    assert deal_id in repr_str
    assert "COMPUTING" in repr_str
    assert "RECALCULATION" in repr_str

    d = log_entry.to_dict()
    assert d["deal_id"] == deal_id
    assert d["status_code"] == "COMPUTING"
    assert d["trigger_event"] == "RECALCULATION"
    assert d["forecast_id"] is None
    assert d["estimated_completion_date"] is None
    assert d["confidence_score_pct"] is None
    assert d["correlation_id"] is None


def test_forecast_evaluation_log_aliases():
    """Test that ForecastRecalculationLog and ForecastLog are aliases for ForecastEvaluationLog."""
    assert ForecastRecalculationLog is ForecastEvaluationLog
    assert ForecastLog is ForecastEvaluationLog


def test_forecast_evaluation_log_persistence_and_relationships(db_session: Session):
    """Test inserting Deal, DealForecast, and ForecastEvaluationLog records and testing relationships."""
    now = datetime.now(timezone.utc)

    # 1. Create and persist Deal
    deal = Deal(
        title="Enterprise CRM Contract",
        deal_value=250000.0,
        milestone_stage="negotiation",
        win_probability=0.85,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)

    # 2. Create and persist DealForecast
    forecast = DealForecast(
        deal_id=deal.id,
        estimated_completion_date=now,
        confidence_score_pct=88.5,
        status_code="READY",
    )
    db_session.add(forecast)
    db_session.commit()
    db_session.refresh(forecast)

    # 3. Create and persist ForecastEvaluationLog
    correlation_id = str(uuid.uuid4())
    log_entry = ForecastEvaluationLog(
        deal_id=deal.id,
        forecast_id=forecast.id,
        estimated_completion_date=now,
        confidence_score_pct=88.5,
        status_code="READY",
        trigger_event="STAGE_CHANGE",
        correlation_id=correlation_id,
        model_metadata="v2.1-ensemble",
        error_detail=None,
    )
    db_session.add(log_entry)
    db_session.commit()
    db_session.refresh(log_entry)

    # Assert persistence
    assert log_entry.id is not None
    assert log_entry.deal_id == deal.id
    assert log_entry.forecast_id == forecast.id
    assert log_entry.confidence_score_pct == 88.5
    assert log_entry.status_code == "READY"
    assert log_entry.trigger_event == "STAGE_CHANGE"
    assert log_entry.correlation_id == correlation_id
    assert log_entry.model_metadata == "v2.1-ensemble"

    # Assert relationships
    assert log_entry.deal is not None
    assert log_entry.deal.title == "Enterprise CRM Contract"
    assert log_entry.forecast is not None
    assert log_entry.forecast.status_code == "READY"

    # Verify dictionary serialization
    d = log_entry.to_dict()
    assert d["id"] == log_entry.id
    assert d["deal_id"] == deal.id
    assert d["forecast_id"] == forecast.id
    assert d["estimated_completion_date"] == now.isoformat()
    assert d["confidence_score_pct"] == 88.5
    assert d["status_code"] == "READY"
    assert d["trigger_event"] == "STAGE_CHANGE"
    assert d["correlation_id"] == correlation_id
    assert d["model_metadata"] == "v2.1-ensemble"
    assert d["error_detail"] is None
    assert d["calculated_on"] is not None


def test_forecast_evaluation_log_query_filtering(db_session: Session):
    """Test querying evaluation log entries by deal_id, correlation_id, and trigger_event."""
    deal = Deal(
        title="Hardware Procurement",
        deal_value=15000.0,
        milestone_stage="qualification",
        win_probability=0.30,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)

    corr_id1 = str(uuid.uuid4())
    corr_id2 = str(uuid.uuid4())

    log1 = ForecastEvaluationLog(
        deal_id=deal.id,
        trigger_event="INITIAL_EVALUATION",
        correlation_id=corr_id1,
        status_code="INSUFFICIENT_DATA",
        error_detail="Not enough historical stage transitions.",
    )
    log2 = ForecastEvaluationLog(
        deal_id=deal.id,
        trigger_event="STAKEHOLDER_UPDATE",
        correlation_id=corr_id2,
        status_code="READY",
        confidence_score_pct=65.0,
    )
    db_session.add_all([log1, log2])
    db_session.commit()

    logs_for_deal = (
        db_session.query(ForecastEvaluationLog)
        .filter(ForecastEvaluationLog.deal_id == deal.id)
        .all()
    )
    assert len(logs_for_deal) == 2

    log_by_corr = (
        db_session.query(ForecastEvaluationLog)
        .filter(ForecastEvaluationLog.correlation_id == corr_id1)
        .first()
    )
    assert log_by_corr is not None
    assert log_by_corr.status_code == "INSUFFICIENT_DATA"
    assert log_by_corr.error_detail == "Not enough historical stage transitions."
