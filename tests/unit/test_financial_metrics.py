"""Unit tests for financial metrics service functions in backend/src/services/financial/metrics.py."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.financial import Invoice, Payment, Quote
from src.services.financial.metrics import (
    FinancialMetricsError,
    InvalidFilterError,
    filter_invoices,
    filter_payments,
    filter_quotes,
    get_ar_aging_metrics,
    get_invoice_by_id,
    get_payment_by_id,
    get_quote_by_id,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_filter_quotes_and_get_quote_by_id(db_session: Session):
    """Test filtering quotes by approval_state and customer_id."""
    cust_a = uuid.uuid4()
    cust_b = uuid.uuid4()
    now = datetime.now(timezone.utc)

    q1 = Quote(
        customer_id=cust_a,
        approval_state="Draft",
        expiration_date_time=now + timedelta(days=10),
        total_amount=Decimal("1000.00"),
        created_at=now - timedelta(hours=2),
    )
    q2 = Quote(
        customer_id=cust_a,
        approval_state="Approved",
        expiration_date_time=now + timedelta(days=30),
        total_amount=Decimal("2500.00"),
        created_at=now - timedelta(hours=1),
    )
    q3 = Quote(
        customer_id=cust_b,
        approval_state="Approved",
        expiration_date_time=now + timedelta(days=5),
        total_amount=Decimal("500.00"),
        created_at=now,
    )
    db_session.add_all([q1, q2, q3])
    db_session.commit()

    # All quotes
    all_quotes = filter_quotes(db_session)
    assert len(all_quotes) == 3

    # Filter by approval_state
    approved_quotes = filter_quotes(db_session, approval_state="Approved")
    assert len(approved_quotes) == 2
    assert {q.quote_id for q in approved_quotes} == {q2.quote_id, q3.quote_id}

    # Filter by customer_id
    cust_a_quotes = filter_quotes(db_session, customer_id=cust_a)
    assert len(cust_a_quotes) == 2
    assert {q.quote_id for q in cust_a_quotes} == {q1.quote_id, q2.quote_id}

    # Filter by both customer_id (as str) and approval_state
    filtered = filter_quotes(
        db_session, approval_state="Draft", customer_id=str(cust_a)
    )
    assert len(filtered) == 1
    assert filtered[0].quote_id == q1.quote_id

    # Get single quote by ID
    found_quote = get_quote_by_id(db_session, q1.quote_id)
    assert found_quote is not None
    assert found_quote.quote_id == q1.quote_id

    # Non-existent quote
    assert get_quote_by_id(db_session, uuid.uuid4()) is None

    # Invalid UUID string
    with pytest.raises(InvalidFilterError):
        filter_quotes(db_session, customer_id="not-a-valid-uuid")


def test_filter_invoices_and_get_invoice_by_id(db_session: Session):
    """Test filtering invoices by status and customer_id."""
    cust_1 = uuid.uuid4()
    cust_2 = uuid.uuid4()
    now = datetime.now(timezone.utc)

    inv1 = Invoice(
        customer_id=cust_1,
        status="Current",
        issue_date_time=now - timedelta(days=5),
        due_date_time=now + timedelta(days=25),
        total_amount=Decimal("1200.00"),
        outstanding_amount=Decimal("1200.00"),
    )
    inv2 = Invoice(
        customer_id=cust_1,
        status="Past_Due",
        issue_date_time=now - timedelta(days=45),
        due_date_time=now - timedelta(days=15),
        total_amount=Decimal("800.00"),
        outstanding_amount=Decimal("800.00"),
    )
    inv3 = Invoice(
        customer_id=cust_2,
        status="Paid",
        issue_date_time=now - timedelta(days=60),
        due_date_time=now - timedelta(days=30),
        total_amount=Decimal("3000.00"),
        outstanding_amount=Decimal("0.00"),
    )
    db_session.add_all([inv1, inv2, inv3])
    db_session.commit()

    # All invoices
    all_invoices = filter_invoices(db_session)
    assert len(all_invoices) == 3

    # Filter by status
    past_due_invoices = filter_invoices(db_session, status="Past_Due")
    assert len(past_due_invoices) == 1
    assert past_due_invoices[0].invoice_id == inv2.invoice_id

    # Filter by customer_id
    cust_1_invoices = filter_invoices(db_session, customer_id=cust_1)
    assert len(cust_1_invoices) == 2

    # Get single invoice
    found_inv = get_invoice_by_id(db_session, str(inv1.invoice_id))
    assert found_inv is not None
    assert found_inv.invoice_id == inv1.invoice_id

    # Invalid UUID string
    with pytest.raises(InvalidFilterError):
        filter_invoices(db_session, customer_id="bad-uuid")


def test_filter_payments_and_get_payment_by_id(db_session: Session):
    """Test filtering payments by invoice_id and customer_id."""
    cust_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    inv = Invoice(
        customer_id=cust_id,
        status="Past_Due",
        issue_date_time=now - timedelta(days=30),
        due_date_time=now - timedelta(days=5),
        total_amount=Decimal("2000.00"),
        outstanding_amount=Decimal("1000.00"),
    )
    db_session.add(inv)
    db_session.commit()

    p1 = Payment(
        invoice_id=inv.invoice_id,
        payment_date_time=now - timedelta(days=4),
        amount_paid=Decimal("500.00"),
        payment_method="CreditCard",
    )
    p2 = Payment(
        invoice_id=inv.invoice_id,
        payment_date_time=now - timedelta(days=2),
        amount_paid=Decimal("500.00"),
        payment_method="ACH",
    )
    db_session.add_all([p1, p2])
    db_session.commit()

    # All payments
    all_payments = filter_payments(db_session)
    assert len(all_payments) == 2

    # Filter by invoice_id
    inv_payments = filter_payments(db_session, invoice_id=inv.invoice_id)
    assert len(inv_payments) == 2

    # Filter by customer_id
    cust_payments = filter_payments(db_session, customer_id=str(cust_id))
    assert len(cust_payments) == 2

    # Get single payment by ID
    found_pay = get_payment_by_id(db_session, p1.payment_id)
    assert found_pay is not None
    assert found_pay.payment_id == p1.payment_id

    # Invalid UUID string
    with pytest.raises(InvalidFilterError):
        filter_payments(db_session, invoice_id="invalid-uuid")


def test_get_ar_aging_metrics_empty_state(db_session: Session):
    """Test AR aging metrics returns 0 balances and counts when no invoices exist."""
    metrics = get_ar_aging_metrics(db_session)
    assert "bucket_0_30" in metrics
    assert "bucket_31_60" in metrics
    assert "bucket_61_90" in metrics
    assert "bucket_90_plus" in metrics

    for bucket_key in ["bucket_0_30", "bucket_31_60", "bucket_61_90", "bucket_90_plus"]:
        assert metrics[bucket_key]["total_outstanding"] == 0.0
        assert metrics[bucket_key]["invoice_count"] == 0


def test_get_ar_aging_metrics_bucket_categorization(db_session: Session):
    """Test categorization of unpaid invoices across 0-30, 31-60, 61-90, 90+ buckets."""
    ref_time = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    cust_id = uuid.uuid4()

    # Bucket 0-30 days: due 10 days ago (2026-10-05)
    inv_0_30 = Invoice(
        customer_id=cust_id,
        status="Current",
        issue_date_time=ref_time - timedelta(days=20),
        due_date_time=ref_time - timedelta(days=10),
        total_amount=Decimal("100.00"),
        outstanding_amount=Decimal("100.00"),
    )
    # Bucket 31-60 days: due 45 days ago (2026-08-31)
    inv_31_60 = Invoice(
        customer_id=cust_id,
        status="Past_Due",
        issue_date_time=ref_time - timedelta(days=60),
        due_date_time=ref_time - timedelta(days=45),
        total_amount=Decimal("250.50"),
        outstanding_amount=Decimal("250.50"),
    )
    # Bucket 61-90 days: due 75 days ago (2026-08-01)
    inv_61_90 = Invoice(
        customer_id=cust_id,
        status="Past_Due",
        issue_date_time=ref_time - timedelta(days=90),
        due_date_time=ref_time - timedelta(days=75),
        total_amount=Decimal("400.00"),
        outstanding_amount=Decimal("400.00"),
    )
    # Bucket 90+ days: due 120 days ago (2026-06-17)
    inv_90_plus = Invoice(
        customer_id=cust_id,
        status="Past_Due",
        issue_date_time=ref_time - timedelta(days=150),
        due_date_time=ref_time - timedelta(days=120),
        total_amount=Decimal("1000.00"),
        outstanding_amount=Decimal("1000.00"),
    )
    # Paid invoice (outstanding_amount = 0): should be ignored
    inv_paid = Invoice(
        customer_id=cust_id,
        status="Paid",
        issue_date_time=ref_time - timedelta(days=100),
        due_date_time=ref_time - timedelta(days=80),
        total_amount=Decimal("5000.00"),
        outstanding_amount=Decimal("0.00"),
    )

    db_session.add_all([inv_0_30, inv_31_60, inv_61_90, inv_90_plus, inv_paid])
    db_session.commit()

    metrics = get_ar_aging_metrics(db_session, customer_id=cust_id, as_of_date_val=ref_time)

    assert metrics["bucket_0_30"]["total_outstanding"] == 100.0
    assert metrics["bucket_0_30"]["invoice_count"] == 1

    assert metrics["bucket_31_60"]["total_outstanding"] == 250.5
    assert metrics["bucket_31_60"]["invoice_count"] == 1

    assert metrics["bucket_61_90"]["total_outstanding"] == 400.0
    assert metrics["bucket_61_90"]["invoice_count"] == 1

    assert metrics["bucket_90_plus"]["total_outstanding"] == 1000.0
    assert metrics["bucket_90_plus"]["invoice_count"] == 1


def test_get_ar_aging_metrics_customer_isolation(db_session: Session):
    """Test that customer_id filtering isolates AR aging metrics per customer."""
    ref_time = datetime(2026, 10, 15, 12, 0, 0, tzinfo=timezone.utc)
    cust_1 = uuid.uuid4()
    cust_2 = uuid.uuid4()

    inv1 = Invoice(
        customer_id=cust_1,
        status="Past_Due",
        issue_date_time=ref_time - timedelta(days=40),
        due_date_time=ref_time - timedelta(days=20),
        total_amount=Decimal("500.00"),
        outstanding_amount=Decimal("500.00"),
    )
    inv2 = Invoice(
        customer_id=cust_2,
        status="Past_Due",
        issue_date_time=ref_time - timedelta(days=40),
        due_date_time=ref_time - timedelta(days=20),
        total_amount=Decimal("1500.00"),
        outstanding_amount=Decimal("1500.00"),
    )
    db_session.add_all([inv1, inv2])
    db_session.commit()

    metrics_1 = get_ar_aging_metrics(db_session, customer_id=cust_1, as_of_date_val=ref_time)
    assert metrics_1["bucket_0_30"]["total_outstanding"] == 500.0
    assert metrics_1["bucket_0_30"]["invoice_count"] == 1

    metrics_2 = get_ar_aging_metrics(db_session, customer_id=str(cust_2), as_of_date_val=ref_time)
    assert metrics_2["bucket_0_30"]["total_outstanding"] == 1500.0
    assert metrics_2["bucket_0_30"]["invoice_count"] == 1
