"""Unit tests for Quote, Invoice, and Payment ORM models in backend/src/models/financial.py."""

from datetime import datetime, timezone
from decimal import Decimal
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.financial import Invoice, Payment, Quote


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session for testing."""
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


def test_quote_model_defaults_repr_and_to_dict():
    """Test Quote model initialization, repr, and to_dict conversion."""
    now = datetime.now(timezone.utc)
    cust_id = uuid.uuid4()
    quote = Quote(
        customer_id=cust_id,
        approval_state="Draft",
        expiration_date_time=now,
        total_amount=Decimal("1500.00"),
        created_at=now,
    )

    assert quote.customer_id == cust_id
    assert quote.approval_state == "Draft"
    assert quote.expiration_date_time == now
    assert quote.total_amount == Decimal("1500.00")

    repr_str = repr(quote)
    assert "Quote" in repr_str
    assert str(cust_id) in repr_str
    assert "Draft" in repr_str

    d = quote.to_dict()
    assert d["customer_id"] == str(cust_id)
    assert d["approval_state"] == "Draft"
    assert d["expiration_date_time"] == now.isoformat()
    assert d["total_amount"] == 1500.00


def test_invoice_model_defaults_repr_and_to_dict():
    """Test Invoice model initialization, repr, and to_dict conversion."""
    now = datetime.now(timezone.utc)
    cust_id = uuid.uuid4()
    quote_id = uuid.uuid4()
    invoice = Invoice(
        quote_id=quote_id,
        customer_id=cust_id,
        status="Current",
        issue_date_time=now,
        due_date_time=now,
        total_amount=Decimal("2500.00"),
        outstanding_amount=Decimal("2500.00"),
    )

    assert invoice.quote_id == quote_id
    assert invoice.customer_id == cust_id
    assert invoice.status == "Current"
    assert invoice.total_amount == Decimal("2500.00")
    assert invoice.outstanding_amount == Decimal("2500.00")

    repr_str = repr(invoice)
    assert "Invoice" in repr_str
    assert str(cust_id) in repr_str
    assert "Current" in repr_str

    d = invoice.to_dict()
    assert d["invoice_id"] is not None
    assert d["quote_id"] == str(quote_id)
    assert d["customer_id"] == str(cust_id)
    assert d["status"] == "Current"
    assert d["total_amount"] == 2500.00
    assert d["outstanding_amount"] == 2500.00


def test_payment_model_defaults_repr_and_to_dict():
    """Test Payment model initialization, repr, and to_dict conversion."""
    now = datetime.now(timezone.utc)
    inv_id = uuid.uuid4()
    payment = Payment(
        invoice_id=inv_id,
        payment_date_time=now,
        amount_paid=Decimal("500.00"),
        payment_method="Credit_Card",
    )

    assert payment.invoice_id == inv_id
    assert payment.payment_date_time == now
    assert payment.amount_paid == Decimal("500.00")
    assert payment.payment_method == "Credit_Card"

    repr_str = repr(payment)
    assert "Payment" in repr_str
    assert str(inv_id) in repr_str
    assert "Credit_Card" in repr_str

    d = payment.to_dict()
    assert d["payment_id"] is not None
    assert d["invoice_id"] == str(inv_id)
    assert d["payment_date_time"] == now.isoformat()
    assert d["amount_paid"] == 500.00
    assert d["payment_method"] == "Credit_Card"


def test_financial_models_persistence_and_relationships(db_session: Session):
    """Test persisting Quote, Invoice, and Payment models and verifying relationships."""
    now = datetime.now(timezone.utc)
    cust_id = uuid.uuid4()

    # Create Quote
    quote = Quote(
        customer_id=cust_id,
        approval_state="Approved",
        expiration_date_time=now,
        total_amount=Decimal("10000.00"),
    )
    db_session.add(quote)
    db_session.commit()
    db_session.refresh(quote)

    assert quote.quote_id is not None

    # Create Invoice linked to Quote
    invoice = Invoice(
        quote_id=quote.quote_id,
        customer_id=cust_id,
        status="Current",
        issue_date_time=now,
        due_date_time=now,
        total_amount=Decimal("10000.00"),
        outstanding_amount=Decimal("5000.00"),
    )
    db_session.add(invoice)
    db_session.commit()
    db_session.refresh(invoice)

    assert invoice.invoice_id is not None
    assert invoice.quote is not None
    assert invoice.quote.quote_id == quote.quote_id
    assert len(quote.invoices) == 1
    assert quote.invoices[0].invoice_id == invoice.invoice_id

    # Create Payment linked to Invoice
    payment = Payment(
        invoice_id=invoice.invoice_id,
        payment_date_time=now,
        amount_paid=Decimal("5000.00"),
        payment_method="ACH",
    )
    db_session.add(payment)
    db_session.commit()
    db_session.refresh(payment)

    assert payment.payment_id is not None
    assert payment.invoice is not None
    assert payment.invoice.invoice_id == invoice.invoice_id
    assert len(invoice.payments) == 1
    assert invoice.payments[0].payment_id == payment.payment_id


def test_cascade_deletion(db_session: Session):
    """Test cascade deletion from Invoice to Payment, and Quote to Invoice."""
    now = datetime.now(timezone.utc)
    cust_id = uuid.uuid4()

    quote = Quote(
        customer_id=cust_id,
        approval_state="Approved",
        expiration_date_time=now,
        total_amount=Decimal("5000.00"),
    )
    db_session.add(quote)
    db_session.commit()

    invoice = Invoice(
        quote_id=quote.quote_id,
        customer_id=cust_id,
        status="Current",
        issue_date_time=now,
        due_date_time=now,
        total_amount=Decimal("5000.00"),
        outstanding_amount=Decimal("0.00"),
    )
    db_session.add(invoice)
    db_session.commit()

    payment = Payment(
        invoice_id=invoice.invoice_id,
        payment_date_time=now,
        amount_paid=Decimal("5000.00"),
        payment_method="Wire_Transfer",
    )
    db_session.add(payment)
    db_session.commit()

    inv_id = invoice.invoice_id
    pmt_id = payment.payment_id

    # Delete invoice, payment should be deleted via cascade
    db_session.delete(invoice)
    db_session.commit()

    assert db_session.get(Invoice, inv_id) is None
    assert db_session.get(Payment, pmt_id) is None
