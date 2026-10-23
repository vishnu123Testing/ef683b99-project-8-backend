"""Unit tests for SLA evaluation logic, target deadline calculation, breach detection, and escalations [FR-4, FR-5, FR-6, FR-7, NFR-1, NFR-3, NFR-4]."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.connectors.notification import NotificationConnector
from src.lib.config import settings
from src.models.database import Base
from src.models.ticket import (
    SLATracking,
    SupportTicket,
    TicketEscalation,
)
from src.services.support.sla_service import (
    SLAService,
    calculate_remaining_time,
    calculate_target_due_at,
    check_and_escalate_ticket,
    evaluate_all_active_slas,
    evaluate_ticket_slas,
    execute_ticket_escalation,
    initialize_sla_tracking,
    recalculate_sla_targets,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session for testing."""
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
def mock_notification_connector():
    """Fixture providing a notification connector for verifying dispatch callbacks."""
    return NotificationConnector()


def test_calculate_target_due_at_priorities():
    """Test target due datetime calculations across P1, P2, and P3 priority levels [FR-4]."""
    start = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)

    # P1: 1h response / 4h resolution
    p1_resp = calculate_target_due_at("P1", "first_response", start)
    p1_res = calculate_target_due_at("P1", "resolution", start)
    assert p1_resp == start + timedelta(hours=settings.sla_p1_response_hours)
    assert p1_res == start + timedelta(hours=settings.sla_p1_resolution_hours)

    # P2: 4h response / 24h resolution
    p2_resp = calculate_target_due_at("P2", "first_response", start)
    p2_res = calculate_target_due_at("P2", "resolution", start)
    assert p2_resp == start + timedelta(hours=settings.sla_p2_response_hours)
    assert p2_res == start + timedelta(hours=settings.sla_p2_resolution_hours)

    # P3: 24h response / 72h resolution
    p3_resp = calculate_target_due_at("P3", "first_response", start)
    p3_res = calculate_target_due_at("P3", "resolution", start)
    assert p3_resp == start + timedelta(hours=settings.sla_p3_response_hours)
    assert p3_res == start + timedelta(hours=settings.sla_p3_resolution_hours)


def test_calculate_target_due_at_case_insensitivity_and_naive_dt():
    """Test case insensitivity and naive datetime baseline handling in target calculation."""
    naive_start = datetime(2026, 10, 6, 12, 0, 0)
    due_at = calculate_target_due_at("p1", "first_response", naive_start)

    assert due_at.tzinfo == timezone.utc
    assert due_at == datetime(2026, 10, 6, 13, 0, 0, tzinfo=timezone.utc)


def test_calculate_remaining_time_active_and_breached():
    """Test countdown remaining time calculations for active and breached targets [FR-4, NFR-1]."""
    now = datetime(2026, 10, 6, 12, 0, 0, tzinfo=timezone.utc)

    # Active countdown (2 hours remaining)
    target_future = now + timedelta(hours=2)
    res_active = calculate_remaining_time(target_future, now)
    assert res_active["is_breached"] is False
    assert res_active["time_remaining_seconds"] == 7200.0
    assert res_active["time_remaining_hours"] == 2.0
    assert res_active["formatted_countdown"] == "02h 00m 00s"

    # Breached countdown (30 minutes past due)
    target_past = now - timedelta(minutes=30)
    res_breached = calculate_remaining_time(target_past, now)
    assert res_breached["is_breached"] is True
    assert res_breached["time_remaining_seconds"] == -1800.0
    assert res_breached["formatted_countdown"] == "-00h 30m 00s"


def test_initialize_sla_tracking(db_session):
    """Test initializing SLA tracking ORM records for a new ticket [FR-4]."""
    client_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    ticket = SupportTicket(
        priority="P2",
        status="open",
        subject="Database slow queries",
        description="High latency on SQL queries",
        client_id=client_id,
        created_at=now,
    )
    db_session.add(ticket)
    db_session.flush()

    trackings = initialize_sla_tracking(db_session, ticket, now)
    assert len(trackings) == 2

    types = {t.target_type: t for t in trackings}
    assert "first_response" in types
    assert "resolution" in types

    assert types["first_response"].target_due_at == now + timedelta(hours=settings.sla_p2_response_hours)
    assert types["resolution"].target_due_at == now + timedelta(hours=settings.sla_p2_resolution_hours)
    assert types["first_response"].is_breached is False


def test_recalculate_sla_targets_priority_upgrade(db_session):
    """Test SLA target recalculation when ticket priority is upgraded [FR-4]."""
    client_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    ticket = SupportTicket(
        priority="P3",
        status="open",
        subject="Minor UI glitch",
        description="Button alignment issue",
        client_id=client_id,
        created_at=now,
    )
    db_session.add(ticket)
    db_session.flush()

    initialize_sla_tracking(db_session, ticket, now)

    # Upgrade priority to P1
    recalculated = recalculate_sla_targets(db_session, ticket, "P1", now)
    assert ticket.priority == "P1"

    types = {t.target_type: t for t in recalculated}
    assert types["first_response"].target_due_at == now + timedelta(hours=settings.sla_p1_response_hours)
    assert types["resolution"].target_due_at == now + timedelta(hours=settings.sla_p1_resolution_hours)


def test_recalculate_sla_targets_invalid_priority(db_session):
    """Test recalculating SLA targets raises ValueError for invalid priority strings."""
    ticket = SupportTicket(
        priority="P2",
        subject="Test",
        description="Test",
        client_id=uuid.uuid4(),
    )
    db_session.add(ticket)
    db_session.flush()

    with pytest.raises(ValueError, match="Invalid ticket priority"):
        recalculate_sla_targets(db_session, ticket, "P999")


def test_evaluate_ticket_slas_warning_and_breach(db_session, mock_notification_connector):
    """Test SLA evaluation detecting warning threshold and breach condition [FR-4, FR-5]."""
    client_id = uuid.uuid4()
    start_time = datetime(2026, 10, 6, 10, 0, 0, tzinfo=timezone.utc)

    ticket = SupportTicket(
        priority="P1",  # P1: 1h response (11:00), 4h resolution (14:00)
        status="open",
        subject="Critical Outage",
        description="System down",
        client_id=client_id,
        created_at=start_time,
    )
    db_session.add(ticket)
    db_session.flush()

    initialize_sla_tracking(db_session, ticket, start_time)

    # Evaluate at 10:50 (50 mins elapsed out of 60 mins = 83.3% elapsed >= 80% threshold)
    eval_time_warning = datetime(2026, 10, 6, 10, 50, 0, tzinfo=timezone.utc)
    res_warning = evaluate_ticket_slas(
        db_session, ticket, mock_notification_connector, eval_time_warning
    )
    assert res_warning["warning_triggered"] is True
    assert res_warning["breach_triggered"] is False

    dispatched = mock_notification_connector.get_dispatched_notifications()
    assert len(dispatched) == 1
    assert dispatched[0].notification_type == "SLA_BREACH_WARNING"

    # Evaluate at 11:15 (past 11:00 response due date -> breach)
    eval_time_breach = datetime(2026, 10, 6, 11, 15, 0, tzinfo=timezone.utc)
    res_breach = evaluate_ticket_slas(
        db_session, ticket, mock_notification_connector, eval_time_breach
    )
    assert res_breach["breach_triggered"] is True

    dispatched = mock_notification_connector.get_dispatched_notifications()
    assert len(dispatched) == 2
    assert dispatched[1].notification_type == "SLA_BREACH_ALERT"


def test_evaluate_ticket_slas_satisfied_when_responded_or_resolved(db_session):
    """Test SLA evaluation satisfies targets when first response or resolution timestamps are set [FR-4]."""
    client_id = uuid.uuid4()
    start_time = datetime(2026, 10, 6, 10, 0, 0, tzinfo=timezone.utc)
    responded_time = datetime(2026, 10, 6, 10, 20, 0, tzinfo=timezone.utc)

    ticket = SupportTicket(
        priority="P1",
        status="open",
        subject="Resolved quickly",
        description="Issue description",
        client_id=client_id,
        created_at=start_time,
        first_responded_at=responded_time,
    )
    db_session.add(ticket)
    db_session.flush()

    initialize_sla_tracking(db_session, ticket, start_time)

    eval_time = datetime(2026, 10, 6, 11, 30, 0, tzinfo=timezone.utc)
    eval_res = evaluate_ticket_slas(db_session, ticket, current_time=eval_time)

    types = {t.target_type: t for t in ticket.sla_trackings}
    assert types["first_response"].is_breached is False


def test_execute_ticket_escalation(db_session, mock_notification_connector):
    """Test executing ticket tier escalation re-assignment and creating audit record [FR-6]."""
    client_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    ticket = SupportTicket(
        priority="P1",
        status="open",
        subject="Escalation test",
        description="Escalation needed",
        client_id=client_id,
        assigned_tier="tier1",
        created_at=now,
    )
    db_session.add(ticket)
    db_session.flush()

    escalation = execute_ticket_escalation(
        db_session,
        ticket,
        reason="No agent assignment within warning threshold",
        notification_connector=mock_notification_connector,
        current_time=now,
    )

    assert ticket.assigned_tier == "tier2"
    assert escalation.source_tier == "tier1"
    assert escalation.target_tier == "tier2"
    assert escalation.reason == "No agent assignment within warning threshold"

    dispatched = mock_notification_connector.get_dispatched_notifications()
    assert len(dispatched) == 1
    assert dispatched[0].notification_type == "TIER_ESCALATION"


def test_check_and_escalate_ticket(db_session, mock_notification_connector):
    """Test automated checking and triggering of ticket escalations [FR-5, FR-6]."""
    client_id = uuid.uuid4()
    start_time = datetime(2026, 10, 6, 10, 0, 0, tzinfo=timezone.utc)

    ticket = SupportTicket(
        priority="P1",
        status="open",
        subject="Resolution warning escalation",
        description="Pending resolution",
        client_id=client_id,
        assigned_tier="tier1",
        created_at=start_time,
    )
    db_session.add(ticket)
    db_session.flush()

    initialize_sla_tracking(db_session, ticket, start_time)

    # Evaluate at resolution warning time (e.g. 13:30, > 80% of 4h resolution duration)
    eval_time = datetime(2026, 10, 6, 13, 30, 0, tzinfo=timezone.utc)
    evaluate_ticket_slas(db_session, ticket, mock_notification_connector, eval_time)

    escalation = check_and_escalate_ticket(
        db_session, ticket, mock_notification_connector, eval_time
    )

    assert escalation is not None
    assert ticket.assigned_tier == "tier2"


def test_evaluate_all_active_slas(db_session, mock_notification_connector):
    """Test periodic scanning and evaluation of all active support tickets [FR-4, FR-5, FR-6]."""
    client_id = uuid.uuid4()
    start_time = datetime(2026, 10, 6, 10, 0, 0, tzinfo=timezone.utc)

    ticket1 = SupportTicket(
        priority="P1",
        status="open",
        subject="Active ticket 1",
        description="Desc 1",
        client_id=client_id,
        created_at=start_time,
    )
    ticket2 = SupportTicket(
        priority="P2",
        status="resolved",
        subject="Resolved ticket 2",
        description="Desc 2",
        client_id=client_id,
        created_at=start_time,
    )
    db_session.add_all([ticket1, ticket2])
    db_session.flush()

    eval_time = datetime(2026, 10, 6, 11, 30, 0, tzinfo=timezone.utc)
    summary = evaluate_all_active_slas(
        db_session, mock_notification_connector, eval_time
    )

    assert summary["evaluated_count"] == 1  # Only ticket1 is open
    assert summary["breaches_count"] >= 1  # P1 response breached at 11:30


def test_sla_service_class_wrapper(db_session, mock_notification_connector):
    """Test SLAService class wrapper methods and dependency injection [NFR-4]."""
    sla_service = SLAService(db=db_session, notification_connector=mock_notification_connector)

    ticket = SupportTicket(
        priority="P3",
        status="open",
        subject="Service class wrapper test",
        description="Testing class wrapper",
        client_id=uuid.uuid4(),
    )
    db_session.add(ticket)
    db_session.flush()

    trackings = sla_service.initialize_sla_tracking(ticket)
    assert len(trackings) == 2

    recalculated = sla_service.recalculate_sla_targets(ticket, "P1")
    assert ticket.priority == "P1"
    assert len(recalculated) == 2

    # Test error when db session is missing
    unbound_service = SLAService()
    with pytest.raises(ValueError, match="Database session is required"):
        unbound_service.initialize_sla_tracking(ticket)
