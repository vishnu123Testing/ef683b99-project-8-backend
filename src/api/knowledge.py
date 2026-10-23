"""FastAPI REST endpoints for knowledge repository search, cataloging, playbook progress tracking, and onboarding resources [FR-1, FR-2, FR-7, FR-8, FR-9, NFR-1, NFR-3, NFR-4]."""

from datetime import datetime
import logging
from typing import Any, List, Optional
import uuid

from fastapi import APIRouter, Body, Depends, HTTPException, Path, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.lib.logging import get_correlation_id
from src.models.database import get_db
from src.services.knowledge.article_service import (
    ArticleNotFoundError,
    ArticleServiceError,
    CategoryNotFoundError,
    InvalidArticleError,
    TagNotFoundError,
    delete_article,
    get_article_by_id,
    search_articles,
)
from src.services.knowledge.playbook_service import (
    InvalidPlaybookError,
    InvalidStepReferenceError,
    PlaybookNotFoundError,
    PlaybookServiceError,
    StepNotFoundError,
    get_playbook_progress,
    toggle_step_progress,
)
from src.services.knowledge.ports import (
    StreamingTokenError,
    VideoNotFoundError,
    VideoProviderError,
    VideoUnavailableError,
)

logger = logging.getLogger(__name__)

knowledge_router = APIRouter(prefix="/knowledge", tags=["Knowledge Repository"])
router = knowledge_router

ALLOWED_CONTENT_TYPES = {"FAQ", "Playbook", "BestPractice", "TrainingVideo"}
VALID_CONTENT_TYPES_MAP = {ct.lower(): ct for ct in ALLOWED_CONTENT_TYPES}


class CategoryResponseSchema(BaseModel):
    """Schema for category details in article response [FR-1, FR-7]."""

    id: uuid.UUID
    name: str
    description: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class TagResponseSchema(BaseModel):
    """Schema for tag details in article response [FR-1, FR-7]."""

    id: uuid.UUID
    name: str

    model_config = ConfigDict(from_attributes=True)


class ArticleCardSchema(BaseModel):
    """Schema representing a knowledge repository article card summary [FR-1, FR-2, FR-7]."""

    id: uuid.UUID
    title: str
    content_type: str
    body: Optional[str] = None
    tenant_id: Optional[str] = None
    category_id: Optional[uuid.UUID] = None
    category: Optional[CategoryResponseSchema] = None
    tags: List[TagResponseSchema] = Field(default_factory=list)
    video_url: Optional[str] = None
    is_published: bool = True
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ArticleListResponseSchema(BaseModel):
    """Schema for paginated search and catalog article list [FR-1, FR-2, FR-7, FR-8]."""

    items: List[ArticleCardSchema] = Field(default_factory=list, description="Matching knowledge article records")
    total_count: int = Field(..., ge=0, description="Total count of matching articles")
    page: int = Field(..., ge=1, description="Current page number (1-based)")
    limit: int = Field(..., ge=1, description="Max items per page")
    total_pages: int = Field(..., ge=0, description="Total available pages")


class AttachmentResponseSchema(BaseModel):
    """Schema for document attachments linked to a knowledge article [FR-6]."""

    id: uuid.UUID
    article_id: uuid.UUID
    file_name: str
    file_url: str
    file_size_bytes: int
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class PlaybookStepResponseSchema(BaseModel):
    """Schema for sequential playbook step details [FR-3, FR-4]."""

    id: uuid.UUID
    article_id: uuid.UUID
    step_number: int
    title: str
    content: str
    checkpoint_instructions: Optional[str] = None

    model_config = ConfigDict(from_attributes=True)


class ArticleDetailResponseSchema(ArticleCardSchema):
    """Schema representing detailed knowledge repository article content with attachments and playbook steps [FR-1, FR-3, FR-5, FR-6]."""

    attachments: List[AttachmentResponseSchema] = Field(default_factory=list)
    playbook_steps: List[PlaybookStepResponseSchema] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class StepProgressToggleRequestSchema(BaseModel):
    """Request schema for toggling playbook step completion progress [FR-4, NFR-3]."""

    is_completed: bool = Field(True, description="Boolean completion state indicator")
    user_id: Optional[str] = Field(None, description="Optional user identifier (defaults to 'default-user' if unauthenticated)")
    tenant_id: Optional[str] = Field(None, description="Optional caller tenant ID boundary filter")


class StepProgressResponseSchema(BaseModel):
    """Response schema for playbook step progress completion toggle [FR-3, FR-4, NFR-3]."""

    step_id: uuid.UUID
    article_id: uuid.UUID
    user_id: str
    tenant_id: str
    is_completed: bool
    completed_at: Optional[datetime] = None
    overall_playbook_progress_percentage: float = Field(..., ge=0.0, le=100.0)
    total_steps: int = Field(..., ge=0)
    completed_steps: int = Field(..., ge=0)

    model_config = ConfigDict(from_attributes=True)


class StepProgressItemSchema(BaseModel):
    """Schema for individual step progress item in playbook overall progress response [FR-3, FR-4]."""

    step_id: uuid.UUID
    step_number: int
    title: str
    is_completed: bool
    completed_at: Optional[datetime] = None

    model_config = ConfigDict(from_attributes=True)


class PlaybookProgressResponseSchema(BaseModel):
    """Schema for overall playbook progress summary [FR-3, FR-4, NFR-3]."""

    article_id: uuid.UUID
    user_id: str
    tenant_id: str
    total_steps: int = Field(..., ge=0)
    completed_steps: int = Field(..., ge=0)
    progress_percentage: float = Field(..., ge=0.0, le=100.0)
    completed_step_ids: List[uuid.UUID] = Field(default_factory=list)
    step_progress: List[StepProgressItemSchema] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


@knowledge_router.get(
    "/articles",
    response_model=ArticleListResponseSchema,
    summary="Search and catalog knowledge repository articles",
    description="Search and filter knowledge articles across titles, body text, categories, and tags with pagination and tenant isolation [FR-1, FR-2, FR-7, FR-8, FR-9, NFR-1, NFR-3].",
)
def list_knowledge_articles(
    query: Optional[str] = Query(None, description="Keyword search string across title, body, category, and tags"),
    category_id: Optional[str] = Query(None, description="Optional category UUID filter"),
    tag_ids: Optional[List[str]] = Query(None, description="Optional list or comma-separated tag UUID filters"),
    content_type: Optional[str] = Query(None, description="Optional content type filter ('FAQ', 'Playbook', 'BestPractice', 'TrainingVideo')"),
    tenant_id: Optional[str] = Query(None, description="Optional caller tenant boundary filter"),
    is_published: Optional[bool] = Query(True, description="Filter by article publication status"),
    page: int = Query(1, ge=1, description="1-based page index"),
    limit: int = Query(20, ge=1, le=100, description="Max items per page (1 to 100)"),
    db: Session = Depends(get_db),
) -> ArticleListResponseSchema:
    """Search and filter knowledge articles with input validation and tenant boundary checks [FR-1, FR-2, FR-7, FR-8, FR-9]."""
    correlation_id = get_correlation_id()
    logger.info(
        "GET /api/v1/knowledge/articles requested with query=%r, category_id=%s, content_type=%s, tenant_id=%s, page=%d, limit=%d [correlation_id=%s]",
        query,
        category_id,
        content_type,
        tenant_id,
        page,
        limit,
        correlation_id,
        extra={"correlation_id": correlation_id},
    )

    # 1. Sanitize string inputs
    query_clean = query.strip() if (query and query.strip()) else None
    cat_id_clean = category_id.strip() if (category_id and category_id.strip()) else None
    tenant_id_clean = tenant_id.strip() if (tenant_id and tenant_id.strip()) else None

    # 2. Parse tag_ids list / comma-separated strings
    clean_tag_ids: Optional[List[str]] = None
    if tag_ids:
        raw_tags: List[str] = []
        for tag_val in tag_ids:
            if isinstance(tag_val, str):
                for sub_tag in tag_val.split(","):
                    sub_clean = sub_tag.strip()
                    if sub_clean:
                        raw_tags.append(sub_clean)
        if raw_tags:
            clean_tag_ids = raw_tags

    # 3. Validate content_type
    clean_content_type: Optional[str] = None
    if content_type and content_type.strip():
        ct_raw = content_type.strip()
        if ct_raw.lower() in VALID_CONTENT_TYPES_MAP:
            clean_content_type = VALID_CONTENT_TYPES_MAP[ct_raw.lower()]
        else:
            msg = f"Invalid content_type '{content_type}'. Must be one of: {', '.join(sorted(list(ALLOWED_CONTENT_TYPES)))}"
            logger.warning(
                "Invalid content_type validation failed: %s [correlation_id=%s]",
                msg,
                correlation_id,
                extra={"correlation_id": correlation_id},
            )
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=msg,
            )

    # 4. Execute domain search
    try:
        articles, total_count = search_articles(
            db=db,
            query=query_clean,
            category_id=cat_id_clean,
            tag_ids=clean_tag_ids,
            content_type=clean_content_type,
            tenant_id=tenant_id_clean,
            is_published=is_published,
            page=page,
            limit=limit,
        )
    except InvalidArticleError as exc:
        logger.warning(
            "Invalid article search parameter: %s [correlation_id=%s]",
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except (CategoryNotFoundError, TagNotFoundError) as exc:
        logger.warning(
            "Category or tag not found during article search: %s [correlation_id=%s]",
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except ArticleServiceError as exc:
        logger.error(
            "Article search failed due to domain error: %s [correlation_id=%s]",
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while searching knowledge articles.",
        )
    except Exception as exc:
        logger.exception(
            "Unhandled exception in GET /articles: %s [correlation_id=%s]",
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while searching knowledge articles.",
        )

    # 5. Compute total pages and construct response
    total_pages = (total_count + limit - 1) // limit if limit > 0 else 1
    if total_count == 0:
        total_pages = 0

    logger.info(
        "GET /api/v1/knowledge/articles succeeded: returned %d articles (total_count=%d, page=%d, total_pages=%d) [correlation_id=%s]",
        len(articles),
        total_count,
        page,
        total_pages,
        correlation_id,
        extra={"correlation_id": correlation_id},
    )

    return ArticleListResponseSchema(
        items=articles,
        total_count=total_count,
        page=page,
        limit=limit,
        total_pages=total_pages,
    )


@knowledge_router.get(
    "/articles/{id}",
    response_model=ArticleDetailResponseSchema,
    summary="Get detailed knowledge repository article by ID",
    description="Retrieve full knowledge article details including category, tags, attachments, ordered playbook steps, and video URL with tenant isolation [FR-1, FR-3, FR-5, FR-6, FR-9, NFR-1, NFR-3].",
)
def get_knowledge_article_detail(
    id: str = Path(..., description="Unique knowledge article UUID identifier"),
    tenant_id: Optional[str] = Query(None, description="Optional caller tenant boundary filter"),
    db: Session = Depends(get_db),
) -> ArticleDetailResponseSchema:
    """Retrieve detailed knowledge article record by ID with multi-tenant access filtering [FR-1, FR-3, FR-5, FR-6, FR-9]."""
    correlation_id = get_correlation_id()
    logger.info(
        "GET /api/v1/knowledge/articles/%s requested with tenant_id=%s [correlation_id=%s]",
        id,
        tenant_id,
        correlation_id,
        extra={"correlation_id": correlation_id},
    )

    tenant_id_clean = tenant_id.strip() if (tenant_id and tenant_id.strip()) else None

    try:
        article = get_article_by_id(
            db=db,
            article_id=id,
            tenant_id=tenant_id_clean,
        )
    except InvalidArticleError as exc:
        logger.warning(
            "Invalid article ID parameter for GET /articles/%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except ArticleNotFoundError as exc:
        logger.warning(
            "Article not found for GET /articles/%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except VideoNotFoundError as exc:
        logger.warning(
            "Video metadata not found for article id=%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except VideoUnavailableError as exc:
        logger.warning(
            "Video unavailable for article id=%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except (VideoProviderError, StreamingTokenError) as exc:
        logger.error(
            "Video provider error for article id=%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while communicating with video provider.",
        )
    except ArticleServiceError as exc:
        logger.error(
            "Failed to retrieve article id=%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving knowledge article detail.",
        )
    except Exception as exc:
        logger.exception(
            "Unhandled exception in GET /articles/%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while retrieving knowledge article detail.",
        )

    logger.info(
        "GET /api/v1/knowledge/articles/%s succeeded: title=%r, content_type=%s [correlation_id=%s]",
        id,
        getattr(article, "title", None),
        getattr(article, "content_type", None),
        correlation_id,
        extra={"correlation_id": correlation_id},
    )

    return article


@knowledge_router.post(
    "/playbooks/{id}/steps/{step_id}/progress",
    response_model=StepProgressResponseSchema,
    summary="Toggle playbook step completion progress",
    description="Mark or toggle an individual step in an operational playbook as completed/uncompleted and persist completion state [FR-3, FR-4, FR-8, FR-9, NFR-3, NFR-4].",
)
def toggle_playbook_step_progress(
    id: str = Path(..., description="Unique parent playbook article UUID identifier"),
    step_id: str = Path(..., description="Unique playbook step UUID identifier"),
    payload: Optional[StepProgressToggleRequestSchema] = Body(None),
    user_id: Optional[str] = Query(None, description="Optional user identifier query parameter override"),
    tenant_id: Optional[str] = Query(None, description="Optional caller tenant boundary filter override"),
    db: Session = Depends(get_db),
) -> StepProgressResponseSchema:
    """Toggle playbook step completion state and return updated user completion metrics [FR-3, FR-4, FR-8, FR-9]."""
    correlation_id = get_correlation_id()
    logger.info(
        "POST /api/v1/knowledge/playbooks/%s/steps/%s/progress requested with user_id=%s, tenant_id=%s [correlation_id=%s]",
        id,
        step_id,
        user_id,
        tenant_id,
        correlation_id,
        extra={"correlation_id": correlation_id},
    )

    is_completed_val = True
    if payload is not None:
        is_completed_val = payload.is_completed

    # Resolve user_id and tenant_id from query params or body or default fallback
    clean_user_id = (
        user_id.strip() if (user_id and user_id.strip())
        else (payload.user_id.strip() if (payload and payload.user_id and payload.user_id.strip()) else "default-user")
    )
    clean_tenant_id = (
        tenant_id.strip() if (tenant_id and tenant_id.strip())
        else (payload.tenant_id.strip() if (payload and payload.tenant_id and payload.tenant_id.strip()) else "default-tenant")
    )

    try:
        result = toggle_step_progress(
            db=db,
            article_id=id,
            step_id=step_id,
            user_id=clean_user_id,
            tenant_id=clean_tenant_id,
            is_completed=is_completed_val,
        )
    except InvalidPlaybookError as exc:
        logger.warning(
            "Invalid playbook request for article_id=%s, step_id=%s: %s [correlation_id=%s]",
            id,
            step_id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except (InvalidStepReferenceError, StepNotFoundError) as exc:
        logger.warning(
            "Step reference error or step not found article_id=%s, step_id=%s: %s [correlation_id=%s]",
            id,
            step_id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except PlaybookNotFoundError as exc:
        logger.warning(
            "Playbook article not found for article_id=%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except PlaybookServiceError as exc:
        logger.error(
            "Failed to toggle step progress for article_id=%s, step_id=%s: %s [correlation_id=%s]",
            id,
            step_id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while updating playbook step progress.",
        )
    except Exception as exc:
        logger.exception(
            "Unhandled exception in POST /playbooks/%s/steps/%s/progress: %s [correlation_id=%s]",
            id,
            step_id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while updating playbook step progress.",
        )

    logger.info(
        "POST /playbooks/%s/steps/%s/progress succeeded for user_id=%s: is_completed=%s, progress=%.1f%% [correlation_id=%s]",
        id,
        step_id,
        clean_user_id,
        is_completed_val,
        result.get("overall_playbook_progress_percentage", 0.0),
        correlation_id,
        extra={"correlation_id": correlation_id},
    )

    return StepProgressResponseSchema(**result)


@knowledge_router.get(
    "/playbooks/{id}/progress",
    response_model=PlaybookProgressResponseSchema,
    summary="Get playbook user overall progress summary",
    description="Retrieve comprehensive user step completion progress, completed step IDs, and percentage completion for an operational playbook [FR-3, FR-4, FR-8, FR-9, NFR-3, NFR-4].",
)
def get_playbook_user_overall_progress(
    id: str = Path(..., description="Unique parent playbook article UUID identifier"),
    user_id: Optional[str] = Query(None, description="Optional user identifier (defaults to 'default-user' if unauthenticated)"),
    tenant_id: Optional[str] = Query(None, description="Optional caller tenant boundary filter"),
    db: Session = Depends(get_db),
) -> PlaybookProgressResponseSchema:
    """Retrieve overall playbook progress summary for a user and tenant [FR-3, FR-4, FR-8, FR-9]."""
    correlation_id = get_correlation_id()
    logger.info(
        "GET /api/v1/knowledge/playbooks/%s/progress requested with user_id=%s, tenant_id=%s [correlation_id=%s]",
        id,
        user_id,
        tenant_id,
        correlation_id,
        extra={"correlation_id": correlation_id},
    )

    clean_user_id = user_id.strip() if (user_id and user_id.strip()) else "default-user"
    clean_tenant_id = tenant_id.strip() if (tenant_id and tenant_id.strip()) else "default-tenant"

    try:
        result = get_playbook_progress(
            db=db,
            article_id=id,
            user_id=clean_user_id,
            tenant_id=clean_tenant_id,
        )
    except InvalidPlaybookError as exc:
        logger.warning(
            "Invalid playbook request for article_id=%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except PlaybookNotFoundError as exc:
        logger.warning(
            "Playbook article not found for progress query article_id=%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except PlaybookServiceError as exc:
        logger.error(
            "Failed to retrieve playbook progress for article_id=%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while retrieving playbook progress.",
        )
    except Exception as exc:
        logger.exception(
            "Unhandled exception in GET /playbooks/%s/progress: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while retrieving playbook progress.",
        )

    logger.info(
        "GET /playbooks/%s/progress succeeded for user_id=%s: progress=%.1f%% (%d/%d steps) [correlation_id=%s]",
        id,
        clean_user_id,
        result.get("progress_percentage", 0.0),
        result.get("completed_steps", 0),
        result.get("total_steps", 0),
        correlation_id,
        extra={"correlation_id": correlation_id},
    )

    return PlaybookProgressResponseSchema(**result)


@knowledge_router.delete(
    "/articles/{id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete knowledge repository article resource",
    description="Delete a knowledge repository article record by ID with multi-tenant access boundary verification [FR-1, FR-9, NFR-3, NFR-4].",
)
def delete_knowledge_article(
    id: str = Path(..., description="Unique knowledge article UUID identifier"),
    tenant_id: Optional[str] = Query(None, description="Optional caller tenant boundary filter"),
    db: Session = Depends(get_db),
) -> Response:
    """Delete a knowledge repository article record by ID [FR-1, FR-9]."""
    correlation_id = get_correlation_id()
    logger.info(
        "DELETE /api/v1/knowledge/articles/%s requested with tenant_id=%s [correlation_id=%s]",
        id,
        tenant_id,
        correlation_id,
        extra={"correlation_id": correlation_id},
    )

    tenant_id_clean = tenant_id.strip() if (tenant_id and tenant_id.strip()) else None

    try:
        delete_article(
            db=db,
            article_id=id,
            tenant_id=tenant_id_clean,
        )
    except InvalidArticleError as exc:
        logger.warning(
            "Invalid article parameter for DELETE /articles/%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        )
    except ArticleNotFoundError as exc:
        logger.warning(
            "Article not found for DELETE /articles/%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        )
    except ArticleServiceError as exc:
        logger.error(
            "Failed to delete article id=%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An error occurred while deleting the knowledge article.",
        )
    except Exception as exc:
        logger.exception(
            "Unhandled exception in DELETE /articles/%s: %s [correlation_id=%s]",
            id,
            exc,
            correlation_id,
            extra={"correlation_id": correlation_id},
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="An internal server error occurred while deleting the knowledge article.",
        )

    logger.info(
        "DELETE /api/v1/knowledge/articles/%s succeeded [correlation_id=%s]",
        id,
        correlation_id,
        extra={"correlation_id": correlation_id},
    )

    return Response(status_code=status.HTTP_204_NO_CONTENT)
