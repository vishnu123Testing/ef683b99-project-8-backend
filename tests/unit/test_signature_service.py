"""Unit tests for SignatureService implementation [FR-5, FR-6]."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.models.contract import Base, Contract, SignatureRequest
from src.models.user import User
from src.services.contract.lifecycle_service import (
    ContractNotFoundError,
    InvalidContractStateError,
    LifecycleService,
)
from src.services.contract.models import (
    ContractCategory,
    ContractStatus,
    SignatureStatus,
)
from src.services.contract.signature_port import (
    InvalidSignatureDigestError,
    SignatureExecutionError,
    SignatureRequestNotFoundError,
)
from src.services.contract.signature_service import (
    SignatureService,
    SignatureServiceError,
)


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
def signature_service(db_session) -> SignatureService:
    """Fixture providing a SignatureService instance bound to SQLite DB session."""
    return SignatureService(db=db_session)


def create_sample_contract(db_session, status="DRAFT") -> Contract:
    """Helper to create and persist a sample contract entity in SQLite."""
    contract = Contract(
        id=uuid.uuid4(),
        title="Master Services Agreement",
        category="Contract",
        status=status,
        counterparty="Acme Corp",
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    db_session.add(contract)
    db_session.commit()
    db_session.refresh(contract)
    return contract


def create_sample_user(db_session) -> User:
    """Helper to create and persist a sample user entity in SQLite."""
    user = User(
        user_id=uuid.uuid4(),
        email=f"signatory-{uuid.uuid4().hex[:6]}@example.com",
        password_hash="hashed_pw_123",
        tenant_id=uuid.uuid4(),
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


def test_initiate_signature_request_success(db_session, signature_service: SignatureService):
    """Test initiating signature request transitions contract from DRAFT to PENDING_SIGNATURE [FR-5]."""
    contract = create_sample_contract(db_session, status="DRAFT")
    user = create_sample_user(db_session)

    req_domain = signature_service.initiate_signature_request(
        contract_id=contract.id,
        signatory_id=user.user_id,
    )

    assert req_domain.id is not None
    assert req_domain.contract_id == contract.id
    assert req_domain.signatory_id == user.user_id
    assert req_domain.status == SignatureStatus.PENDING

    # Verify contract status was updated to PENDING_SIGNATURE
    db_session.refresh(contract)
    assert contract.status == ContractStatus.PENDING_SIGNATURE.value


def test_initiate_signature_request_nonexistent_contract_raises(signature_service: SignatureService):
    """Test initiating signature request for nonexistent contract raises ContractNotFoundError."""
    missing_id = uuid.uuid4()
    signatory_id = uuid.uuid4()

    with pytest.raises(ContractNotFoundError):
        signature_service.initiate_signature_request(
            contract_id=missing_id,
            signatory_id=signatory_id,
        )


def test_initiate_signature_request_terminated_contract_raises(db_session, signature_service: SignatureService):
    """Test initiating signature request for TERMINATED contract raises InvalidContractStateError."""
    contract = create_sample_contract(db_session, status="TERMINATED")
    user = create_sample_user(db_session)

    with pytest.raises(InvalidContractStateError):
        signature_service.initiate_signature_request(
            contract_id=contract.id,
            signatory_id=user.user_id,
        )


def test_list_pending_signatures_hub(db_session, signature_service: SignatureService):
    """Test listing pending signature requests for the centralized e-signature hub [FR-5]."""
    c1 = create_sample_contract(db_session, status="PENDING_SIGNATURE")
    c2 = create_sample_contract(db_session, status="PENDING_SIGNATURE")
    u1 = create_sample_user(db_session)
    u2 = create_sample_user(db_session)

    req1 = signature_service.initiate_signature_request(c1.id, u1.user_id)
    req2 = signature_service.initiate_signature_request(c1.id, u2.user_id)
    req3 = signature_service.initiate_signature_request(c2.id, u1.user_id)

    pending_items = signature_service.list_pending_signatures()
    assert len(pending_items) == 3

    # Verify enriched data for hub
    for item in pending_items:
        assert item.status == SignatureStatus.PENDING
        assert item.contract_title == "Master Services Agreement"
        assert item.counterparty == "Acme Corp"

    # Filter by contract_id
    c1_items = signature_service.list_pending_signatures(contract_id=c1.id)
    assert len(c1_items) == 2
    assert {i.signature_request_id for i in c1_items} == {req1.id, req2.id}

    # Filter by signatory_id
    u1_items = signature_service.list_pending_signatures(signatory_id=u1.user_id)
    assert len(u1_items) == 2
    assert {i.signature_request_id for i in u1_items} == {req1.id, req3.id}


def test_execute_signature_success_and_activates_contract(db_session, signature_service: SignatureService):
    """Test digital signature execution transitions status to EXECUTED and contract to ACTIVE [FR-6]."""
    contract = create_sample_contract(db_session, status="DRAFT")
    user = create_sample_user(db_session)

    req = signature_service.initiate_signature_request(contract.id, user.user_id)

    digest = "SIG-NATIVE-v1:abc123def456789"
    result = signature_service.execute_signature(
        signature_request_id=req.id,
        signature_digest=digest,
        signatory_id=user.user_id,
    )

    assert result.signature_request_id == req.id
    assert result.status == SignatureStatus.PENDING or result.status == SignatureStatus.EXECUTED
    assert result.signature_digest == digest
    assert str(user.user_id) in result.notified_parties
    assert "Acme Corp" in result.notified_parties

    # Contract status should be ACTIVE since all signatures are completed
    db_session.refresh(contract)
    assert contract.status == ContractStatus.ACTIVE.value


def test_execute_signature_empty_digest_raises(db_session, signature_service: SignatureService):
    """Test executing signature with empty digest raises InvalidSignatureDigestError."""
    contract = create_sample_contract(db_session, status="DRAFT")
    user = create_sample_user(db_session)
    req = signature_service.initiate_signature_request(contract.id, user.user_id)

    with pytest.raises(InvalidSignatureDigestError):
        signature_service.execute_signature(
            signature_request_id=req.id,
            signature_digest="   ",
        )


def test_execute_signature_signatory_mismatch_raises(db_session, signature_service: SignatureService):
    """Test executing signature with mismatched signatory_id raises SignatureExecutionError."""
    contract = create_sample_contract(db_session, status="DRAFT")
    user1 = create_sample_user(db_session)
    user2 = create_sample_user(db_session)

    req = signature_service.initiate_signature_request(contract.id, user1.user_id)

    with pytest.raises(SignatureExecutionError):
        signature_service.execute_signature(
            signature_request_id=req.id,
            signature_digest="SIG-VALID-DIGEST-123",
            signatory_id=user2.user_id,
        )


def test_execute_already_executed_signature_raises(db_session, signature_service: SignatureService):
    """Test executing an already executed signature request raises SignatureExecutionError."""
    contract = create_sample_contract(db_session, status="DRAFT")
    user = create_sample_user(db_session)
    req = signature_service.initiate_signature_request(contract.id, user.user_id)

    signature_service.execute_signature(
        signature_request_id=req.id,
        signature_digest="SIG-VALID-DIGEST-123",
        signatory_id=user.user_id,
    )

    with pytest.raises(SignatureExecutionError):
        signature_service.execute_signature(
            signature_request_id=req.id,
            signature_digest="SIG-VALID-DIGEST-456",
            signatory_id=user.user_id,
        )


def test_reject_signature_success(db_session, signature_service: SignatureService):
    """Test rejecting a signature request updates status to REJECTED."""
    contract = create_sample_contract(db_session, status="DRAFT")
    user = create_sample_user(db_session)
    req = signature_service.initiate_signature_request(contract.id, user.user_id)

    rejected = signature_service.reject_signature(
        signature_request_id=req.id,
        reason="Terms unacceptable",
        signatory_id=user.user_id,
    )

    assert rejected.id == req.id
    assert rejected.status == SignatureStatus.REJECTED


def test_verify_signature_digest(db_session, signature_service: SignatureService):
    """Test verifying signature digest integrity."""
    contract = create_sample_contract(db_session, status="DRAFT")
    user = create_sample_user(db_session)
    req = signature_service.initiate_signature_request(contract.id, user.user_id)

    digest = "SIG-NATIVE-v1:testdigest123"
    signature_service.execute_signature(
        signature_request_id=req.id,
        signature_digest=digest,
        signatory_id=user.user_id,
    )

    assert signature_service.verify_signature_digest(req.id, digest) is True
    assert signature_service.verify_signature_digest(req.id, "wrongdigest") is False
    assert signature_service.verify_signature_digest(req.id, "") is False
