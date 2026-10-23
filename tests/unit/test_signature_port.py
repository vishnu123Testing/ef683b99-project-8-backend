"""Unit tests for e-signature tracking port interface, exception hierarchy, and DTOs [FR-5, FR-6]."""

from datetime import datetime, timezone
from typing import Dict, List, Optional
import uuid

import pytest

from src.services.contract.models import SignatureRequestDomain, SignatureStatus
from src.services.contract.signature_port import (
    InvalidSignatureDigestError,
    PendingSignatureItem,
    SignatureExecutionError,
    SignatureExecutionResult,
    SignaturePort,
    SignaturePortError,
    SignatureRequestNotFoundError,
)


class DummySignatureAdapter(SignaturePort):
    """Minimal in-memory adapter implementing SignaturePort for contract verification."""

    def __init__(self) -> None:
        self._requests: Dict[uuid.UUID, SignatureRequestDomain] = {}

    def create_signature_request(
        self,
        contract_id: uuid.UUID,
        signatory_id: uuid.UUID,
    ) -> SignatureRequestDomain:
        req_id = uuid.uuid4()
        req = SignatureRequestDomain(
            id=req_id,
            contract_id=contract_id,
            signatory_id=signatory_id,
            status=SignatureStatus.PENDING,
        )
        self._requests[req_id] = req
        return req

    def get_signature_request(
        self,
        signature_request_id: uuid.UUID,
    ) -> SignatureRequestDomain:
        if signature_request_id not in self._requests:
            raise SignatureRequestNotFoundError(f"Signature request {signature_request_id} not found.")
        return self._requests[signature_request_id]

    def list_pending_signatures(
        self,
        contract_id: Optional[uuid.UUID] = None,
        signatory_id: Optional[uuid.UUID] = None,
    ) -> List[SignatureRequestDomain]:
        results = [r for r in self._requests.values() if r.status == SignatureStatus.PENDING]
        if contract_id:
            results = [r for r in results if r.contract_id == contract_id]
        if signatory_id:
            results = [r for r in results if r.signatory_id == signatory_id]
        return results

    def list_signatures_for_contract(
        self,
        contract_id: uuid.UUID,
    ) -> List[SignatureRequestDomain]:
        return [r for r in self._requests.values() if r.contract_id == contract_id]

    def execute_signature(
        self,
        signature_request_id: uuid.UUID,
        signature_digest: str,
        signatory_id: Optional[uuid.UUID] = None,
    ) -> SignatureExecutionResult:
        if not signature_digest:
            raise InvalidSignatureDigestError("Signature digest cannot be empty.")
        req = self.get_signature_request(signature_request_id)
        if req.status != SignatureStatus.PENDING:
            raise SignatureExecutionError(f"Cannot execute signature request in status {req.status}.")
        if signatory_id and req.signatory_id != signatory_id:
            raise SignatureExecutionError("Signatory ID mismatch.")

        executed_at = datetime.now(timezone.utc)
        req.status = SignatureStatus.EXECUTED
        req.signature_digest = signature_digest
        req.executed_at = executed_at

        return SignatureExecutionResult(
            signature_request_id=req.id,
            contract_id=req.contract_id,
            signatory_id=req.signatory_id,
            status=req.status,
            signature_digest=signature_digest,
            executed_at=executed_at,
            notified_parties=[str(req.signatory_id)],
        )

    def reject_signature(
        self,
        signature_request_id: uuid.UUID,
        reason: Optional[str] = None,
    ) -> SignatureRequestDomain:
        req = self.get_signature_request(signature_request_id)
        if req.status != SignatureStatus.PENDING:
            raise SignatureExecutionError(f"Cannot reject signature request in status {req.status}.")
        req.status = SignatureStatus.REJECTED
        return req

    def verify_signature_digest(
        self,
        signature_request_id: uuid.UUID,
        signature_digest: str,
    ) -> bool:
        req = self.get_signature_request(signature_request_id)
        return req.status == SignatureStatus.EXECUTED and req.signature_digest == signature_digest


def test_signature_exception_hierarchy():
    """Verify exception hierarchy for signature port errors."""
    assert issubclass(SignatureRequestNotFoundError, SignaturePortError)
    assert issubclass(SignatureExecutionError, SignaturePortError)
    assert issubclass(InvalidSignatureDigestError, SignatureExecutionError)


def test_pending_signature_item_serialization():
    """Verify PendingSignatureItem DTO instantiation and serialization [FR-5]."""
    req_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    signatory_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    item = PendingSignatureItem(
        signature_request_id=req_id,
        contract_id=contract_id,
        signatory_id=signatory_id,
        status=SignatureStatus.PENDING,
        contract_title="Master Services Agreement",
        counterparty="Acme Corp",
        requested_at=now,
    )

    d = item.to_dict()
    assert d["signature_request_id"] == str(req_id)
    assert d["contract_id"] == str(contract_id)
    assert d["signatory_id"] == str(signatory_id)
    assert d["status"] == "PENDING"
    assert d["contract_title"] == "Master Services Agreement"
    assert d["counterparty"] == "Acme Corp"
    assert d["requested_at"] == now.isoformat()


def test_signature_execution_result_serialization():
    """Verify SignatureExecutionResult DTO instantiation and serialization [FR-6]."""
    req_id = uuid.uuid4()
    contract_id = uuid.uuid4()
    signatory_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    res = SignatureExecutionResult(
        signature_request_id=req_id,
        contract_id=contract_id,
        signatory_id=signatory_id,
        status=SignatureStatus.EXECUTED,
        signature_digest="sha256:abc123digest",
        executed_at=now,
        notified_parties=["user1@acme.com", "user2@provider.com"],
    )

    d = res.to_dict()
    assert d["signature_request_id"] == str(req_id)
    assert d["status"] == "EXECUTED"
    assert d["signature_digest"] == "sha256:abc123digest"
    assert d["executed_at"] == now.isoformat()
    assert len(d["notified_parties"]) == 2


def test_signature_port_adapter_workflow():
    """Verify full end-to-end signature workflow using SignaturePort abstract contract [FR-5, FR-6]."""
    adapter = DummySignatureAdapter()
    contract_id = uuid.uuid4()
    signatory_id = uuid.uuid4()

    # Create request
    req = adapter.create_signature_request(contract_id, signatory_id)
    assert req.status == SignatureStatus.PENDING

    # List pending signatures [FR-5]
    pending = adapter.list_pending_signatures(contract_id=contract_id)
    assert len(pending) == 1
    assert pending[0].id == req.id

    # Execute signature [FR-6]
    digest = "sha256:testsignaturedigest"
    result = adapter.execute_signature(req.id, digest, signatory_id=signatory_id)
    assert result.status == SignatureStatus.EXECUTED
    assert result.signature_digest == digest
    assert len(result.notified_parties) == 1

    # Verify executed signature
    assert adapter.verify_signature_digest(req.id, digest) is True

    # Confirm list pending signatures is now empty [FR-5]
    pending_after = adapter.list_pending_signatures(contract_id=contract_id)
    assert len(pending_after) == 0

    # Confirm error when re-executing
    with pytest.raises(SignatureExecutionError):
        adapter.execute_signature(req.id, digest)


def test_signature_port_rejection():
    """Verify rejection workflow in signature port adapter."""
    adapter = DummySignatureAdapter()
    contract_id = uuid.uuid4()
    signatory_id = uuid.uuid4()

    req = adapter.create_signature_request(contract_id, signatory_id)
    rejected = adapter.reject_signature(req.id, reason="Signatory declined terms.")
    assert rejected.status == SignatureStatus.REJECTED


def test_signature_port_not_found():
    """Verify exception raised when signature request is missing."""
    adapter = DummySignatureAdapter()
    missing_id = uuid.uuid4()

    with pytest.raises(SignatureRequestNotFoundError):
        adapter.get_signature_request(missing_id)
