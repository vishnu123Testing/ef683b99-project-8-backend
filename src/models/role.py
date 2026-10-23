"""Role and UserRole ORM models for role-based access control (RBAC)."""

import uuid

from sqlalchemy import ForeignKey, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base
from src.models.tenant import Tenant
from src.models.user import User

# System role name constants
ROLE_SYSTEM_ADMIN = "SystemAdmin"
ROLE_CLIENT_ADMIN = "ClientAdmin"
ROLE_CLIENT_VIEWER = "ClientViewer"


class Role(Base):
    """Role ORM model representing RBAC security roles."""

    __tablename__ = "roles"

    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
        index=True,
    )
    description: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<Role(role_id={self.role_id}, name={self.name!r})>"


class UserRole(Base):
    """UserRole ORM model representing mapping between users, roles, and tenants."""

    __tablename__ = "user_roles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("users.user_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        index=True,
    )
    role_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("roles.role_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("tenants.tenant_id", ondelete="CASCADE"),
        primary_key=True,
        nullable=False,
        index=True,
    )

    user: Mapped[User] = relationship(
        "User",
        foreign_keys=[user_id],
    )
    role: Mapped[Role] = relationship(
        "Role",
        foreign_keys=[role_id],
    )
    tenant: Mapped[Tenant] = relationship(
        "Tenant",
        foreign_keys=[tenant_id],
    )

    def __repr__(self) -> str:
        return (
            f"<UserRole(user_id={self.user_id}, "
            f"role_id={self.role_id}, "
            f"tenant_id={self.tenant_id})>"
        )
