"""Abstract port interface for e-signature tracking and execution.

Defines the contract for creating, listing, executing, rejecting, and verifying
digital e-signature requests for contracts [FR-5, FR-6].
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
import uuid

from src.services.contract.models import SignatureRequestDomain, SignatureStatus


class SignaturePortError(Exception):
    """Base exception for e-signature tracking operations."""

    pass


class SignatureRequestNotFoundError(SignaturePortError):
    """Raised when a requested signature request is not found."""

    pass


class SignatureExecutionError(SignaturePortError):
    """Raised when digital signature execution fails or state transition is invalid."""

    pass


class InvalidSignatureDigestError(SignatureExecutionError):
    """Raised when a signature digest fails validation or verification."""

    pass


@dataclass
class PendingSignatureItem:
    """Dataclass representing a pending signature item for the centralized hub [FR-5]."""

    signature_request_id: uuid.UUID
    contract_id: uuid.UUID
    signatory_id: uuid.UUID
    status: SignatureStatus = SignatureStatus.PENDING
    contract_title: Optional[str] = None
    counterparty: Optional[str] = None
    requested_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert pending signature item to dictionary representation."""
        return {
            "signature_request_id": str(self.signature_request_id),
            "contract_id": str(self.contract_id),
            "signatory_id": str(self.signatory_id),
            "status": self.status.value if isinstance(self.status, SignatureStatus) else str(self.status),
            "contract_title": self.contract_title,
            "counterparty": self.counterparty,
            "requested_at": self.requested_at.isoformat() if self.requested_at else None,
        }


@dataclass
class SignatureExecutionResult:
    """Dataclass holding metadata resulting from a digital signature execution [FR-6]."""

    signature_request_id: uuid.UUID
    contract_id: uuid.UUID
    signatory_id: uuid.UUID
    status: SignatureStatus
    signature_digest: str
    executed_at: datetime
    notified_parties: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert signature execution result to dictionary representation."""
        return {
            "signature_request_id": str(self.signature_request_id),
            "contract_id": str(self.contract_id),
            "signatory_id": str(self.signatory_id),
            "status": self.status.value if isinstance(self.status, SignatureStatus) else str(self.status),
            "signature_digest": self.signature_digest,
            "executed_at": self.executed_at.isoformat(),
            "notified_parties": self.notified_parties,
        }


class SignaturePort(ABC):
    """Abstract port interface for e-signature tracking and execution.

    Abstracts underlying e-signature providers (native internal signing engine,
    third-party e-signature APIs) from domain business logic [FR-5, FR-6].
    """

    @abstractmethod
    def create_signature_request(
        self,
        contract_id: uuid.UUID,
        signatory_id: uuid.UUID,
    ) -> SignatureRequestDomain:
        """Create and initiate a new signature request for a contract.

        Args:
            contract_id: Unique identifier of the contract.
            signatory_id: Unique identifier of the intended signatory.

        Returns:
            SignatureRequestDomain entity initialized in PENDING status.

        Raises:
            SignatureExecutionError: If request creation fails or invalid contract state.
        """
        pass

    @abstractmethod
    def get_signature_request(
        self,
        signature_request_id: uuid.UUID,
    ) -> SignatureRequestDomain:
        """Retrieve a signature request by its unique identifier.

        Args:
            signature_request_id: Unique identifier of the signature request.

        Returns:
            SignatureRequestDomain entity.

        Raises:
            SignatureRequestNotFoundError: If the signature request is not found.
        """
        pass

    @abstractmethod
    def list_pending_signatures(
        self,
        contract_id: Optional[uuid.UUID] = None,
        signatory_id: Optional[uuid.UUID] = None,
    ) -> List[SignatureRequestDomain]:
        """List all contracts currently pending signature execution alongside signatory status [FR-5].

        Args:
            contract_id: Optional filter for a specific contract.
            signatory_id: Optional filter for a specific signatory.

        Returns:
            List of SignatureRequestDomain entities currently in PENDING status.
        """
        pass

    @abstractmethod
    def list_signatures_for_contract(
        self,
        contract_id: uuid.UUID,
    ) -> List[SignatureRequestDomain]:
        """List all signature requests (pending, executed, rejected) associated with a contract.

        Args:
            contract_id: Unique identifier of the contract.

        Returns:
            List of SignatureRequestDomain entities for the given contract.
        """
        pass

    @abstractmethod
    def execute_signature(
        self,
        signature_request_id: uuid.UUID,
        signature_digest: str,
        signatory_id: Optional[uuid.UUID] = None,
    ) -> SignatureExecutionResult:
        """Capture digital signature execution, transition status to EXECUTED, and notify parties [FR-6].

        Args:
            signature_request_id: Unique identifier of the pending signature request.
            signature_digest: Cryptographic digest or signature token capturing execution.
            signatory_id: Optional expected signatory identifier for authorization verification.

        Returns:
            SignatureExecutionResult containing updated status, execution timestamp, and notification details.

        Raises:
            SignatureRequestNotFoundError: If signature request does not exist.
            SignatureExecutionError: If state transition is invalid or already executed/rejected.
            InvalidSignatureDigestError: If signature_digest is empty or invalid.
        """
        pass

    @abstractmethod
    def reject_signature(
        self,
        signature_request_id: uuid.UUID,
        reason: Optional[str] = None,
    ) -> SignatureRequestDomain:
        """Reject a pending signature request and transition status to REJECTED.

        Args:
            signature_request_id: Unique identifier of the pending signature request.
            reason: Optional explanation for rejection.

        Returns:
            Updated SignatureRequestDomain entity in REJECTED status.

        Raises:
            SignatureRequestNotFoundError: If signature request does not exist.
            SignatureExecutionError: If state transition is invalid.
        """
        pass

    @abstractmethod
    def verify_signature_digest(
        self,
        signature_request_id: uuid.UUID,
        signature_digest: str,
    ) -> bool:
        """Verify the integrity or authenticity of an executed signature digest.

        Args:
            signature_request_id: Unique identifier of the signature request.
            signature_digest: Digest string to verify against stored execution metadata.

        Returns:
            True if valid, False otherwise.
        """
        pass
