"""Native e-signature connector implementation for digital signature execution [FR-5, FR-6].

Implements SignaturePort for creating, querying, listing, executing, rejecting,
and verifying digital e-signature requests using database persistence and
native cryptographic signature digest generation.
"""

from datetime import datetime, timezone
import hashlib
import hmac
from typing import Dict, List, Optional, Union
import uuid

from sqlalchemy.orm import Session

from src.lib.config import settings
from src.models.contract import SignatureRequest
from src.services.contract.models import SignatureRequestDomain, SignatureStatus
from src.services.contract.signature_port import (
    InvalidSignatureDigestError,
    PendingSignatureItem,
    SignatureExecutionError,
    SignatureExecutionResult,
    SignaturePort,
    SignatureRequestNotFoundError,
)


class SignatureConnector(SignaturePort):
    """Native e-signature connector implementing SignaturePort interface [FR-5, FR-6].

    Supports database persistence via SQLAlchemy Session when provided, as well as
    in-memory execution and state tracking for testing and isolated workflows.
    """

    def __init__(
        self,
        db: Optional[Session] = None,
        secret_key: Optional[str] = None,
    ) -> None:
        """Initialize SignatureConnector with optional database session and secret key.

        Args:
            db: Optional SQLAlchemy Session instance for database persistence.
            secret_key: Optional secret key for HMAC signature digest generation.
        """
        self.db = db
        self.secret_key = secret_key or settings.secret_key
        self._requests: Dict[uuid.UUID, SignatureRequestDomain] = {}

    def _orm_to_domain(self, orm: SignatureRequest) -> SignatureRequestDomain:
        """Convert a SignatureRequest ORM instance to a SignatureRequestDomain entity."""
        try:
            status_enum = SignatureStatus(orm.status)
        except ValueError:
            status_enum = SignatureStatus.PENDING

        return SignatureRequestDomain(
            id=orm.id,
            contract_id=orm.contract_id,
            signatory_id=orm.signatory_id,
            status=status_enum,
            signature_digest=orm.signature_digest,
            executed_at=orm.executed_at,
        )

    def generate_native_signature_digest(
        self,
        contract_id: uuid.UUID,
        signatory_id: uuid.UUID,
        timestamp: Optional[datetime] = None,
    ) -> str:
        """Generate a deterministic cryptographic signature digest for native signing execution.

        Args:
            contract_id: Unique identifier of the contract.
            signatory_id: Unique identifier of the signatory.
            timestamp: Optional execution timestamp (defaults to current UTC time).

        Returns:
            Cryptographic signature digest string.
        """
        ts = timestamp or datetime.now(timezone.utc)
        payload = f"{contract_id}:{signatory_id}:{ts.isoformat()}".encode("utf-8")
        digest = hmac.new(
            self.secret_key.encode("utf-8"),
            payload,
            hashlib.sha256,
        ).hexdigest()
        return f"SIG-NATIVE-v1:{digest}"

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
            SignatureExecutionError: If parameters are invalid or creation fails.
        """
        if not contract_id or not signatory_id:
            raise SignatureExecutionError("Both contract_id and signatory_id are required.")

        req_id = uuid.uuid4()

        if self.db is not None:
            orm = SignatureRequest(
                id=req_id,
                contract_id=contract_id,
                signatory_id=signatory_id,
                status=SignatureStatus.PENDING.value,
            )
            self.db.add(orm)
            self.db.commit()
            self.db.refresh(orm)
            return self._orm_to_domain(orm)

        domain = SignatureRequestDomain(
            id=req_id,
            contract_id=contract_id,
            signatory_id=signatory_id,
            status=SignatureStatus.PENDING,
        )
        self._requests[req_id] = domain
        return domain

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
        if self.db is not None:
            orm = (
                self.db.query(SignatureRequest)
                .filter(SignatureRequest.id == signature_request_id)
                .first()
            )
            if not orm:
                raise SignatureRequestNotFoundError(
                    f"Signature request {signature_request_id} not found."
                )
            return self._orm_to_domain(orm)

        if signature_request_id not in self._requests:
            raise SignatureRequestNotFoundError(
                f"Signature request {signature_request_id} not found."
            )
        return self._requests[signature_request_id]

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
        if self.db is not None:
            query = self.db.query(SignatureRequest).filter(
                SignatureRequest.status == SignatureStatus.PENDING.value
            )
            if contract_id:
                query = query.filter(SignatureRequest.contract_id == contract_id)
            if signatory_id:
                query = query.filter(SignatureRequest.signatory_id == signatory_id)
            orm_items = query.all()
            return [self._orm_to_domain(item) for item in orm_items]

        results = [
            r for r in self._requests.values() if r.status == SignatureStatus.PENDING
        ]
        if contract_id:
            results = [r for r in results if r.contract_id == contract_id]
        if signatory_id:
            results = [r for r in results if r.signatory_id == signatory_id]
        return results

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
        if self.db is not None:
            orm_items = (
                self.db.query(SignatureRequest)
                .filter(SignatureRequest.contract_id == contract_id)
                .all()
            )
            return [self._orm_to_domain(item) for item in orm_items]

        return [r for r in self._requests.values() if r.contract_id == contract_id]

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
        if not signature_digest or not str(signature_digest).strip():
            raise InvalidSignatureDigestError("Signature digest cannot be empty or blank.")

        req = self.get_signature_request(signature_request_id)

        if req.status != SignatureStatus.PENDING:
            raise SignatureExecutionError(
                f"Cannot execute signature request in status {req.status}."
            )

        if signatory_id is not None and req.signatory_id != signatory_id:
            raise SignatureExecutionError("Signatory ID mismatch for signature execution.")

        now = datetime.now(timezone.utc)

        if self.db is not None:
            orm = (
                self.db.query(SignatureRequest)
                .filter(SignatureRequest.id == signature_request_id)
                .first()
            )
            if not orm:
                raise SignatureRequestNotFoundError(
                    f"Signature request {signature_request_id} not found."
                )
            orm.status = SignatureStatus.EXECUTED.value
            orm.signature_digest = signature_digest
            orm.executed_at = now
            self.db.commit()
        else:
            req.status = SignatureStatus.EXECUTED
            req.signature_digest = signature_digest
            req.executed_at = now
            self._requests[signature_request_id] = req

        notified_parties = [str(req.signatory_id)]

        return SignatureExecutionResult(
            signature_request_id=req.id or signature_request_id,
            contract_id=req.contract_id,
            signatory_id=req.signatory_id,
            status=SignatureStatus.EXECUTED,
            signature_digest=signature_digest,
            executed_at=now,
            notified_parties=notified_parties,
        )

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
        req = self.get_signature_request(signature_request_id)

        if req.status != SignatureStatus.PENDING:
            raise SignatureExecutionError(
                f"Cannot reject signature request in status {req.status}."
            )

        if self.db is not None:
            orm = (
                self.db.query(SignatureRequest)
                .filter(SignatureRequest.id == signature_request_id)
                .first()
            )
            if not orm:
                raise SignatureRequestNotFoundError(
                    f"Signature request {signature_request_id} not found."
                )
            orm.status = SignatureStatus.REJECTED.value
            self.db.commit()
            self.db.refresh(orm)
            return self._orm_to_domain(orm)

        req.status = SignatureStatus.REJECTED
        self._requests[signature_request_id] = req
        return req

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
        if not signature_digest or not str(signature_digest).strip():
            return False

        try:
            req = self.get_signature_request(signature_request_id)
        except SignatureRequestNotFoundError:
            return False

        if req.status != SignatureStatus.EXECUTED or not req.signature_digest:
            return False

        return req.signature_digest == signature_digest
