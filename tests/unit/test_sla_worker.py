"""Unit tests for Celery SLA countdown evaluation and escalation worker [FR-4, FR-5, FR-6, NFR-1, NFR-3]."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.connectors.notification import NotificationConnector
from src.models.database import Base
from src.models.ticket import SLATracking, SupportTicket, TicketEscalation
from src.services.support.sla_service import initialize_sla_tracking
from src.workers.sla_worker import (
    celery_app,
    process_sla_countdown_evaluations,
    process_sla_evaluations,
    run_scheduled_sla_evaluation,
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
def sample_client_id():
    """Fixture providing a sample client UUID."""
    return uuid.uuid4()


def test_process_sla_evaluations_happy_path(db_session: Session, sample_client_id: uuid.UUID):
    """Verify SLA evaluation scan correctly processes active open tickets [FR-4, FR-5, FR-6]."""
    now = datetime.now(timezone.utc)
    
    # Create an active open ticket created 5 hours ago (P1 resolution SLA is 4h -> breached)
    past_creation = now - timedelta(hours=5)
    t1 = SupportTicket(
        id=uuid.uuid4(),
        priority="P1",
        status="open",
        subject="Critical Outage",
        description="P1 server down",
        client_id=sample_client_id,
        assigned_tier="tier1",
        created_at=past_creation,
        updated_at=past_creation,
    )
    db_session.add(t1)
    db_session.commit()
    db_session.refresh(t1)

    # Initialize SLA tracking records
    initialize_sla_tracking(db_session, t1, current_time=past_creation)
    db_session.commit()

    notification_mock = MagicMock()

    result = process_sla_evaluations(
        db=db_session,
        current_time=now,
        notification_port=notification_mock,
        correlation_id="test-cid-sla-worker-1",
    )

    assert result["status"] == "success"
    assert result["evaluated_count"] == 1
    assert result["warnings_count"] >= 0
    assert result["breaches_count"] >= 1
    assert result["escalations_count"] == 1
    assert result["correlation_id"] == "test-cid-sla-worker-1"

    # Verify ticket escalation was stored in DB
    escalations = db_session.query(TicketEscalation).filter(TicketEscalation.ticket_id == t1.id).all()
    assert len(escalations) == 1
    assert escalations[0].source_tier == "tier1"
    assert escalations[0].target_tier == "tier2"


def test_process_sla_evaluations_alias_and_task(db_session: Session, sample_client_id: uuid.UUID):
    """Verify alias function process_sla_countdown_evaluations and Celery task [FR-4, FR-5]."""
    now = datetime.now(timezone.utc)

    t1 = SupportTicket(
        id=uuid.uuid4(),
        priority="P3",
        status="open",
        subject="Normal query",
        description="P3 question",
        client_id=sample_client_id,
        assigned_tier="tier1",
        created_at=now,
        updated_at=now,
    )
    db_session.add(t1)
    db_session.commit()

    initialize_sla_tracking(db_session, t1, current_time=now)
    db_session.commit()

    # Test alias process_sla_countdown_evaluations
    result = process_sla_countdown_evaluations(
        db=db_session,
        current_time=now,
        correlation_id="test-cid-alias",
    )
    assert result["status"] == "success"
    assert result["evaluated_count"] == 1

    # Test scheduled Celery task with ISO string
    with patch("src.workers.sla_worker.process_sla_evaluations") as mock_proc:
        mock_proc.return_value = {"status": "success", "evaluated_count": 1}
        task_res = run_scheduled_sla_evaluation(current_time_iso=now.isoformat())
        assert task_res["status"] == "success"
        mock_proc.assert_called_once()


def test_process_sla_evaluations_no_db_provided(sample_client_id: uuid.UUID):
    """Verify process_sla_evaluations manages SessionLocal when db argument is None."""
    now = datetime.now(timezone.utc)
    with patch("src.workers.sla_worker.SessionLocal") as mock_session_factory, patch("src.workers.sla_worker.evaluate_all_active_slas") as mock_eval:
        mock_db = MagicMock()
        mock_session_factory.return_value = mock_db
        mock_eval.return_value = {
            "evaluated_count": 2,
            "warnings_count": 1,
            "breaches_count": 0,
            "escalations_count": 0,
        }

        result = process_sla_evaluations(current_time=now)

        assert result["status"] == "success"
        assert result["evaluated_count"] == 2
        mock_db.close.assert_called_once()


def test_process_sla_evaluations_exception_handling(db_session: Session):
    """Verify process_sla_evaluations logs and re-raises exceptions when errors occur."""
    with patch("src.workers.sla_worker.evaluate_all_active_slas", side_effect=RuntimeError("DB Connection Lost")):
        with pytest.raises(RuntimeError, match="DB Connection Lost"):
            process_sla_evaluations(db=db_session)
