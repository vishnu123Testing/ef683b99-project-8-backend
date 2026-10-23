"""Unit tests for contract domain entities, enums, dataclasses, and lifecycle event payloads.

Verifies domain model definitions in backend/src/services/contract/models.py [FR-12].
"""

from datetime import date as date_type, datetime, timezone
import uuid

import pytest

from src.services.contract.models import (
    ContractAmendedPayload,
    ContractAmendmentDomain,
    ContractCategory,
    ContractClauseDomain,
    ContractCreatedPayload,
    ContractDomain,
    ContractLifecycleEvent,
    ContractRenewedPayload,
    ContractSignedPayload,
    ContractSLADomain,
    ContractStatus,
    DocumentUploadedPayload,
    DocumentVersionDomain,
    LifecycleEventMetadata,
    LifecycleEventType,
    RenewalAlertDomain,
    RenewalThreshold,
    SignatureRequestDomain,
    SignatureStatus,
)


def test_contract_enums():
    """Verify enum values for categories, status, event types, and renewal thresholds."""
    assert ContractCategory.CONTRACT == "Contract"
    assert ContractCategory.SOW == "SOW"
    assert ContractCategory.NDA == "NDA"
    assert ContractCategory.EXHIBIT == "Exhibit"

    assert ContractStatus.DRAFT == "DRAFT"
    assert ContractStatus.PENDING_SIGNATURE == "PENDING_SIGNATURE"
    assert ContractStatus.ACTIVE == "ACTIVE"
    assert ContractStatus.AMENDED == "AMENDED"
    assert ContractStatus.TERMINATED == "TERMINATED"
    assert ContractStatus.EXPIRED == "EXPIRED"

    assert SignatureStatus.PENDING == "PENDING"
    assert SignatureStatus.EXECUTED == "EXECUTED"
    assert SignatureStatus.REJECTED == "REJECTED"

    assert LifecycleEventType.CREATION == "CREATION"
    assert LifecycleEventType.SIGNING == "SIGNING"
    assert LifecycleEventType.AMENDMENT == "AMENDMENT"
    assert LifecycleEventType.RENEWAL == "RENEWAL"
    assert LifecycleEventType.DOCUMENT_UPLOAD == "DOCUMENT_UPLOAD"

    assert RenewalThreshold.DAYS_90 == 90
    assert RenewalThreshold.DAYS_60 == 60
    assert RenewalThreshold.DAYS_30 == 30


def test_contract_domain_serialization():
    """Verify ContractDomain creation and dictionary serialization."""
    cid = uuid.uuid4()
    parent_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    contract = ContractDomain(
        id=cid,
        title="Master Service Agreement",
        category=ContractCategory.CONTRACT,
        status=ContractStatus.ACTIVE,
        counterparty="Tech Corp",
        start_on=date_type(2026, 1, 1),
        expires_on=date_type(2027, 1, 1),
        parent_contract_id=parent_id,
        created_at=now,
        updated_at=now,
    )

    data = contract.to_dict()
    assert data["id"] == str(cid)
    assert data["title"] == "Master Service Agreement"
    assert data["category"] == "Contract"
    assert data["status"] == "ACTIVE"
    assert data["counterparty"] == "Tech Corp"
    assert data["start_on"] == "2026-01-01"
    assert data["expires_on"] == "2027-01-01"
    assert data["parent_contract_id"] == str(parent_id)
    assert data["created_at"] == now.isoformat()


def test_clause_and_sla_domain_serialization():
    """Verify ContractClauseDomain and ContractSLADomain serialization."""
    cid = uuid.uuid4()
    clause_id = uuid.uuid4()
    sla_id = uuid.uuid4()

    clause = ContractClauseDomain(
        id=clause_id,
        contract_id=cid,
        clause_type="Indemnification",
        title="Standard Indemnity",
        body_text="Party A agrees to indemnification...",
        is_highlighted=True,
    )
    c_dict = clause.to_dict()
    assert c_dict["id"] == str(clause_id)
    assert c_dict["contract_id"] == str(cid)
    assert c_dict["clause_type"] == "Indemnification"
    assert c_dict["is_highlighted"] is True

    sla = ContractSLADomain(
        id=sla_id,
        contract_id=cid,
        metric_name="Uptime Guarantee",
        target_value="99.9%",
        penalty_terms="10% credit",
    )
    s_dict = sla.to_dict()
    assert s_dict["id"] == str(sla_id)
    assert s_dict["metric_name"] == "Uptime Guarantee"
    assert s_dict["penalty_terms"] == "10% credit"


def test_document_version_domain_serialization():
    """Verify DocumentVersionDomain serialization."""
    doc_id = uuid.uuid4()
    cid = uuid.uuid4()
    uid = uuid.uuid4()
    now = datetime.now(timezone.utc)

    doc = DocumentVersionDomain(
        id=doc_id,
        contract_id=cid,
        category_type=ContractCategory.SOW,
        version_number=1,
        file_path="/storage/sow_v1.pdf",
        checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        uploaded_by_id=uid,
        uploaded_at=now,
    )
    d_dict = doc.to_dict()
    assert d_dict["id"] == str(doc_id)
    assert d_dict["category_type"] == "SOW"
    assert d_dict["version_number"] == 1
    assert d_dict["file_path"] == "/storage/sow_v1.pdf"
    assert d_dict["uploaded_by_id"] == str(uid)


def test_amendment_and_signature_domain_serialization():
    """Verify ContractAmendmentDomain, SignatureRequestDomain, and RenewalAlertDomain serialization."""
    cid = uuid.uuid4()
    author_id = uuid.uuid4()
    signatory_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    amendment = ContractAmendmentDomain(
        id=uuid.uuid4(),
        contract_id=cid,
        sequence_number=1,
        summary="Updated pricing terms",
        author_id=author_id,
        created_at=now,
    )
    a_dict = amendment.to_dict()
    assert a_dict["sequence_number"] == 1
    assert a_dict["author_id"] == str(author_id)

    sig = SignatureRequestDomain(
        id=uuid.uuid4(),
        contract_id=cid,
        signatory_id=signatory_id,
        status=SignatureStatus.EXECUTED,
        signature_digest="sha256:abc123digest",
        executed_at=now,
    )
    sig_dict = sig.to_dict()
    assert sig_dict["status"] == "EXECUTED"
    assert sig_dict["signature_digest"] == "sha256:abc123digest"

    alert = RenewalAlertDomain(
        id=uuid.uuid4(),
        contract_id=cid,
        threshold_days=RenewalThreshold.DAYS_90,
        dispatched_at=now,
    )
    alert_dict = alert.to_dict()
    assert alert_dict["threshold_days"] == 90


def test_lifecycle_event_creation_payload_and_event():
    """Verify structured lifecycle event payloads and ContractLifecycleEvent creation [FR-12]."""
    cid = uuid.uuid4()
    actor_id = uuid.uuid4()
    tenant_id = uuid.uuid4()
    event_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    creation_payload = ContractCreatedPayload(
        contract_id=cid,
        title="Master Agreement",
        category="Contract",
        counterparty="Acme Corp",
        start_on=date_type(2026, 1, 1),
        expires_on=date_type(2027, 1, 1),
    )

    metadata = LifecycleEventMetadata(
        actor_id=actor_id,
        actor_role="LegalAdmin",
        tenant_id=tenant_id,
        client_ip="192.168.1.1",
        correlation_id="corr-123",
    )

    event = ContractLifecycleEvent(
        event_id=event_id,
        event_type=LifecycleEventType.CREATION,
        contract_id=cid,
        occurred_at=now,
        metadata=metadata,
        payload=creation_payload,
    )

    e_dict = event.to_dict()
    assert e_dict["event_id"] == str(event_id)
    assert e_dict["event_type"] == "CREATION"
    assert e_dict["contract_id"] == str(cid)
    assert e_dict["metadata"]["actor_id"] == str(actor_id)
    assert e_dict["metadata"]["actor_role"] == "LegalAdmin"
    assert e_dict["payload"]["title"] == "Master Agreement"
    assert e_dict["payload"]["start_on"] == "2026-01-01"


def test_lifecycle_event_signing_amendment_renewal_payloads():
    """Verify event payloads for signing, amendment, renewal, and document upload [FR-12]."""
    cid = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Signing
    sig_payload = ContractSignedPayload(
        contract_id=cid,
        signature_request_id=uuid.uuid4(),
        signatory_id=uuid.uuid4(),
        signature_digest="digest-xyz",
        executed_at=now,
        resulting_status=ContractStatus.ACTIVE,
    )
    sig_dict = sig_payload.to_dict()
    assert sig_dict["resulting_status"] == "ACTIVE"
    assert sig_dict["signature_digest"] == "digest-xyz"

    # Amendment
    amend_payload = ContractAmendedPayload(
        contract_id=cid,
        amendment_id=uuid.uuid4(),
        sequence_number=2,
        summary="Scope expansion",
        author_id=uuid.uuid4(),
    )
    amend_dict = amend_payload.to_dict()
    assert amend_dict["sequence_number"] == 2
    assert amend_dict["summary"] == "Scope expansion"

    # Renewal
    renew_payload = ContractRenewedPayload(
        contract_id=cid,
        threshold_days=60,
        dispatched_at=now,
        expires_on=date_type(2027, 6, 30),
    )
    renew_dict = renew_payload.to_dict()
    assert renew_dict["threshold_days"] == 60
    assert renew_dict["expires_on"] == "2027-06-30"

    # Document upload
    upload_payload = DocumentUploadedPayload(
        contract_id=cid,
        document_version_id=uuid.uuid4(),
        category_type="Exhibit",
        version_number=3,
        checksum="hash123",
    )
    upload_dict = upload_payload.to_dict()
    assert upload_dict["category_type"] == "Exhibit"
    assert upload_dict["version_number"] == 3
