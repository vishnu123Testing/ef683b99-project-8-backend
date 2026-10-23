"""SQLAlchemy ORM models for financial records: Quote, Invoice, and Payment.

Defines database entities for quotes, invoices, and payment histories
with explicit indexes and relationships.
"""

from datetime import datetime, timezone
from decimal import Decimal
from typing import List, Optional
import uuid

from sqlalchemy import (
    DateTime,
    ForeignKey,
    Numeric,
    String,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base


class Quote(Base):
    """Quote ORM model representing authorized price proposals and approval states."""

    __tablename__ = "quotes"

    quote_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    approval_state: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        default="Draft",
    )  # Values: Draft, Pending_Approval, Approved, Expired
    expiration_date_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    invoices: Mapped[List["Invoice"]] = relationship(
        "Invoice",
        back_populates="quote",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Quote(quote_id={self.quote_id}, customer_id={self.customer_id}, "
            f"approval_state='{self.approval_state}', total_amount={self.total_amount})>"
        )

    def to_dict(self) -> dict:
        return {
            "quote_id": str(self.quote_id),
            "customer_id": str(self.customer_id),
            "approval_state": self.approval_state,
            "expiration_date_time": (
                self.expiration_date_time.isoformat()
                if self.expiration_date_time
                else None
            ),
            "total_amount": (
                float(self.total_amount) if self.total_amount is not None else 0.0
            ),
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Invoice(Base):
    """Invoice ORM model representing current, pending, past due, or paid billing records."""

    __tablename__ = "invoices"

    invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    quote_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("quotes.quote_id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    customer_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        default="Current",
    )  # Values: Current, Pending, Past_Due, Paid
    issue_date_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    due_date_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    total_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    outstanding_amount: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )

    # Relationships
    quote: Mapped[Optional["Quote"]] = relationship(
        "Quote",
        back_populates="invoices",
    )
    payments: Mapped[List["Payment"]] = relationship(
        "Payment",
        back_populates="invoice",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return (
            f"<Invoice(invoice_id={self.invoice_id}, customer_id={self.customer_id}, "
            f"status='{self.status}', total_amount={self.total_amount}, "
            f"outstanding_amount={self.outstanding_amount})>"
        )

    def to_dict(self) -> dict:
        return {
            "invoice_id": str(self.invoice_id),
            "quote_id": str(self.quote_id) if self.quote_id else None,
            "customer_id": str(self.customer_id),
            "status": self.status,
            "issue_date_time": (
                self.issue_date_time.isoformat() if self.issue_date_time else None
            ),
            "due_date_time": (
                self.due_date_time.isoformat() if self.due_date_time else None
            ),
            "total_amount": (
                float(self.total_amount) if self.total_amount is not None else 0.0
            ),
            "outstanding_amount": (
                float(self.outstanding_amount)
                if self.outstanding_amount is not None
                else 0.0
            ),
        }


class Payment(Base):
    """Payment ORM model representing payments made toward an invoice."""

    __tablename__ = "payments"

    payment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    invoice_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("invoices.invoice_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    payment_date_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )
    amount_paid: Mapped[Decimal] = mapped_column(
        Numeric(12, 2),
        nullable=False,
    )
    payment_method: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )

    # Relationships
    invoice: Mapped["Invoice"] = relationship(
        "Invoice",
        back_populates="payments",
    )

    def __repr__(self) -> str:
        return (
            f"<Payment(payment_id={self.payment_id}, invoice_id={self.invoice_id}, "
            f"amount_paid={self.amount_paid}, payment_method='{self.payment_method}')>"
        )

    def to_dict(self) -> dict:
        return {
            "payment_id": str(self.payment_id),
            "invoice_id": str(self.invoice_id),
            "payment_date_time": (
                self.payment_date_time.isoformat()
                if self.payment_date_time
                else None
            ),
            "amount_paid": (
                float(self.amount_paid) if self.amount_paid is not None else 0.0
            ),
            "payment_method": self.payment_method,
        }
