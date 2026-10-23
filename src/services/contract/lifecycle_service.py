"""Core domain service for contract lifecycle management [FR-1, FR-4, FR-11, FR-12, NFR-1].

Provides business logic for contract creation, retrieving contract details with
highlighted clauses and active SLAs [FR-1], sequential version-controlled document storage [FR-4],
search filtering across categories, statuses, expiration dates, and counterparties [FR-11],
and context-correlated structured event emission [FR-12, NFR-1].
"""

from datetime import date as date_type, datetime, timezone
import json
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from sqlalchemy import or_
from sqlalchemy.orm import Session

from src.lib.logging import get_correlation_id, mask_sensitive_data
from src.models.contract import (
    Contract,
    ContractAmendment,
    ContractClause,
    ContractSLA,
    DocumentVersion,
    RenewalAlert,
    SignatureRequest,
)
from src.services.contract.models import (
    ContractCategory,
    ContractCreatedPayload,
    ContractDomain,
    ContractLifecycleEvent,
    ContractStatus,
    DocumentUploadedPayload,
    LifecycleEventMetadata,
    LifecycleEventType,
)
from src.services.contract.storage_port import StoragePort

logger = logging.getLogger(__name__)


class LifecycleServiceError(Exception):
    """Base exception for contract lifecycle service operations."""

    pass


class ContractNotFoundError(LifecycleServiceError):
    """Raised when a requested contract record is not found."""

    pass


class InvalidContractStateError(LifecycleServiceError):
    """Raised when an operation is invalid for a contract's current lifecycle state."""

    pass


class DocumentVersionNotFoundError(LifecycleServiceError):
    """Raised when a requested document version record or file is not found."""

    pass


class LifecycleService:
    """Core domain service for managing contract lifecycles and associated artifacts."""

    def __init__(
        self,
        db: Session,
        storage_port: Optional[StoragePort] = None,
    ) -> None:
        """Initialize LifecycleService with database session and optional storage port."""
        self.db = db
        self._storage_port = storage_port

    @property
    def storage_port(self) -> StoragePort:
        """Lazy-load default StorageConnector if explicit StoragePort was not supplied."""
        if self._storage_port is None:
            from src.connectors.storage_connector import StorageConnector

            self._storage_port = StorageConnector()
        return self._storage_port

    def emit_lifecycle_event(
        self,
        event_type: LifecycleEventType,
        contract_id: uuid.UUID,
        payload: Any,
        metadata: Optional[LifecycleEventMetadata] = None,
    ) -> ContractLifecycleEvent:
        """Construct and log a structured contract lifecycle audit event [FR-12, NFR-1]."""
        if metadata is None:
            metadata = LifecycleEventMetadata()

        # Inject context correlation_id if not present
        if not metadata.correlation_id:
            metadata.correlation_id = get_correlation_id()

        event = ContractLifecycleEvent(
            event_type=event_type,
            contract_id=contract_id,
            payload=payload,
            metadata=metadata,
        )

        event_dict = mask_sensitive_data(event.to_dict())
        logger.info(
            "Contract Lifecycle Event emitted: %s",
            json.dumps(event_dict),
            extra={
                "event_type": str(
                    event_type.value if hasattr(event_type, "value") else event_type
                ),
                "contract_id": str(contract_id),
                "correlation_id": metadata.correlation_id,
            },
        )
        return event

    def create_contract(
        self,
        title: str,
        category: Union[ContractCategory, str],
        counterparty: str,
        start_on: Optional[date_type] = None,
        expires_on: Optional[date_type] = None,
        parent_contract_id: Optional[uuid.UUID] = None,
        status: Union[ContractStatus, str] = ContractStatus.DRAFT,
        clauses: Optional[List[Dict[str, Any]]] = None,
        slas: Optional[List[Dict[str, Any]]] = None,
        metadata: Optional[LifecycleEventMetadata] = None,
    ) -> Contract:
        """Create a new contract record with optional clauses and SLAs [FR-1, FR-12]."""
        category_str = (
            category.value if isinstance(category, ContractCategory) else str(category)
        )
        status_str = (
            status.value if isinstance(status, ContractStatus) else str(status)
        )

        if parent_contract_id:
            parent_exists = (
                self.db.query(Contract)
                .filter(Contract.id == parent_contract_id)
                .first()
            )
            if not parent_exists:
                raise ContractNotFoundError(
                    f"Parent contract with ID '{parent_contract_id}' not found."
                )

        contract = Contract(
            title=title,
            category=category_str,
            status=status_str,
            counterparty=counterparty,
            start_on=start_on,
            expires_on=expires_on,
            parent_contract_id=parent_contract_id,
        )
        self.db.add(contract)
        self.db.flush()  # Assigns contract.id

        if clauses:
            for clause_data in clauses:
                clause = ContractClause(
                    contract_id=contract.id,
                    clause_type=clause_data.get("clause_type", "General"),
                    title=clause_data.get("title", "Untitled Clause"),
                    body_text=clause_data.get("body_text", ""),
                    is_highlighted=bool(clause_data.get("is_highlighted", False)),
                )
                self.db.add(clause)

        if slas:
            for sla_data in slas:
                sla = ContractSLA(
                    contract_id=contract.id,
                    metric_name=sla_data.get("metric_name", "Target SLA"),
                    target_value=str(sla_data.get("target_value", "100%")),
                    penalty_terms=sla_data.get("penalty_terms"),
                )
                self.db.add(sla)

        self.db.commit()
        self.db.refresh(contract)

        # Emit structured audit log
        creation_payload = ContractCreatedPayload(
            contract_id=contract.id,
            title=contract.title,
            category=contract.category,
            counterparty=contract.counterparty,
            start_on=contract.start_on,
            expires_on=contract.expires_on,
            parent_contract_id=contract.parent_contract_id,
        )
        self.emit_lifecycle_event(
            event_type=LifecycleEventType.CREATION,
            contract_id=contract.id,
            payload=creation_payload,
            metadata=metadata,
        )

        return contract

    def get_contract(self, contract_id: uuid.UUID) -> Contract:
        """Retrieve contract ORM entity by ID or raise ContractNotFoundError."""
        contract = (
            self.db.query(Contract).filter(Contract.id == contract_id).first()
        )
        if not contract:
            raise ContractNotFoundError(f"Contract with ID '{contract_id}' not found.")
        return contract

    def get_contract_details(self, contract_id: uuid.UUID) -> Dict[str, Any]:
        """Retrieve contract along with highlighted clauses, key terms, active SLAs, and document versions [FR-1]."""
        contract = self.get_contract(contract_id)

        clauses = [clause.to_dict() for clause in contract.clauses]
        slas = [sla.to_dict() for sla in contract.slas]
        evaluated_slas = self.evaluate_slas(contract_id)
        document_versions = [doc.to_dict() for doc in contract.document_versions]
        amendments = [amendment.to_dict() for amendment in contract.amendments]

        details = contract.to_dict()
        details["clauses"] = clauses
        details["highlighted_clauses"] = [c for c in clauses if c.get("is_highlighted")]
        details["slas"] = slas
        details["evaluated_slas"] = evaluated_slas
        details["document_versions"] = document_versions
        details["amendments"] = amendments
        return details

    def add_clause(
        self,
        contract_id: uuid.UUID,
        clause_type: str,
        title: str,
        body_text: str,
        is_highlighted: bool = False,
    ) -> ContractClause:
        """Add a clause to an existing contract [FR-1]."""
        self.get_contract(contract_id)
        clause = ContractClause(
            contract_id=contract_id,
            clause_type=clause_type,
            title=title,
            body_text=body_text,
            is_highlighted=is_highlighted,
        )
        self.db.add(clause)
        self.db.commit()
        self.db.refresh(clause)
        return clause

    def add_sla(
        self,
        contract_id: uuid.UUID,
        metric_name: str,
        target_value: str,
        penalty_terms: Optional[str] = None,
    ) -> ContractSLA:
        """Add an SLA metric specification to an existing contract [FR-1]."""
        self.get_contract(contract_id)
        sla = ContractSLA(
            contract_id=contract_id,
            metric_name=metric_name,
            target_value=target_value,
            penalty_terms=penalty_terms,
        )
        self.db.add(sla)
        self.db.commit()
        self.db.refresh(sla)
        return sla

    def evaluate_slas(
        self,
        contract_id: uuid.UUID,
        performance_data: Optional[Dict[str, Any]] = None,
    ) -> List[Dict[str, Any]]:
        """Evaluate contract SLAs against current or provided operational performance metrics [FR-1]."""
        contract = self.get_contract(contract_id)
        evaluations: List[Dict[str, Any]] = []

        if performance_data is None:
            performance_data = {}

        for sla in contract.slas:
            metric_key = sla.metric_name
            actual_val = performance_data.get(metric_key)

            status = "ACTIVE"
            penalty_applicable = False

            if actual_val is not None:
                try:
                    target_str = str(sla.target_value).replace("%", "").strip()
                    target_num = float(target_str)
                    actual_num = float(actual_val)

                    if actual_num >= target_num:
                        status = "COMPLIANT"
                        penalty_applicable = False
                    else:
                        status = "BREACHED"
                        penalty_applicable = True
                except (ValueError, TypeError):
                    status = "ACTIVE"

            evaluations.append(
                {
                    "sla_id": str(sla.id),
                    "contract_id": str(contract_id),
                    "metric_name": sla.metric_name,
                    "target_value": sla.target_value,
                    "actual_value": actual_val,
                    "status": status,
                    "penalty_applicable": penalty_applicable,
                    "penalty_terms": sla.penalty_terms,
                }
            )

        return evaluations

    def upload_document_version(
        self,
        contract_id: uuid.UUID,
        category_type: Union[ContractCategory, str],
        file_name: str,
        content: bytes,
        uploaded_by_id: Optional[uuid.UUID] = None,
        metadata: Optional[LifecycleEventMetadata] = None,
    ) -> DocumentVersion:
        """Upload a new revised document version for a contract, incrementing version numbers [FR-4, FR-12]."""
        contract = self.get_contract(contract_id)
        cat_type_str = (
            category_type.value
            if isinstance(category_type, ContractCategory)
            else str(category_type)
        )

        # Compute next sequential version_number for this contract and category
        existing_versions = (
            self.db.query(DocumentVersion)
            .filter(
                DocumentVersion.contract_id == contract_id,
                DocumentVersion.category_type == cat_type_str,
            )
            .all()
        )
        if existing_versions:
            max_version = max(v.version_number for v in existing_versions)
            next_version = max_version + 1
        else:
            next_version = 1

        # Persist document file via StoragePort
        storage_result = self.storage_port.save_document(
            contract_id=contract_id,
            category=cat_type_str,
            version_number=next_version,
            file_name=file_name,
            content=content,
        )

        # Create DocumentVersion entity in DB
        doc_version = DocumentVersion(
            contract_id=contract_id,
            category_type=cat_type_str,
            version_number=next_version,
            file_path=storage_result.file_path,
            checksum=storage_result.checksum,
            uploaded_by_id=uploaded_by_id,
        )
        self.db.add(doc_version)
        self.db.commit()
        self.db.refresh(doc_version)

        # Emit audit event log [FR-12]
        upload_payload = DocumentUploadedPayload(
            contract_id=contract_id,
            document_version_id=doc_version.id,
            category_type=cat_type_str,
            version_number=next_version,
            checksum=doc_version.checksum,
            uploaded_by_id=uploaded_by_id,
        )
        self.emit_lifecycle_event(
            event_type=LifecycleEventType.DOCUMENT_UPLOAD,
            contract_id=contract_id,
            payload=upload_payload,
            metadata=metadata,
        )

        return doc_version

    def get_document_versions(
        self,
        contract_id: uuid.UUID,
        category_type: Optional[Union[ContractCategory, str]] = None,
    ) -> List[DocumentVersion]:
        """Retrieve all document versions for a contract, preserving historical versions [FR-4]."""
        self.get_contract(contract_id)
        query = self.db.query(DocumentVersion).filter(
            DocumentVersion.contract_id == contract_id
        )

        if category_type is not None:
            cat_str = (
                category_type.value
                if isinstance(category_type, ContractCategory)
                else str(category_type)
            )
            query = query.filter(DocumentVersion.category_type == cat_str)

        return query.order_by(DocumentVersion.version_number.asc()).all()

    def get_document_version(self, version_id: uuid.UUID) -> DocumentVersion:
        """Retrieve a specific document version record by version ID [FR-4]."""
        doc_version = (
            self.db.query(DocumentVersion)
            .filter(DocumentVersion.id == version_id)
            .first()
        )
        if not doc_version:
            raise DocumentVersionNotFoundError(
                f"Document version with ID '{version_id}' not found."
            )
        return doc_version

    def download_document(self, version_id: uuid.UUID) -> Tuple[bytes, DocumentVersion]:
        """Download binary payload of a document version via StoragePort [FR-4]."""
        doc_version = self.get_document_version(version_id)
        file_bytes = self.storage_port.get_document(doc_version.file_path)
        return file_bytes, doc_version

    def search_contracts(
        self,
        category: Optional[Union[ContractCategory, str]] = None,
        status: Optional[Union[ContractStatus, str]] = None,
        counterparty: Optional[str] = None,
        expires_before: Optional[date_type] = None,
        expires_after: Optional[date_type] = None,
        parent_contract_id: Optional[uuid.UUID] = None,
        search_query: Optional[str] = None,
        page: int = 1,
        limit: int = 20,
        offset: Optional[int] = None,
    ) -> Tuple[List[Contract], int]:
        """Filter and paginate contract repository by category, status, expiration, and counterparty [FR-11]."""
        query = self.db.query(Contract)

        if category is not None:
            cat_str = (
                category.value
                if isinstance(category, ContractCategory)
                else str(category)
            )
            query = query.filter(Contract.category == cat_str)

        if status is not None:
            status_str = (
                status.value
                if isinstance(status, ContractStatus)
                else str(status)
            )
            query = query.filter(Contract.status == status_str)

        if counterparty:
            query = query.filter(Contract.counterparty.ilike(f"%{counterparty}%"))

        if expires_before is not None:
            query = query.filter(Contract.expires_on <= expires_before)

        if expires_after is not None:
            query = query.filter(Contract.expires_on >= expires_after)

        if parent_contract_id is not None:
            query = query.filter(Contract.parent_contract_id == parent_contract_id)

        if search_query:
            query = query.filter(
                or_(
                    Contract.title.ilike(f"%{search_query}%"),
                    Contract.counterparty.ilike(f"%{search_query}%"),
                )
            )

        total_count = query.count()

        calc_offset = offset if offset is not None else (max(1, page) - 1) * limit
        results = (
            query.order_by(Contract.created_at.desc())
            .offset(calc_offset)
            .limit(limit)
            .all()
        )

        return results, total_count

    def update_contract_status(
        self,
        contract_id: uuid.UUID,
        new_status: Union[ContractStatus, str],
        metadata: Optional[LifecycleEventMetadata] = None,
    ) -> Contract:
        """Update lifecycle status for a contract and emit status change event [FR-12]."""
        contract = self.get_contract(contract_id)
        status_str = (
            new_status.value
            if isinstance(new_status, ContractStatus)
            else str(new_status)
        )

        old_status = contract.status
        contract.status = status_str
        self.db.commit()
        self.db.refresh(contract)

        self.emit_lifecycle_event(
            event_type=LifecycleEventType.STATUS_CHANGE,
            contract_id=contract_id,
            payload={
                "contract_id": str(contract_id),
                "previous_status": old_status,
                "new_status": status_str,
            },
            metadata=metadata,
        )

        return contract

    def terminate_contract(
        self,
        contract_id: uuid.UUID,
        reason: Optional[str] = None,
        metadata: Optional[LifecycleEventMetadata] = None,
    ) -> Contract:
        """Terminate a contract and emit termination audit event [FR-12]."""
        contract = self.get_contract(contract_id)
        contract.status = ContractStatus.TERMINATED.value
        self.db.commit()
        self.db.refresh(contract)

        self.emit_lifecycle_event(
            event_type=LifecycleEventType.TERMINATION,
            contract_id=contract_id,
            payload={
                "contract_id": str(contract_id),
                "resulting_status": ContractStatus.TERMINATED.value,
                "reason": reason,
            },
            metadata=metadata,
        )

        return contract

    def delete_contract(
        self,
        contract_id: uuid.UUID,
        metadata: Optional[LifecycleEventMetadata] = None,
    ) -> None:
        """Delete a contract record and clean up associated resources [FR-8, NFR-4].

        If the contract has immutable amendments, attempting deletion is rejected to protect
        the audit trail [FR-8, NFR-4].
        """
        contract = self.get_contract(contract_id)
        if contract.amendments:
            from src.services.contract.amendment_service import AmendmentMutationForbiddenError

            raise AmendmentMutationForbiddenError(
                f"Deleting contract '{contract_id}' with existing amendment audit trail records is strictly forbidden [FR-8, NFR-4]."
            )

        self.db.delete(contract)
        self.db.commit()
