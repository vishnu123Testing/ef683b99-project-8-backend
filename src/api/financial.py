"""FastAPI REST endpoints for financial transparency dashboard: quotes, invoices, payment history, and AR aging [FR-1, FR-2, FR-3, FR-4, FR-9, FR-10]."""

from datetime import datetime
import logging
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.api.deps import RequireRole
from src.models.database import get_db
from src.services.access_control.rbac import UserAccessContext
from src.services.financial.metrics import (
    FinancialMetricsError,
    InvalidFilterError,
    filter_invoices,
    filter_payments,
    filter_quotes,
    get_ar_aging_metrics,
)

logger = logging.getLogger(__name__)

financial_router = APIRouter(prefix="/financial", tags=["Financial"])
router = financial_router


# Pydantic Schemas for Request & Response validation [FR-10]


class QuoteResponseSchema(BaseModel):
    """Response schema for a quote record [FR-1, FR-10]."""

    quote_id: uuid.UUID = Field(..., description="Unique UUID identifier for the quote")
    customer_id: uuid.UUID = Field(..., description="Customer UUID identifier")
    approval_state: str = Field(..., description="Approval state (Draft, Pending_Approval, Approved, Expired)")
    expiration_date_time: datetime = Field(..., description="Quote expiration date and time")
    total_amount: float = Field(..., description="Total monetary quote amount")
    created_at: datetime = Field(..., description="Creation timestamp")

    model_config = ConfigDict(from_attributes=True)


class InvoiceResponseSchema(BaseModel):
    """Response schema for an invoice record [FR-2, FR-10]."""

    invoice_id: uuid.UUID = Field(..., description="Unique UUID identifier for the invoice")
    quote_id: Optional[uuid.UUID] = Field(None, description="Optional linked quote UUID identifier")
    customer_id: uuid.UUID = Field(..., description="Customer UUID identifier")
    status: str = Field(..., description="Invoice status (Current, Pending, Past_Due, Paid)")
    issue_date_time: datetime = Field(..., description="Issue date and time")
    due_date_time: datetime = Field(..., description="Due date and time")
    total_amount: float = Field(..., description="Total invoice amount")
    outstanding_amount: float = Field(..., description="Outstanding unpaid amount")

    model_config = ConfigDict(from_attributes=True)


class PaymentResponseSchema(BaseModel):
    """Response schema for a payment history record [FR-3, FR-10]."""

    payment_id: uuid.UUID = Field(..., description="Unique UUID identifier for the payment")
    invoice_id: uuid.UUID = Field(..., description="Linked invoice UUID identifier")
    payment_date_time: datetime = Field(..., description="Payment completion timestamp")
    amount_paid: float = Field(..., description="Amount paid")
    payment_method: str = Field(..., description="Payment method used")

    model_config = ConfigDict(from_attributes=True)


class ARAgingBucketSchema(BaseModel):
    """Schema representing AR aging totals and count for a single duration bucket [FR-4, FR-10]."""

    total_outstanding: float = Field(..., ge=0.0, description="Total outstanding balance in bucket")
    invoice_count: int = Field(..., ge=0, description="Number of unpaid invoices in bucket")


class ARAgingMetricsResponseSchema(BaseModel):
    """Response schema for accounts-receivable (AR) aging duration buckets [FR-4, FR-10]."""

    bucket_0_30: ARAgingBucketSchema = Field(..., description="Current or 0 to 30 days past due bucket")
    bucket_31_60: ARAgingBucketSchema = Field(..., description="31 to 60 days past due bucket")
    bucket_61_90: ARAgingBucketSchema = Field(..., description="61 to 90 days past due bucket")
    bucket_90_plus: ARAgingBucketSchema = Field(..., description="91+ days past due bucket")


@financial_router.get(
    "/quotes",
    response_model=List[QuoteResponseSchema],
    summary="List authorized quotes with approval states and expiration dates",
    description="Retrieve authorized quotes filtered optionally by approval state or customer ID [FR-1, FR-9, FR-10].",
)
def list_quotes(
    approval_state: Optional[str] = Query(None, description="Filter by quote approval state (Draft, Pending_Approval, Approved, Expired)"),
    customer_id: Optional[str] = Query(None, description="Filter by customer UUID"),
    db: Session = Depends(get_db),
    _access_context: UserAccessContext = Depends(RequireRole("financial:read")),
) -> List[QuoteResponseSchema]:
    """Retrieve filtered list of quotes [FR-1, FR-9, FR-10]."""
    if approval_state is not None:
        approval_state = approval_state.strip()
        if not approval_state:
            approval_state = None

    if customer_id is not None:
        customer_id = customer_id.strip()
        if not customer_id:
            customer_id = None

    try:
        quotes = filter_quotes(
            session=db,
            approval_state=approval_state,
            customer_id=customer_id,
        )
        return [QuoteResponseSchema.model_validate(q) for q in quotes]
    except InvalidFilterError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except FinancialMetricsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@financial_router.get(
    "/invoices",
    response_model=List[InvoiceResponseSchema],
    summary="List current, pending, and historical invoices with status filters",
    description="Retrieve invoices filtered optionally by status or customer ID [FR-2, FR-9, FR-10].",
)
def list_invoices(
    status_filter: Optional[str] = Query(None, alias="status", description="Filter by invoice status (Current, Pending, Past_Due, Paid)"),
    customer_id: Optional[str] = Query(None, description="Filter by customer UUID"),
    db: Session = Depends(get_db),
    _access_context: UserAccessContext = Depends(RequireRole("financial:read")),
) -> List[InvoiceResponseSchema]:
    """Retrieve filtered list of invoices [FR-2, FR-9, FR-10]."""
    if status_filter is not None:
        status_filter = status_filter.strip()
        if not status_filter:
            status_filter = None

    if customer_id is not None:
        customer_id = customer_id.strip()
        if not customer_id:
            customer_id = None

    try:
        invoices = filter_invoices(
            session=db,
            status=status_filter,
            customer_id=customer_id,
        )
        return [InvoiceResponseSchema.model_validate(inv) for inv in invoices]
    except InvalidFilterError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except FinancialMetricsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@financial_router.get(
    "/payments",
    response_model=List[PaymentResponseSchema],
    summary="Get complete payment history records",
    description="Retrieve payment records linked to an invoice or customer [FR-3, FR-9, FR-10].",
)
def list_payments(
    invoice_id: Optional[str] = Query(None, description="Filter payments by invoice UUID"),
    customer_id: Optional[str] = Query(None, description="Filter payments by customer UUID"),
    db: Session = Depends(get_db),
    _access_context: UserAccessContext = Depends(RequireRole("financial:read")),
) -> List[PaymentResponseSchema]:
    """Retrieve payment history records [FR-3, FR-9, FR-10]."""
    if invoice_id is not None:
        invoice_id = invoice_id.strip()
        if not invoice_id:
            invoice_id = None

    if customer_id is not None:
        customer_id = customer_id.strip()
        if not customer_id:
            customer_id = None

    try:
        payments = filter_payments(
            session=db,
            invoice_id=invoice_id,
            customer_id=customer_id,
        )
        return [PaymentResponseSchema.model_validate(p) for p in payments]
    except InvalidFilterError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except FinancialMetricsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )


@financial_router.get(
    "/ar-aging",
    response_model=ARAgingMetricsResponseSchema,
    summary="Get visual AR aging metrics duration buckets",
    description="Retrieve Accounts Receivable (AR) aging metrics segmented into 0-30, 31-60, 61-90, and 90+ day buckets [FR-4, FR-9, FR-10].",
)
def get_ar_aging(
    customer_id: Optional[str] = Query(None, description="Optional customer UUID filter"),
    as_of_date_val: Optional[datetime] = Query(None, description="Optional reference evaluation timestamp"),
    db: Session = Depends(get_db),
    _access_context: UserAccessContext = Depends(RequireRole("financial:read")),
) -> ARAgingMetricsResponseSchema:
    """Retrieve AR aging metrics segmented into duration buckets [FR-4, FR-9, FR-10]."""
    if customer_id is not None:
        customer_id = customer_id.strip()
        if not customer_id:
            customer_id = None

    try:
        aging_data = get_ar_aging_metrics(
            session=db,
            customer_id=customer_id,
            as_of_date_val=as_of_date_val,
        )
        return ARAgingMetricsResponseSchema.model_validate(aging_data)
    except InvalidFilterError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except FinancialMetricsError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
