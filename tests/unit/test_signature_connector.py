"""Unit tests for SignatureConnector implementation [FR-5, FR-6]."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.connectors.signature_connector import SignatureConnector
from src.models.contract import Base, Contract, SignatureRequest
from src.models.user import User
from src.services.contract.models import SignatureStatus
from src.services.contract.signature_port import (
    InvalidSignatureDigestError,
    SignatureExecutionError,
    SignatureRequestNotFoundError,
)


@pytest.fixture
def in_memory_connector() -> SignatureConnector:
    """Fixture providing an in-memory SignatureConnector instance."""
    return SignatureConnector(secret_key="test-secret-key")


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite SQLAlchemy session."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def db_connector(db_session) -> SignatureConnector:
    """Fixture providing a SignatureConnector configured with SQLite session."""
    return SignatureConnector(db=db_session, secret_key="test-secret-key")


def test_create_and_get_signature_request(in_memory_connector: SignatureConnector):
    """Test creating and retrieving a signature request in memory."""
    contract_id = uuid.uuid4()
    signatory_id = uuid.uuid4()

    created = in_memory_connector.create_signature_request(
        contract_id=contract_id,
        signatory_id=signatory_id,
    )

    assert created.id is not None
    assert created.contract_id == contract_id
    assert created.signatory_id == signatory_id
    assert created.status == SignatureStatus.PENDING

    fetched = in_memory_connector.get_signature_request(created.id)
    assert fetched.id == created.id
    assert fetched.status == SignatureStatus.PENDING


def test_get_nonexistent_signature_request_raises(in_memory_connector: SignatureConnector):
    """Test that retrieving a nonexistent signature request raises SignatureRequestNotFoundError."""
    missing_id = uuid.uuid4()
    with pytest.raises(SignatureRequestNotFoundError):
        in_memory_connector.get_signature_request(missing_id)


def test_list_pending_signatures(in_memory_connector: SignatureConnector):
    """Test listing pending signature requests with optional filtering [FR-5]."""
    c1 = uuid.uuid4()
    c2 = uuid.uuid4()
    s1 = uuid.uuid4()
    s2 = uuid.uuid4()

    req1 = in_memory_connector.create_signature_request(c1, s1)
    req2 = in_memory_connector.create_signature_request(c1, s2)
    req3 = in_memory_connector.create_signature_request(c2, s1)

    pending_all = in_memory_connector.list_pending_signatures()
    assert len(pending_all) == 3

    pending_c1 = in_memory_connector.list_pending_signatures(contract_id=c1)
    assert len(pending_c1) == 2
    assert {r.id for r in pending_c1} == {req1.id, req2.id}

    pending_s1 = in_memory_connector.list_pending_signatures(signatory_id=s1)
    assert len(pending_s1) == 2
    assert {r.id for r in pending_s1} == {req1.id, req3.id}


def test_execute_signature_success(in_memory_connector: SignatureConnector):
    """Test successful digital signature execution and party notification [FR-6]."""
    c_id = uuid.uuid4()
    s_id = uuid.uuid4()
    req = in_memory_connector.create_signature_request(c_id, s_id)

    digest = in_memory_connector.generate_native_signature_digest(c_id, s_id)
    result = in_memory_connector.execute_signature(
        signature_request_id=req.id,
        signature_digest=digest,
        signatory_id=s_id,
    )

    assert result.signature_request_id == req.id
    assert result.status == SignatureStatus.EXECUTED
    assert result.signature_digest == digest
    assert str(s_id) in result.notified_parties
    assert isinstance(result.executed_at, datetime)

    updated_req = in_memory_connector.get_signature_request(req.id)
    assert updated_req.status == SignatureStatus.EXECUTED
    assert updated_req.signature_digest == digest


def test_execute_signature_empty_digest_raises(in_memory_connector: SignatureConnector):
    """Test executing a signature with empty/blank digest raises InvalidSignatureDigestError."""
    req = in_memory_connector.create_signature_request(uuid.uuid4(), uuid.uuid4())
    with pytest.raises(InvalidSignatureDigestError):
        in_memory_connector.execute_signature(req.id, signature_digest="")

    with pytest.raises(InvalidSignatureDigestError):
        in_memory_connector.execute_signature(req.id, signature_digest="   ")


def test_execute_signature_signatory_mismatch_raises(in_memory_connector: SignatureConnector):
    """Test executing a signature with wrong signatory ID raises SignatureExecutionError."""
    req = in_memory_connector.create_signature_request(uuid.uuid4(), uuid.uuid4())
    other_s_id = uuid.uuid4()
    with pytest.raises(SignatureExecutionError, match="Signatory ID mismatch"):
        in_memory_connector.execute_signature(
            signature_request_id=req.id,
            signature_digest="valid-digest",
            signatory_id=other_s_id,
        )


def test_execute_already_executed_signature_raises(in_memory_connector: SignatureConnector):
    """Test re-executing an already executed signature raises SignatureExecutionError."""
    req = in_memory_connector.create_signature_request(uuid.uuid4(), uuid.uuid4())
    in_memory_connector.execute_signature(req.id, "digest-1")

    with pytest.raises(SignatureExecutionError):
        in_memory_connector.execute_signature(req.id, "digest-2")


def test_reject_signature(in_memory_connector: SignatureConnector):
    """Test rejecting a pending signature request."""
    req = in_memory_connector.create_signature_request(uuid.uuid4(), uuid.uuid4())
    rejected = in_memory_connector.reject_signature(req.id, reason="Declined by user")

    assert rejected.status == SignatureStatus.REJECTED

    with pytest.raises(SignatureExecutionError):
        in_memory_connector.execute_signature(req.id, "digest-3")


def test_verify_signature_digest(in_memory_connector: SignatureConnector):
    """Test verifying signature digest integrity."""
    req = in_memory_connector.create_signature_request(uuid.uuid4(), uuid.uuid4())
    digest = "valid-signature-digest-123"

    assert not in_memory_connector.verify_signature_digest(req.id, digest)

    in_memory_connector.execute_signature(req.id, digest)

    assert in_memory_connector.verify_signature_digest(req.id, digest)
    assert not in_memory_connector.verify_signature_digest(req.id, "invalid-digest")
    assert not in_memory_connector.verify_signature_digest(uuid.uuid4(), digest)


def test_db_persistence_workflow(db_connector: SignatureConnector, db_session):
    """Test SignatureConnector operations with SQLAlchemy DB session."""
    user = User(
        email="signatory@example.com",
        password_hash="hashed_pw",
        tenant_id=uuid.uuid4(),
    )
    db_session.add(user)
    contract = Contract(
        title="Master Services Agreement",
        category="Contract",
        counterparty="Acme Corp",
        status="PENDING_SIGNATURE",
    )
    db_session.add(contract)
    db_session.commit()

    req = db_connector.create_signature_request(
        contract_id=contract.id,
        signatory_id=user.user_id,
    )

    assert req.id is not None
    assert req.status == SignatureStatus.PENDING

    pending = db_connector.list_pending_signatures(contract_id=contract.id)
    assert len(pending) == 1
    assert pending[0].id == req.id

    digest = db_connector.generate_native_signature_digest(contract.id, user.user_id)
    exec_res = db_connector.execute_signature(
        signature_request_id=req.id,
        signature_digest=digest,
        signatory_id=user.user_id,
    )

    assert exec_res.status == SignatureStatus.EXECUTED
    assert exec_res.signature_digest == digest

    db_req = db_session.query(SignatureRequest).filter(SignatureRequest.id == req.id).first()
    assert db_req is not None
    assert db_req.status == "EXECUTED"
    assert db_req.signature_digest == digest
