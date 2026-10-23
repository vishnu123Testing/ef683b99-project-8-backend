"""SQLAlchemy ORM models for knowledge repository and onboarding resources.

Defines database entities for Category, Tag, KnowledgeArticle, PlaybookStep,
UserStepProgress, and Attachment.
"""

from datetime import datetime, timezone
from typing import Any, List, Optional
import uuid

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base

# Content type constants
CONTENT_TYPE_FAQ = "FAQ"
CONTENT_TYPE_PLAYBOOK = "Playbook"
CONTENT_TYPE_BEST_PRACTICE = "BestPractice"
CONTENT_TYPE_TRAINING_VIDEO = "TrainingVideo"

# Many-to-many association table between KnowledgeArticle and Tag
article_tags = Table(
    "article_tags",
    Base.metadata,
    Column(
        "article_id",
        Uuid(as_uuid=True),
        ForeignKey("knowledge_articles.id", ondelete="CASCADE"),
        primary_key=True,
    ),
    Column(
        "tag_id",
        Uuid(as_uuid=True),
        ForeignKey("tags.id", ondelete="CASCADE"),
        primary_key=True,
    ),
)


class Category(Base):
    """Category ORM model for categorizing knowledge repository content."""

    __tablename__ = "categories"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    articles: Mapped[List["KnowledgeArticle"]] = relationship(
        "KnowledgeArticle",
        back_populates="category",
    )

    def __repr__(self) -> str:
        return f"<Category(id={self.id}, name={self.name!r})>"

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation of Category."""
        return {
            "id": str(self.id),
            "name": self.name,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Tag(Base):
    """Tag ORM model for functional tagging of knowledge articles."""

    __tablename__ = "tags"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    name: Mapped[str] = mapped_column(
        String(100),
        unique=True,
        nullable=False,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    articles: Mapped[List["KnowledgeArticle"]] = relationship(
        "KnowledgeArticle",
        secondary=article_tags,
        back_populates="tags",
    )

    def __repr__(self) -> str:
        return f"<Tag(id={self.id}, name={self.name!r})>"

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation of Tag."""
        return {
            "id": str(self.id),
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class KnowledgeArticle(Base):
    """KnowledgeArticle ORM model representing FAQs, Playbooks, Best Practices, and Training Videos."""

    __tablename__ = "knowledge_articles"

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
    content_type: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        index=True,
    )
    body: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    tenant_id: Mapped[Optional[str]] = mapped_column(
        String(255),
        nullable=True,
        index=True,
    )
    category_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    video_url: Mapped[Optional[str]] = mapped_column(
        String(512),
        nullable=True,
    )
    is_published: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
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

    category: Mapped[Optional[Category]] = relationship(
        "Category",
        back_populates="articles",
    )
    tags: Mapped[List[Tag]] = relationship(
        "Tag",
        secondary=article_tags,
        back_populates="articles",
    )
    playbook_steps: Mapped[List["PlaybookStep"]] = relationship(
        "PlaybookStep",
        back_populates="article",
        cascade="all, delete-orphan",
        order_by="PlaybookStep.step_number",
    )
    attachments: Mapped[List["Attachment"]] = relationship(
        "Attachment",
        back_populates="article",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<KnowledgeArticle(id={self.id}, title={self.title!r}, content_type={self.content_type!r})>"

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation of KnowledgeArticle."""
        return {
            "id": str(self.id),
            "title": self.title,
            "content_type": self.content_type,
            "body": self.body,
            "tenant_id": self.tenant_id,
            "category_id": str(self.category_id) if self.category_id else None,
            "video_url": self.video_url,
            "is_published": self.is_published,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class PlaybookStep(Base):
    """PlaybookStep ORM model representing individual sequential steps within an operational playbook."""

    __tablename__ = "playbook_steps"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_articles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    step_number: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    content: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    checkpoint_instructions: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )

    article: Mapped[KnowledgeArticle] = relationship(
        "KnowledgeArticle",
        back_populates="playbook_steps",
    )
    user_progress: Mapped[List["UserStepProgress"]] = relationship(
        "UserStepProgress",
        back_populates="step",
        cascade="all, delete-orphan",
    )

    def __repr__(self) -> str:
        return f"<PlaybookStep(id={self.id}, step_number={self.step_number}, title={self.title!r})>"

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation of PlaybookStep."""
        return {
            "id": str(self.id),
            "article_id": str(self.article_id),
            "step_number": self.step_number,
            "title": self.title,
            "content": self.content,
            "checkpoint_instructions": self.checkpoint_instructions,
        }


class UserStepProgress(Base):
    """UserStepProgress ORM model representing user completion tracking for playbook steps."""

    __tablename__ = "user_step_progress"
    __table_args__ = (
        UniqueConstraint("user_id", "step_id", name="uq_user_step_progress"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    tenant_id: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
        index=True,
    )
    step_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("playbook_steps.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    is_completed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    step: Mapped[PlaybookStep] = relationship(
        "PlaybookStep",
        back_populates="user_progress",
    )

    def __repr__(self) -> str:
        return (
            f"<UserStepProgress(id={self.id}, user_id={self.user_id!r}, "
            f"step_id={self.step_id}, is_completed={self.is_completed})>"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation of UserStepProgress."""
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "tenant_id": self.tenant_id,
            "step_id": str(self.step_id),
            "is_completed": self.is_completed,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class Attachment(Base):
    """Attachment ORM model representing downloadable files and documentation linked to articles."""

    __tablename__ = "attachments"

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    article_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("knowledge_articles.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_name: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )
    file_url: Mapped[str] = mapped_column(
        String(512),
        nullable=False,
    )
    file_size_bytes: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    article: Mapped[KnowledgeArticle] = relationship(
        "KnowledgeArticle",
        back_populates="attachments",
    )

    def __repr__(self) -> str:
        return f"<Attachment(id={self.id}, file_name={self.file_name!r}, article_id={self.article_id})>"

    def to_dict(self) -> dict[str, Any]:
        """Return dictionary representation of Attachment."""
        return {
            "id": str(self.id),
            "article_id": str(self.article_id),
            "file_name": self.file_name,
            "file_url": self.file_url,
            "file_size_bytes": self.file_size_bytes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
