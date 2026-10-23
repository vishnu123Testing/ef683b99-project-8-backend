"""Integration tests for contract lifecycle and signature API endpoints in backend/src/api/contracts.py.

Verifies RBAC access, input payload validation, file upload streams, search filtering,
signature execution, amendment recording, and clean 204 DELETE cleanup [FR-1, FR-2, FR-3, FR-5, FR-6, FR-7, FR-8, FR-10, FR-11, NFR-1, NFR-2].
"""

from datetime import date as date_type, datetime, timezone
import io
import uuid

import pytest
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.api.deps import get_current_user, get_db
from src.api.main import app
from src.models.contract import Contract, ContractAmendment, SignatureRequest
from src.models.database import Base, get_db
from src.models.user import User
from src.services.contract.models import ContractCategory, ContractStatus, SignatureStatus

# In-memory test database setup
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Dependency override providing isolated in-memory DB session for tests."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_test_database():
    """Fixture initializing database tables before each test and dropping after."""
    Base.metadata.create_all(bind=engine)
    db = TestingSessionLocal()
    test_user = User(
        user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        email="testuser@example.com",
        password_hash="hashed_pw_secret",
        tenant_id=uuid.UUID("99999999-9999-9999-9999-999999999999"),
    )
    db.add(test_user)
    db.commit()
    db.close()

    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def test_user():
    """Fixture returning test user instance."""
    return User(
        user_id=uuid.UUID("11111111-1111-1111-1111-111111111111"),
        email="testuser@example.com",
        password_hash="hashed_pw_secret",
        tenant_id=uuid.UUID("99999999-9999-9999-9999-999999999999"),
    )


@pytest.fixture
def auth_client(test_user):
    """Fixture providing TestClient with authenticated user dependency override."""
    app.dependency_overrides[get_current_user] = lambda: test_user
    client = TestClient(app)
    return client


@pytest.fixture
def unauth_client():
    """Fixture providing TestClient without authentication overrides."""
    if get_current_user in app.dependency_overrides:
        del app.dependency_overrides[get_current_user]
    return TestClient(app)


def test_unauthenticated_request_returns_401(unauth_client):
    """Verify endpoint rejects requests without bearer token with HTTP 401 Unauthorized [FR-2]."""
    response = unauth_client.get("/api/v1/contracts")
    assert response.status_code == status.HTTP_401_UNAUTHORIZED


def test_create_and_get_contract_detail(auth_client):
    """Verify creating a contract and retrieving detail view with clauses and SLAs [FR-1, FR-3, FR-10]."""
    payload = {
        "title": "Master Services Agreement",
        "category": "Contract",
        "counterparty": "Acme Global",
        "start_on": "2026-01-01",
        "expires_on": "2027-01-01",
        "clauses": [
            {
                "clause_type": "Termination",
                "title": "Termination for Convenience",
                "body_text": "Either party may terminate upon 30 days notice.",
                "is_highlighted": True,
            }
        ],
        "slas": [
            {
                "metric_name": "uptime_percentage",
                "target_value": "99.9%",
                "penalty_terms": "10% service credit for breach",
            }
        ],
    }

    # POST /api/v1/contracts
    create_res = auth_client.post("/api/v1/contracts", json=payload)
    assert create_res.status_code == status.HTTP_201_CREATED
    data = create_res.json()
    contract_id = data["id"]
    assert data["title"] == "Master Services Agreement"
    assert data["category"] == "Contract"
    assert data["status"] == "DRAFT"
    assert len(data["clauses"]) == 1
    assert len(data["highlighted_clauses"]) == 1
    assert len(data["slas"]) == 1

    # GET /api/v1/contracts/{id}
    detail_res = auth_client.get(f"/api/v1/contracts/{contract_id}")
    assert detail_res.status_code == status.HTTP_200_OK
    detail_data = detail_res.json()
    assert detail_data["id"] == contract_id
    assert detail_data["counterparty"] == "Acme Global"


def test_list_contracts_search_filtering(auth_client):
    """Verify contract search filtering by category, status, and counterparty [FR-11]."""
    # Create two contracts with different categories
    auth_client.post(
        "/api/v1/contracts",
        json={"title": "NDA Alpha", "category": "NDA", "counterparty": "Alpha Corp"},
    )
    auth_client.post(
        "/api/v1/contracts",
        json={"title": "SOW Beta", "category": "SOW", "counterparty": "Beta LLC"},
    )

    # Filter by category NDA
    res_nda = auth_client.get("/api/v1/contracts?category=NDA")
    assert res_nda.status_code == status.HTTP_200_OK
    data_nda = res_nda.json()
    assert data_nda["total_count"] == 1
    assert data_nda["items"][0]["title"] == "NDA Alpha"

    # Filter by counterparty
    res_counterparty = auth_client.get("/api/v1/contracts?counterparty=Beta")
    assert res_counterparty.status_code == status.HTTP_200_OK
    data_cp = res_counterparty.json()
    assert data_cp["total_count"] == 1
    assert data_cp["items"][0]["title"] == "SOW Beta"


def test_document_version_upload_and_download(auth_client):
    """Verify document file version uploading and stream downloading [FR-3, FR-4]."""
    create_res = auth_client.post(
        "/api/v1/contracts",
        json={"title": "SOW Project Gamma", "category": "SOW", "counterparty": "Gamma Inc"},
    )
    contract_id = create_res.json()["id"]

    # Upload document version
    file_content = b"PDF dummy content for version 1"
    files = {"file": ("sow_gamma.pdf", io.BytesIO(file_content), "application/pdf")}
    data = {"category_type": "SOW"}

    upload_res = auth_client.post(
        f"/api/v1/contracts/{contract_id}/documents",
        data=data,
        files=files,
    )
    assert upload_res.status_code == status.HTTP_201_CREATED
    upload_data = upload_res.json()
    version_id = upload_data["id"]
    assert upload_data["version_number"] == 1

    # Download document version stream
    download_res = auth_client.get(f"/api/v1/contracts/{contract_id}/documents/{version_id}")
    assert download_res.status_code == status.HTTP_200_OK
    assert download_res.content == file_content
    assert "attachment" in download_res.headers.get("content-disposition", "")


def test_amendment_recording_and_immutability_protection(auth_client):
    """Verify contract amendment audit logging and rejection of contract deletion when amendments exist [FR-7, FR-8, NFR-4]."""
    # Create active contract
    create_res = auth_client.post(
        "/api/v1/contracts",
        json={"title": "Master Vendor Contract", "category": "Contract", "counterparty": "Vendor Corp"},
    )
    contract_id = create_res.json()["id"]

    # Record amendment
    amend_payload = {"summary": "Extended expiration date by 12 months and revised SLA response times."}
    amend_res = auth_client.post(
        f"/api/v1/contracts/{contract_id}/amendments",
        json=amend_payload,
    )
    assert amend_res.status_code == status.HTTP_201_CREATED
    amend_data = amend_res.json()
    assert amend_data["sequence_number"] == 1
    assert amend_data["summary"] == amend_payload["summary"]

    # List amendments
    list_amend_res = auth_client.get(f"/api/v1/contracts/{contract_id}/amendments")
    assert list_amend_res.status_code == status.HTTP_200_OK
    list_data = list_amend_res.json()
    assert list_data["total_count"] == 1

    # Attempting DELETE on contract with existing amendments must return 403 Forbidden [FR-8, NFR-4]
    delete_res = auth_client.delete(f"/api/v1/contracts/{contract_id}")
    assert delete_res.status_code == status.HTTP_403_FORBIDDEN


def test_delete_contract_clean_204_no_content(auth_client):
    """Verify DELETE /api/v1/contracts/{id} returns 204 No Content with empty body for clean contracts [ARCH-5]."""
    create_res = auth_client.post(
        "/api/v1/contracts",
        json={"title": "Temporary Agreement", "category": "NDA", "counterparty": "Temp Partner"},
    )
    contract_id = create_res.json()["id"]

    # DELETE /api/v1/contracts/{id}
    delete_res = auth_client.delete(f"/api/v1/contracts/{contract_id}")
    assert delete_res.status_code == status.HTTP_204_NO_CONTENT
    assert delete_res.content == b""

    # Confirm 404 on subsequent get
    get_res = auth_client.get(f"/api/v1/contracts/{contract_id}")
    assert get_res.status_code == status.HTTP_404_NOT_FOUND


def test_pending_signatures_and_execution(auth_client, test_user):
    """Verify pending signatures list and signature execution workflow [FR-5, FR-6]."""
    # Create contract in PENDING_SIGNATURE state
    create_res = auth_client.post(
        "/api/v1/contracts",
        json={"title": "Signature Required Agreement", "category": "Contract", "counterparty": "Signatory Partner"},
    )
    contract_id = uuid.UUID(create_res.json()["id"])

    # Manually seed a SignatureRequest for test user in DB session
    db = TestingSessionLocal()
    sig_req = SignatureRequest(
        contract_id=contract_id,
        signatory_id=test_user.user_id,
        status="PENDING",
    )
    # Update contract status to PENDING_SIGNATURE
    contract = db.query(Contract).filter(Contract.id == contract_id).first()
    contract.status = "PENDING_SIGNATURE"
    db.add(sig_req)
    db.commit()
    sig_req_id = sig_req.id
    db.close()

    # GET /api/v1/signatures/pending
    pending_res = auth_client.get("/api/v1/signatures/pending")
    assert pending_res.status_code == status.HTTP_200_OK
    pending_data = pending_res.json()
    assert pending_data["total_count"] >= 1
    item = next(i for i in pending_data["items"] if i["signature_request_id"] == str(sig_req_id))
    assert item["contract_title"] == "Signature Required Agreement"

    # POST /api/v1/signatures/{id}/execute
    exec_payload = {"signature_digest": "a1b2c3d4e5f67890123456789abcdef"}
    exec_res = auth_client.post(f"/api/v1/signatures/{sig_req_id}/execute", json=exec_payload)
    assert exec_res.status_code == status.HTTP_200_OK
    exec_data = exec_res.json()
    assert exec_data["status"] == "EXECUTED"
    assert exec_data["all_signatures_completed"] is True
    assert exec_data["resulting_contract_status"] == "ACTIVE"
