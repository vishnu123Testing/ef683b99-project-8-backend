"""Domain service for knowledge repository article search, filtering, and detail retrieval [FR-1, FR-2, FR-6, FR-7, FR-8, FR-9, NFR-1, NFR-3, NFR-4].

Provides core business rules and persistence operations for:
- Full-text search across titles, body content, tags, and category metadata [FR-2]
- Content filtering by category, tag set, content type, publishing state, and tenant boundary [FR-1, FR-7, FR-9]
- Article detail retrieval with category, tags, attachments, ordered playbook steps, and media URLs [FR-1, FR-3, FR-5, FR-6]
- Article lifecycle management (creation, update, soft/hard deletion)
- Category and functional tag management helpers
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from src.lib.logging import get_correlation_id
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
)
from src.services.knowledge.ports import VideoProviderPort

logger = logging.getLogger(__name__)


class ArticleServiceError(Exception):
    """Base exception class for knowledge article service operations."""

    pass


class ArticleNotFoundError(ArticleServiceError):
    """Raised when a requested knowledge article is not found or inaccessible."""

    pass


class CategoryNotFoundError(ArticleServiceError):
    """Raised when a specified category is not found."""

    pass


class TagNotFoundError(ArticleServiceError):
    """Raised when a specified tag is not found."""

    pass


class InvalidArticleError(ArticleServiceError):
    """Raised when article payload or query filter parameters fail validation."""

    pass


def _parse_uuid(val: Optional[Union[uuid.UUID, str]], field_name: str = "id") -> Optional[uuid.UUID]:
    """Safely parse UUID object or string representation.

    Args:
        val: UUID instance or UUID string.
        field_name: Name of the field for error reporting.

    Returns:
        Parsed uuid.UUID or None if input is None.

    Raises:
        InvalidArticleError: If val is an invalid string format.
    """
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError, AttributeError) as err:
        raise InvalidArticleError(f"Invalid UUID format for {field_name}: '{val}'") from err


def search_articles(
    db: Session,
    query: Optional[str] = None,
    category_id: Optional[Union[uuid.UUID, str]] = None,
    tag_ids: Optional[List[Union[uuid.UUID, str]]] = None,
    content_type: Optional[str] = None,
    tenant_id: Optional[str] = None,
    is_published: Optional[bool] = True,
    page: int = 1,
    limit: int = 20,
) -> Tuple[List[KnowledgeArticle], int]:
    """Search and filter knowledge repository articles [FR-1, FR-2, FR-7, FR-8, FR-9].

    Executes full-text/keyword search across article title, body, category name,
    and tag names while applying category, tag, content type, publishing state,
    and multi-tenant filters.

    Args:
        db: Synchronous SQLAlchemy database session.
        query: Optional search keyword/phrase.
        category_id: Optional category filter ID.
        tag_ids: Optional list of tag filter IDs.
        content_type: Optional content type filter (FAQ, Playbook, BestPractice, TrainingVideo).
        tenant_id: Caller's tenant identifier for access isolation [FR-9].
        is_published: Filtering state for article publication status (default True).
        page: Page index (1-based, default 1).
        limit: Max items per page (default 20).

    Returns:
        Tuple containing paginated list of KnowledgeArticle entities and total matching count.
    """
    correlation_id = get_correlation_id()
    logger.debug(
        "Executing article search with query=%r, category_id=%s, content_type=%s, tenant_id=%s [correlation_id=%s]",
        query,
        category_id,
        content_type,
        tenant_id,
        correlation_id,
    )

    page_num = max(1, page)
    page_limit = max(1, min(100, limit))

    stmt = db.query(KnowledgeArticle)

    # 1. Publication status filter
    if is_published is not None:
        stmt = stmt.filter(KnowledgeArticle.is_published == is_published)

    # 2. Multi-tenant access boundary filter [FR-9]
    if tenant_id is not None:
        stmt = stmt.filter(
            or_(
                KnowledgeArticle.tenant_id == tenant_id,
                KnowledgeArticle.tenant_id.is_(None),
            )
        )

    # 3. Content type filter [FR-7]
    if content_type and content_type.strip():
        stmt = stmt.filter(KnowledgeArticle.content_type == content_type.strip())

    # 4. Category filter
    if category_id is not None:
        parsed_cat_id = _parse_uuid(category_id, field_name="category_id")
        stmt = stmt.filter(KnowledgeArticle.category_id == parsed_cat_id)

    # 5. Tag set filter
    if tag_ids:
        parsed_tag_ids = [_parse_uuid(t, field_name="tag_ids") for t in tag_ids if t is not None]
        parsed_tag_ids = [t for t in parsed_tag_ids if t is not None]
        if parsed_tag_ids:
            stmt = stmt.join(KnowledgeArticle.tags).filter(Tag.id.in_(parsed_tag_ids))

    # 6. Keyword / Full-text search filter across title, body, category, and tags [FR-2]
    if query and query.strip():
        q_pattern = f"%{query.strip()}%"
        stmt = stmt.outerjoin(KnowledgeArticle.category).outerjoin(KnowledgeArticle.tags)
        stmt = stmt.filter(
            or_(
                KnowledgeArticle.title.ilike(q_pattern),
                KnowledgeArticle.body.ilike(q_pattern),
                Category.name.ilike(q_pattern),
                Tag.name.ilike(q_pattern),
            )
        )

    # Compute total distinct matching count
    count_subq = stmt.distinct().subquery()
    total_count = db.query(func.count(count_subq.c.id)).scalar() or 0

    # Paginate distinct results
    offset_val = (page_num - 1) * page_limit
    articles = (
        stmt.distinct()
        .order_by(KnowledgeArticle.updated_at.desc(), KnowledgeArticle.created_at.desc())
        .offset(offset_val)
        .limit(page_limit)
        .all()
    )

    logger.info(
        "Article search completed: found %d total articles (page %d, limit %d) [correlation_id=%s]",
        total_count,
        page_num,
        page_limit,
        correlation_id,
    )
    return articles, total_count


def get_article_by_id(
    db: Session,
    article_id: Union[uuid.UUID, str],
    tenant_id: Optional[str] = None,
) -> KnowledgeArticle:
    """Retrieve detailed knowledge article record by ID [FR-1, FR-3, FR-5, FR-6, FR-9].

    Validates existence, tenant visibility boundaries, and loads related entities.

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: Target article UUID or UUID string.
        tenant_id: Caller's tenant identifier for boundary check [FR-9].

    Returns:
        KnowledgeArticle entity with relationships populated.

    Raises:
        ArticleNotFoundError: If the article does not exist or fails tenant visibility check.
    """
    parsed_id = _parse_uuid(article_id, field_name="article_id")
    if parsed_id is None:
        raise ArticleNotFoundError(f"Article with id '{article_id}' not found.")

    article = (
        db.query(KnowledgeArticle)
        .options(
            joinedload(KnowledgeArticle.category),
            joinedload(KnowledgeArticle.tags),
            joinedload(KnowledgeArticle.attachments),
            joinedload(KnowledgeArticle.playbook_steps),
        )
        .filter(KnowledgeArticle.id == parsed_id)
        .first()
    )

    if not article:
        logger.warning("Article id=%s not found", parsed_id)
        raise ArticleNotFoundError(f"Article with id '{article_id}' not found.")

    # Tenant access boundary verification [FR-9]
    if tenant_id is not None and article.tenant_id is not None and article.tenant_id != tenant_id:
        logger.warning(
            "Access denied to article id=%s for tenant_id=%s (article tenant_id=%s)",
            parsed_id,
            tenant_id,
            article.tenant_id,
        )
        raise ArticleNotFoundError(f"Article with id '{article_id}' not found.")

    return article


def create_article(
    db: Session,
    title: str,
    content_type: str,
    body: str,
    category_id: Optional[Union[uuid.UUID, str]] = None,
    tag_ids: Optional[List[Union[uuid.UUID, str]]] = None,
    video_url: Optional[str] = None,
    tenant_id: Optional[str] = None,
    is_published: bool = True,
    playbook_steps: Optional[List[Dict[str, Any]]] = None,
    attachments: Optional[List[Dict[str, Any]]] = None,
) -> KnowledgeArticle:
    """Create and persist a new KnowledgeArticle record.

    Args:
        db: Synchronous SQLAlchemy database session.
        title: Title of the article.
        content_type: Content classification (FAQ, Playbook, BestPractice, TrainingVideo).
        body: Main markdown or rich text article body.
        category_id: Optional associated Category ID.
        tag_ids: Optional list of associated Tag IDs.
        video_url: Optional streaming video URL for training video modules.
        tenant_id: Optional tenant isolation scope (None = global).
        is_published: Publication status flag.
        playbook_steps: Optional list of step dictionaries for Playbooks.
        attachments: Optional list of document attachment dictionaries.

    Returns:
        Newly created KnowledgeArticle entity.

    Raises:
        InvalidArticleError: If required fields are missing or malformed.
        CategoryNotFoundError: If specified category ID does not exist.
        TagNotFoundError: If specified tag ID does not exist.
    """
    if not title or not title.strip():
        raise InvalidArticleError("Article title cannot be empty.")
    if not content_type or not content_type.strip():
        raise InvalidArticleError("Article content_type cannot be empty.")
    if not body or not body.strip():
        raise InvalidArticleError("Article body content cannot be empty.")

    cleaned_title = title.strip()
    cleaned_type = content_type.strip()
    cleaned_body = body.strip()

    parsed_cat_id: Optional[uuid.UUID] = None
    if category_id is not None:
        parsed_cat_id = _parse_uuid(category_id, field_name="category_id")
        category = db.query(Category).filter(Category.id == parsed_cat_id).first()
        if not category:
            raise CategoryNotFoundError(f"Category with id '{category_id}' not found.")

    tag_objects: List[Tag] = []
    if tag_ids:
        for tid in tag_ids:
            if tid is None:
                continue
            parsed_tid = _parse_uuid(tid, field_name="tag_ids")
            tag_obj = db.query(Tag).filter(Tag.id == parsed_tid).first()
            if not tag_obj:
                raise TagNotFoundError(f"Tag with id '{tid}' not found.")
            tag_objects.append(tag_obj)

    article = KnowledgeArticle(
        title=cleaned_title,
        content_type=cleaned_type,
        body=cleaned_body,
        category_id=parsed_cat_id,
        video_url=video_url.strip() if video_url else None,
        tenant_id=tenant_id.strip() if tenant_id else None,
        is_published=is_published,
    )
    if tag_objects:
        article.tags = tag_objects

    # Add playbook steps if provided
    if playbook_steps:
        for idx, step_data in enumerate(playbook_steps, start=1):
            step_num = step_data.get("step_number", idx)
            step_title = step_data.get("title", f"Step {step_num}")
            step_content = step_data.get("content", "")
            checkpoint = step_data.get("checkpoint_instructions")
            step_obj = PlaybookStep(
                step_number=step_num,
                title=step_title,
                content=step_content,
                checkpoint_instructions=checkpoint,
            )
            article.playbook_steps.append(step_obj)

    # Add attachments if provided
    if attachments:
        for att_data in attachments:
            file_name = att_data.get("file_name", "document.pdf")
            file_url = att_data.get("file_url", "#")
            file_size = att_data.get("file_size_bytes", 0)
            att_obj = Attachment(
                file_name=file_name,
                file_url=file_url,
                file_size_bytes=file_size,
            )
            article.attachments.append(att_obj)

    db.add(article)
    db.commit()
    db.refresh(article)

    logger.info("Created article id=%s title=%r content_type=%s", article.id, article.title, article.content_type)
    return article


def update_article(
    db: Session,
    article_id: Union[uuid.UUID, str],
    title: Optional[str] = None,
    content_type: Optional[str] = None,
    body: Optional[str] = None,
    category_id: Optional[Union[uuid.UUID, str]] = None,
    tag_ids: Optional[List[Union[uuid.UUID, str]]] = None,
    video_url: Optional[str] = None,
    tenant_id: Optional[str] = None,
    is_published: Optional[bool] = None,
    tenant_scope_check: Optional[str] = None,
) -> KnowledgeArticle:
    """Update an existing KnowledgeArticle record.

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: ID of the article to update.
        title: Optional updated title.
        content_type: Optional updated content classification.
        body: Optional updated body content.
        category_id: Optional updated category ID.
        tag_ids: Optional updated list of tag IDs.
        video_url: Optional updated video URL.
        tenant_id: Optional updated tenant ID.
        is_published: Optional updated publication status flag.
        tenant_scope_check: Optional caller tenant ID to verify update permission.

    Returns:
        Updated KnowledgeArticle entity.
    """
    article = get_article_by_id(db, article_id, tenant_id=tenant_scope_check)

    if title is not None:
        if not title.strip():
            raise InvalidArticleError("Article title cannot be empty.")
        article.title = title.strip()

    if content_type is not None:
        if not content_type.strip():
            raise InvalidArticleError("Article content_type cannot be empty.")
        article.content_type = content_type.strip()

    if body is not None:
        if not body.strip():
            raise InvalidArticleError("Article body content cannot be empty.")
        article.body = body.strip()

    if category_id is not None:
        parsed_cat_id = _parse_uuid(category_id, field_name="category_id")
        category = db.query(Category).filter(Category.id == parsed_cat_id).first()
        if not category:
            raise CategoryNotFoundError(f"Category with id '{category_id}' not found.")
        article.category_id = parsed_cat_id

    if tag_ids is not None:
        tag_objects: List[Tag] = []
        for tid in tag_ids:
            if tid is None:
                continue
            parsed_tid = _parse_uuid(tid, field_name="tag_ids")
            tag_obj = db.query(Tag).filter(Tag.id == parsed_tid).first()
            if not tag_obj:
                raise TagNotFoundError(f"Tag with id '{tid}' not found.")
            tag_objects.append(tag_obj)
        article.tags = tag_objects

    if video_url is not None:
        article.video_url = video_url.strip() if video_url else None

    if tenant_id is not None:
        article.tenant_id = tenant_id.strip() if tenant_id else None

    if is_published is not None:
        article.is_published = is_published

    article.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(article)

    logger.info("Updated article id=%s", article.id)
    return article


def delete_article(
    db: Session,
    article_id: Union[uuid.UUID, str],
    tenant_id: Optional[str] = None,
) -> bool:
    """Delete a KnowledgeArticle record by ID [NFR-3, NFR-4].

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: ID of the article to delete.
        tenant_id: Caller tenant ID for boundary verification.

    Returns:
        True if successfully deleted.

    Raises:
        ArticleNotFoundError: If article is not found or inaccessible.
    """
    article = get_article_by_id(db, article_id, tenant_id=tenant_id)
    db.delete(article)
    db.commit()
    logger.info("Deleted article id=%s", article_id)
    return True


# --- Category and Tag Domain Helpers ---


def list_categories(db: Session) -> List[Category]:
    """Retrieve all categories ordered by name ascending."""
    return db.query(Category).order_by(Category.name.asc()).all()


def get_category_by_id(db: Session, category_id: Union[uuid.UUID, str]) -> Category:
    """Retrieve a Category by ID or raise CategoryNotFoundError."""
    parsed_id = _parse_uuid(category_id, field_name="category_id")
    category = db.query(Category).filter(Category.id == parsed_id).first()
    if not category:
        raise CategoryNotFoundError(f"Category with id '{category_id}' not found.")
    return category


def create_category(db: Session, name: str, description: Optional[str] = None) -> Category:
    """Create and persist a new Category record."""
    if not name or not name.strip():
        raise InvalidArticleError("Category name cannot be empty.")
    category = Category(name=name.strip(), description=description.strip() if description else None)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


def list_tags(db: Session) -> List[Tag]:
    """Retrieve all tags ordered by name ascending."""
    return db.query(Tag).order_by(Tag.name.asc()).all()


def get_tag_by_id(db: Session, tag_id: Union[uuid.UUID, str]) -> Tag:
    """Retrieve a Tag by ID or raise TagNotFoundError."""
    parsed_id = _parse_uuid(tag_id, field_name="tag_id")
    tag = db.query(Tag).filter(Tag.id == parsed_id).first()
    if not tag:
        raise TagNotFoundError(f"Tag with id '{tag_id}' not found.")
    return tag


def create_tag(db: Session, name: str) -> Tag:
    """Create and persist a new Tag record."""
    if not name or not name.strip():
        raise InvalidArticleError("Tag name cannot be empty.")
    tag = Tag(name=name.strip())
    db.add(tag)
    db.commit()
    db.refresh(tag)
    return tag


# --- Domain Service Class ---


class ArticleService:
    """Domain service class encapsulating knowledge repository search, filtering, and detail operations."""

    def __init__(self, db: Session, video_provider: Optional[VideoProviderPort] = None) -> None:
        """Initialize ArticleService with a database session and optional video provider port."""
        self.db = db
        self.video_provider = video_provider

    def search_articles(
        self,
        query: Optional[str] = None,
        category_id: Optional[Union[uuid.UUID, str]] = None,
        tag_ids: Optional[List[Union[uuid.UUID, str]]] = None,
        content_type: Optional[str] = None,
        tenant_id: Optional[str] = None,
        is_published: Optional[bool] = True,
        page: int = 1,
        limit: int = 20,
    ) -> Tuple[List[KnowledgeArticle], int]:
        """Search and filter knowledge articles."""
        return search_articles(
            db=self.db,
            query=query,
            category_id=category_id,
            tag_ids=tag_ids,
            content_type=content_type,
            tenant_id=tenant_id,
            is_published=is_published,
            page=page,
            limit=limit,
        )

    def get_article_by_id(
        self,
        article_id: Union[uuid.UUID, str],
        tenant_id: Optional[str] = None,
    ) -> KnowledgeArticle:
        """Retrieve detailed article record by ID."""
        return get_article_by_id(db=self.db, article_id=article_id, tenant_id=tenant_id)

    def create_article(
        self,
        title: str,
        content_type: str,
        body: str,
        category_id: Optional[Union[uuid.UUID, str]] = None,
        tag_ids: Optional[List[Union[uuid.UUID, str]]] = None,
        video_url: Optional[str] = None,
        tenant_id: Optional[str] = None,
        is_published: bool = True,
        playbook_steps: Optional[List[Dict[str, Any]]] = None,
        attachments: Optional[List[Dict[str, Any]]] = None,
    ) -> KnowledgeArticle:
        """Create a new article."""
        return create_article(
            db=self.db,
            title=title,
            content_type=content_type,
            body=body,
            category_id=category_id,
            tag_ids=tag_ids,
            video_url=video_url,
            tenant_id=tenant_id,
            is_published=is_published,
            playbook_steps=playbook_steps,
            attachments=attachments,
        )

    def update_article(
        self,
        article_id: Union[uuid.UUID, str],
        title: Optional[str] = None,
        content_type: Optional[str] = None,
        body: Optional[str] = None,
        category_id: Optional[Union[uuid.UUID, str]] = None,
        tag_ids: Optional[List[Union[uuid.UUID, str]]] = None,
        video_url: Optional[str] = None,
        tenant_id: Optional[str] = None,
        is_published: Optional[bool] = None,
        tenant_scope_check: Optional[str] = None,
    ) -> KnowledgeArticle:
        """Update an existing article."""
        return update_article(
            db=self.db,
            article_id=article_id,
            title=title,
            content_type=content_type,
            body=body,
            category_id=category_id,
            tag_ids=tag_ids,
            video_url=video_url,
            tenant_id=tenant_id,
            is_published=is_published,
            tenant_scope_check=tenant_scope_check,
        )

    def delete_article(
        self,
        article_id: Union[uuid.UUID, str],
        tenant_id: Optional[str] = None,
    ) -> bool:
        """Delete an article."""
        return delete_article(db=self.db, article_id=article_id, tenant_id=tenant_id)

    def list_categories(self) -> List[Category]:
        """List all categories."""
        return list_categories(self.db)

    def get_category_by_id(self, category_id: Union[uuid.UUID, str]) -> Category:
        """Get category by ID."""
        return get_category_by_id(self.db, category_id)

    def create_category(self, name: str, description: Optional[str] = None) -> Category:
        """Create a category."""
        return create_category(self.db, name, description)

    def list_tags(self) -> List[Tag]:
        """List all tags."""
        return list_tags(self.db)

    def get_tag_by_id(self, tag_id: Union[uuid.UUID, str]) -> Tag:
        """Get tag by ID."""
        return get_tag_by_id(self.db, tag_id)

    def create_tag(self, name: str) -> Tag:
        """Create a tag."""
        return create_tag(self.db, name)
