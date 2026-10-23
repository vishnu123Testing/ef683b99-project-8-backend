"""Unit tests for Celery forecast recalculation worker tasks in backend/src/workers/forecast_worker.py [FR-010, NFR-002, NFR-004]."""

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.connectors.crm_connector import CRMConnector
from src.connectors.forecast_connector import ForecastConnector
from src.models.database import Base
from src.models.deal import Deal, DealForecast
from src.models.forecast import ForecastEvaluationLog
from src.workers.forecast_worker import (
    celery_app,
    process_batch_forecast_recalculations,
    process_forecast_recalculation,
    recalculate_batch_forecasts_task,
    recalculate_deal_forecast_task,
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


@pytest.fixture
def sample_deal(db_session: Session) -> Deal:
    """Fixture creating a sample Deal record in the test database."""
    deal = Deal(
        title="Global Enterprise Agreement",
        deal_value=500000.0,
        milestone_stage="negotiation",
        win_probability=0.85,
    )
    db_session.add(deal)
    db_session.commit()
    db_session.refresh(deal)
    return deal


def test_process_forecast_recalculation_happy_path(db_session: Session, sample_deal: Deal):
    """Verify single deal forecast recalculation computes timeline and logs evaluation [FR-010, NFR-002, NFR-004]."""
    crm = CRMConnector()
    forecast_conn = ForecastConnector(crm_provider=crm)

    result = process_forecast_recalculation(
        deal_id=sample_deal.id,
        trigger_event="STAGE_UPDATE",
        db=db_session,
        crm_provider=crm,
        forecast_provider=forecast_conn,
        correlation_id="test-cid-forecast-123",
    )

    assert result["status"] == "success"
    assert result["deal_id"] == sample_deal.id
    assert result["trigger_event"] == "STAGE_UPDATE"
    assert result["correlation_id"] == "test-cid-forecast-123"

    forecast_payload = result["forecast"]
    assert forecast_payload["deal_id"] == sample_deal.id
    assert forecast_payload["status_code"] == "READY"
    assert forecast_payload["confidence_score_pct"] is not None
    assert forecast_payload["confidence_score_pct"] >= 50.0

    # Verify DealForecast persisted in DB
    db_forecast = (
        db_session.query(DealForecast)
        .filter(DealForecast.deal_id == sample_deal.id)
        .first()
    )
    assert db_forecast is not None
    assert db_forecast.status_code == "READY"

    # Verify ForecastEvaluationLog recorded with correlation ID in DB
    db_log = (
        db_session.query(ForecastEvaluationLog)
        .filter(ForecastEvaluationLog.deal_id == sample_deal.id)
        .first()
    )
    assert db_log is not None
    assert db_log.correlation_id == "test-cid-forecast-123"
    assert db_log.trigger_event == "STAGE_UPDATE"


def test_process_forecast_recalculation_deal_not_found(db_session: Session):
    """Verify handling when specified deal_id is missing from database."""
    missing_id = str(uuid.uuid4())

    result = process_forecast_recalculation(
        deal_id=missing_id,
        trigger_event="VALUE_UPDATE",
        db=db_session,
        correlation_id="missing-deal-cid",
    )

    assert result["status"] == "error"
    assert result["deal_id"] == missing_id
    assert result["correlation_id"] == "missing-deal-cid"
    assert "not found" in result["error"].lower()


def test_process_forecast_recalculation_auto_db_session(sample_deal: Deal):
    """Verify process_forecast_recalculation manages DB session lifecycle when db is None."""
    with patch("src.workers.forecast_worker.SessionLocal") as mock_session_local:
        mock_db = MagicMock()
        mock_session_local.return_value = mock_db

        with patch("src.workers.forecast_worker.ForecastingService") as mock_service_cls:
            mock_service_inst = MagicMock()
            mock_service_inst.recalculate_forecast.return_value = {
                "id": "fc-123",
                "deal_id": sample_deal.id,
                "status_code": "READY",
                "confidence_score_pct": 80.0,
                "estimated_completion_date": "2026-07-01T00:00:00Z",
            }
            mock_service_cls.return_value = mock_service_inst

            res = process_forecast_recalculation(
                deal_id=sample_deal.id,
                trigger_event="ACTIVITY_UPDATE",
                db=None,
            )

            assert res["status"] == "success"
            mock_session_local.assert_called_once()
            mock_db.close.assert_called_once()


def test_process_batch_forecast_recalculations(db_session: Session, sample_deal: Deal):
    """Verify batch forecast recalculation processes multiple deal records [FR-010]."""
    deal2 = Deal(
        title="Mid-Market Subscription",
        deal_value=75000.0,
        milestone_stage="proposal",
        win_probability=0.6,
    )
    db_session.add(deal2)
    db_session.commit()

    crm = CRMConnector()
    forecast_conn = ForecastConnector(crm_provider=crm)

    result = process_batch_forecast_recalculations(
        deal_ids=[sample_deal.id, deal2.id],
        trigger_event="BATCH_REFRESH",
        db=db_session,
        crm_provider=crm,
        forecast_provider=forecast_conn,
        correlation_id="batch-cid-999",
    )

    assert result["status"] == "success"
    assert result["processed_count"] == 2
    assert result["correlation_id"] == "batch-cid-999"
    assert len(result["forecasts"]) == 2


def test_recalculate_deal_forecast_task(db_session: Session, sample_deal: Deal):
    """Verify Celery task execution for single deal recalculation [FR-010, NFR-002, NFR-004]."""
    crm = CRMConnector()
    forecast_conn = ForecastConnector(crm_provider=crm)

    with patch("src.workers.forecast_worker.SessionLocal", return_value=db_session):
        db_session.close = MagicMock()

        res = recalculate_deal_forecast_task(
            deal_id=sample_deal.id,
            trigger_event="STAGE_UPDATE",
            correlation_id="task-cid-001",
        )

        assert res["status"] == "success"
        assert res["deal_id"] == sample_deal.id
        assert res["correlation_id"] == "task-cid-001"


def test_recalculate_batch_forecasts_task(db_session: Session, sample_deal: Deal):
    """Verify Celery task execution for batch deal recalculation [FR-010, NFR-002, NFR-004]."""
    crm = CRMConnector()
    forecast_conn = ForecastConnector(crm_provider=crm)

    with patch("src.workers.forecast_worker.SessionLocal", return_value=db_session):
        db_session.close = MagicMock()

        res = recalculate_batch_forecasts_task(
            deal_ids=[sample_deal.id],
            trigger_event="BATCH_TRIGGER",
            correlation_id="task-batch-cid-002",
        )

        assert res["status"] == "success"
        assert res["processed_count"] == 1
        assert res["correlation_id"] == "task-batch-cid-002"
