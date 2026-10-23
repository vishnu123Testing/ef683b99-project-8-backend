"""Integration tests for support ticket REST API endpoints in backend/src/api/tickets.py [FR-1, FR-2, FR-3, FR-4, FR-7, FR-9, NFR-2, NFR-3]."""

import io
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.main import app
from src.models.database import Base, get_db

# Set up in-memory SQLite database for testing API endpoints
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Dependency override providing isolated SQLite test session."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = override_get_db


@pytest.fixture(autouse=True)
def setup_database():
    """Fixture ensuring fresh database tables for each test case."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


client = TestClient(app)


def test_create_and_get_ticket():
    """Verify POST /api/v1/tickets creates a ticket and GET /api/v1/tickets/{id} retrieves it [FR-1, FR-4]."""
    client_id = str(uuid.uuid4())
    payload = {
        "priority": "P1",
        "subject": "System Outage",
        "description": "Production server unresponsive",
        "client_id": client_id,
        "assigned_tier": "tier1",
    }

    res = client.post("/api/v1/tickets", json=payload)
    assert res.status_code == 201
    data = res.json()
    assert data["id"] is not None
    assert data["priority"] == "P1"
    assert data["status"] == "open"
    assert data["subject"] == "System Outage"
    assert data["client_id"] == client_id
    assert "sla_trackings" in data
    assert len(data["sla_trackings"]) == 2

    ticket_id = data["id"]

    # Get ticket detail
    get_res = client.get(f"/api/v1/tickets/{ticket_id}")
    assert get_res.status_code == 200
    get_data = get_res.json()
    assert get_data["id"] == ticket_id
    assert get_data["subject"] == "System Outage"


def test_list_and_filter_tickets():
    """Verify GET /api/v1/tickets lists and filters tickets [FR-1]."""
    client_id = str(uuid.uuid4())

    # Create two tickets
    client.post(
        "/api/v1/tickets",
        json={
            "priority": "P1",
            "subject": "Ticket 1",
            "description": "Desc 1",
            "client_id": client_id,
        },
    )
    client.post(
        "/api/v1/tickets",
        json={
            "priority": "P3",
            "subject": "Ticket 2",
            "description": "Desc 2",
            "client_id": client_id,
        },
    )

    # List all
    res = client.get("/api/v1/tickets")
    assert res.status_code == 200
    data = res.json()
    assert data["total_count"] == 2
    assert len(data["items"]) == 2

    # Filter by priority P1
    res_p1 = client.get("/api/v1/tickets?priority=P1")
    assert res_p1.status_code == 200
    data_p1 = res_p1.json()
    assert data_p1["total_count"] == 1
    assert data_p1["items"][0]["priority"] == "P1"


def test_update_ticket():
    """Verify PUT /api/v1/tickets/{id} updates priority and status [FR-1, FR-4]."""
    client_id = str(uuid.uuid4())
    create_res = client.post(
        "/api/v1/tickets",
        json={
            "priority": "P3",
            "subject": "Minor Bug",
            "description": "UI alignment issue",
            "client_id": client_id,
        },
    )
    ticket_id = create_res.json()["id"]

    # Update to P1 and pending
    update_res = client.put(
        f"/api/v1/tickets/{ticket_id}",
        json={
            "priority": "P1",
            "status": "pending",
        },
    )
    assert update_res.status_code == 200
    updated_data = update_res.json()
    assert updated_data["priority"] == "P1"
    assert updated_data["status"] == "pending"


def test_delete_ticket():
    """Verify DELETE /api/v1/tickets/{id} returns 204 No Content with empty body [FR-1, ARCHITECTURE.md §5]."""
    client_id = str(uuid.uuid4())
    create_res = client.post(
        "/api/v1/tickets",
        json={
            "priority": "P2",
            "subject": "To be deleted",
            "description": "Temporary ticket",
            "client_id": client_id,
        },
    )
    ticket_id = create_res.json()["id"]

    # Delete ticket
    del_res = client.delete(f"/api/v1/tickets/{ticket_id}")
    assert del_res.status_code == 204
    assert del_res.text == ""  # Empty response body

    # Verify 404 on get
    get_res = client.get(f"/api/v1/tickets/{ticket_id}")
    assert get_res.status_code == 404


def test_create_message_and_get_messages():
    """Verify posting messages and retrieving ticket thread history [FR-2]."""
    client_id = str(uuid.uuid4())
    agent_id = str(uuid.uuid4())

    create_res = client.post(
        "/api/v1/tickets",
        json={
            "priority": "P2",
            "subject": "Thread Test",
            "description": "Initial description",
            "client_id": client_id,
        },
    )
    ticket_id = create_res.json()["id"]

    # Post agent reply
    msg_res = client.post(
        f"/api/v1/tickets/{ticket_id}/messages",
        json={
            "sender_id": agent_id,
            "sender_type": "agent",
            "message_body": "Investigating the issue now.",
        },
    )
    assert msg_res.status_code == 201
    msg_data = msg_res.json()
    assert msg_data["message_body"] == "Investigating the issue now."
    assert msg_data["sender_type"] == "agent"

    # Get thread
    thread_res = client.get(f"/api/v1/tickets/{ticket_id}/messages")
    assert thread_res.status_code == 200
    thread = thread_res.json()
    assert len(thread) == 2  # Initial message + agent reply
    assert thread[1]["message_body"] == "Investigating the issue now."


def test_message_with_file_attachment():
    """Verify posting message with file upload and retrieving attachment bytes [FR-2, FR-3]."""
    client_id = str(uuid.uuid4())
    create_res = client.post(
        "/api/v1/tickets",
        json={
            "priority": "P1",
            "subject": "Attachment Test",
            "description": "See attached log",
            "client_id": client_id,
        },
    )
    ticket_id = create_res.json()["id"]

    file_content = b"Error log data line 1\nError log data line 2"
    files = {
        "file": ("error_log.txt", io.BytesIO(file_content), "text/plain"),
    }
    data = {
        "sender_id": client_id,
        "sender_type": "client",
        "message_body": "Attached error log file.",
    }

    msg_res = client.post(
        f"/api/v1/tickets/{ticket_id}/messages",
        data=data,
        files=files,
    )
    assert msg_res.status_code == 201
    msg_data = msg_res.json()
    assert len(msg_data["attachments"]) == 1
    attachment_id = msg_data["attachments"][0]["id"]
    assert msg_data["attachments"][0]["filename"] == "error_log.txt"

    # Download attachment
    dl_res = client.get(f"/api/v1/tickets/attachments/{attachment_id}")
    assert dl_res.status_code == 200
    assert dl_res.content == file_content


def test_upload_attachment_endpoint():
    """Verify standalone file attachment upload endpoint POST /api/v1/tickets/{id}/attachments [FR-2, FR-3]."""
    client_id = str(uuid.uuid4())
    create_res = client.post(
        "/api/v1/tickets",
        json={
            "priority": "P2",
            "subject": "Standalone Upload Test",
            "description": "Testing standalone upload",
            "client_id": client_id,
        },
    )
    ticket_id = create_res.json()["id"]

    file_content = b"PDF mock content data line"
    files = {
        "file": ("document.pdf", io.BytesIO(file_content), "application/pdf"),
    }
    data = {
        "sender_id": client_id,
        "sender_type": "client",
    }

    att_res = client.post(
        f"/api/v1/tickets/{ticket_id}/attachments",
        data=data,
        files=files,
    )
    assert att_res.status_code == 201
    att_data = att_res.json()
    assert att_data["filename"] == "document.pdf"
    assert att_data["file_size_bytes"] == len(file_content)
    assert att_data["mime_type"] == "application/pdf"


def test_message_payload_sanitization_and_validation():
    """Verify payload sanitization and boundary validation for message posting [NFR-2]."""
    client_id = str(uuid.uuid4())
    create_res = client.post(
        "/api/v1/tickets",
        json={
            "priority": "P3",
            "subject": "Sanitization Test",
            "description": "Validation test ticket",
            "client_id": client_id,
        },
    )
    ticket_id = create_res.json()["id"]

    # Invalid sender_type -> 400 Bad Request
    res_inv_type = client.post(
        f"/api/v1/tickets/{ticket_id}/messages",
        json={
            "sender_id": client_id,
            "sender_type": "super_admin",  # Invalid type
            "message_body": "Hello",
        },
    )
    assert res_inv_type.status_code == 400

    # Blank message_body -> 400 Bad Request
    res_blank_body = client.post(
        f"/api/v1/tickets/{ticket_id}/messages",
        json={
            "sender_id": client_id,
            "sender_type": "client",
            "message_body": "   ",  # Blank body
        },
    )
    assert res_blank_body.status_code == 400


def test_submit_and_get_csat_rating():
    """Verify CSAT rating submission and retrieval for resolved ticket [FR-9]."""
    client_id = str(uuid.uuid4())
    create_res = client.post(
        "/api/v1/tickets",
        json={
            "priority": "P2",
            "subject": "CSAT Test",
            "description": "Issue to resolve",
            "client_id": client_id,
        },
    )
    ticket_id = create_res.json()["id"]

    # Try submitting CSAT before resolving -> 400 Bad Request
    csat_early = client.post(
        f"/api/v1/tickets/{ticket_id}/csat",
        json={"score": 5, "feedback_text": "Great service!"},
    )
    assert csat_early.status_code == 400

    # Resolve ticket
    client.put(
        f"/api/v1/tickets/{ticket_id}",
        json={"status": "resolved"},
    )

    # Submit CSAT rating
    csat_res = client.post(
        f"/api/v1/tickets/{ticket_id}/csat",
        json={"score": 5, "feedback_text": "Great service!"},
    )
    assert csat_res.status_code == 201
    csat_data = csat_res.json()
    assert csat_data["score"] == 5
    assert csat_data["feedback_text"] == "Great service!"

    # Duplicate submission -> 409 Conflict
    dup_res = client.post(
        f"/api/v1/tickets/{ticket_id}/csat",
        json={"score": 4},
    )
    assert dup_res.status_code == 409

    # Get CSAT rating
    get_csat = client.get(f"/api/v1/tickets/{ticket_id}/csat")
    assert get_csat.status_code == 200
    assert get_csat.json()["score"] == 5

    # Invalid score -> 422 Unprocessable Entity
    inv_score = client.post(
        f"/api/v1/tickets/{ticket_id}/csat",
        json={"score": 10},
    )
    assert inv_score.status_code == 422


def test_not_found_and_validation_errors():
    """Verify 404 for non-existent tickets and 400 for invalid inputs [NFR-2]."""
    fake_id = str(uuid.uuid4())

    # 404 Get
    res_get = client.get(f"/api/v1/tickets/{fake_id}")
    assert res_get.status_code == 404

    # 404 Delete
    res_del = client.delete(f"/api/v1/tickets/{fake_id}")
    assert res_del.status_code == 404

    # 404 CSAT submission
    res_csat = client.post(
        f"/api/v1/tickets/{fake_id}/csat",
        json={"score": 5},
    )
    assert res_csat.status_code == 404

    # 422 Invalid Priority in create
    res_inv = client.post(
        "/api/v1/tickets",
        json={
            "priority": "P99",  # Invalid priority
            "subject": "Test",
            "description": "Test",
            "client_id": str(uuid.uuid4()),
        },
    )
    assert res_inv.status_code == 422
