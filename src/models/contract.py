"""SQLAlchemy ORM models for contract lifecycle management.

Defines database entities for contracts, clauses, SLAs, document versions,
amendments, signature requests, and renewal alerts.
"""

from datetime import date as date_type, datetime, timezone
from typing import List, Optional
import uuid

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base
from src.models.user import User


class Contract(Base):
    """Contract ORM entity representing active agreements, SOWs, NDAs, and Exhibits."""

    __tablename__ = "contracts"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    category: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # Values: Contract, SOW, NDA, Exhibit
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
        default="DRAFT",
    )  # Values: DRAFT, PENDING_SIGNATURE, ACTIVE, AMENDED, TERMINATED, EXPIRED
    counterparty: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    start_on: Mapped[Optional[date_type]] = mapped_column(
        Date,
        nullable=True,
    )
    expires_on: Mapped[Optional[date_type]] = mapped_column(
        Date,
        nullable=True,
        index=True,
    )
    parent_contract_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("contracts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    # Relationships
    parent_contract: Mapped[Optional["Contract"]] = relationship(
        "Contract",
        remote_side=[id],
        back_populates="child_contracts",
        foreign_keys=[parent_contract_id],
    )
    child_contracts: Mapped[List["Contract"]] = relationship(
        "Contract",
        back_populates="parent_contract",
        cascade="all, delete-orphan",
    )
    clauses: Mapped[List["ContractClause"]] = relationship(
        "ContractClause",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    slas: Mapped[List["ContractSLA"]] = relationship(
        "ContractSLA",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    document_versions: Mapped[List["DocumentVersion"]] = relationship(
        "DocumentVersion",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    amendments: Mapped[List["ContractAmendment"]] = relationship(
        "ContractAmendment",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    signature_requests: Mapped[List["SignatureRequest"]] = relationship(
        "SignatureRequest",
        back_populates="contract",
        cascade="all, delete-orphan",
    )
    renewal_alerts: Mapped[List["RenewalAlert"]] = relationship(
        "RenewalAlert",
        back_populates="contract",
        cascade="all, delete-orphan",
    )

    def to_dict(self) -> dict:
        """Convert Contract instance into a dictionary representation."""
        return {
            "id": str(self.id),
            "title": self.title,
            "category": self.category,
            "status": self.status,
            "counterparty": self.counterparty,
            "start_on": self.start_on.isoformat() if self.start_on else None,
            "expires_on": self.expires_on.isoformat() if self.expires_on else None,
            "parent_contract_id": str(self.parent_contract_id)
            if self.parent_contract_id
            else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<Contract(id={self.id}, "
            f"title={self.title!r}, "
            f"category={self.category!r}, "
            f"status={self.status!r})>"
        )


class ContractClause(Base):
    """ContractClause ORM entity representing categorized contract clauses."""

    __tablename__ = "contract_clauses"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    clause_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    body_text: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    is_highlighted: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        back_populates="clauses",
    )

    def to_dict(self) -> dict:
        """Convert ContractClause into a dictionary representation."""
        return {
            "id": str(self.id),
            "contract_id": str(self.contract_id),
            "clause_type": self.clause_type,
            "title": self.title,
            "body_text": self.body_text,
            "is_highlighted": self.is_highlighted,
        }

    def __repr__(self) -> str:
        return (
            f"<ContractClause(id={self.id}, "
            f"contract_id={self.contract_id}, "
            f"clause_type={self.clause_type!r}, "
            f"title={self.title!r})>"
        )


class ContractSLA(Base):
    """ContractSLA ORM entity representing Service Level Agreements and target metrics."""

    __tablename__ = "contract_slas"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    metric_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    target_value: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )
    penalty_terms: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        back_populates="slas",
    )

    def to_dict(self) -> dict:
        """Convert ContractSLA into a dictionary representation."""
        return {
            "id": str(self.id),
            "contract_id": str(self.contract_id),
            "metric_name": self.metric_name,
            "target_value": self.target_value,
            "penalty_terms": self.penalty_terms,
        }

    def __repr__(self) -> str:
        return (
            f"<ContractSLA(id={self.id}, "
            f"contract_id={self.contract_id}, "
            f"metric_name={self.metric_name!r})>"
        )


class DocumentVersion(Base):
    """DocumentVersion ORM entity representing version-controlled uploaded agreement files."""

    __tablename__ = "document_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
    )  # Values: Contract, SOW, NDA, Exhibit
    version_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    file_path: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    checksum: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
    )
    uploaded_by_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        back_populates="document_versions",
    )
    uploaded_by: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[uploaded_by_id],
    )

    def to_dict(self) -> dict:
        """Convert DocumentVersion into a dictionary representation."""
        return {
            "id": str(self.id),
            "contract_id": str(self.contract_id),
            "category_type": self.category_type,
            "version_number": self.version_number,
            "file_path": self.file_path,
            "checksum": self.checksum,
            "uploaded_by_id": str(self.uploaded_by_id)
            if self.uploaded_by_id
            else None,
            "uploaded_at": self.uploaded_at.isoformat() if self.uploaded_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<DocumentVersion(id={self.id}, "
            f"contract_id={self.contract_id}, "
            f"version_number={self.version_number}, "
            f"category_type={self.category_type!r})>"
        )


class ContractAmendment(Base):
    """ContractAmendment ORM entity for immutable append-only record of agreement changes."""

    __tablename__ = "contract_amendments"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    sequence_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    summary: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    author_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.user_id", ondelete="SET NULL"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        back_populates="amendments",
    )
    author: Mapped[Optional[User]] = relationship(
        "User",
        foreign_keys=[author_id],
    )

    def to_dict(self) -> dict:
        """Convert ContractAmendment into a dictionary representation."""
        return {
            "id": str(self.id),
            "contract_id": str(self.contract_id),
            "sequence_number": self.sequence_number,
            "summary": self.summary,
            "author_id": str(self.author_id) if self.author_id else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<ContractAmendment(id={self.id}, "
            f"contract_id={self.contract_id}, "
            f"sequence_number={self.sequence_number})>"
        )


class SignatureRequest(Base):
    """SignatureRequest ORM entity representing e-signature execution tasks."""

    __tablename__ = "signature_requests"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    signatory_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="PENDING",
        index=True,
    )  # Values: PENDING, EXECUTED, REJECTED
    signature_digest: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
    )
    executed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        back_populates="signature_requests",
    )
    signatory: Mapped[User] = relationship(
        "User",
        foreign_keys=[signatory_id],
    )

    def to_dict(self) -> dict:
        """Convert SignatureRequest into a dictionary representation."""
        return {
            "id": str(self.id),
            "contract_id": str(self.contract_id),
            "signatory_id": str(self.signatory_id),
            "status": self.status,
            "signature_digest": self.signature_digest,
            "executed_at": self.executed_at.isoformat() if self.executed_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<SignatureRequest(id={self.id}, "
            f"contract_id={self.contract_id}, "
            f"signatory_id={self.signatory_id}, "
            f"status={self.status!r})>"
        )


class RenewalAlert(Base):
    """RenewalAlert ORM entity representing dispatched contract expiration alerts."""

    __tablename__ = "renewal_alerts"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    contract_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("contracts.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    threshold_days: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )  # Values: 90, 60, 30
    dispatched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    contract: Mapped["Contract"] = relationship(
        "Contract",
        back_populates="renewal_alerts",
    )

    def to_dict(self) -> dict:
        """Convert RenewalAlert into a dictionary representation."""
        return {
            "id": str(self.id),
            "contract_id": str(self.contract_id),
            "threshold_days": self.threshold_days,
            "dispatched_at": self.dispatched_at.isoformat() if self.dispatched_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<RenewalAlert(id={self.id}, "
            f"contract_id={self.contract_id}, "
            f"threshold_days={self.threshold_days})>"
        )
