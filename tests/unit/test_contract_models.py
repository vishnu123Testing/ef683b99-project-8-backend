"""Unit tests for Contract and related ORM models in backend/src/models/contract.py."""

from datetime import date as date_type, datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.contract import (
    Contract,
    ContractAmendment,
    ContractClause,
    ContractSLA,
    DocumentVersion,
    RenewalAlert,
    SignatureRequest,
)
from src.models.database import Base
from src.models.tenant import Tenant
from src.models.user import User, hash_password


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


def test_contract_persistence_and_to_dict(db_session: Session):
    """Test creating and persisting a Contract model instance."""
    contract = Contract(
        title="Master Services Agreement",
        category="Contract",
        status="ACTIVE",
        counterparty="Acme Corp",
        start_on=date_type(2026, 1, 1),
        expires_on=date_type(2027, 1, 1),
    )
    db_session.add(contract)
    db_session.commit()
    db_session.refresh(contract)

    assert isinstance(contract.id, uuid.UUID)
    assert contract.title == "Master Services Agreement"
    assert contract.category == "Contract"
    assert contract.status == "ACTIVE"
    assert contract.counterparty == "Acme Corp"
    assert contract.start_on == date_type(2026, 1, 1)
    assert contract.expires_on == date_type(2027, 1, 1)
    assert repr(contract).startswith("<Contract")

    d = contract.to_dict()
    assert d["id"] == str(contract.id)
    assert d["title"] == "Master Services Agreement"
    assert d["start_on"] == "2026-01-01"
    assert d["expires_on"] == "2027-01-01"


def test_parent_child_contract_relationship(db_session: Session):
    """Test parent contract and attached child SOW/Exhibit contracts."""
    parent = Contract(
        title="Master Agreement 2026",
        category="Contract",
        status="ACTIVE",
        counterparty="Global Corp",
    )
    db_session.add(parent)
    db_session.commit()
    db_session.refresh(parent)

    child_sow = Contract(
        title="Statement of Work #1",
        category="SOW",
        status="ACTIVE",
        counterparty="Global Corp",
        parent_contract_id=parent.id,
    )
    child_exhibit = Contract(
        title="Exhibit A - Pricing",
        category="Exhibit",
        status="ACTIVE",
        counterparty="Global Corp",
        parent_contract_id=parent.id,
    )
    db_session.add_all([child_sow, child_exhibit])
    db_session.commit()

    db_session.refresh(parent)
    assert len(parent.child_contracts) == 2
    child_titles = [c.title for c in parent.child_contracts]
    assert "Statement of Work #1" in child_titles
    assert "Exhibit A - Pricing" in child_titles

    assert child_sow.parent_contract.id == parent.id


def test_clauses_and_slas_relationships(db_session: Session):
    """Test creating clauses and SLAs associated with a contract."""
    contract = Contract(
        title="SLA Agreement",
        category="Contract",
        status="DRAFT",
        counterparty="Tech Inc",
    )
    db_session.add(contract)
    db_session.commit()

    clause = ContractClause(
        contract_id=contract.id,
        clause_type="Indemnification",
        title="Mutual Indemnity",
        body_text="Each party agrees to indemnify...",
        is_highlighted=True,
    )
    sla = ContractSLA(
        contract_id=contract.id,
        metric_name="Uptime Guarantee",
        target_value="99.9%",
        penalty_terms="10% credit per 0.1% breach",
    )
    db_session.add_all([clause, sla])
    db_session.commit()

    db_session.refresh(contract)
    assert len(contract.clauses) == 1
    assert contract.clauses[0].clause_type == "Indemnification"
    assert contract.clauses[0].is_highlighted is True

    assert len(contract.slas) == 1
    assert contract.slas[0].metric_name == "Uptime Guarantee"
    assert contract.slas[0].target_value == "99.9%"


def test_document_versions_amendments_signatures_alerts(db_session: Session):
    """Test document versions, amendments, signature requests, and renewal alerts."""
    tenant = Tenant(name="Contracting Org")
    db_session.add(tenant)
    db_session.commit()

    user = User(
        email="signatory@org.com",
        password_hash=hash_password("Pass123!"),
        tenant_id=tenant.tenant_id,
    )
    db_session.add(user)
    db_session.commit()

    contract = Contract(
        title="NDA Agreement",
        category="NDA",
        status="PENDING_SIGNATURE",
        counterparty="Partner LLC",
    )
    db_session.add(contract)
    db_session.commit()

    doc_version = DocumentVersion(
        contract_id=contract.id,
        category_type="NDA",
        version_number=1,
        file_path="/storage/nda_v1.pdf",
        checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        uploaded_by_id=user.user_id,
    )
    amendment = ContractAmendment(
        contract_id=contract.id,
        sequence_number=1,
        summary="Added confidentiality exception for public knowledge.",
        author_id=user.user_id,
    )
    sig_request = SignatureRequest(
        contract_id=contract.id,
        signatory_id=user.user_id,
        status="PENDING",
    )
    alert = RenewalAlert(
        contract_id=contract.id,
        threshold_days=90,
    )

    db_session.add_all([doc_version, amendment, sig_request, alert])
    db_session.commit()

    db_session.refresh(contract)
    assert len(contract.document_versions) == 1
    assert contract.document_versions[0].version_number == 1
    assert contract.document_versions[0].uploaded_by.email == "signatory@org.com"

    assert len(contract.amendments) == 1
    assert contract.amendments[0].sequence_number == 1

    assert len(contract.signature_requests) == 1
    assert contract.signature_requests[0].signatory.email == "signatory@org.com"

    assert len(contract.renewal_alerts) == 1
    assert contract.renewal_alerts[0].threshold_days == 90


def test_cascade_deletion(db_session: Session):
    """Test deleting parent contract cascades to clauses, SLAs, versions, amendments, signatures, and alerts."""
    contract = Contract(
        title="Temporary Contract",
        category="Contract",
        status="DRAFT",
        counterparty="Temp Inc",
    )
    db_session.add(contract)
    db_session.commit()

    clause = ContractClause(
        contract_id=contract.id,
        clause_type="Termination",
        title="Term Clause",
        body_text="30 days notice",
    )
    sla = ContractSLA(
        contract_id=contract.id,
        metric_name="Response Time",
        target_value="4 hours",
    )
    doc_ver = DocumentVersion(
        contract_id=contract.id,
        category_type="Contract",
        version_number=1,
        file_path="/path/v1.pdf",
        checksum="1234567890abcdef1234567890abcdef1234567890abcdef1234567890abcdef",
    )
    amendment = ContractAmendment(
        contract_id=contract.id,
        sequence_number=1,
        summary="Initial draft changes",
    )
    alert = RenewalAlert(
        contract_id=contract.id,
        threshold_days=30,
    )
    db_session.add_all([clause, sla, doc_ver, amendment, alert])
    db_session.commit()

    # Delete contract
    db_session.delete(contract)
    db_session.commit()

    # Assert children are deleted
    assert db_session.query(ContractClause).filter_by(contract_id=contract.id).first() is None
    assert db_session.query(ContractSLA).filter_by(contract_id=contract.id).first() is None
    assert db_session.query(DocumentVersion).filter_by(contract_id=contract.id).first() is None
    assert db_session.query(ContractAmendment).filter_by(contract_id=contract.id).first() is None
    assert db_session.query(RenewalAlert).filter_by(contract_id=contract.id).first() is None
