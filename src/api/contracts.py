"""FastAPI routes for contract lifecycle management, e-signatures, and amendments [FR-1, FR-2, FR-3, FR-5, FR-6, FR-7, FR-8, FR-10, FR-11, NFR-1, NFR-2]."""

from datetime import date as date_type, datetime
from typing import Any, Dict, List, Optional
import uuid

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Response,
    UploadFile,
    status,
)
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps import get_current_user
from src.lib.config import settings
from src.models.database import get_db
from src.models.user import User
from src.services.contract.amendment_service import (
    AmendmentMutationForbiddenError,
    AmendmentNotFoundError,
    AmendmentService,
)
from src.services.contract.lifecycle_service import (
    ContractNotFoundError,
    DocumentVersionNotFoundError,
    InvalidContractStateError,
    LifecycleService,
)
from src.services.contract.models import ContractCategory, ContractStatus
from src.services.contract.signature_service import (
    InvalidSignatureDigestError,
    SignatureExecutionError,
    SignatureRequestNotFoundError,
    SignatureService,
)

contracts_router = APIRouter(prefix="/contracts", tags=["Contracts"])
signatures_router = APIRouter(prefix="/signatures", tags=["Signatures"])


# Request and Response Pydantic Schemas [NFR-2]


class ClauseCreateSchema(BaseModel):
    """Schema for clause creation payload."""

    clause_type: str = Field(default="General", min_length=1, max_length=100)
    title: str = Field(default="Untitled Clause", min_length=1, max_length=255)
    body_text: str = Field(default="")
    is_highlighted: bool = Field(default=False)


class SLACreateSchema(BaseModel):
    """Schema for SLA metric specification payload."""

    metric_name: str = Field(default="Target SLA", min_length=1, max_length=255)
    target_value: str = Field(default="100%", min_length=1, max_length=100)
    penalty_terms: Optional[str] = Field(default=None)


class ContractCreateRequest(BaseModel):
    """Schema for creating a new contract record [FR-3, FR-10]."""

    title: str = Field(..., min_length=1, max_length=255)
    category: ContractCategory
    counterparty: str = Field(..., min_length=1, max_length=255)
    start_on: Optional[date_type] = None
    expires_on: Optional[date_type] = None
    parent_contract_id: Optional[uuid.UUID] = None
    clauses: Optional[List[ClauseCreateSchema]] = None
    slas: Optional[List[SLACreateSchema]] = None


class ClauseResponse(BaseModel):
    """Schema for clause response representation."""

    id: uuid.UUID
    contract_id: uuid.UUID
    clause_type: str
    title: str
    body_text: str
    is_highlighted: bool


class SLAResponse(BaseModel):
    """Schema for SLA metric response representation."""

    id: uuid.UUID
    contract_id: uuid.UUID
    metric_name: str
    target_value: str
    penalty_terms: Optional[str] = None


class EvaluatedSLAResponse(BaseModel):
    """Schema for evaluated SLA performance metrics."""

    sla_id: str
    contract_id: str
    metric_name: str
    target_value: str
    actual_value: Optional[Any] = None
    status: str
    penalty_applicable: bool
    penalty_terms: Optional[str] = None


class DocumentVersionResponse(BaseModel):
    """Schema for document version response representation."""

    id: uuid.UUID
    contract_id: uuid.UUID
    category_type: str
    version_number: int
    file_path: str
    checksum: str
    uploaded_by_id: Optional[uuid.UUID] = None
    uploaded_at: datetime


class AmendmentDetailResponse(BaseModel):
    """Schema for contract amendment response representation."""

    id: uuid.UUID
    contract_id: uuid.UUID
    sequence_number: int
    summary: str
    author_id: Optional[uuid.UUID] = None
    created_at: datetime


class ContractSummaryResponse(BaseModel):
    """Schema for contract summary list item representation."""

    id: uuid.UUID
    title: str
    category: str
    status: str
    counterparty: str
    start_on: Optional[date_type] = None
    expires_on: Optional[date_type] = None
    parent_contract_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime


class ContractListResponse(BaseModel):
    """Schema for paginated contract list response [FR-11]."""

    items: List[ContractSummaryResponse]
    total_count: int
    page: int
    limit: int


class ContractDetailResponse(BaseModel):
    """Schema for detailed contract view including clauses, SLAs, versions, and amendments [FR-1]."""

    id: uuid.UUID
    title: str
    category: str
    status: str
    counterparty: str
    start_on: Optional[date_type] = None
    expires_on: Optional[date_type] = None
    parent_contract_id: Optional[uuid.UUID] = None
    created_at: datetime
    updated_at: datetime
    clauses: List[ClauseResponse] = []
    highlighted_clauses: List[ClauseResponse] = []
    slas: List[SLAResponse] = []
    evaluated_slas: List[EvaluatedSLAResponse] = []
    document_versions: List[DocumentVersionResponse] = []
    amendments: List[AmendmentDetailResponse] = []


class PendingSignatureItemResponse(BaseModel):
    """Schema for pending signature item representation [FR-5]."""

    signature_request_id: uuid.UUID
    contract_id: uuid.UUID
    contract_title: Optional[str] = None
    counterparty: Optional[str] = None
    signatory_id: uuid.UUID
    status: str
    created_at: Optional[datetime] = None


class SignatureListResponse(BaseModel):
    """Schema for pending signature list response [FR-5]."""

    items: List[PendingSignatureItemResponse]
    total_count: int


class SignatureExecuteRequest(BaseModel):
    """Schema for signature execution request payload [FR-6]."""

    signature_digest: str = Field(..., min_length=1, max_length=512)


class SignatureExecuteResponse(BaseModel):
    """Schema for signature execution response payload [FR-6]."""

    signature_request_id: uuid.UUID
    contract_id: uuid.UUID
    status: str
    executed_at: datetime
    all_signatures_completed: bool
    resulting_contract_status: str


class AmendmentCreateRequest(BaseModel):
    """Schema for creating a contract amendment record [FR-7]."""

    summary: str = Field(..., min_length=1)


class AmendmentListResponse(BaseModel):
    """Schema for contract amendment list response [FR-7]."""

    items: List[AmendmentDetailResponse]
    total_count: int


# Contract Routes (/api/v1/contracts)


@contracts_router.get("", response_model=ContractListResponse, status_code=status.HTTP_200_OK)
def list_contracts(
    category: Optional[ContractCategory] = Query(None, description="Filter by contract category"),
    status_filter: Optional[ContractStatus] = Query(None, alias="status", description="Filter by contract status"),
    expires_before: Optional[date_type] = Query(None, description="Filter contracts expiring on or before date"),
    counterparty: Optional[str] = Query(None, description="Filter by counterparty name search"),
    page: int = Query(1, ge=1, description="Page number"),
    limit: int = Query(50, ge=1, le=100, description="Page items limit"),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ContractListResponse:
    """Filter and paginate repository contracts by category, status, expiration date, and counterparty [FR-11]."""
    lifecycle_service = LifecycleService(db=db)
    results, total_count = lifecycle_service.search_contracts(
        category=category,
        status=status_filter,
        expires_before=expires_before,
        counterparty=counterparty,
        page=page,
        limit=limit,
    )
    items = [
        ContractSummaryResponse(
            id=c.id,
            title=c.title,
            category=c.category,
            status=c.status,
            counterparty=c.counterparty,
            start_on=c.start_on,
            expires_on=c.expires_on,
            parent_contract_id=c.parent_contract_id,
            created_at=c.created_at,
            updated_at=c.updated_at,
        )
        for c in results
    ]
    return ContractListResponse(
        items=items,
        total_count=total_count,
        page=page,
        limit=limit,
    )


@contracts_router.post("", response_model=ContractDetailResponse, status_code=status.HTTP_201_CREATED)
def create_contract(
    payload: ContractCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ContractDetailResponse:
    """Create a new contract record with optional clauses and SLAs [FR-3, FR-10]."""
    lifecycle_service = LifecycleService(db=db)
    clauses_data = [c.model_dump() for c in payload.clauses] if payload.clauses else None
    slas_data = [s.model_dump() for s in payload.slas] if payload.slas else None

    try:
        contract = lifecycle_service.create_contract(
            title=payload.title,
            category=payload.category,
            counterparty=payload.counterparty,
            start_on=payload.start_on,
            expires_on=payload.expires_on,
            parent_contract_id=payload.parent_contract_id,
            clauses=clauses_data,
            slas=slas_data,
        )
        details = lifecycle_service.get_contract_details(contract.id)
        return ContractDetailResponse(**details)
    except ContractNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@contracts_router.get("/{id}", response_model=ContractDetailResponse, status_code=status.HTTP_200_OK)
def get_contract_detail(
    id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ContractDetailResponse:
    """Retrieve contract detail including clauses, SLAs, document versions, and amendments [FR-1]."""
    lifecycle_service = LifecycleService(db=db)
    try:
        details = lifecycle_service.get_contract_details(contract_id=id)
        return ContractDetailResponse(**details)
    except ContractNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@contracts_router.post("/{id}/documents", response_model=DocumentVersionResponse, status_code=status.HTTP_201_CREATED)
async def upload_document_version(
    id: uuid.UUID,
    category_type: ContractCategory = Form(...),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentVersionResponse:
    """Upload a new revised document version for a contract [FR-3, FR-4, FR-10]."""
    content = await file.read()
    if len(content) > settings.max_upload_size_bytes:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"File size exceeds maximum allowed threshold of {settings.max_upload_size_bytes} bytes",
        )

    lifecycle_service = LifecycleService(db=db)
    try:
        file_name = file.filename or "document.pdf"
        doc_version = lifecycle_service.upload_document_version(
            contract_id=id,
            category_type=category_type,
            file_name=file_name,
            content=content,
            uploaded_by_id=current_user.user_id,
        )
        return DocumentVersionResponse(
            id=doc_version.id,
            contract_id=doc_version.contract_id,
            category_type=doc_version.category_type,
            version_number=doc_version.version_number,
            file_path=doc_version.file_path,
            checksum=doc_version.checksum,
            uploaded_by_id=doc_version.uploaded_by_id,
            uploaded_at=doc_version.uploaded_at,
        )
    except ContractNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@contracts_router.get("/{id}/documents/{version_id}", status_code=status.HTTP_200_OK)
def download_document_version(
    id: uuid.UUID,
    version_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Download binary octet file stream for a specific document version [FR-4]."""
    lifecycle_service = LifecycleService(db=db)
    try:
        file_bytes, doc_version = lifecycle_service.download_document(version_id=version_id)
        if doc_version.contract_id != id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Document version '{version_id}' does not belong to contract '{id}'.",
            )
        filename = doc_version.file_path.split("/")[-1]
        return Response(
            content=file_bytes,
            media_type="application/octet-stream",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
    except (DocumentVersionNotFoundError, ContractNotFoundError) as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@contracts_router.post("/{id}/amendments", response_model=AmendmentDetailResponse, status_code=status.HTTP_201_CREATED)
def create_contract_amendment(
    id: uuid.UUID,
    payload: AmendmentCreateRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AmendmentDetailResponse:
    """Record a time-stamped contract amendment in the append-only audit trail [FR-7]."""
    amendment_service = AmendmentService(db=db)
    try:
        amendment = amendment_service.record_amendment(
            contract_id=id,
            summary=payload.summary,
            author_id=current_user.user_id,
        )
        return AmendmentDetailResponse(
            id=amendment.id,
            contract_id=amendment.contract_id,
            sequence_number=amendment.sequence_number,
            summary=amendment.summary,
            author_id=amendment.author_id,
            created_at=amendment.created_at,
        )
    except ContractNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except InvalidContractStateError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@contracts_router.get("/{id}/amendments", response_model=AmendmentListResponse, status_code=status.HTTP_200_OK)
def list_contract_amendments(
    id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AmendmentListResponse:
    """Retrieve immutable amendment audit trail entries for a contract [FR-7, FR-8]."""
    amendment_service = AmendmentService(db=db)
    try:
        amendments = amendment_service.list_amendments(contract_id=id)
        items = [
            AmendmentDetailResponse(
                id=a.id,
                contract_id=a.contract_id,
                sequence_number=a.sequence_number,
                summary=a.summary,
                author_id=a.author_id,
                created_at=a.created_at,
            )
            for a in amendments
        ]
        return AmendmentListResponse(items=items, total_count=len(items))
    except ContractNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )


@contracts_router.delete("/{id}", response_model=None, status_code=status.HTTP_204_NO_CONTENT)
def delete_contract(
    id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a contract record and clean up associated resources [FR-8, NFR-4]."""
    lifecycle_service = LifecycleService(db=db)
    try:
        lifecycle_service.delete_contract(contract_id=id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ContractNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except AmendmentMutationForbiddenError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(exc),
        )


# Signature Routes (/api/v1/signatures)


@signatures_router.get("/pending", response_model=SignatureListResponse, status_code=status.HTTP_200_OK)
def list_pending_signatures(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SignatureListResponse:
    """List contracts currently pending signature execution alongside signatory status [FR-5]."""
    signature_service = SignatureService(db=db)
    pending_items = signature_service.list_pending_signatures(
        signatory_id=current_user.user_id,
    )
    items = [
        PendingSignatureItemResponse(
            signature_request_id=p.signature_request_id,
            contract_id=p.contract_id,
            contract_title=p.contract_title or "Untitled Contract",
            counterparty=p.counterparty or "Unknown Counterparty",
            signatory_id=p.signatory_id,
            status=p.status.value if hasattr(p.status, "value") else str(p.status),
            created_at=p.requested_at,
        )
        for p in pending_items
    ]
    return SignatureListResponse(items=items, total_count=len(items))


@signatures_router.post("/{id}/execute", response_model=SignatureExecuteResponse, status_code=status.HTTP_200_OK)
def execute_signature(
    id: uuid.UUID,
    payload: SignatureExecuteRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> SignatureExecuteResponse:
    """Capture digital execution transition status and notify all parties [FR-6]."""
    signature_service = SignatureService(db=db)
    try:
        result = signature_service.execute_signature(
            signature_request_id=id,
            signature_digest=payload.signature_digest,
            signatory_id=current_user.user_id,
        )
        return SignatureExecuteResponse(
            signature_request_id=result.signature_request_id,
            contract_id=result.contract_id,
            status=result.status,
            executed_at=result.executed_at,
            all_signatures_completed=result.all_signatures_completed,
            resulting_contract_status=result.resulting_contract_status,
        )
    except SignatureRequestNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except (InvalidSignatureDigestError, InvalidContractStateError, SignatureExecutionError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
