"""E-signature domain service for state machine transitions and execution verification [FR-5, FR-6].

Manages digital signature execution workflows, state machine transitions (PENDING -> EXECUTED/REJECTED),
verification of signature digests, party notification [FR-6], and centralized pending signature listing [FR-5].
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy.orm import Session

from src.models.contract import Contract
from src.services.contract.lifecycle_service import (
    ContractNotFoundError,
    InvalidContractStateError,
    LifecycleService,
)
from src.services.contract.models import (
    ContractSignedPayload,
    ContractStatus,
    LifecycleEventMetadata,
    LifecycleEventType,
    SignatureRequestDomain,
    SignatureStatus,
)
from src.services.contract.notification_port import NotificationPort
from src.services.contract.signature_port import (
    InvalidSignatureDigestError,
    PendingSignatureItem,
    SignatureExecutionError,
    SignatureExecutionResult,
    SignaturePort,
    SignaturePortError,
    SignatureRequestNotFoundError,
)

logger = logging.getLogger(__name__)


class SignatureServiceError(SignaturePortError):
    """Base exception for signature service operations."""

    pass


class SignatureService:
    """Domain service managing e-signature state transitions and execution verification [FR-5, FR-6]."""

    def __init__(
        self,
        db: Optional[Session] = None,
        signature_port: Optional[SignaturePort] = None,
        notification_port: Optional[NotificationPort] = None,
        lifecycle_service: Optional[LifecycleService] = None,
    ) -> None:
        """Initialize SignatureService with database session and optional ports or services.

        Args:
            db: Optional SQLAlchemy Session instance for database query execution.
            signature_port: Optional SignaturePort adapter instance.
            notification_port: Optional NotificationPort adapter instance.
            lifecycle_service: Optional LifecycleService instance.
        """
        self.db = db
        self._signature_port = signature_port
        self._notification_port = notification_port
        self._lifecycle_service = lifecycle_service

    @property
    def signature_port(self) -> SignaturePort:
        """Lazy-load default SignatureConnector if explicit SignaturePort was not supplied."""
        if self._signature_port is None:
            from src.connectors.signature_connector import SignatureConnector

            self._signature_port = SignatureConnector(db=self.db)
        return self._signature_port

    @property
    def notification_port(self) -> NotificationPort:
        """Lazy-load default NotificationConnector if explicit NotificationPort was not supplied."""
        if self._notification_port is None:
            from src.connectors.notification_connector import NotificationConnector

            self._notification_port = NotificationConnector(db=self.db)
        return self._notification_port

    @property
    def lifecycle_service(self) -> LifecycleService:
        """Lazy-load default LifecycleService if explicit instance was not supplied."""
        if self._lifecycle_service is None:
            if self.db is None:
                raise ValueError("Database session is required to initialize default LifecycleService.")
            self._lifecycle_service = LifecycleService(db=self.db)
        return self._lifecycle_service

    def initiate_signature_request(
        self,
        contract_id: uuid.UUID,
        signatory_id: uuid.UUID,
        metadata: Optional[LifecycleEventMetadata] = None,
    ) -> SignatureRequestDomain:
        """Initiate a signature request for a contract and transition contract status to PENDING_SIGNATURE [FR-5].

        Args:
            contract_id: Unique identifier of the target contract.
            signatory_id: Unique identifier of the intended signatory.
            metadata: Optional event metadata for audit logging.

        Returns:
            SignatureRequestDomain entity initialized in PENDING status.

        Raises:
            ContractNotFoundError: If target contract is not found in database.
            InvalidContractStateError: If contract is in TERMINATED or EXPIRED status.
            SignatureExecutionError: If signature request creation fails.
        """
        if self.db:
            contract = self.db.query(Contract).filter(Contract.id == contract_id).first()
            if not contract:
                raise ContractNotFoundError(f"Contract with ID '{contract_id}' not found.")

            if contract.status in {ContractStatus.TERMINATED.value, ContractStatus.EXPIRED.value}:
                raise InvalidContractStateError(
                    f"Cannot initiate signature request for contract '{contract_id}' in '{contract.status}' status."
                )

        req_domain = self.signature_port.create_signature_request(
            contract_id=contract_id,
            signatory_id=signatory_id,
        )

        if self.db:
            contract = self.db.query(Contract).filter(Contract.id == contract_id).first()
            if contract and contract.status == ContractStatus.DRAFT.value:
                contract.status = ContractStatus.PENDING_SIGNATURE.value
                contract.updated_at = datetime.now(timezone.utc)
                self.db.commit()

        if self._lifecycle_service or self.db:
            try:
                self.lifecycle_service.emit_lifecycle_event(
                    event_type=LifecycleEventType.STATUS_CHANGE,
                    contract_id=contract_id,
                    payload={
                        "action": "INITIATE_SIGNATURE_REQUEST",
                        "signature_request_id": str(req_domain.id),
                        "signatory_id": str(signatory_id),
                        "contract_status": ContractStatus.PENDING_SIGNATURE.value,
                    },
                    metadata=metadata,
                )
            except Exception as exc:
                logger.warning("Failed to emit status change lifecycle event during signature initiation: %s", exc)

        logger.info(
            "Initiated signature request %s for contract %s (signatory=%s)",
            req_domain.id,
            contract_id,
            signatory_id,
        )
        return req_domain

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
        return self.signature_port.get_signature_request(signature_request_id)

    def list_pending_signatures(
        self,
        contract_id: Optional[uuid.UUID] = None,
        signatory_id: Optional[uuid.UUID] = None,
    ) -> List[PendingSignatureItem]:
        """List all pending signature execution requests alongside contract and signatory status for centralized hub [FR-5].

        Args:
            contract_id: Optional filter for a specific contract.
            signatory_id: Optional filter for a specific signatory.

        Returns:
            List of PendingSignatureItem dataclasses containing pending signature details and contract metadata.
        """
        raw_requests = self.signature_port.list_pending_signatures(
            contract_id=contract_id,
            signatory_id=signatory_id,
        )

        items: List[PendingSignatureItem] = []
        for req in raw_requests:
            contract_title = None
            counterparty = None
            requested_at = None

            if self.db:
                contract = self.db.query(Contract).filter(Contract.id == req.contract_id).first()
                if contract:
                    contract_title = contract.title
                    counterparty = contract.counterparty
                    requested_at = contract.created_at

            status_val = req.status if isinstance(req.status, SignatureStatus) else SignatureStatus(req.status)

            items.append(
                PendingSignatureItem(
                    signature_request_id=req.id,
                    contract_id=req.contract_id,
                    signatory_id=req.signatory_id,
                    status=status_val,
                    contract_title=contract_title,
                    counterparty=counterparty,
                    requested_at=requested_at,
                )
            )

        return items

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
        return self.signature_port.list_signatures_for_contract(contract_id)

    def execute_signature(
        self,
        signature_request_id: uuid.UUID,
        signature_digest: str,
        signatory_id: Optional[uuid.UUID] = None,
        metadata: Optional[LifecycleEventMetadata] = None,
    ) -> SignatureExecutionResult:
        """Capture digital signature execution, transition status to EXECUTED, and notify all parties [FR-6].

        Args:
            signature_request_id: Unique identifier of the pending signature request.
            signature_digest: Cryptographic digest or signature token capturing execution.
            signatory_id: Optional expected signatory identifier for authorization verification.
            metadata: Optional event metadata for audit logging.

        Returns:
            SignatureExecutionResult containing updated status, execution timestamp, and notification details.

        Raises:
            SignatureRequestNotFoundError: If signature request does not exist.
            SignatureExecutionError: If state transition is invalid or signatory mismatch.
            InvalidSignatureDigestError: If signature_digest is empty or whitespace-only.
        """
        if not signature_digest or not signature_digest.strip():
            raise InvalidSignatureDigestError("Signature digest cannot be empty or whitespace-only.")

        existing_req = self.signature_port.get_signature_request(signature_request_id)
        if existing_req.status != SignatureStatus.PENDING:
            raise SignatureExecutionError(
                f"Cannot execute signature request '{signature_request_id}' in status '{existing_req.status}'."
            )

        if signatory_id and existing_req.signatory_id != signatory_id:
            raise SignatureExecutionError(
                f"Signatory ID mismatch: expected '{existing_req.signatory_id}', got '{signatory_id}'."
            )

        exec_result = self.signature_port.execute_signature(
            signature_request_id=signature_request_id,
            signature_digest=signature_digest.strip(),
            signatory_id=signatory_id,
        )

        contract_id = existing_req.contract_id
        all_requests = self.signature_port.list_signatures_for_contract(contract_id)
        all_executed = all_requests and all(r.status == SignatureStatus.EXECUTED for r in all_requests)

        resulting_contract_status = ContractStatus.PENDING_SIGNATURE
        contract = None
        if self.db:
            contract = self.db.query(Contract).filter(Contract.id == contract_id).first()

        if all_executed:
            resulting_contract_status = ContractStatus.ACTIVE
            if contract:
                contract.status = ContractStatus.ACTIVE.value
                contract.updated_at = datetime.now(timezone.utc)
                self.db.commit()

        # Gather notified parties [FR-6]
        notified_parties: List[str] = list(exec_result.notified_parties or [])
        signatory_str = str(existing_req.signatory_id)
        if signatory_str not in notified_parties:
            notified_parties.append(signatory_str)

        if contract and contract.counterparty and contract.counterparty not in notified_parties:
            notified_parties.append(contract.counterparty)

        exec_result.notified_parties = notified_parties

        # Emit structured lifecycle event [FR-12]
        if self._lifecycle_service or self.db:
            try:
                signed_payload = ContractSignedPayload(
                    contract_id=contract_id,
                    signature_request_id=signature_request_id,
                    signatory_id=existing_req.signatory_id,
                    signature_digest=signature_digest.strip(),
                    executed_at=exec_result.executed_at,
                    resulting_status=resulting_contract_status,
                )
                self.lifecycle_service.emit_lifecycle_event(
                    event_type=LifecycleEventType.SIGNING,
                    contract_id=contract_id,
                    payload=signed_payload,
                    metadata=metadata,
                )
            except Exception as exc:
                logger.warning("Failed to emit contract signing lifecycle event: %s", exc)

        logger.info(
            "Executed signature request %s for contract %s (contract_status=%s, notified=%d parties)",
            signature_request_id,
            contract_id,
            resulting_contract_status.value,
            len(notified_parties),
        )

        return exec_result

    def reject_signature(
        self,
        signature_request_id: uuid.UUID,
        reason: Optional[str] = None,
        signatory_id: Optional[uuid.UUID] = None,
        metadata: Optional[LifecycleEventMetadata] = None,
    ) -> SignatureRequestDomain:
        """Reject a pending signature request and record state transition.

        Args:
            signature_request_id: Unique identifier of the signature request.
            reason: Optional explanation for rejection.
            signatory_id: Optional expected signatory identifier for authorization check.
            metadata: Optional audit event metadata.

        Returns:
            Updated SignatureRequestDomain entity in REJECTED status.

        Raises:
            SignatureRequestNotFoundError: If signature request does not exist.
            SignatureExecutionError: If state transition is invalid or signatory mismatch.
        """
        existing_req = self.signature_port.get_signature_request(signature_request_id)
        if existing_req.status != SignatureStatus.PENDING:
            raise SignatureExecutionError(
                f"Cannot reject signature request '{signature_request_id}' in status '{existing_req.status}'."
            )

        if signatory_id and existing_req.signatory_id != signatory_id:
            raise SignatureExecutionError(
                f"Signatory ID mismatch: expected '{existing_req.signatory_id}', got '{signatory_id}'."
            )

        rejected_req = self.signature_port.reject_signature(
            signature_request_id=signature_request_id,
            reason=reason,
        )

        if self._lifecycle_service or self.db:
            try:
                self.lifecycle_service.emit_lifecycle_event(
                    event_type=LifecycleEventType.STATUS_CHANGE,
                    contract_id=existing_req.contract_id,
                    payload={
                        "action": "REJECT_SIGNATURE_REQUEST",
                        "signature_request_id": str(signature_request_id),
                        "signatory_id": str(existing_req.signatory_id),
                        "reason": reason or "",
                    },
                    metadata=metadata,
                )
            except Exception as exc:
                logger.warning("Failed to emit signature rejection lifecycle event: %s", exc)

        logger.info(
            "Rejected signature request %s for contract %s (reason=%s)",
            signature_request_id,
            existing_req.contract_id,
            reason,
        )
        return rejected_req

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
        if not signature_digest or not signature_digest.strip():
            return False

        return self.signature_port.verify_signature_digest(
            signature_request_id=signature_request_id,
            signature_digest=signature_digest.strip(),
        )
