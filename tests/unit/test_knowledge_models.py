"""Unit tests for Category, Tag, KnowledgeArticle, PlaybookStep, UserStepProgress, and Attachment ORM models."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.knowledge import (
    CONTENT_TYPE_BEST_PRACTICE,
    CONTENT_TYPE_FAQ,
    CONTENT_TYPE_PLAYBOOK,
    CONTENT_TYPE_TRAINING_VIDEO,
    Attachment,
    Category,
    KnowledgeArticle,
    PlaybookStep,
    Tag,
    UserStepProgress,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session for testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_category_model_defaults_repr_and_dict():
    """Test Category model initialization, __repr__, and to_dict method."""
    cat = Category(
        name="FAQs",
        description="Frequently asked questions and troubleshooting guides",
    )
    assert cat.name == "FAQs"
    assert cat.description == "Frequently asked questions and troubleshooting guides"

    repr_str = repr(cat)
    assert "Category" in repr_str
    assert "FAQs" in repr_str

    d = cat.to_dict()
    assert d["name"] == "FAQs"
    assert d["description"] == "Frequently asked questions and troubleshooting guides"


def test_tag_model_defaults_repr_and_dict():
    """Test Tag model initialization, __repr__, and to_dict method."""
    tag = Tag(name="Onboarding")
    assert tag.name == "Onboarding"

    repr_str = repr(tag)
    assert "Tag" in repr_str
    assert "Onboarding" in repr_str

    d = tag.to_dict()
    assert d["name"] == "Onboarding"


def test_category_and_tag_unique_constraints(db_session: Session):
    """Test unique name constraints on Category and Tag tables."""
    cat1 = Category(name="Unique Category")
    db_session.add(cat1)
    db_session.commit()

    cat2 = Category(name="Unique Category")
    db_session.add(cat2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()

    tag1 = Tag(name="Unique Tag")
    db_session.add(tag1)
    db_session.commit()

    tag2 = Tag(name="Unique Tag")
    db_session.add(tag2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_knowledge_article_creation_and_relationships(db_session: Session):
    """Test persisting KnowledgeArticle with Category, Tags, PlaybookSteps, and Attachments."""
    category = Category(name="Playbooks", description="Step-by-step guides")
    tag1 = Tag(name="Security")
    tag2 = Tag(name="Setup")

    db_session.add_all([category, tag1, tag2])
    db_session.commit()

    article = KnowledgeArticle(
        title="Initial Security Configuration Playbook",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="This playbook walks through initial security setup.",
        tenant_id="tenant-123",
        category_id=category.id,
        is_published=True,
    )
    article.tags.extend([tag1, tag2])

    db_session.add(article)
    db_session.commit()
    db_session.refresh(article)

    assert article.id is not None
    assert isinstance(article.id, uuid.UUID)
    assert article.category.name == "Playbooks"
    assert len(article.tags) == 2
    assert {t.name for t in article.tags} == {"Security", "Setup"}

    # Add PlaybookStep
    step1 = PlaybookStep(
        article_id=article.id,
        step_number=1,
        title="Configure MFA",
        content="Navigate to Security Settings and enable Multi-Factor Authentication.",
        checkpoint_instructions="Verify MFA prompt displays upon login.",
    )
    step2 = PlaybookStep(
        article_id=article.id,
        step_number=2,
        title="Set API Key Expiration",
        content="Set key lifetime to 90 days maximum.",
    )
    db_session.add_all([step1, step2])

    # Add Attachment
    attachment = Attachment(
        article_id=article.id,
        file_name="security_policy.pdf",
        file_url="https://storage.example.com/docs/security_policy.pdf",
        file_size_bytes=102400,
    )
    db_session.add(attachment)

    db_session.commit()
    db_session.refresh(article)

    assert len(article.playbook_steps) == 2
    assert article.playbook_steps[0].step_number == 1
    assert article.playbook_steps[0].title == "Configure MFA"
    assert article.playbook_steps[1].step_number == 2

    assert len(article.attachments) == 1
    assert article.attachments[0].file_name == "security_policy.pdf"

    # Test article to_dict and repr
    repr_str = repr(article)
    assert "Initial Security Configuration Playbook" in repr_str

    d = article.to_dict()
    assert d["title"] == "Initial Security Configuration Playbook"
    assert d["content_type"] == CONTENT_TYPE_PLAYBOOK
    assert d["tenant_id"] == "tenant-123"
    assert d["category_id"] == str(category.id)


def test_user_step_progress_persistence_and_unique_constraint(db_session: Session):
    """Test UserStepProgress creation, relationship, and unique constraint."""
    article = KnowledgeArticle(
        title="Sample FAQ",
        content_type=CONTENT_TYPE_FAQ,
        body="Sample FAQ content",
    )
    db_session.add(article)
    db_session.commit()

    step = PlaybookStep(
        article_id=article.id,
        step_number=1,
        title="Step 1",
        content="Content for step 1",
    )
    db_session.add(step)
    db_session.commit()

    now = datetime.now(timezone.utc)
    progress = UserStepProgress(
        user_id="user-001",
        tenant_id="tenant-100",
        step_id=step.id,
        is_completed=True,
        completed_at=now,
    )
    db_session.add(progress)
    db_session.commit()
    db_session.refresh(progress)

    assert progress.id is not None
    assert progress.user_id == "user-001"
    assert progress.tenant_id == "tenant-100"
    assert progress.is_completed is True
    assert progress.step.title == "Step 1"

    # Duplicate user_id + step_id should raise IntegrityError
    dup_progress = UserStepProgress(
        user_id="user-001",
        tenant_id="tenant-100",
        step_id=step.id,
        is_completed=False,
    )
    db_session.add(dup_progress)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_cascade_deletion(db_session: Session):
    """Test deleting KnowledgeArticle cascades to PlaybookSteps and Attachments."""
    article = KnowledgeArticle(
        title="Training Module",
        content_type=CONTENT_TYPE_TRAINING_VIDEO,
        body="Training video overview",
        video_url="https://stream.example.com/video/123",
    )
    db_session.add(article)
    db_session.commit()

    step = PlaybookStep(
        article_id=article.id,
        step_number=1,
        title="Watch Video",
        content="Watch video and follow along.",
    )
    attachment = Attachment(
        article_id=article.id,
        file_name="slides.pdf",
        file_url="https://storage.example.com/slides.pdf",
        file_size_bytes=50000,
    )
    db_session.add_all([step, attachment])
    db_session.commit()

    step_id = step.id
    attachment_id = attachment.id

    # Delete article
    db_session.delete(article)
    db_session.commit()

    assert db_session.get(PlaybookStep, step_id) is None
    assert db_session.get(Attachment, attachment_id) is None
