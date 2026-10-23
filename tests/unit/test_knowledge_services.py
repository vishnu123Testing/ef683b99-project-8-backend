"""Unit tests for knowledge article domain service operations [FR-1, FR-2, FR-6, FR-7, FR-8, FR-9]."""

from datetime import datetime, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.knowledge import (
    CONTENT_TYPE_BEST_PRACTICE,
    CONTENT_TYPE_FAQ,
    CONTENT_TYPE_PLAYBOOK,
    CONTENT_TYPE_TRAINING_VIDEO,
    Category,
    KnowledgeArticle,
    Tag,
)
from src.services.knowledge.article_service import (
    ArticleNotFoundError,
    ArticleService,
    CategoryNotFoundError,
    InvalidArticleError,
    TagNotFoundError,
    create_article,
    create_category,
    create_tag,
    delete_article,
    get_article_by_id,
    get_category_by_id,
    get_tag_by_id,
    list_categories,
    list_tags,
    search_articles,
    update_article,
)
from src.services.knowledge.playbook_service import (
    InvalidPlaybookError,
    InvalidStepReferenceError,
    PlaybookNotFoundError,
    PlaybookService,
    create_playbook_step,
    delete_playbook_step,
    get_next_uncompleted_step,
    get_playbook_progress,
    get_playbook_step_by_id,
    get_playbook_steps,
    reset_playbook_progress,
    toggle_step_progress,
    update_playbook_step,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for unit testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_create_category_and_tag(db_session: Session):
    """Test category and tag creation and retrieval."""
    cat = create_category(db_session, name="Onboarding", description="Onboarding resources")
    assert cat.id is not None
    assert cat.name == "Onboarding"

    tag = create_tag(db_session, name="Security")
    assert tag.id is not None
    assert tag.name == "Security"

    categories = list_categories(db_session)
    assert len(categories) == 1
    assert categories[0].name == "Onboarding"

    tags = list_tags(db_session)
    assert len(tags) == 1
    assert tags[0].name == "Security"

    assert get_category_by_id(db_session, cat.id).name == "Onboarding"
    assert get_tag_by_id(db_session, tag.id).name == "Security"


def test_create_category_and_tag_invalid_inputs(db_session: Session):
    """Test validation errors for empty category/tag names."""
    with pytest.raises(InvalidArticleError):
        create_category(db_session, name="   ")

    with pytest.raises(InvalidArticleError):
        create_tag(db_session, name="")

    with pytest.raises(CategoryNotFoundError):
        get_category_by_id(db_session, uuid.uuid4())

    with pytest.raises(TagNotFoundError):
        get_tag_by_id(db_session, uuid.uuid4())


def test_create_article_success(db_session: Session):
    """Test creating a KnowledgeArticle with category, tags, steps, and attachments."""
    cat = create_category(db_session, name="Playbooks")
    tag1 = create_tag(db_session, name="Setup")
    tag2 = create_tag(db_session, name="Admin")

    steps = [
        {"step_number": 1, "title": "Step 1", "content": "Execute step 1"},
        {"step_number": 2, "title": "Step 2", "content": "Execute step 2"},
    ]
    attachments = [
        {"file_name": "guide.pdf", "file_url": "https://example.com/guide.pdf", "file_size_bytes": 1024}
    ]

    article = create_article(
        db_session,
        title="Initial System Setup Playbook",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Detailed setup instructions for administrators.",
        category_id=cat.id,
        tag_ids=[tag1.id, tag2.id],
        tenant_id="tenant-100",
        playbook_steps=steps,
        attachments=attachments,
    )

    assert article.id is not None
    assert article.title == "Initial System Setup Playbook"
    assert article.content_type == CONTENT_TYPE_PLAYBOOK
    assert article.category_id == cat.id
    assert len(article.tags) == 2
    assert len(article.playbook_steps) == 2
    assert len(article.attachments) == 1
    assert article.tenant_id == "tenant-100"


def test_create_article_invalid_references(db_session: Session):
    """Test creating article with non-existent category or tag ID."""
    with pytest.raises(CategoryNotFoundError):
        create_article(
            db_session,
            title="Invalid Category Article",
            content_type=CONTENT_TYPE_FAQ,
            body="Sample body content",
            category_id=uuid.uuid4(),
        )

    with pytest.raises(TagNotFoundError):
        create_article(
            db_session,
            title="Invalid Tag Article",
            content_type=CONTENT_TYPE_FAQ,
            body="Sample body content",
            tag_ids=[uuid.uuid4()],
        )


def test_get_article_by_id_and_tenant_boundary(db_session: Session):
    """Test article detail retrieval and tenant isolation enforcement [FR-9]."""
    article_global = create_article(
        db_session,
        title="Global FAQ",
        content_type=CONTENT_TYPE_FAQ,
        body="Global FAQ content accessible to all.",
        tenant_id=None,
    )

    article_tenant_a = create_article(
        db_session,
        title="Tenant A Playbook",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Tenant A proprietary playbook.",
        tenant_id="tenant-A",
    )

    # 1. Global article accessible with or without tenant_id
    fetched_global_1 = get_article_by_id(db_session, article_global.id, tenant_id=None)
    assert fetched_global_1.id == article_global.id

    fetched_global_2 = get_article_by_id(db_session, article_global.id, tenant_id="tenant-B")
    assert fetched_global_2.id == article_global.id

    # 2. Tenant A article accessible with matching tenant_id
    fetched_a = get_article_by_id(db_session, article_tenant_a.id, tenant_id="tenant-A")
    assert fetched_a.id == article_tenant_a.id

    # 3. Tenant A article raises ArticleNotFoundError for Tenant B caller
    with pytest.raises(ArticleNotFoundError):
        get_article_by_id(db_session, article_tenant_a.id, tenant_id="tenant-B")


def test_search_articles_filters_and_text_query(db_session: Session):
    """Test search_articles filtering by query, category, content_type, tags, and tenant [FR-2, FR-7, FR-9]."""
    cat_faq = create_category(db_session, name="FAQs")
    cat_playbook = create_category(db_session, name="Playbooks")

    tag_security = create_tag(db_session, name="Security")
    tag_billing = create_tag(db_session, name="Billing")

    # Global FAQ
    art1 = create_article(
        db_session,
        title="How to reset password",
        content_type=CONTENT_TYPE_FAQ,
        body="Instructions on resetting user password securely.",
        category_id=cat_faq.id,
        tag_ids=[tag_security.id],
        tenant_id=None,
    )

    # Tenant-1 Playbook
    art2 = create_article(
        db_session,
        title="Quarterly Billing Audit Playbook",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Procedure for performing quarterly billing audit.",
        category_id=cat_playbook.id,
        tag_ids=[tag_billing.id],
        tenant_id="tenant-1",
    )

    # Tenant-2 Best Practice
    art3 = create_article(
        db_session,
        title="Security Best Practices for Cloud Deployments",
        content_type=CONTENT_TYPE_BEST_PRACTICE,
        body="Recommendations for securing cloud deployment endpoints.",
        category_id=cat_faq.id,
        tag_ids=[tag_security.id],
        tenant_id="tenant-2",
    )

    # 1. Search without filters for tenant-1
    res, count = search_articles(db_session, tenant_id="tenant-1")
    # Should see global art1 and tenant-1 art2 (art3 is tenant-2)
    assert count == 2
    found_ids = {a.id for a in res}
    assert art1.id in found_ids
    assert art2.id in found_ids
    assert art3.id not in found_ids

    # 2. Text query search for 'password'
    res, count = search_articles(db_session, query="password", tenant_id="tenant-1")
    assert count == 1
    assert res[0].id == art1.id

    # 3. Search by content_type='Playbook'
    res, count = search_articles(db_session, content_type=CONTENT_TYPE_PLAYBOOK, tenant_id="tenant-1")
    assert count == 1
    assert res[0].id == art2.id

    # 4. Search by category_id
    res, count = search_articles(db_session, category_id=cat_faq.id, tenant_id="tenant-1")
    assert count == 1
    assert res[0].id == art1.id

    # 5. Search by tag_ids
    res, count = search_articles(db_session, tag_ids=[tag_billing.id], tenant_id="tenant-1")
    assert count == 1
    assert res[0].id == art2.id


def test_update_and_delete_article(db_session: Session):
    """Test updating article content and deleting article record."""
    cat1 = create_category(db_session, name="Category 1")
    cat2 = create_category(db_session, name="Category 2")

    article = create_article(
        db_session,
        title="Original Title",
        content_type=CONTENT_TYPE_FAQ,
        body="Original body text",
        category_id=cat1.id,
        tenant_id="tenant-100",
    )

    updated = update_article(
        db_session,
        article_id=article.id,
        title="Updated Title",
        category_id=cat2.id,
        body="Updated body content",
        tenant_scope_check="tenant-100",
    )

    assert updated.title == "Updated Title"
    assert updated.category_id == cat2.id
    assert updated.body == "Updated body content"

    # Delete article
    deleted_ok = delete_article(db_session, article_id=article.id, tenant_id="tenant-100")
    assert deleted_ok is True

    with pytest.raises(ArticleNotFoundError):
        get_article_by_id(db_session, article.id, tenant_id="tenant-100")


def test_article_service_class(db_session: Session):
    """Test ArticleService class method delegation."""
    service = ArticleService(db=db_session)

    cat = service.create_category(name="Training")
    tag = service.create_tag(name="Video")

    art = service.create_article(
        title="Video Onboarding Module",
        content_type=CONTENT_TYPE_TRAINING_VIDEO,
        body="Watch training video",
        category_id=cat.id,
        tag_ids=[tag.id],
        video_url="https://cdn.example.com/embed/vid-101",
    )

    fetched = service.get_article_by_id(art.id)
    assert fetched.title == "Video Onboarding Module"

    results, total = service.search_articles(query="Video")
    assert total == 1
    assert results[0].id == art.id

    assert len(service.list_categories()) == 1
    assert len(service.list_tags()) == 1


# --- Playbook Service Tests ---


def test_playbook_steps_and_progression(db_session: Session):
    """Test creating playbook steps, retrieving ordered steps, and toggling progress."""
    cat = create_category(db_session, name="Operations")
    art = create_article(
        db_session,
        title="Incident Escalation Playbook",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Steps to handle incident escalations.",
        category_id=cat.id,
        tenant_id="tenant-alpha",
        playbook_steps=[
            {
                "step_number": 1,
                "title": "Acknowledge Alert",
                "content": "Acknowledge incoming alert in PagerDuty.",
                "checkpoint_instructions": "Verify alert status is 'Acknowledged'.",
            },
            {
                "step_number": 2,
                "title": "Triage Severity",
                "content": "Determine impact and assign severity level.",
            },
            {
                "step_number": 3,
                "title": "Notify Stakeholders",
                "content": "Post update to incident channel.",
            },
        ],
    )

    steps = get_playbook_steps(db_session, article_id=art.id, tenant_id="tenant-alpha")
    assert len(steps) == 3
    assert [s.step_number for s in steps] == [1, 2, 3]

    # Toggle progress for step 1
    step1 = steps[0]
    res1 = toggle_step_progress(
        db_session,
        article_id=art.id,
        step_id=step1.id,
        user_id="user-101",
        tenant_id="tenant-alpha",
        is_completed=True,
    )
    assert res1["is_completed"] is True
    assert res1["overall_playbook_progress_percentage"] == 33.33
    assert res1["completed_steps"] == 1
    assert res1["total_steps"] == 3

    # Check next uncompleted step (should be step 2)
    next_step = get_next_uncompleted_step(
        db_session, article_id=art.id, user_id="user-101", tenant_id="tenant-alpha"
    )
    assert next_step is not None
    assert next_step.step_number == 2

    # Toggle progress for step 2 and step 3
    step2, step3 = steps[1], steps[2]
    toggle_step_progress(
        db_session,
        article_id=art.id,
        step_id=step2.id,
        user_id="user-101",
        tenant_id="tenant-alpha",
        is_completed=True,
    )
    res3 = toggle_step_progress(
        db_session,
        article_id=art.id,
        step_id=step3.id,
        user_id="user-101",
        tenant_id="tenant-alpha",
        is_completed=True,
    )
    assert res3["is_completed"] is True
    assert res3["overall_playbook_progress_percentage"] == 100.0
    assert res3["completed_steps"] == 3

    # Next uncompleted step should be None now
    assert (
        get_next_uncompleted_step(
            db_session, article_id=art.id, user_id="user-101", tenant_id="tenant-alpha"
        )
        is None
    )

    # Get overall progress summary
    summary = get_playbook_progress(
        db_session, article_id=art.id, user_id="user-101", tenant_id="tenant-alpha"
    )
    assert summary["progress_percentage"] == 100.0
    assert summary["completed_steps"] == 3
    assert len(summary["completed_step_ids"]) == 3
    assert len(summary["step_progress"]) == 3

    # Uncomplete step 2
    res_uncomp = toggle_step_progress(
        db_session,
        article_id=art.id,
        step_id=step2.id,
        user_id="user-101",
        tenant_id="tenant-alpha",
        is_completed=False,
    )
    assert res_uncomp["is_completed"] is False
    assert res_uncomp["overall_playbook_progress_percentage"] == 66.67

    # Reset progress
    reset_res = reset_playbook_progress(
        db_session, article_id=art.id, user_id="user-101", tenant_id="tenant-alpha"
    )
    assert reset_res["progress_percentage"] == 0.0
    assert reset_res["completed_steps"] == 0
    assert len(reset_res["completed_step_ids"]) == 0


def test_playbook_service_error_conditions(db_session: Session):
    """Test error handling and validation in playbook service."""
    cat = create_category(db_session, name="Support")
    playbook = create_article(
        db_session,
        title="Client Onboarding Playbook",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Playbook body",
        tenant_id="tenant-1",
        playbook_steps=[{"step_number": 1, "title": "Setup", "content": "Initial setup"}],
    )
    faq = create_article(
        db_session,
        title="General FAQ",
        content_type=CONTENT_TYPE_FAQ,
        body="FAQ body",
        tenant_id="tenant-1",
    )

    step = playbook.playbook_steps[0]

    # Non-existent playbook
    with pytest.raises(PlaybookNotFoundError):
        get_playbook_steps(db_session, article_id=uuid.uuid4(), tenant_id="tenant-1")

    # Non-playbook content type (FAQ)
    with pytest.raises(PlaybookNotFoundError):
        get_playbook_steps(db_session, article_id=faq.id, tenant_id="tenant-1")

    # Tenant boundary mismatch
    with pytest.raises(PlaybookNotFoundError):
        get_playbook_steps(db_session, article_id=playbook.id, tenant_id="tenant-other")

    # Invalid UUID format
    with pytest.raises(InvalidPlaybookError):
        get_playbook_steps(db_session, article_id="not-a-uuid", tenant_id="tenant-1")

    # Invalid user_id or tenant_id
    with pytest.raises(InvalidPlaybookError):
        toggle_step_progress(
            db_session,
            article_id=playbook.id,
            step_id=step.id,
            user_id="",
            tenant_id="tenant-1",
        )

    with pytest.raises(InvalidPlaybookError):
        toggle_step_progress(
            db_session,
            article_id=playbook.id,
            step_id=step.id,
            user_id="user-1",
            tenant_id="   ",
        )

    # Step not belonging to playbook (create second playbook)
    playbook2 = create_article(
        db_session,
        title="Second Playbook",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Body",
        tenant_id="tenant-1",
        playbook_steps=[{"step_number": 1, "title": "P2 Step", "content": "P2 Content"}],
    )
    step2 = playbook2.playbook_steps[0]

    with pytest.raises(InvalidStepReferenceError):
        toggle_step_progress(
            db_session,
            article_id=playbook.id,
            step_id=step2.id,
            user_id="user-1",
            tenant_id="tenant-1",
        )


def test_playbook_step_crud_and_service_class(db_session: Session):
    """Test step creation, updating, deletion, and PlaybookService class wrapper."""
    playbook = create_article(
        db_session,
        title="API Integration Playbook",
        content_type=CONTENT_TYPE_PLAYBOOK,
        body="Playbook for integrating APIs.",
        tenant_id="tenant-dev",
    )

    service = PlaybookService(db=db_session)

    # Create steps via service
    s1 = service.create_playbook_step(
        article_id=playbook.id,
        step_number=1,
        title="Generate Credentials",
        content="Request API credentials from portal.",
        checkpoint_instructions="Verify API key is generated.",
        tenant_id="tenant-dev",
    )
    assert s1.id is not None
    assert s1.step_number == 1

    s2 = service.create_playbook_step(
        article_id=playbook.id,
        step_number=2,
        title="Configure Webhooks",
        content="Set webhook URL in settings.",
        tenant_id="tenant-dev",
    )
    assert s2.step_number == 2

    # Duplicate step number error
    with pytest.raises(InvalidPlaybookError):
        service.create_playbook_step(
            article_id=playbook.id,
            step_number=1,
            title="Duplicate Step",
            content="Content",
            tenant_id="tenant-dev",
        )

    # Update step
    updated = service.update_playbook_step(
        article_id=playbook.id,
        step_id=s1.id,
        title="Generate OAuth Credentials",
        tenant_id="tenant-dev",
    )
    assert updated.title == "Generate OAuth Credentials"

    # Toggle progress via class wrapper
    p1 = service.toggle_step_progress(
        article_id=playbook.id,
        step_id=s1.id,
        user_id="dev-user",
        tenant_id="tenant-dev",
        is_completed=True,
    )
    assert p1["is_completed"] is True
    assert p1["overall_playbook_progress_percentage"] == 50.0

    # Get step by id via class wrapper
    fetched_step = service.get_playbook_step_by_id(
        article_id=playbook.id, step_id=s1.id, tenant_id="tenant-dev"
    )
    assert fetched_step.title == "Generate OAuth Credentials"

    # Delete step 2
    deleted = service.delete_playbook_step(
        article_id=playbook.id, step_id=s2.id, tenant_id="tenant-dev"
    )
    assert deleted is True

    # Progress percentage should now be 100% since only step 1 remains
    prog = service.get_playbook_progress(
        article_id=playbook.id, user_id="dev-user", tenant_id="tenant-dev"
    )
    assert prog["total_steps"] == 1
    assert prog["progress_percentage"] == 100.0
