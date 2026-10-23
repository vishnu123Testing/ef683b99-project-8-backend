"""User ORM model with tenant bindings and hashed credentials."""

from datetime import datetime, timezone
import uuid

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError
from sqlalchemy import DateTime, ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base
from src.models.tenant import Tenant

_ph = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash plain text password using Argon2id."""
    return _ph.hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """Verify plain text password against stored Argon2id hash."""
    try:
        return _ph.verify(hashed_password, plain_password)
    except (VerifyMismatchError, VerificationError, ValueError):
        return False


class User(Base):
    """User ORM entity model representing system users bound to tenants."""

    __tablename__ = "users"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )
    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
    )

    def set_password(self, password: str) -> None:
        """Hash and set user password."""
        self.password_hash = hash_password(password)

    def check_password(self, password: str) -> bool:
        """Verify password against stored password_hash."""
        if not self.password_hash:
            return False
        return verify_password(password, self.password_hash)

    def __repr__(self) -> str:
        return f"<User(user_id={self.user_id}, email={self.email!r}, tenant_id={self.tenant_id})>"
