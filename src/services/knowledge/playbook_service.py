"""Domain service for playbook step progression, completion toggling, and progress calculations [FR-3, FR-4, FR-8, FR-9, NFR-3, NFR-4].

Provides core business rules and persistence operations for:
- Sequential playbook step ordering and retrieval [FR-3]
- Step completion toggling and persistence per user and tenant [FR-4, FR-9]
- Playbook completion percentage progress calculation [FR-3, FR-4]
- Next uncompleted step determination and progress resets [FR-3, FR-4]
- Playbook step creation, modification, and deletion helpers
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple, Union
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.lib.logging import get_correlation_id
from src.models.knowledge import (
    CONTENT_TYPE_PLAYBOOK,
    KnowledgeArticle,
    PlaybookStep,
    UserStepProgress,
)

logger = logging.getLogger(__name__)


# --- Domain Exceptions ---


class PlaybookServiceError(Exception):
    """Base exception class for playbook service operations."""

    pass


class PlaybookNotFoundError(PlaybookServiceError):
    """Raised when a playbook article is not found or inaccessible."""

    pass


class StepNotFoundError(PlaybookServiceError):
    """Raised when a specific playbook step is not found."""

    pass


class InvalidStepReferenceError(PlaybookServiceError):
    """Raised when a step does not belong to the referenced playbook article [FR-4, FR-8]."""

    pass


class InvalidPlaybookError(PlaybookServiceError):
    """Raised when parameters or payloads fail validation [NFR-3]."""

    pass


# --- Helper Functions ---


def _parse_uuid(
    val: Optional[Union[uuid.UUID, str]],
    field_name: str = "id",
) -> Optional[uuid.UUID]:
    """Safely parse UUID object or string representation.

    Args:
        val: UUID instance or UUID string.
        field_name: Name of the field for error reporting.

    Returns:
        Parsed uuid.UUID or None if input is None.

    Raises:
        InvalidPlaybookError: If val is an invalid string format.
    """
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError, AttributeError) as err:
        raise InvalidPlaybookError(
            f"Invalid UUID format for {field_name}: '{val}'"
        ) from err


def _verify_playbook_article(
    db: Session,
    article_id: Union[uuid.UUID, str],
    tenant_id: Optional[str] = None,
) -> KnowledgeArticle:
    """Verify that a KnowledgeArticle exists, is a Playbook, and satisfies tenant access bounds.

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: Playbook article identifier.
        tenant_id: Optional caller tenant identifier for multi-tenant checks [FR-9].

    Returns:
        Verified KnowledgeArticle entity.

    Raises:
        PlaybookNotFoundError: If article does not exist, is not a playbook, or fails tenant check.
    """
    parsed_article_id = _parse_uuid(article_id, field_name="article_id")
    article = (
        db.query(KnowledgeArticle)
        .filter(KnowledgeArticle.id == parsed_article_id)
        .first()
    )

    if not article:
        raise PlaybookNotFoundError(
            f"Playbook article with id '{article_id}' not found."
        )

    if article.content_type != CONTENT_TYPE_PLAYBOOK:
        raise PlaybookNotFoundError(
            f"Article '{article_id}' is of content type '{article.content_type}', not '{CONTENT_TYPE_PLAYBOOK}'."
        )

    # Multi-tenant boundary check [FR-9]
    if (
        tenant_id is not None
        and article.tenant_id is not None
        and article.tenant_id != tenant_id
    ):
        raise PlaybookNotFoundError(
            f"Playbook article '{article_id}' is not accessible for tenant '{tenant_id}'."
        )

    return article


# --- Functional Business Operations ---


def get_playbook_steps(
    db: Session,
    article_id: Union[uuid.UUID, str],
    tenant_id: Optional[str] = None,
) -> List[PlaybookStep]:
    """Retrieve all steps for a playbook ordered by step_number ascending [FR-3].

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: Parent playbook article identifier.
        tenant_id: Optional caller tenant identifier for multi-tenant checks [FR-9].

    Returns:
        List of PlaybookStep entities ordered by step_number.

    Raises:
        PlaybookNotFoundError: If playbook is missing or inaccessible.
    """
    correlation_id = get_correlation_id()
    logger.debug(
        "Fetching playbook steps for article_id=%s, tenant_id=%s [correlation_id=%s]",
        article_id,
        tenant_id,
        correlation_id,
    )

    article = _verify_playbook_article(db, article_id=article_id, tenant_id=tenant_id)

    steps = (
        db.query(PlaybookStep)
        .filter(PlaybookStep.article_id == article.id)
        .order_by(PlaybookStep.step_number.asc())
        .all()
    )
    return steps


def get_playbook_step_by_id(
    db: Session,
    article_id: Union[uuid.UUID, str],
    step_id: Union[uuid.UUID, str],
    tenant_id: Optional[str] = None,
) -> PlaybookStep:
    """Retrieve a specific PlaybookStep and verify it belongs to the target playbook [FR-3, FR-8].

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: Parent playbook article identifier.
        step_id: Playbook step identifier.
        tenant_id: Optional caller tenant identifier [FR-9].

    Returns:
        Verified PlaybookStep entity.

    Raises:
        PlaybookNotFoundError: If playbook is missing or inaccessible.
        InvalidStepReferenceError: If step is missing or does not belong to article_id.
    """
    article = _verify_playbook_article(db, article_id=article_id, tenant_id=tenant_id)
    parsed_step_id = _parse_uuid(step_id, field_name="step_id")

    step = db.query(PlaybookStep).filter(PlaybookStep.id == parsed_step_id).first()
    if not step:
        raise InvalidStepReferenceError(
            f"Step with id '{step_id}' not found."
        )

    if step.article_id != article.id:
        raise InvalidStepReferenceError(
            f"Step '{step_id}' does not belong to playbook '{article_id}'."
        )

    return step


def toggle_step_progress(
    db: Session,
    article_id: Union[uuid.UUID, str],
    step_id: Union[uuid.UUID, str],
    user_id: str,
    tenant_id: str,
    is_completed: bool = True,
) -> Dict[str, Any]:
    """Toggle or set completion state for a playbook step and calculate overall progress percentage [FR-4, FR-8, FR-9].

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: Parent playbook article identifier.
        step_id: Playbook step identifier.
        user_id: Caller user identifier.
        tenant_id: Caller tenant identifier [FR-9].
        is_completed: Boolean target completion flag.

    Returns:
        Dictionary containing updated step completion details and overall playbook progress percentage.

    Raises:
        InvalidPlaybookError: If user_id or tenant_id is invalid/empty.
        PlaybookNotFoundError: If playbook is missing or inaccessible.
        InvalidStepReferenceError: If step does not belong to the referenced playbook.
    """
    correlation_id = get_correlation_id()
    if not user_id or not str(user_id).strip():
        raise InvalidPlaybookError("User identifier 'user_id' cannot be empty.")
    if not tenant_id or not str(tenant_id).strip():
        raise InvalidPlaybookError("Tenant identifier 'tenant_id' cannot be empty.")

    clean_user_id = str(user_id).strip()
    clean_tenant_id = str(tenant_id).strip()

    # 1. Verify playbook article existence and tenant access
    article = _verify_playbook_article(db, article_id=article_id, tenant_id=clean_tenant_id)

    # 2. Verify step existence and article association
    step = get_playbook_step_by_id(
        db, article_id=article.id, step_id=step_id, tenant_id=clean_tenant_id
    )

    now_utc = datetime.now(timezone.utc)

    # 3. Upsert user progress record
    progress = (
        db.query(UserStepProgress)
        .filter(
            UserStepProgress.user_id == clean_user_id,
            UserStepProgress.step_id == step.id,
        )
        .first()
    )

    if progress:
        progress.is_completed = bool(is_completed)
        progress.tenant_id = clean_tenant_id
        progress.completed_at = now_utc
    else:
        progress = UserStepProgress(
            id=uuid.uuid4(),
            user_id=clean_user_id,
            tenant_id=clean_tenant_id,
            step_id=step.id,
            is_completed=bool(is_completed),
            completed_at=now_utc,
        )
        db.add(progress)

    db.commit()
    db.refresh(progress)

    # 4. Calculate overall progress for the user on this playbook
    all_steps = (
        db.query(PlaybookStep.id)
        .filter(PlaybookStep.article_id == article.id)
        .all()
    )
    all_step_ids = [s.id for s in all_steps]
    total_steps = len(all_step_ids)

    completed_count = 0
    overall_percentage = 0.0

    if total_steps > 0:
        completed_count = (
            db.query(func.count(UserStepProgress.id))
            .filter(
                UserStepProgress.user_id == clean_user_id,
                UserStepProgress.step_id.in_(all_step_ids),
                UserStepProgress.is_completed == True,  # noqa: E712
            )
            .scalar()
            or 0
        )
        overall_percentage = round((completed_count / total_steps) * 100.0, 2)

    logger.info(
        "Toggled step progress: article_id=%s, step_id=%s, user_id=%s, is_completed=%s, "
        "progress=%.2f%% (%d/%d) [correlation_id=%s]",
        article.id,
        step.id,
        clean_user_id,
        progress.is_completed,
        overall_percentage,
        completed_count,
        total_steps,
        correlation_id,
    )

    return {
        "step_id": str(step.id),
        "article_id": str(article.id),
        "user_id": clean_user_id,
        "tenant_id": clean_tenant_id,
        "is_completed": progress.is_completed,
        "completed_at": progress.completed_at.isoformat() if progress.completed_at else None,
        "overall_playbook_progress_percentage": overall_percentage,
        "total_steps": total_steps,
        "completed_steps": completed_count,
    }


def get_playbook_progress(
    db: Session,
    article_id: Union[uuid.UUID, str],
    user_id: str,
    tenant_id: str,
) -> Dict[str, Any]:
    """Retrieve comprehensive user progress summary for an operational playbook [FR-3, FR-4, FR-9].

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: Parent playbook article identifier.
        user_id: Caller user identifier.
        tenant_id: Caller tenant identifier [FR-9].

    Returns:
        Dictionary containing progress breakdown, list of completed step IDs, and overall percentage.

    Raises:
        InvalidPlaybookError: If user_id or tenant_id is empty.
        PlaybookNotFoundError: If playbook is missing or inaccessible.
    """
    correlation_id = get_correlation_id()
    if not user_id or not str(user_id).strip():
        raise InvalidPlaybookError("User identifier 'user_id' cannot be empty.")
    if not tenant_id or not str(tenant_id).strip():
        raise InvalidPlaybookError("Tenant identifier 'tenant_id' cannot be empty.")

    clean_user_id = str(user_id).strip()
    clean_tenant_id = str(tenant_id).strip()

    article = _verify_playbook_article(db, article_id=article_id, tenant_id=clean_tenant_id)

    steps = (
        db.query(PlaybookStep)
        .filter(PlaybookStep.article_id == article.id)
        .order_by(PlaybookStep.step_number.asc())
        .all()
    )

    total_steps = len(steps)
    if total_steps == 0:
        return {
            "article_id": str(article.id),
            "user_id": clean_user_id,
            "tenant_id": clean_tenant_id,
            "total_steps": 0,
            "completed_steps": 0,
            "progress_percentage": 0.0,
            "completed_step_ids": [],
            "step_progress": [],
        }

    step_ids = [step.id for step in steps]

    progress_records = (
        db.query(UserStepProgress)
        .filter(
            UserStepProgress.user_id == clean_user_id,
            UserStepProgress.step_id.in_(step_ids),
        )
        .all()
    )

    progress_map = {p.step_id: p for p in progress_records}

    completed_step_ids: List[str] = []
    step_progress_list: List[Dict[str, Any]] = []

    for step in steps:
        prog = progress_map.get(step.id)
        is_comp = prog.is_completed if prog else False
        comp_at = (
            prog.completed_at.isoformat()
            if (prog and prog.completed_at and is_comp)
            else None
        )

        if is_comp:
            completed_step_ids.append(str(step.id))

        step_progress_list.append(
            {
                "step_id": str(step.id),
                "step_number": step.step_number,
                "title": step.title,
                "is_completed": is_comp,
                "completed_at": comp_at,
            }
        )

    completed_count = len(completed_step_ids)
    progress_percentage = round((completed_count / total_steps) * 100.0, 2)

    logger.debug(
        "Fetched playbook progress: article_id=%s, user_id=%s, progress=%.2f%% (%d/%d) [correlation_id=%s]",
        article.id,
        clean_user_id,
        progress_percentage,
        completed_count,
        total_steps,
        correlation_id,
    )

    return {
        "article_id": str(article.id),
        "user_id": clean_user_id,
        "tenant_id": clean_tenant_id,
        "total_steps": total_steps,
        "completed_steps": completed_count,
        "progress_percentage": progress_percentage,
        "completed_step_ids": completed_step_ids,
        "step_progress": step_progress_list,
    }


def get_next_uncompleted_step(
    db: Session,
    article_id: Union[uuid.UUID, str],
    user_id: str,
    tenant_id: str,
) -> Optional[PlaybookStep]:
    """Determine the next sequential uncompleted step in a playbook for a given user [FR-3, FR-4].

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: Parent playbook article identifier.
        user_id: Caller user identifier.
        tenant_id: Caller tenant identifier [FR-9].

    Returns:
        Next uncompleted PlaybookStep entity, or None if all steps are completed or no steps exist.

    Raises:
        PlaybookNotFoundError: If playbook is missing or inaccessible.
    """
    progress_summary = get_playbook_progress(
        db, article_id=article_id, user_id=user_id, tenant_id=tenant_id
    )

    completed_set = set(progress_summary["completed_step_ids"])
    steps = get_playbook_steps(db, article_id=article_id, tenant_id=tenant_id)

    for step in steps:
        if str(step.id) not in completed_set:
            return step

    return None


def reset_playbook_progress(
    db: Session,
    article_id: Union[uuid.UUID, str],
    user_id: str,
    tenant_id: str,
) -> Dict[str, Any]:
    """Reset user progress for all steps in a playbook [FR-4].

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: Parent playbook article identifier.
        user_id: Caller user identifier.
        tenant_id: Caller tenant identifier [FR-9].

    Returns:
        Updated progress summary showing zero completion.

    Raises:
        PlaybookNotFoundError: If playbook is missing or inaccessible.
    """
    correlation_id = get_correlation_id()
    if not user_id or not str(user_id).strip():
        raise InvalidPlaybookError("User identifier 'user_id' cannot be empty.")
    if not tenant_id or not str(tenant_id).strip():
        raise InvalidPlaybookError("Tenant identifier 'tenant_id' cannot be empty.")

    clean_user_id = str(user_id).strip()
    clean_tenant_id = str(tenant_id).strip()

    article = _verify_playbook_article(db, article_id=article_id, tenant_id=clean_tenant_id)
    steps = get_playbook_steps(db, article_id=article.id, tenant_id=clean_tenant_id)

    if steps:
        step_ids = [step.id for step in steps]
        db.query(UserStepProgress).filter(
            UserStepProgress.user_id == clean_user_id,
            UserStepProgress.step_id.in_(step_ids),
        ).delete(synchronize_session=False)
        db.commit()

    logger.info(
        "Reset playbook progress: article_id=%s, user_id=%s [correlation_id=%s]",
        article.id,
        clean_user_id,
        correlation_id,
    )

    return get_playbook_progress(
        db, article_id=article.id, user_id=clean_user_id, tenant_id=clean_tenant_id
    )


# --- Step Management Helpers ---


def create_playbook_step(
    db: Session,
    article_id: Union[uuid.UUID, str],
    step_number: int,
    title: str,
    content: str,
    checkpoint_instructions: Optional[str] = None,
    tenant_id: Optional[str] = None,
) -> PlaybookStep:
    """Create and persist a new step within a playbook [FR-3].

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: Parent playbook article identifier.
        step_number: Sequential ordering index (must be >= 1).
        title: Step heading title.
        content: Step execution content instructions.
        checkpoint_instructions: Optional verification instructions.
        tenant_id: Optional caller tenant identifier [FR-9].

    Returns:
        Created PlaybookStep entity.

    Raises:
        InvalidPlaybookError: If title, content, or step_number is invalid, or step_number is duplicate.
        PlaybookNotFoundError: If playbook article is missing or inaccessible.
    """
    article = _verify_playbook_article(db, article_id=article_id, tenant_id=tenant_id)

    if not title or not title.strip():
        raise InvalidPlaybookError("Step title cannot be empty.")
    if not content or not content.strip():
        raise InvalidPlaybookError("Step content cannot be empty.")
    if step_number < 1:
        raise InvalidPlaybookError("Step number must be a positive integer (>= 1).")

    # Check for existing step_number conflict within the same playbook
    existing = (
        db.query(PlaybookStep)
        .filter(
            PlaybookStep.article_id == article.id,
            PlaybookStep.step_number == step_number,
        )
        .first()
    )
    if existing:
        raise InvalidPlaybookError(
            f"Step number {step_number} already exists for playbook '{article_id}'."
        )

    step = PlaybookStep(
        id=uuid.uuid4(),
        article_id=article.id,
        step_number=step_number,
        title=title.strip(),
        content=content.strip(),
        checkpoint_instructions=checkpoint_instructions.strip()
        if checkpoint_instructions
        else None,
    )

    db.add(step)
    db.commit()
    db.refresh(step)

    return step


def update_playbook_step(
    db: Session,
    article_id: Union[uuid.UUID, str],
    step_id: Union[uuid.UUID, str],
    title: Optional[str] = None,
    content: Optional[str] = None,
    checkpoint_instructions: Optional[str] = None,
    step_number: Optional[int] = None,
    tenant_id: Optional[str] = None,
) -> PlaybookStep:
    """Update an existing playbook step [FR-3].

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: Parent playbook article identifier.
        step_id: Playbook step identifier.
        title: Optional updated title.
        content: Optional updated content.
        checkpoint_instructions: Optional updated checkpoint instructions.
        step_number: Optional updated step sequence index.
        tenant_id: Optional caller tenant identifier [FR-9].

    Returns:
        Updated PlaybookStep entity.

    Raises:
        InvalidPlaybookError: If field values fail validation.
        PlaybookNotFoundError: If playbook is missing or inaccessible.
        InvalidStepReferenceError: If step does not belong to playbook.
    """
    step = get_playbook_step_by_id(
        db, article_id=article_id, step_id=step_id, tenant_id=tenant_id
    )

    if title is not None:
        if not title.strip():
            raise InvalidPlaybookError("Step title cannot be empty.")
        step.title = title.strip()

    if content is not None:
        if not content.strip():
            raise InvalidPlaybookError("Step content cannot be empty.")
        step.content = content.strip()

    if checkpoint_instructions is not None:
        step.checkpoint_instructions = (
            checkpoint_instructions.strip() if checkpoint_instructions.strip() else None
        )

    if step_number is not None:
        if step_number < 1:
            raise InvalidPlaybookError("Step number must be a positive integer (>= 1).")
        if step_number != step.step_number:
            existing = (
                db.query(PlaybookStep)
                .filter(
                    PlaybookStep.article_id == step.article_id,
                    PlaybookStep.step_number == step_number,
                    PlaybookStep.id != step.id,
                )
                .first()
            )
            if existing:
                raise InvalidPlaybookError(
                    f"Step number {step_number} is already in use by another step in this playbook."
                )
            step.step_number = step_number

    db.commit()
    db.refresh(step)
    return step


def delete_playbook_step(
    db: Session,
    article_id: Union[uuid.UUID, str],
    step_id: Union[uuid.UUID, str],
    tenant_id: Optional[str] = None,
) -> bool:
    """Delete a playbook step and associated user progress records [FR-3, FR-4].

    Args:
        db: Synchronous SQLAlchemy database session.
        article_id: Parent playbook article identifier.
        step_id: Playbook step identifier.
        tenant_id: Optional caller tenant identifier [FR-9].

    Returns:
        True if successfully deleted.

    Raises:
        PlaybookNotFoundError: If playbook is missing or inaccessible.
        InvalidStepReferenceError: If step does not belong to playbook.
    """
    step = get_playbook_step_by_id(
        db, article_id=article_id, step_id=step_id, tenant_id=tenant_id
    )

    db.delete(step)
    db.commit()
    return True


# --- Domain Service Class ---


class PlaybookService:
    """Domain service class encapsulating playbook step progression, completion toggling, and progress tracking."""

    def __init__(self, db: Session) -> None:
        """Initialize PlaybookService with a database session."""
        self.db = db

    def get_playbook_steps(
        self,
        article_id: Union[uuid.UUID, str],
        tenant_id: Optional[str] = None,
    ) -> List[PlaybookStep]:
        """Retrieve all steps for a playbook ordered by step_number."""
        return get_playbook_steps(self.db, article_id=article_id, tenant_id=tenant_id)

    def get_playbook_step_by_id(
        self,
        article_id: Union[uuid.UUID, str],
        step_id: Union[uuid.UUID, str],
        tenant_id: Optional[str] = None,
    ) -> PlaybookStep:
        """Retrieve a specific playbook step and verify its parent playbook."""
        return get_playbook_step_by_id(
            self.db, article_id=article_id, step_id=step_id, tenant_id=tenant_id
        )

    def toggle_step_progress(
        self,
        article_id: Union[uuid.UUID, str],
        step_id: Union[uuid.UUID, str],
        user_id: str,
        tenant_id: str,
        is_completed: bool = True,
    ) -> Dict[str, Any]:
        """Toggle step completion state and calculate progress percentage."""
        return toggle_step_progress(
            self.db,
            article_id=article_id,
            step_id=step_id,
            user_id=user_id,
            tenant_id=tenant_id,
            is_completed=is_completed,
        )

    def get_playbook_progress(
        self,
        article_id: Union[uuid.UUID, str],
        user_id: str,
        tenant_id: str,
    ) -> Dict[str, Any]:
        """Retrieve progress summary for a playbook and user."""
        return get_playbook_progress(
            self.db,
            article_id=article_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    def get_next_uncompleted_step(
        self,
        article_id: Union[uuid.UUID, str],
        user_id: str,
        tenant_id: str,
    ) -> Optional[PlaybookStep]:
        """Get the next uncompleted step in sequential order."""
        return get_next_uncompleted_step(
            self.db,
            article_id=article_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    def reset_playbook_progress(
        self,
        article_id: Union[uuid.UUID, str],
        user_id: str,
        tenant_id: str,
    ) -> Dict[str, Any]:
        """Reset all step progress records for a user on a playbook."""
        return reset_playbook_progress(
            self.db,
            article_id=article_id,
            user_id=user_id,
            tenant_id=tenant_id,
        )

    def create_playbook_step(
        self,
        article_id: Union[uuid.UUID, str],
        step_number: int,
        title: str,
        content: str,
        checkpoint_instructions: Optional[str] = None,
        tenant_id: Optional[str] = None,
    ) -> PlaybookStep:
        """Create a new playbook step."""
        return create_playbook_step(
            self.db,
            article_id=article_id,
            step_number=step_number,
            title=title,
            content=content,
            checkpoint_instructions=checkpoint_instructions,
            tenant_id=tenant_id,
        )

    def update_playbook_step(
        self,
        article_id: Union[uuid.UUID, str],
        step_id: Union[uuid.UUID, str],
        title: Optional[str] = None,
        content: Optional[str] = None,
        checkpoint_instructions: Optional[str] = None,
        step_number: Optional[int] = None,
        tenant_id: Optional[str] = None,
    ) -> PlaybookStep:
        """Update an existing playbook step."""
        return update_playbook_step(
            self.db,
            article_id=article_id,
            step_id=step_id,
            title=title,
            content=content,
            checkpoint_instructions=checkpoint_instructions,
            step_number=step_number,
            tenant_id=tenant_id,
        )

    def delete_playbook_step(
        self,
        article_id: Union[uuid.UUID, str],
        step_id: Union[uuid.UUID, str],
        tenant_id: Optional[str] = None,
    ) -> bool:
        """Delete a playbook step."""
        return delete_playbook_step(
            self.db,
            article_id=article_id,
            step_id=step_id,
            tenant_id=tenant_id,
        )
