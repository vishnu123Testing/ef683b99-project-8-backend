"""Financial Transparency Domain Metrics Service [FR-1, FR-2, FR-3, FR-4].

Provides business logic for filtering quotes, invoices, payments, and
aggregating AR aging duration buckets (0-30, 31-60, 61-90, 90+ days).
"""

from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional, Union
import uuid

from sqlalchemy.orm import Session

from src.models.financial import Invoice, Payment, Quote

logger = logging.getLogger(__name__)


class FinancialMetricsError(Exception):
    """Base exception class for financial metrics service errors."""

    pass


class InvalidFilterError(FinancialMetricsError):
    """Raised when filter parameters (e.g., invalid UUID formats) are invalid."""

    pass


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime object is timezone-aware and set to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_uuid(val: Union[uuid.UUID, str], field_name: str = "id") -> uuid.UUID:
    """Safely parse UUID object or string representation."""
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise InvalidFilterError(f"Invalid UUID for {field_name}: {val}") from exc


def filter_quotes(
    session: Session,
    approval_state: Optional[str] = None,
    customer_id: Optional[Union[uuid.UUID, str]] = None,
) -> List[Quote]:
    """Filter quotes by approval_state and/or customer_id [FR-1].

    :param session: Synchronous SQLAlchemy session
    :param approval_state: Optional quote approval state (Draft, Pending_Approval, Approved, Expired)
    :param customer_id: Optional customer UUID or string
    :return: List of Quote ORM instances matching filter criteria
    """
    query = session.query(Quote)

    if approval_state is not None and approval_state.strip():
        query = query.filter(Quote.approval_state == approval_state.strip())

    if customer_id is not None:
        cust_uuid = _parse_uuid(customer_id, "customer_id")
        query = query.filter(Quote.customer_id == cust_uuid)

    query = query.order_by(Quote.created_at.desc())
    return list(query.all())


# Alias for filter_quotes
get_quotes = filter_quotes


def get_quote_by_id(
    session: Session,
    quote_id: Union[uuid.UUID, str],
) -> Optional[Quote]:
    """Retrieve a single quote by its primary key UUID.

    :param session: Synchronous SQLAlchemy session
    :param quote_id: Quote UUID or string
    :return: Quote ORM instance if found, else None
    """
    parsed_id = _parse_uuid(quote_id, "quote_id")
    return session.query(Quote).filter(Quote.quote_id == parsed_id).first()


def filter_invoices(
    session: Session,
    status: Optional[str] = None,
    customer_id: Optional[Union[uuid.UUID, str]] = None,
) -> List[Invoice]:
    """Filter invoices by status and/or customer_id [FR-2].

    :param session: Synchronous SQLAlchemy session
    :param status: Optional invoice status (Current, Pending, Past_Due, Paid)
    :param customer_id: Optional customer UUID or string
    :return: List of Invoice ORM instances matching filter criteria
    """
    query = session.query(Invoice)

    if status is not None and status.strip():
        query = query.filter(Invoice.status == status.strip())

    if customer_id is not None:
        cust_uuid = _parse_uuid(customer_id, "customer_id")
        query = query.filter(Invoice.customer_id == cust_uuid)

    query = query.order_by(Invoice.due_date_time.asc())
    return list(query.all())


# Alias for filter_invoices
get_invoices = filter_invoices


def get_invoice_by_id(
    session: Session,
    invoice_id: Union[uuid.UUID, str],
) -> Optional[Invoice]:
    """Retrieve a single invoice by its primary key UUID.

    :param session: Synchronous SQLAlchemy session
    :param invoice_id: Invoice UUID or string
    :return: Invoice ORM instance if found, else None
    """
    parsed_id = _parse_uuid(invoice_id, "invoice_id")
    return session.query(Invoice).filter(Invoice.invoice_id == parsed_id).first()


def filter_payments(
    session: Session,
    invoice_id: Optional[Union[uuid.UUID, str]] = None,
    customer_id: Optional[Union[uuid.UUID, str]] = None,
) -> List[Payment]:
    """Filter payment history by invoice_id and/or customer_id [FR-3].

    :param session: Synchronous SQLAlchemy session
    :param invoice_id: Optional invoice UUID or string
    :param customer_id: Optional customer UUID or string
    :return: List of Payment ORM instances matching filter criteria
    """
    query = session.query(Payment)

    if invoice_id is not None:
        inv_uuid = _parse_uuid(invoice_id, "invoice_id")
        query = query.filter(Payment.invoice_id == inv_uuid)

    if customer_id is not None:
        cust_uuid = _parse_uuid(customer_id, "customer_id")
        query = query.join(Invoice, Payment.invoice_id == Invoice.invoice_id).filter(
            Invoice.customer_id == cust_uuid
        )

    query = query.order_by(Payment.payment_date_time.desc())
    return list(query.all())


# Alias for filter_payments
get_payments = filter_payments


def get_payment_by_id(
    session: Session,
    payment_id: Union[uuid.UUID, str],
) -> Optional[Payment]:
    """Retrieve a single payment record by its primary key UUID.

    :param session: Synchronous SQLAlchemy session
    :param payment_id: Payment UUID or string
    :return: Payment ORM instance if found, else None
    """
    parsed_id = _parse_uuid(payment_id, "payment_id")
    return session.query(Payment).filter(Payment.payment_id == parsed_id).first()


def get_ar_aging_metrics(
    session: Session,
    customer_id: Optional[Union[uuid.UUID, str]] = None,
    as_of_date_val: Optional[datetime] = None,
) -> Dict[str, Dict[str, Any]]:
    """Aggregate accounts-receivable (AR) aging metrics into duration buckets [FR-4].

    Categorizes outstanding balance balances into:
    - bucket_0_30: current or 0-30 days past due
    - bucket_31_60: 31-60 days past due
    - bucket_61_90: 61-90 days past due
    - bucket_90_plus: 91+ days past due

    :param session: Synchronous SQLAlchemy session
    :param customer_id: Optional customer UUID or string
    :param as_of_date_val: Reference evaluation timestamp (defaults to current UTC time)
    :return: Dict mapping bucket keys to total_outstanding balance and invoice_count
    """
    ref_dt = (
        _ensure_utc(as_of_date_val) if as_of_date_val else datetime.now(timezone.utc)
    )

    query = session.query(Invoice).filter(
        Invoice.outstanding_amount > Decimal("0.00"),
        Invoice.status != "Paid",
    )

    if customer_id is not None:
        cust_uuid = _parse_uuid(customer_id, "customer_id")
        query = query.filter(Invoice.customer_id == cust_uuid)

    invoices = query.all()

    buckets: Dict[str, Dict[str, Any]] = {
        "bucket_0_30": {"total_outstanding": 0.0, "invoice_count": 0},
        "bucket_31_60": {"total_outstanding": 0.0, "invoice_count": 0},
        "bucket_61_90": {"total_outstanding": 0.0, "invoice_count": 0},
        "bucket_90_plus": {"total_outstanding": 0.0, "invoice_count": 0},
    }

    for inv in invoices:
        due_dt = _ensure_utc(inv.due_date_time)
        if due_dt is None:
            continue

        days_overdue = (ref_dt - due_dt).days
        amount = (
            float(inv.outstanding_amount) if inv.outstanding_amount is not None else 0.0
        )

        if days_overdue <= 30:
            target = "bucket_0_30"
        elif 31 <= days_overdue <= 60:
            target = "bucket_31_60"
        elif 61 <= days_overdue <= 90:
            target = "bucket_61_90"
        else:
            target = "bucket_90_plus"

        buckets[target]["total_outstanding"] += amount
        buckets[target]["invoice_count"] += 1

    for key in buckets:
        buckets[key]["total_outstanding"] = round(buckets[key]["total_outstanding"], 2)

    return buckets


# Alias for get_ar_aging_metrics
calculate_ar_aging = get_ar_aging_metrics
