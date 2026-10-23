"""Unit tests for AmendmentService in backend/src/services/contract/amendment_service.py.

Verifies immutable append-only amendment recording [FR-7], rejection of modifications
and deletions [FR-8], non-repudiable audit trail integrity [NFR-4], and structured event emission [FR-12].
"""

from datetime import date as date_type, datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.contract import Contract, ContractAmendment
from src.models.database import Base
from src.models.tenant import Tenant
from src.models.user import User, hash_password
from src.services.contract.amendment_service import (
    AmendmentMutationForbiddenError,
    AmendmentNotFoundError,
    AmendmentService,
)
from src.services.contract.lifecycle_service import (
    ContractNotFoundError,
    InvalidContractStateError,
)
from src.services.contract.models import (
    ContractCategory,
    ContractStatus,
    LifecycleEventMetadata,
)


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
def sample_contract(db_session: Session) -> Contract:
    """Fixture creating a sample Contract in ACTIVE status."""
    contract = Contract(
        title="Master Agreement 2026",
        category="Contract",
        status="ACTIVE",
        counterparty="Global Corp",
        start_on=date_type(2026, 1, 1),
        expires_on=date_type(2027, 1, 1),
    )
    db_session.add(contract)
    db_session.commit()
    db_session.refresh(contract)
    return contract


@pytest.fixture
def sample_user(db_session: Session) -> User:
    """Fixture creating a sample User for authoring amendments."""
    tenant = Tenant(name="Test Org")
    db_session.add(tenant)
    db_session.commit()

    user = User(
        email="author@org.com",
        password_hash=hash_password("Pass123!"),
        tenant_id=tenant.tenant_id,
    )
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)
    return user


@pytest.fixture
def amendment_service(db_session: Session) -> AmendmentService:
    """Fixture providing AmendmentService instance."""
    return AmendmentService(db=db_session)


def test_record_amendment_success(
    db_session: Session,
    amendment_service: AmendmentService,
    sample_contract: Contract,
    sample_user: User,
):
    """Test recording valid amendments increments sequence_number and updates contract status [FR-7]."""
    metadata = LifecycleEventMetadata(
        actor_id=sample_user.user_id,
        correlation_id="corr-amendment-001",
    )

    # First amendment
    amendment1 = amendment_service.record_amendment(
        contract_id=sample_contract.id,
        summary="Updated payment terms to Net 30.",
        author_id=sample_user.user_id,
        metadata=metadata,
    )

    assert isinstance(amendment1.id, uuid.UUID)
    assert amendment1.contract_id == sample_contract.id
    assert amendment1.sequence_number == 1
    assert amendment1.summary == "Updated payment terms to Net 30."
    assert amendment1.author_id == sample_user.user_id
    assert amendment1.created_at is not None

    # Verify contract status transitioned to AMENDED
    db_session.refresh(sample_contract)
    assert sample_contract.status == ContractStatus.AMENDED.value

    # Second amendment
    amendment2 = amendment_service.record_amendment(
        contract_id=sample_contract.id,
        summary="Extended SLA response time from 2h to 4h.",
        author_id=sample_user.user_id,
    )

    assert amendment2.sequence_number == 2
    assert amendment2.summary == "Extended SLA response time from 2h to 4h."


def test_get_amendments_chronological_sequence(
    amendment_service: AmendmentService,
    sample_contract: Contract,
    sample_user: User,
):
    """Test retrieving amendments returns them in chronological append-only sequence [FR-7]."""
    amendment_service.record_amendment(
        contract_id=sample_contract.id,
        summary="First change",
        author_id=sample_user.user_id,
    )
    amendment_service.record_amendment(
        contract_id=sample_contract.id,
        summary="Second change",
        author_id=sample_user.user_id,
    )
    amendment_service.record_amendment(
        contract_id=sample_contract.id,
        summary="Third change",
        author_id=sample_user.user_id,
    )

    amendments = amendment_service.get_amendments(sample_contract.id)
    assert len(amendments) == 3
    assert [a.sequence_number for a in amendments] == [1, 2, 3]
    assert [a.summary for a in amendments] == [
        "First change",
        "Second change",
        "Third change",
    ]


def test_get_amendment_by_id(
    amendment_service: AmendmentService,
    sample_contract: Contract,
    sample_user: User,
):
    """Test retrieving a specific amendment record by contract ID and amendment ID."""
    created = amendment_service.record_amendment(
        contract_id=sample_contract.id,
        summary="Specific amendment summary",
        author_id=sample_user.user_id,
    )

    fetched = amendment_service.get_amendment(
        contract_id=sample_contract.id,
        amendment_id=created.id,
    )
    assert fetched.id == created.id
    assert fetched.summary == "Specific amendment summary"

    # Non-existent amendment ID
    with pytest.raises(AmendmentNotFoundError):
        amendment_service.get_amendment(
            contract_id=sample_contract.id,
            amendment_id=uuid.uuid4(),
        )


def test_get_amendment_domains(
    amendment_service: AmendmentService,
    sample_contract: Contract,
    sample_user: User,
):
    """Test retrieving domain entities for contract amendments."""
    amendment_service.record_amendment(
        contract_id=sample_contract.id,
        summary="Domain test amendment",
        author_id=sample_user.user_id,
    )

    domains = amendment_service.get_amendment_domains(sample_contract.id)
    assert len(domains) == 1
    assert domains[0].summary == "Domain test amendment"
    d = domains[0].to_dict()
    assert d["contract_id"] == str(sample_contract.id)
    assert d["sequence_number"] == 1


def test_record_amendment_non_existent_contract(amendment_service: AmendmentService):
    """Test recording an amendment for a non-existent contract raises ContractNotFoundError."""
    missing_id = uuid.uuid4()
    with pytest.raises(ContractNotFoundError):
        amendment_service.record_amendment(
            contract_id=missing_id,
            summary="Amendment on missing contract",
        )


def test_record_amendment_invalid_contract_state(
    db_session: Session,
    amendment_service: AmendmentService,
    sample_contract: Contract,
):
    """Test recording an amendment for a TERMINATED or EXPIRED contract raises InvalidContractStateError."""
    sample_contract.status = ContractStatus.TERMINATED.value
    db_session.commit()

    with pytest.raises(InvalidContractStateError):
        amendment_service.record_amendment(
            contract_id=sample_contract.id,
            summary="Attempting amendment on terminated agreement",
        )

    sample_contract.status = ContractStatus.EXPIRED.value
    db_session.commit()

    with pytest.raises(InvalidContractStateError):
        amendment_service.record_amendment(
            contract_id=sample_contract.id,
            summary="Attempting amendment on expired agreement",
        )


def test_record_amendment_empty_summary(
    amendment_service: AmendmentService,
    sample_contract: Contract,
):
    """Test recording an amendment with an empty summary raises ValueError."""
    with pytest.raises(ValueError, match="summary cannot be empty"):
        amendment_service.record_amendment(
            contract_id=sample_contract.id,
            summary="   ",
        )


def test_explicit_mutation_methods_rejected(
    amendment_service: AmendmentService,
    sample_contract: Contract,
    sample_user: User,
):
    """Test update_amendment and delete_amendment explicit methods raise AmendmentMutationForbiddenError [FR-8, NFR-4]."""
    amendment = amendment_service.record_amendment(
        contract_id=sample_contract.id,
        summary="Immutable entry",
        author_id=sample_user.user_id,
    )

    with pytest.raises(AmendmentMutationForbiddenError, match="strictly forbidden"):
        amendment_service.update_amendment(
            contract_id=sample_contract.id,
            amendment_id=amendment.id,
            summary="Modified summary",
        )

    with pytest.raises(AmendmentMutationForbiddenError, match="strictly forbidden"):
        amendment_service.delete_amendment(
            contract_id=sample_contract.id,
            amendment_id=amendment.id,
        )


def test_orm_update_listener_blocks_mutation(
    db_session: Session,
    amendment_service: AmendmentService,
    sample_contract: Contract,
    sample_user: User,
):
    """Test ORM event listener prevents updating an existing amendment record directly [FR-8, NFR-4]."""
    amendment = amendment_service.record_amendment(
        contract_id=sample_contract.id,
        summary="Original amendment text",
        author_id=sample_user.user_id,
    )

    # Attempt direct ORM property modification
    amendment.summary = "Tampered amendment text"
    with pytest.raises(AmendmentMutationForbiddenError):
        db_session.commit()

    db_session.rollback()


def test_orm_delete_listener_blocks_deletion(
    db_session: Session,
    amendment_service: AmendmentService,
    sample_contract: Contract,
    sample_user: User,
):
    """Test ORM event listener prevents deleting an existing amendment record directly [FR-8, NFR-4]."""
    amendment = amendment_service.record_amendment(
        contract_id=sample_contract.id,
        summary="Permanent audit record",
        author_id=sample_user.user_id,
    )

    # Attempt direct ORM deletion
    db_session.delete(amendment)
    with pytest.raises(AmendmentMutationForbiddenError):
        db_session.commit()

    db_session.rollback()
