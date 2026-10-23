"""Unit tests for SupportTicket, TicketMessage, TicketAttachment, SLATracking, TicketEscalation, and CSATRating ORM models."""

from datetime import datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.ticket import (
    CSATRating,
    SLATracking,
    SupportTicket,
    TicketAttachment,
    TicketEscalation,
    TicketMessage,
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


def test_support_ticket_defaults_and_to_dict():
    """Test SupportTicket model initialization, repr, and to_dict method."""
    client_id = uuid.uuid4()
    agent_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    ticket = SupportTicket(
        priority="P1",
        status="open",
        subject="System Outage",
        description="Database connection timeout error",
        client_id=client_id,
        assigned_tier="tier1",
        assigned_agent_id=agent_id,
        created_at=now,
        updated_at=now,
    )

    assert ticket.priority == "P1"
    assert ticket.status == "open"
    assert ticket.subject == "System Outage"
    assert ticket.description == "Database connection timeout error"
    assert ticket.client_id == client_id

    repr_str = repr(ticket)
    assert "System Outage" in repr_str
    assert "P1" in repr_str

    d = ticket.to_dict()
    assert d["priority"] == "P1"
    assert d["subject"] == "System Outage"
    assert d["client_id"] == str(client_id)
    assert d["assigned_agent_id"] == str(agent_id)
    assert d["created_at"] == now.isoformat()


def test_support_ticket_db_persistence_and_relationships(db_session: Session):
    """Test persisting SupportTicket and associated messages and attachments."""
    client_id = uuid.uuid4()
    agent_id = uuid.uuid4()

    ticket = SupportTicket(
        priority="P2",
        status="open",
        subject="API Rate Limit Bug",
        description="Requests failing with 429 prematurely",
        client_id=client_id,
        assigned_agent_id=agent_id,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    assert ticket.id is not None
    assert isinstance(ticket.id, uuid.UUID)

    # Add message
    msg = TicketMessage(
        ticket_id=ticket.id,
        sender_id=client_id,
        sender_type="client",
        message_body="Please investigate the rate limit settings.",
    )
    db_session.add(msg)
    db_session.commit()
    db_session.refresh(msg)

    assert msg.id is not None
    assert len(ticket.messages) == 1
    assert ticket.messages[0].message_body == "Please investigate the rate limit settings."

    # Add attachment to message
    attachment = TicketAttachment(
        message_id=msg.id,
        filename="error_log.txt",
        file_size_bytes=1024,
        mime_type="text/plain",
        storage_key="attachments/error_log.txt",
    )
    db_session.add(attachment)
    db_session.commit()
    db_session.refresh(attachment)

    assert attachment.id is not None
    assert len(msg.attachments) == 1
    assert msg.attachments[0].filename == "error_log.txt"


def test_sla_tracking_unique_constraint(db_session: Session):
    """Test SLATracking model creation and unique constraint on (ticket_id, target_type)."""
    ticket = SupportTicket(
        priority="P1",
        subject="P1 Urgent Issue",
        description="Critical system down",
        client_id=uuid.uuid4(),
    )
    db_session.add(ticket)
    db_session.commit()

    due_at = datetime.now(timezone.utc) + timedelta(hours=1)
    sla1 = SLATracking(
        ticket_id=ticket.id,
        target_type="first_response",
        target_due_at=due_at,
        is_breached=False,
    )
    db_session.add(sla1)
    db_session.commit()

    assert sla1.id is not None
    assert sla1.target_type == "first_response"

    # Attempt to add duplicate ticket_id + target_type
    sla_duplicate = SLATracking(
        ticket_id=ticket.id,
        target_type="first_response",
        target_due_at=due_at,
        is_breached=False,
    )
    db_session.add(sla_duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_ticket_escalation_and_csat_rating(db_session: Session):
    """Test TicketEscalation and CSATRating models."""
    ticket = SupportTicket(
        priority="P1",
        subject="Unresolved Outage",
        description="Escalating to tier2",
        client_id=uuid.uuid4(),
    )
    db_session.add(ticket)
    db_session.commit()

    # Add Escalation
    escalation = TicketEscalation(
        ticket_id=ticket.id,
        source_tier="tier1",
        target_tier="tier2",
        reason="First response SLA approaching breach",
    )
    db_session.add(escalation)

    # Add CSAT Rating
    csat = CSATRating(
        ticket_id=ticket.id,
        score=5,
        feedback_text="Excellent support resolution!",
    )
    db_session.add(csat)
    db_session.commit()

    db_session.refresh(ticket)
    assert len(ticket.escalations) == 1
    assert ticket.escalations[0].target_tier == "tier2"
    assert ticket.csat_rating is not None
    assert ticket.csat_rating.score == 5

    # Test CSAT unique ticket_id constraint
    duplicate_csat = CSATRating(
        ticket_id=ticket.id,
        score=4,
        feedback_text="Second rating attempt",
    )
    db_session.add(duplicate_csat)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_cascade_deletion(db_session: Session):
    """Test cascading delete of ticket deletes messages, attachments, SLA tracking, escalations, and CSAT rating."""
    ticket = SupportTicket(
        priority="P3",
        subject="General Inquiry",
        description="Feature request details",
        client_id=uuid.uuid4(),
    )
    db_session.add(ticket)
    db_session.commit()

    msg = TicketMessage(
        ticket_id=ticket.id,
        sender_id=uuid.uuid4(),
        sender_type="client",
        message_body="Question about export",
    )
    db_session.add(msg)
    db_session.commit()

    att = TicketAttachment(
        message_id=msg.id,
        filename="doc.pdf",
        file_size_bytes=2048,
        mime_type="application/pdf",
        storage_key="attachments/doc.pdf",
    )
    sla = SLATracking(
        ticket_id=ticket.id,
        target_type="resolution",
        target_due_at=datetime.now(timezone.utc) + timedelta(hours=72),
    )
    esc = TicketEscalation(
        ticket_id=ticket.id,
        source_tier="tier1",
        target_tier="tier2",
        reason="Requested manager review",
    )
    csat = CSATRating(
        ticket_id=ticket.id,
        score=4,
    )
    db_session.add_all([att, sla, esc, csat])
    db_session.commit()

    # Delete ticket
    db_session.delete(ticket)
    db_session.commit()

    assert db_session.query(SupportTicket).filter_by(id=ticket.id).first() is None
    assert db_session.query(TicketMessage).filter_by(ticket_id=ticket.id).first() is None
    assert db_session.query(TicketAttachment).filter_by(id=att.id).first() is None
    assert db_session.query(SLATracking).filter_by(ticket_id=ticket.id).first() is None
    assert db_session.query(TicketEscalation).filter_by(ticket_id=ticket.id).first() is None
    assert db_session.query(CSATRating).filter_by(ticket_id=ticket.id).first() is None
