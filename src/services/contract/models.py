"""Domain entities, dataclasses, enums, and event payloads for contract lifecycle management.

Defines pure domain representations, lifecycle states, signature workflow states,
renewal alert thresholds, and structured event payloads for lifecycle event audit logging [FR-12].
"""

from dataclasses import dataclass, field
from datetime import date as date_type, datetime, timezone
from enum import Enum, IntEnum
from typing import Any, Dict, List, Optional
import uuid


class ContractCategory(str, Enum):
    """Categorization types for contracts and associated documents."""

    CONTRACT = "Contract"
    SOW = "SOW"
    NDA = "NDA"
    EXHIBIT = "Exhibit"


class ContractStatus(str, Enum):
    """Lifecycle states for contract records."""

    DRAFT = "DRAFT"
    PENDING_SIGNATURE = "PENDING_SIGNATURE"
    ACTIVE = "ACTIVE"
    AMENDED = "AMENDED"
    TERMINATED = "TERMINATED"
    EXPIRED = "EXPIRED"


class SignatureStatus(str, Enum):
    """Execution status for e-signature requests."""

    PENDING = "PENDING"
    EXECUTED = "EXECUTED"
    REJECTED = "REJECTED"


class LifecycleEventType(str, Enum):
    """Supported contract lifecycle event types for structured audit metadata [FR-12]."""

    CREATION = "CREATION"
    SIGNING = "SIGNING"
    AMENDMENT = "AMENDMENT"
    RENEWAL = "RENEWAL"
    DOCUMENT_UPLOAD = "DOCUMENT_UPLOAD"
    STATUS_CHANGE = "STATUS_CHANGE"
    TERMINATION = "TERMINATION"
    EXPIRATION = "EXPIRATION"


class RenewalThreshold(IntEnum):
    """Supported threshold intervals in days prior to contract expiration."""

    DAYS_90 = 90
    DAYS_60 = 60
    DAYS_30 = 30


@dataclass
class ContractDomain:
    """Domain entity representing a contract or agreement record."""

    title: str
    category: ContractCategory
    status: ContractStatus
    counterparty: str
    id: Optional[uuid.UUID] = None
    start_on: Optional[date_type] = None
    expires_on: Optional[date_type] = None
    parent_contract_id: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert domain entity to dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "title": self.title,
            "category": self.category.value if isinstance(self.category, Enum) else self.category,
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
            "counterparty": self.counterparty,
            "start_on": self.start_on.isoformat() if self.start_on else None,
            "expires_on": self.expires_on.isoformat() if self.expires_on else None,
            "parent_contract_id": str(self.parent_contract_id) if self.parent_contract_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class ContractClauseDomain:
    """Domain entity representing a categorized clause within a contract."""

    contract_id: uuid.UUID
    clause_type: str
    title: str
    body_text: str
    id: Optional[uuid.UUID] = None
    is_highlighted: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """Convert clause domain entity to dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "contract_id": str(self.contract_id),
            "clause_type": self.clause_type,
            "title": self.title,
            "body_text": self.body_text,
            "is_highlighted": self.is_highlighted,
        }


@dataclass
class ContractSLADomain:
    """Domain entity representing a Service Level Agreement metric."""

    contract_id: uuid.UUID
    metric_name: str
    target_value: str
    id: Optional[uuid.UUID] = None
    penalty_terms: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert SLA domain entity to dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "contract_id": str(self.contract_id),
            "metric_name": self.metric_name,
            "target_value": self.target_value,
            "penalty_terms": self.penalty_terms,
        }


@dataclass
class DocumentVersionDomain:
    """Domain entity representing a version-controlled contract document file."""

    contract_id: uuid.UUID
    category_type: ContractCategory
    version_number: int
    file_path: str
    checksum: str
    id: Optional[uuid.UUID] = None
    uploaded_by_id: Optional[uuid.UUID] = None
    uploaded_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert document version entity to dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "contract_id": str(self.contract_id),
            "category_type": self.category_type.value if isinstance(self.category_type, Enum) else self.category_type,
            "version_number": self.version_number,
            "file_path": self.file_path,
            "checksum": self.checksum,
            "uploaded_by_id": str(self.uploaded_by_id) if self.uploaded_by_id else None,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
        }


@dataclass
class ContractAmendmentDomain:
    """Domain entity representing an immutable append-only contract amendment entry."""

    contract_id: uuid.UUID
    sequence_number: int
    summary: str
    id: Optional[uuid.UUID] = None
    author_id: Optional[uuid.UUID] = None
    created_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert amendment domain entity to dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "contract_id": str(self.contract_id),
            "sequence_number": self.sequence_number,
            "summary": self.summary,
            "author_id": str(self.author_id) if self.author_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class SignatureRequestDomain:
    """Domain entity representing an e-signature request."""

    contract_id: uuid.UUID
    signatory_id: uuid.UUID
    id: Optional[uuid.UUID] = None
    status: SignatureStatus = SignatureStatus.PENDING
    signature_digest: Optional[str] = None
    executed_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert signature request domain entity to dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "contract_id": str(self.contract_id),
            "signatory_id": str(self.signatory_id),
            "status": self.status.value if isinstance(self.status, Enum) else self.status,
            "signature_digest": self.signature_digest,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }


@dataclass
class RenewalAlertDomain:
    """Domain entity representing a dispatched contract renewal alert."""

    contract_id: uuid.UUID
    threshold_days: int
    id: Optional[uuid.UUID] = None
    dispatched_at: Optional[datetime] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert renewal alert domain entity to dictionary representation."""
        return {
            "id": str(self.id) if self.id else None,
            "contract_id": str(self.contract_id),
            "threshold_days": self.threshold_days,
            "dispatched_at": self.dispatched_at.isoformat() if self.dispatched_at else None,
        }


@dataclass
class LifecycleEventMetadata:
    """Contextual metadata captured during contract lifecycle event processing."""

    actor_id: Optional[uuid.UUID] = None
    actor_role: Optional[str] = None
    tenant_id: Optional[uuid.UUID] = None
    client_ip: Optional[str] = None
    correlation_id: Optional[str] = None
    additional_info: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert event metadata to dictionary representation."""
        return {
            "actor_id": str(self.actor_id) if self.actor_id else None,
            "actor_role": self.actor_role,
            "tenant_id": str(self.tenant_id) if self.tenant_id else None,
            "client_ip": self.client_ip,
            "correlation_id": self.correlation_id,
            "additional_info": self.additional_info,
        }


@dataclass
class ContractCreatedPayload:
    """Payload data for contract creation lifecycle event [FR-12]."""

    contract_id: uuid.UUID
    title: str
    category: str
    counterparty: str
    start_on: Optional[date_type] = None
    expires_on: Optional[date_type] = None
    parent_contract_id: Optional[uuid.UUID] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert creation payload to dictionary representation."""
        return {
            "contract_id": str(self.contract_id),
            "title": self.title,
            "category": self.category,
            "counterparty": self.counterparty,
            "start_on": self.start_on.isoformat() if self.start_on else None,
            "expires_on": self.expires_on.isoformat() if self.expires_on else None,
            "parent_contract_id": str(self.parent_contract_id) if self.parent_contract_id else None,
        }


@dataclass
class ContractSignedPayload:
    """Payload data for e-signature execution lifecycle event [FR-12]."""

    contract_id: uuid.UUID
    signature_request_id: uuid.UUID
    signatory_id: uuid.UUID
    signature_digest: str
    executed_at: datetime
    resulting_status: ContractStatus = ContractStatus.ACTIVE

    def to_dict(self) -> Dict[str, Any]:
        """Convert signature payload to dictionary representation."""
        return {
            "contract_id": str(self.contract_id),
            "signature_request_id": str(self.signature_request_id),
            "signatory_id": str(self.signatory_id),
            "signature_digest": self.signature_digest,
            "executed_at": self.executed_at.isoformat(),
            "resulting_status": self.resulting_status.value
            if isinstance(self.resulting_status, Enum)
            else self.resulting_status,
        }


@dataclass
class ContractAmendedPayload:
    """Payload data for amendment recording lifecycle event [FR-12]."""

    contract_id: uuid.UUID
    amendment_id: uuid.UUID
    sequence_number: int
    summary: str
    author_id: Optional[uuid.UUID] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert amendment payload to dictionary representation."""
        return {
            "contract_id": str(self.contract_id),
            "amendment_id": str(self.amendment_id),
            "sequence_number": self.sequence_number,
            "summary": self.summary,
            "author_id": str(self.author_id) if self.author_id else None,
        }


@dataclass
class ContractRenewedPayload:
    """Payload data for contract renewal notification lifecycle event [FR-12]."""

    contract_id: uuid.UUID
    threshold_days: int
    dispatched_at: datetime
    alert_id: Optional[uuid.UUID] = None
    expires_on: Optional[date_type] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert renewal payload to dictionary representation."""
        return {
            "contract_id": str(self.contract_id),
            "alert_id": str(self.alert_id) if self.alert_id else None,
            "threshold_days": self.threshold_days,
            "expires_on": self.expires_on.isoformat() if self.expires_on else None,
            "dispatched_at": self.dispatched_at.isoformat(),
        }


@dataclass
class DocumentUploadedPayload:
    """Payload data for document version upload lifecycle event [FR-12]."""

    contract_id: uuid.UUID
    document_version_id: uuid.UUID
    category_type: str
    version_number: int
    checksum: str
    uploaded_by_id: Optional[uuid.UUID] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert document upload payload to dictionary representation."""
        return {
            "contract_id": str(self.contract_id),
            "document_version_id": str(self.document_version_id),
            "category_type": self.category_type,
            "version_number": self.version_number,
            "checksum": self.checksum,
            "uploaded_by_id": str(self.uploaded_by_id) if self.uploaded_by_id else None,
        }


@dataclass
class ContractLifecycleEvent:
    """Structured container representing a contract lifecycle event for audit recording [FR-12, NFR-1]."""

    event_type: LifecycleEventType
    contract_id: uuid.UUID
    payload: Any
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    occurred_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: LifecycleEventMetadata = field(default_factory=LifecycleEventMetadata)

    def to_dict(self) -> Dict[str, Any]:
        """Convert contract lifecycle event into structured JSON-compatible dictionary."""
        payload_dict = (
            self.payload.to_dict()
            if hasattr(self.payload, "to_dict")
            else (self.payload if isinstance(self.payload, dict) else str(self.payload))
        )
        return {
            "event_id": str(self.event_id),
            "event_type": self.event_type.value if isinstance(self.event_type, Enum) else self.event_type,
            "contract_id": str(self.contract_id),
            "occurred_at": self.occurred_at.isoformat(),
            "metadata": self.metadata.to_dict(),
            "payload": payload_dict,
        }
