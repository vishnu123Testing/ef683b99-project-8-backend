"""Unit tests for Support Ticket Domain Service [FR-1, FR-2, FR-3, FR-7, FR-8, FR-9, NFR-2, NFR-3, NFR-4]."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.connectors.storage import BlobStorageAdapter, StorageValidationError
from src.models.database import Base
from src.models.ticket import (
    CSATRating,
    SLATracking,
    SupportTicket,
    TicketAttachment,
    TicketMessage,
)
from src.services.support.ticket_service import (
    CSATAlreadySubmittedError,
    InvalidCSATError,
    InvalidMessageError,
    InvalidTicketError,
    TicketAttachmentError,
    TicketNotFoundError,
    TicketService,
    add_message,
    create_ticket,
    delete_ticket,
    get_messages,
    get_support_analytics,
    get_ticket,
    list_tickets,
    submit_csat_rating,
    update_ticket,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for unit testing."""
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
def mock_storage_adapter(tmp_path):
    """Fixture providing a BlobStorageAdapter configured with a temporary directory."""
    return BlobStorageAdapter(
        storage_dir=str(tmp_path),
        max_file_size_bytes=10 * 1024 * 1024,
        allowed_mime_types=["text/plain", "image/png", "application/pdf"],
        allowed_extensions=[".txt", ".png", ".pdf"],
    )


def test_create_ticket_success(db_session: Session, mock_storage_adapter):
    """Test successful ticket creation with SLA tracking and initial message initialization [FR-1, FR-4]."""
    client_id = uuid.uuid4()
    service = TicketService(db_session, storage_port=mock_storage_adapter)

    ticket = service.create_ticket(
        priority="P1",
        subject="Database Outage",
        description="Main production database unreachable",
        client_id=client_id,
        assigned_tier="tier1",
    )

    assert ticket.id is not None
    assert ticket.priority == "P1"
    assert ticket.status == "open"
    assert ticket.subject == "Database Outage"
    assert ticket.client_id == client_id

    # Verify SLA trackings created
    trackings = db_session.query(SLATracking).filter(SLATracking.ticket_id == ticket.id).all()
    assert len(trackings) == 2
    target_types = {t.target_type for t in trackings}
    assert "first_response" in target_types
    assert "resolution" in target_types

    # Verify initial message created
    messages = db_session.query(TicketMessage).filter(TicketMessage.ticket_id == ticket.id).all()
    assert len(messages) == 1
    assert messages[0].message_body == "Main production database unreachable"
    assert messages[0].sender_type == "client"


def test_create_ticket_validation_failures(db_session: Session):
    """Test ticket creation boundary validation failures [NFR-2]."""
    service = TicketService(db_session)
    client_id = uuid.uuid4()

    # Invalid priority
    with pytest.raises(InvalidTicketError, match="Invalid ticket priority"):
        service.create_ticket(
            priority="INVALID",
            subject="Valid Subject",
            description="Valid Description",
            client_id=client_id,
        )

    # Empty subject
    with pytest.raises(InvalidTicketError, match="subject cannot be empty"):
        service.create_ticket(
            priority="P2",
            subject="  ",
            description="Valid Description",
            client_id=client_id,
        )

    # Empty description
    with pytest.raises(InvalidTicketError, match="description cannot be empty"):
        service.create_ticket(
            priority="P2",
            subject="Valid Subject",
            description="",
            client_id=client_id,
        )

    # Invalid client UUID format
    with pytest.raises(InvalidTicketError, match="Invalid client_id UUID format"):
        service.create_ticket(
            priority="P2",
            subject="Valid Subject",
            description="Valid Description",
            client_id="not-a-uuid",
        )


def test_get_ticket_success_and_not_found(db_session: Session):
    """Test retrieving ticket by ID and error when not found [FR-1]."""
    service = TicketService(db_session)
    client_id = uuid.uuid4()

    created = service.create_ticket(
        priority="P2",
        subject="Login Timeout",
        description="Users experiencing 504 on login",
        client_id=client_id,
    )

    fetched = service.get_ticket(created.id)
    assert fetched.id == created.id
    assert fetched.subject == "Login Timeout"

    # Non-existent ticket ID
    random_id = uuid.uuid4()
    with pytest.raises(TicketNotFoundError, match=str(random_id)):
        service.get_ticket(random_id)


def test_update_ticket_priority_recalculates_slas(db_session: Session):
    """Test priority update recalculates SLA target deadlines [FR-1, FR-4]."""
    service = TicketService(db_session)
    client_id = uuid.uuid4()

    ticket = service.create_ticket(
        priority="P3",
        subject="Minor Typo",
        description="Typo on landing page",
        client_id=client_id,
    )

    p3_trackings = {t.target_type: t.target_due_at for t in ticket.sla_trackings}

    # Escalating priority from P3 to P1
    updated = service.update_ticket(ticket.id, priority="P1")

    assert updated.priority == "P1"
    p1_trackings = {t.target_type: t.target_due_at for t in updated.sla_trackings}

    # P1 deadlines must be earlier than P3 deadlines
    assert p1_trackings["first_response"] < p3_trackings["first_response"]
    assert p1_trackings["resolution"] < p3_trackings["resolution"]


def test_update_ticket_status_sets_resolved_at(db_session: Session):
    """Test status update to resolved records resolved_at timestamp [FR-1, FR-7]."""
    service = TicketService(db_session)
    client_id = uuid.uuid4()

    ticket = service.create_ticket(
        priority="P2",
        subject="Payment Failure",
        description="Checkout endpoint returning 500",
        client_id=client_id,
    )
    assert ticket.resolved_at is None

    # Resolve ticket
    updated = service.update_ticket(ticket.id, status="resolved")

    assert updated.status == "resolved"
    assert updated.resolved_at is not None
    assert isinstance(updated.resolved_at, datetime)


def test_list_and_filter_tickets(db_session: Session):
    """Test listing and filtering support tickets by priority, status, and client_id [FR-1]."""
    service = TicketService(db_session)
    client_a = uuid.uuid4()
    client_b = uuid.uuid4()

    t1 = service.create_ticket("P1", "P1 Ticket Client A", "Desc 1", client_id=client_a)
    t2 = service.create_ticket("P2", "P2 Ticket Client A", "Desc 2", client_id=client_a)
    t3 = service.create_ticket("P1", "P1 Ticket Client B", "Desc 3", client_id=client_b)

    service.update_ticket(t2.id, status="resolved")

    # Filter by priority
    p1_tickets = service.list_tickets(priority="P1")
    assert len(p1_tickets) == 2
    assert {t.id for t in p1_tickets} == {t1.id, t3.id}

    # Filter by client_id
    client_a_tickets = service.list_tickets(client_id=client_a)
    assert len(client_a_tickets) == 2

    # Filter by status
    resolved_tickets = service.list_tickets(status="resolved")
    assert len(resolved_tickets) == 1
    assert resolved_tickets[0].id == t2.id

    assert service.count_tickets(priority="P1") == 2
    assert service.count_tickets(client_id=client_a) == 2


def test_delete_ticket_success(db_session: Session):
    """Test deleting support ticket [FR-1]."""
    service = TicketService(db_session)
    client_id = uuid.uuid4()

    ticket = service.create_ticket("P3", "To Delete", "Description", client_id=client_id)
    ticket_id = ticket.id

    res = service.delete_ticket(ticket_id)
    assert res is True

    with pytest.raises(TicketNotFoundError):
        service.get_ticket(ticket_id)


def test_add_message_and_first_agent_response(db_session: Session, mock_storage_adapter):
    """Test thread message history and recording first agent response timestamp [FR-2, FR-7]."""
    service = TicketService(db_session, storage_port=mock_storage_adapter)
    client_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    ticket = service.create_ticket("P2", "Bug Report", "Initial Client Message", client_id=client_id)
    assert ticket.first_responded_at is None

    # Client follow-up message
    msg1 = service.add_message(
        ticket_id=ticket.id,
        sender_id=client_id,
        sender_type="client",
        message_body="Any update on this?",
    )
    assert msg1.sender_type == "client"
    assert ticket.first_responded_at is None

    # First agent response
    msg2 = service.add_message(
        ticket_id=ticket.id,
        sender_id=agent_id,
        sender_type="agent",
        message_body="We are investigating the logs now.",
    )
    assert msg2.sender_type == "agent"
    assert ticket.first_responded_at is not None

    messages = service.get_messages(ticket.id)
    assert len(messages) == 3  # Initial message + 2 thread messages


def test_add_message_with_attachment_and_retrieval(db_session: Session, mock_storage_adapter):
    """Test adding thread message with file attachment bindings and content retrieval [FR-2, FR-3]."""
    service = TicketService(db_session, storage_port=mock_storage_adapter)
    client_id = uuid.uuid4()

    ticket = service.create_ticket("P2", "Attachment Test", "See attached log", client_id=client_id)

    attachments_input = [
        {"filename": "error.txt", "content": b"Error 500 in auth worker", "mime_type": "text/plain"},
    ]

    msg = service.add_message(
        ticket_id=ticket.id,
        sender_id=client_id,
        sender_type="client",
        message_body="Attaching relevant log file.",
        attachments=attachments_input,
    )

    assert len(msg.attachments) == 1
    att_record = msg.attachments[0]
    assert att_record.filename == "error.txt"
    assert att_record.file_size_bytes == len(b"Error 500 in auth worker")

    # Retrieve attachment content
    content_bytes, fetched_att = service.get_attachment_bytes(att_record.id)
    assert content_bytes == b"Error 500 in auth worker"
    assert fetched_att.id == att_record.id


def test_add_message_attachment_validation_failure(db_session: Session, mock_storage_adapter):
    """Test attachment validation failure on prohibited file format or size [FR-3, NFR-2]."""
    service = TicketService(db_session, storage_port=mock_storage_adapter)
    client_id = uuid.uuid4()

    ticket = service.create_ticket("P3", "Exec Upload", "Attempting script upload", client_id=client_id)

    prohibited_attachment = [
        {"filename": "malicious.exe", "content": b"binary payload", "mime_type": "application/x-msdownload"},
    ]

    with pytest.raises(InvalidMessageError, match="Attachment validation failed"):
        service.add_message(
            ticket_id=ticket.id,
            sender_id=client_id,
            sender_type="client",
            message_body="Uploading binary file.",
            attachments=prohibited_attachment,
        )


def test_submit_csat_rating_success(db_session: Session):
    """Test submitting CSAT rating for a resolved ticket [FR-9]."""
    service = TicketService(db_session)
    client_id = uuid.uuid4()

    ticket = service.create_ticket("P2", "CSAT Test", "Problem description", client_id=client_id)
    service.update_ticket(ticket.id, status="resolved")

    csat = service.submit_csat_rating(
        ticket_id=ticket.id,
        score=5,
        feedback_text="Excellent and swift support!",
    )

    assert csat.id is not None
    assert csat.score == 5
    assert csat.feedback_text == "Excellent and swift support!"

    fetched_csat = service.get_csat_rating(ticket.id)
    assert fetched_csat is not None
    assert fetched_csat.score == 5


def test_submit_csat_rating_unresolved_or_duplicate(db_session: Session):
    """Test CSAT submission failure on open ticket or duplicate submission [FR-9]."""
    service = TicketService(db_session)
    client_id = uuid.uuid4()

    ticket = service.create_ticket("P2", "CSAT Fail Test", "Problem description", client_id=client_id)

    # Attempting CSAT on open ticket
    with pytest.raises(InvalidCSATError, match="Ticket must be resolved or closed"):
        service.submit_csat_rating(ticket.id, score=4)

    # Resolve ticket and submit first rating
    service.update_ticket(ticket.id, status="resolved")
    service.submit_csat_rating(ticket.id, score=4)

    # Attempting duplicate submission
    with pytest.raises(CSATAlreadySubmittedError, match="already submitted"):
        service.submit_csat_rating(ticket.id, score=5)


def test_submit_csat_rating_out_of_bounds_score(db_session: Session):
    """Test CSAT score boundary validation (1–5 scale) [FR-9, NFR-2]."""
    service = TicketService(db_session)
    client_id = uuid.uuid4()

    ticket = service.create_ticket("P2", "CSAT Score Test", "Problem", client_id=client_id)
    service.update_ticket(ticket.id, status="resolved")

    with pytest.raises(InvalidCSATError, match="integer between 1 and 5"):
        service.submit_csat_rating(ticket.id, score=6)

    with pytest.raises(InvalidCSATError, match="integer between 1 and 5"):
        service.submit_csat_rating(ticket.id, score=0)


def test_get_support_analytics_aggregation(db_session: Session, mock_storage_adapter):
    """Test support analytics metric aggregations over specified window [FR-8]."""
    service = TicketService(db_session, storage_port=mock_storage_adapter)
    client_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    now = datetime.now(timezone.utc)
    one_hour_ago = now - timedelta(hours=1)
    two_hours_ago = now - timedelta(hours=2)

    # Create Ticket 1 (P1, Resolved, First response in 30m, Resolved in 1.5h, CSAT 5)
    t1 = service.create_ticket("P1", "P1 Critical", "Outage", client_id=client_id)
    t1.created_at = two_hours_ago
    t1.first_responded_at = two_hours_ago + timedelta(minutes=30)
    t1.resolved_at = two_hours_ago + timedelta(hours=1, minutes=30)
    t1.status = "resolved"
    db_session.flush()

    service.submit_csat_rating(t1.id, score=5, feedback_text="Great job!")

    # Create Ticket 2 (P2, Open)
    t2 = service.create_ticket("P2", "P2 Non-critical", "Issue", client_id=client_id)
    t2.created_at = one_hour_ago
    db_session.flush()

    analytics = service.get_support_analytics()

    assert analytics["total_tickets"] == 2
    assert analytics["open_tickets_count"] == 1
    assert analytics["resolved_tickets_count"] == 1
    assert analytics["average_response_time_minutes"] == 30.0
    assert analytics["mean_time_to_resolution_hours"] == 1.5
    assert analytics["average_csat_score"] == 5.0
    assert analytics["csat_response_count"] == 1
    assert analytics["priority_breakdown"]["P1"] == 1
    assert analytics["priority_breakdown"]["P2"] == 1


def test_standalone_functions_wrapper(db_session: Session):
    """Test standalone functional wrappers for ticket service."""
    client_id = uuid.uuid4()

    ticket = create_ticket(
        db=db_session,
        priority="P3",
        subject="Wrapper Test",
        description="Functional wrapper test description",
        client_id=client_id,
    )
    assert ticket.id is not None

    fetched = get_ticket(db_session, ticket.id)
    assert fetched.subject == "Wrapper Test"

    updated = update_ticket(db_session, ticket.id, status="resolved")
    assert updated.status == "resolved"

    tickets = list_tickets(db_session, priority="P3")
    assert len(tickets) == 1

    submit_csat_rating(db_session, ticket.id, score=4)

    analytics = get_support_analytics(db_session)
    assert analytics["total_tickets"] == 1

    deleted = delete_ticket(db_session, ticket.id)
    assert deleted is True
