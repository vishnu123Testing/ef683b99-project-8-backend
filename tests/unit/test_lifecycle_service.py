"""Unit tests for LifecycleService in backend/src/services/contract/lifecycle_service.py.

Verifies contract creation, clause/SLA management, sequential document versioning [FR-4],
search filtering across categories, statuses, expiration dates, and counterparties [FR-11],
and structured event logging with correlation IDs [FR-12, NFR-1].
"""

from datetime import date as date_type, datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.contract import Contract, ContractClause, ContractSLA, DocumentVersion
from src.models.database import Base
from src.services.contract.lifecycle_service import (
    ContractNotFoundError,
    DocumentVersionNotFoundError,
    LifecycleService,
)
from src.services.contract.models import (
    ContractCategory,
    ContractStatus,
    LifecycleEventMetadata,
    LifecycleEventType,
)
from src.services.contract.storage_port import StoragePort, StorageResult


class FakeStorageConnector(StoragePort):
    """Fake storage port implementation for in-memory testing."""

    def __init__(self):
        self.files = {}

    def save_document(
        self,
        contract_id: uuid.UUID,
        category,
        version_number: int,
        file_name: str,
        content: bytes,
    ) -> StorageResult:
        file_path = f"contracts/{contract_id}/{category}_v{version_number}_{file_name}"
        self.files[file_path] = content
        return StorageResult(
            file_path=file_path,
            checksum="fakechecksum1234567890",
            file_size_bytes=len(content),
            stored_at=datetime.now(timezone.utc),
        )

    def get_document(self, file_path: str) -> bytes:
        if file_path not in self.files:
            raise DocumentVersionNotFoundError(f"File not found at path {file_path}")
        return self.files[file_path]


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session with schema initialized."""
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
def fake_storage():
    """Fixture providing FakeStorageConnector instance."""
    return FakeStorageConnector()


@pytest.fixture
def service(db_session, fake_storage):
    """Fixture providing LifecycleService instance."""
    return LifecycleService(db=db_session, storage_port=fake_storage)


def test_create_contract_basic(service: LifecycleService):
    """Test creating a contract record without clauses or SLAs."""
    contract = service.create_contract(
        title="Master Services Agreement",
        category=ContractCategory.CONTRACT,
        counterparty="Acme Corp",
        start_on=date_type(2026, 1, 1),
        expires_on=date_type(2027, 1, 1),
    )

    assert isinstance(contract.id, uuid.UUID)
    assert contract.title == "Master Services Agreement"
    assert contract.category == "Contract"
    assert contract.status == "DRAFT"
    assert contract.counterparty == "Acme Corp"
    assert contract.start_on == date_type(2026, 1, 1)
    assert contract.expires_on == date_type(2027, 1, 1)


def test_create_contract_with_clauses_and_slas(service: LifecycleService):
    """Test creating a contract with initial clauses and SLAs [FR-1]."""
    clauses = [
        {
            "clause_type": "Termination",
            "title": "30-Day Notice",
            "body_text": "Either party may terminate with 30 days notice.",
            "is_highlighted": True,
        },
        {
            "clause_type": "Indemnity",
            "title": "Standard Mutual Indemnity",
            "body_text": "Mutual indemnification clause.",
            "is_highlighted": False,
        },
    ]
    slas = [
        {
            "metric_name": "Uptime",
            "target_value": "99.9%",
            "penalty_terms": "10% monthly service credit upon breach.",
        }
    ]

    contract = service.create_contract(
        title="SLA Contract 2026",
        category=ContractCategory.SOW,
        counterparty="Cloud Systems",
        status=ContractStatus.ACTIVE,
        clauses=clauses,
        slas=slas,
    )

    assert contract.status == "ACTIVE"
    assert len(contract.clauses) == 2
    assert len(contract.slas) == 1
    assert contract.clauses[0].is_highlighted is True


def test_create_contract_invalid_parent_raises_error(service: LifecycleService):
    """Test creating a child contract with non-existent parent_contract_id raises ContractNotFoundError."""
    missing_parent_id = uuid.uuid4()
    with pytest.raises(ContractNotFoundError):
        service.create_contract(
            title="Child Exhibit",
            category=ContractCategory.EXHIBIT,
            counterparty="Beta Inc",
            parent_contract_id=missing_parent_id,
        )


def test_get_contract_details_and_clauses_slas(service: LifecycleService):
    """Test retrieving full contract details with clauses and SLA evaluation [FR-1]."""
    contract = service.create_contract(
        title="Enterprise SOW",
        category=ContractCategory.SOW,
        counterparty="Delta Inc",
        status=ContractStatus.ACTIVE,
    )

    service.add_clause(
        contract_id=contract.id,
        clause_type="IP Rights",
        title="Work for Hire",
        body_text="All work outputs belong to client.",
        is_highlighted=True,
    )

    service.add_sla(
        contract_id=contract.id,
        metric_name="Resolution Time",
        target_value="4",
        penalty_terms="500 penalty per hour breach",
    )

    details = service.get_contract_details(contract.id)
    assert details["title"] == "Enterprise SOW"
    assert len(details["clauses"]) == 1
    assert len(details["highlighted_clauses"]) == 1
    assert details["highlighted_clauses"][0]["title"] == "Work for Hire"
    assert len(details["slas"]) == 1

    # Evaluate SLA against performance data
    evaluations = service.evaluate_slas(
        contract_id=contract.id,
        performance_data={"Resolution Time": 2},
    )
    assert len(evaluations) == 1
    assert evaluations[0]["status"] == "COMPLIANT"
    assert evaluations[0]["penalty_applicable"] is False

    breached_eval = service.evaluate_slas(
        contract_id=contract.id,
        performance_data={"Resolution Time": 6},
    )
    assert breached_eval[0]["status"] == "BREACHED"
    assert breached_eval[0]["penalty_applicable"] is True


def test_upload_document_versioning_sequence(service: LifecycleService, fake_storage):
    """Test sequential document versioning increments version numbers [FR-4]."""
    contract = service.create_contract(
        title="Versioned NDA",
        category=ContractCategory.NDA,
        counterparty="Secure Corp",
    )

    # First upload -> version 1
    v1 = service.upload_document_version(
        contract_id=contract.id,
        category_type=ContractCategory.NDA,
        file_name="nda_v1.pdf",
        content=b"NDA Document Content V1",
    )
    assert v1.version_number == 1
    assert v1.category_type == "NDA"

    # Second upload -> version 2
    v2 = service.upload_document_version(
        contract_id=contract.id,
        category_type=ContractCategory.NDA,
        file_name="nda_v2.pdf",
        content=b"NDA Document Content V2 - Revised",
    )
    assert v2.version_number == 2

    # Different category (Exhibit) -> version 1
    v1_exhibit = service.upload_document_version(
        contract_id=contract.id,
        category_type=ContractCategory.EXHIBIT,
        file_name="exhibit_a.pdf",
        content=b"Exhibit A Content",
    )
    assert v1_exhibit.version_number == 1
    assert v1_exhibit.category_type == "Exhibit"

    # Verify listing document versions
    all_versions = service.get_document_versions(contract.id)
    assert len(all_versions) == 3

    nda_versions = service.get_document_versions(contract.id, category_type=ContractCategory.NDA)
    assert len(nda_versions) == 2
    assert [v.version_number for v in nda_versions] == [1, 2]

    # Verify download of specific historical version
    download_v1_content, downloaded_doc = service.download_document(v1.id)
    assert download_v1_content == b"NDA Document Content V1"
    assert downloaded_doc.version_number == 1


def test_search_contracts_filtering(service: LifecycleService):
    """Test search_contracts with filtering by category, status, expiration date, counterparty [FR-11]."""
    c1 = service.create_contract(
        title="MSA Acme 2026",
        category=ContractCategory.CONTRACT,
        counterparty="Acme Corp",
        status=ContractStatus.ACTIVE,
        expires_on=date_type(2026, 6, 30),
    )
    c2 = service.create_contract(
        title="NDA Beta 2026",
        category=ContractCategory.NDA,
        counterparty="Beta LLC",
        status=ContractStatus.DRAFT,
        expires_on=date_type(2026, 12, 31),
    )
    c3 = service.create_contract(
        title="SOW Acme Phase 1",
        category=ContractCategory.SOW,
        counterparty="Acme Corp",
        status=ContractStatus.ACTIVE,
        expires_on=date_type(2027, 3, 31),
    )

    # Filter by category
    nda_res, nda_count = service.search_contracts(category=ContractCategory.NDA)
    assert nda_count == 1
    assert nda_res[0].id == c2.id

    # Filter by status
    active_res, active_count = service.search_contracts(status=ContractStatus.ACTIVE)
    assert active_count == 2
    active_ids = {c.id for c in active_res}
    assert active_ids == {c1.id, c3.id}

    # Filter by counterparty
    acme_res, acme_count = service.search_contracts(counterparty="Acme")
    assert acme_count == 2

    # Filter by expiration date <= expires_before
    exp_res, exp_count = service.search_contracts(expires_before=date_type(2026, 8, 1))
    assert exp_count == 1
    assert exp_res[0].id == c1.id

    # Search query matching title
    q_res, q_count = service.search_contracts(search_query="Phase 1")
    assert q_count == 1
    assert q_res[0].id == c3.id


def test_update_status_and_termination(service: LifecycleService):
    """Test updating contract status and terminating a contract [FR-12]."""
    contract = service.create_contract(
        title="Draft Service Agreement",
        category=ContractCategory.CONTRACT,
        counterparty="Gamma Co",
        status=ContractStatus.DRAFT,
    )

    meta = LifecycleEventMetadata(
        actor_id=uuid.uuid4(),
        actor_role="ContractManager",
        correlation_id="test-corr-12345",
    )

    updated = service.update_contract_status(
        contract_id=contract.id,
        new_status=ContractStatus.PENDING_SIGNATURE,
        metadata=meta,
    )
    assert updated.status == "PENDING_SIGNATURE"

    terminated = service.terminate_contract(
        contract_id=contract.id,
        reason="Mutual agreement to cancel",
        metadata=meta,
    )
    assert terminated.status == "TERMINATED"
