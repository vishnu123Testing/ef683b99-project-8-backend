"""Amendment domain service for managing contract amendments [FR-7, FR-8, NFR-4].

Enforces immutable append-only audit trail operations for contract amendments.
All amendments are time-stamped, sequentially numbered per contract, and cannot
be updated, mutated, truncated, or deleted once created.
"""

from datetime import datetime, timezone
import logging
from typing import List, Optional
import uuid

from sqlalchemy import event, func
from sqlalchemy.orm import Session

from src.models.contract import Contract, ContractAmendment
from src.services.contract.lifecycle_service import (
    ContractNotFoundError,
    InvalidContractStateError,
    LifecycleService,
)
from src.services.contract.models import (
    ContractAmendedPayload,
    ContractAmendmentDomain,
    ContractStatus,
    LifecycleEventMetadata,
    LifecycleEventType,
)

logger = logging.getLogger(__name__)


class AmendmentServiceError(Exception):
    """Base exception for amendment service operations."""

    pass


class AmendmentNotFoundError(AmendmentServiceError):
    """Raised when a requested contract amendment record is not found."""

    pass


class AmendmentMutationForbiddenError(AmendmentServiceError):
    """Raised when an operation attempts to update, modify, or delete an existing amendment audit record [FR-8, NFR-4]."""

    pass


# Register SQLAlchemy ORM event listeners to enforce immutability at DB session level [FR-8, NFR-4]
@event.listens_for(ContractAmendment, "before_update")
def _prevent_amendment_update(mapper, connection, target):
    """Prevent modification of existing ContractAmendment records [FR-8, NFR-4]."""
    raise AmendmentMutationForbiddenError(
        f"Modifying existing amendment audit trail record '{target.id}' is strictly forbidden [FR-8, NFR-4]."
    )


@event.listens_for(ContractAmendment, "before_delete")
def _prevent_amendment_delete(mapper, connection, target):
    """Prevent deletion of existing ContractAmendment records [FR-8, NFR-4]."""
    raise AmendmentMutationForbiddenError(
        f"Deleting existing amendment audit trail record '{target.id}' is strictly forbidden [FR-8, NFR-4]."
    )


class AmendmentService:
    """Domain service managing immutable append-only contract amendment recording and validation [FR-7, FR-8, NFR-4]."""

    def __init__(
        self,
        db: Session,
        lifecycle_service: Optional[LifecycleService] = None,
    ) -> None:
        """Initialize AmendmentService with database session and optional LifecycleService."""
        self.db = db
        self._lifecycle_service = lifecycle_service

    @property
    def lifecycle_service(self) -> LifecycleService:
        """Lazy-load LifecycleService if explicit instance was not supplied."""
        if self._lifecycle_service is None:
            self._lifecycle_service = LifecycleService(db=self.db)
        return self._lifecycle_service

    def record_amendment(
        self,
        contract_id: uuid.UUID,
        summary: str,
        author_id: Optional[uuid.UUID] = None,
        metadata: Optional[LifecycleEventMetadata] = None,
    ) -> ContractAmendment:
        """Record a new contract amendment in the append-only audit trail [FR-7, FR-8, NFR-4].

        Args:
            contract_id: UUID of the target contract.
            summary: Summary description of the amendment changes.
            author_id: Optional UUID of the user authoring the amendment.
            metadata: Optional event metadata for correlation logging.

        Returns:
            The created ContractAmendment ORM entity.

        Raises:
            ContractNotFoundError: If the target contract does not exist.
            InvalidContractStateError: If the contract is in a TERMINATED or EXPIRED state.
            ValueError: If summary is empty or whitespace-only.
        """
        if not summary or not summary.strip():
            raise ValueError("Amendment summary cannot be empty.")

        # Ensure target contract exists
        contract = (
            self.db.query(Contract).filter(Contract.id == contract_id).first()
        )
        if not contract:
            raise ContractNotFoundError(f"Contract with ID '{contract_id}' not found.")

        # Check if contract state permits amendments (TERMINATED or EXPIRED contracts cannot be amended)
        if contract.status in {ContractStatus.TERMINATED.value, ContractStatus.EXPIRED.value}:
            raise InvalidContractStateError(
                f"Cannot record amendment for contract '{contract_id}' in '{contract.status}' status."
            )

        # Calculate next sequence number for this contract
        max_seq = (
            self.db.query(func.max(ContractAmendment.sequence_number))
            .filter(ContractAmendment.contract_id == contract_id)
            .scalar()
        )
        next_sequence = (max_seq or 0) + 1

        now = datetime.now(timezone.utc)
        amendment = ContractAmendment(
            id=uuid.uuid4(),
            contract_id=contract_id,
            sequence_number=next_sequence,
            summary=summary.strip(),
            author_id=author_id,
            created_at=now,
        )

        # Append amendment entry
        self.db.add(amendment)

        # Update contract status to AMENDED if active or draft or pending signature, and refresh updated_at
        if contract.status in {
            ContractStatus.ACTIVE.value,
            ContractStatus.DRAFT.value,
            ContractStatus.PENDING_SIGNATURE.value,
        }:
            contract.status = ContractStatus.AMENDED.value
        contract.updated_at = now

        self.db.commit()
        self.db.refresh(amendment)

        # Emit structured audit event
        amendment_payload = ContractAmendedPayload(
            contract_id=contract_id,
            amendment_id=amendment.id,
            sequence_number=amendment.sequence_number,
            summary=amendment.summary,
            author_id=amendment.author_id,
        )
        self.lifecycle_service.emit_lifecycle_event(
            event_type=LifecycleEventType.AMENDMENT,
            contract_id=contract_id,
            payload=amendment_payload,
            metadata=metadata,
        )

        logger.info(
            "Contract amendment recorded: contract_id=%s, sequence=%d, amendment_id=%s",
            contract_id,
            amendment.sequence_number,
            amendment.id,
        )
        return amendment

    def get_amendment(
        self,
        contract_id: uuid.UUID,
        amendment_id: uuid.UUID,
    ) -> ContractAmendment:
        """Retrieve a specific amendment record by contract ID and amendment ID."""
        contract = self.db.query(Contract).filter(Contract.id == contract_id).first()
        if not contract:
            raise ContractNotFoundError(f"Contract with ID '{contract_id}' not found.")

        amendment = (
            self.db.query(ContractAmendment)
            .filter(
                ContractAmendment.id == amendment_id,
                ContractAmendment.contract_id == contract_id,
            )
            .first()
        )
        if not amendment:
            raise AmendmentNotFoundError(
                f"Amendment '{amendment_id}' not found for contract '{contract_id}'."
            )
        return amendment

    def get_amendments(
        self,
        contract_id: uuid.UUID,
    ) -> List[ContractAmendment]:
        """Retrieve all amendments for a contract in chronological append-only sequence order [FR-7, FR-8]."""
        contract = self.db.query(Contract).filter(Contract.id == contract_id).first()
        if not contract:
            raise ContractNotFoundError(f"Contract with ID '{contract_id}' not found.")

        return (
            self.db.query(ContractAmendment)
            .filter(ContractAmendment.contract_id == contract_id)
            .order_by(ContractAmendment.sequence_number.asc())
            .all()
        )

    def get_amendment_domains(
        self,
        contract_id: uuid.UUID,
    ) -> List[ContractAmendmentDomain]:
        """Retrieve all amendments for a contract mapped to domain entities [FR-7]."""
        amendments = self.get_amendments(contract_id)
        return [
            ContractAmendmentDomain(
                id=a.id,
                contract_id=a.contract_id,
                sequence_number=a.sequence_number,
                summary=a.summary,
                author_id=a.author_id,
                created_at=a.created_at,
            )
            for a in amendments
        ]

    def update_amendment(
        self,
        contract_id: uuid.UUID,
        amendment_id: uuid.UUID,
        summary: str,
    ) -> None:
        """Explicitly reject attempts to modify existing amendment records [FR-8, NFR-4]."""
        raise AmendmentMutationForbiddenError(
            "Modifying existing amendment audit trail records is strictly forbidden [FR-8, NFR-4]."
        )

    def delete_amendment(
        self,
        contract_id: uuid.UUID,
        amendment_id: uuid.UUID,
    ) -> None:
        """Explicitly reject attempts to delete existing amendment records [FR-8, NFR-4]."""
        raise AmendmentMutationForbiddenError(
            "Deleting existing amendment audit trail records is strictly forbidden [FR-8, NFR-4]."
        )
