"""Stakeholder Service for contact mapping, influence tracking, and chronological activity feeds [FR-006, FR-007].

Provides domain business logic for mapping stakeholders to sales deals, tracking organizational roles
and influence levels, recording stakeholder interaction events, and retrieving chronological activity feeds.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy.orm import Session

from src.lib.logging import get_correlation_id
from src.models.deal import Deal, Stakeholder, StakeholderActivity

logger = logging.getLogger(__name__)

ALLOWED_INFLUENCE_LEVELS = {"high": "High", "medium": "Medium", "low": "Low"}
ALLOWED_ACTIVITY_TYPES = {"email": "Email", "meeting": "Meeting", "call": "Call", "note": "Note"}


class StakeholderServiceError(Exception):
    """Base exception for stakeholder domain service errors."""

    pass


class StakeholderNotFoundError(StakeholderServiceError):
    """Raised when a requested stakeholder record is not found."""

    pass


class StakeholderDealNotFoundError(StakeholderServiceError):
    """Raised when the specified deal for a stakeholder or activity does not exist."""

    pass


class InvalidStakeholderError(StakeholderServiceError):
    """Raised when stakeholder fields fail validation checks."""

    pass


class InvalidActivityError(StakeholderServiceError):
    """Raised when stakeholder activity fields fail validation checks."""

    pass


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime object is timezone-aware and set to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class StakeholderService:
    """Domain service encapsulating stakeholder mapping, influence tracking, and activity feed retrieval."""

    def __init__(self, db: Session) -> None:
        """Initialize StakeholderService with a database session.

        Args:
            db: Synchronous SQLAlchemy database session.
        """
        self.db = db

    def create_stakeholder(
        self,
        deal_id: str,
        full_name: str,
        email: str,
        org_role: str,
        influence_level: str,
    ) -> Dict[str, Any]:
        """Map a new stakeholder contact to a deal [FR-006].

        Args:
            deal_id: UUID of the target deal.
            full_name: Full name of the stakeholder.
            email: Email address of the stakeholder.
            org_role: Organizational role or job title.
            influence_level: Influence level ('High', 'Medium', or 'Low').

        Returns:
            Dictionary representation of created stakeholder.

        Raises:
            StakeholderDealNotFoundError: If specified deal ID does not exist.
            InvalidStakeholderError: If any field is empty or influence level is invalid.
        """
        if not full_name or not full_name.strip():
            raise InvalidStakeholderError("Stakeholder full name cannot be empty.")
        if not email or not email.strip() or "@" not in email:
            raise InvalidStakeholderError("A valid email address must be provided.")
        if not org_role or not org_role.strip():
            raise InvalidStakeholderError("Organizational role cannot be empty.")

        norm_influence = influence_level.strip().lower() if influence_level else ""
        if norm_influence not in ALLOWED_INFLUENCE_LEVELS:
            raise InvalidStakeholderError(
                f"Invalid influence level '{influence_level}'. Must be one of: High, Medium, Low."
            )
        influence_str = ALLOWED_INFLUENCE_LEVELS[norm_influence]

        # Confirm deal exists
        deal = self.db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise StakeholderDealNotFoundError(f"Deal with ID '{deal_id}' not found.")

        stakeholder = Stakeholder(
            id=str(uuid.uuid4()),
            deal_id=deal_id,
            full_name=full_name.strip(),
            email=email.strip().lower(),
            org_role=org_role.strip(),
            influence_level=influence_str,
            is_active=True,
            created_on=datetime.now(timezone.utc),
        )

        self.db.add(stakeholder)
        self.db.commit()
        self.db.refresh(stakeholder)

        correlation_id = get_correlation_id()
        logger.info(
            f"Mapped stakeholder '{stakeholder.id}' ({stakeholder.full_name}) to deal '{deal_id}'.",
            extra={
                "correlation_id": correlation_id,
                "event_type": "stakeholder_created",
                "stakeholder_id": stakeholder.id,
                "deal_id": deal_id,
                "full_name": stakeholder.full_name,
                "org_role": stakeholder.org_role,
                "influence_level": stakeholder.influence_level,
            },
        )

        return stakeholder.to_dict()

    def get_stakeholder(self, stakeholder_id: str) -> Dict[str, Any]:
        """Retrieve a stakeholder record by ID.

        Args:
            stakeholder_id: UUID of stakeholder.

        Returns:
            Dictionary representation of stakeholder.

        Raises:
            StakeholderNotFoundError: If stakeholder is missing.
        """
        stakeholder = self.db.query(Stakeholder).filter(Stakeholder.id == stakeholder_id).first()
        if not stakeholder:
            raise StakeholderNotFoundError(f"Stakeholder with ID '{stakeholder_id}' not found.")
        return stakeholder.to_dict()

    def update_stakeholder(
        self,
        stakeholder_id: str,
        full_name: Optional[str] = None,
        email: Optional[str] = None,
        org_role: Optional[str] = None,
        influence_level: Optional[str] = None,
        is_active: Optional[bool] = None,
    ) -> Dict[str, Any]:
        """Update contact details, organizational role, or influence level for a stakeholder [FR-006].

        Args:
            stakeholder_id: UUID of stakeholder to update.
            full_name: Optional updated full name.
            email: Optional updated email address.
            org_role: Optional updated organizational role.
            influence_level: Optional updated influence level ('High', 'Medium', 'Low').
            is_active: Optional updated active status flag.

        Returns:
            Updated stakeholder dictionary representation.

        Raises:
            StakeholderNotFoundError: If stakeholder does not exist.
            InvalidStakeholderError: If updated field validation fails.
        """
        stakeholder = self.db.query(Stakeholder).filter(Stakeholder.id == stakeholder_id).first()
        if not stakeholder:
            raise StakeholderNotFoundError(f"Stakeholder with ID '{stakeholder_id}' not found.")

        if full_name is not None:
            if not full_name.strip():
                raise InvalidStakeholderError("Stakeholder full name cannot be empty.")
            stakeholder.full_name = full_name.strip()

        if email is not None:
            if not email.strip() or "@" not in email:
                raise InvalidStakeholderError("A valid email address must be provided.")
            stakeholder.email = email.strip().lower()

        if org_role is not None:
            if not org_role.strip():
                raise InvalidStakeholderError("Organizational role cannot be empty.")
            stakeholder.org_role = org_role.strip()

        if influence_level is not None:
            norm_inf = influence_level.strip().lower()
            if norm_inf not in ALLOWED_INFLUENCE_LEVELS:
                raise InvalidStakeholderError(
                    f"Invalid influence level '{influence_level}'. Must be one of: High, Medium, Low."
                )
            stakeholder.influence_level = ALLOWED_INFLUENCE_LEVELS[norm_inf]

        if is_active is not None:
            stakeholder.is_active = is_active

        self.db.commit()
        self.db.refresh(stakeholder)

        logger.info(
            f"Updated stakeholder '{stakeholder.id}' details (influence level: '{stakeholder.influence_level}').",
            extra={
                "correlation_id": get_correlation_id(),
                "event_type": "stakeholder_updated",
                "stakeholder_id": stakeholder.id,
                "deal_id": stakeholder.deal_id,
                "influence_level": stakeholder.influence_level,
                "is_active": stakeholder.is_active,
            },
        )

        return stakeholder.to_dict()

    def list_stakeholders_for_deal(
        self,
        deal_id: str,
        active_only: bool = True,
    ) -> List[Dict[str, Any]]:
        """Retrieve all mapped stakeholders for a deal [FR-006].

        Args:
            deal_id: UUID of target deal.
            active_only: If True, returns only active stakeholders.

        Returns:
            List of stakeholder dictionary representations.
        """
        query = self.db.query(Stakeholder).filter(Stakeholder.deal_id == deal_id)
        if active_only:
            query = query.filter(Stakeholder.is_active == True)  # noqa: E712

        stakeholders = query.order_by(Stakeholder.created_on.asc()).all()
        return [s.to_dict() for s in stakeholders]

    def delete_stakeholder(self, stakeholder_id: str) -> None:
        """Delete a stakeholder record and associated activities from a deal.

        Args:
            stakeholder_id: UUID of stakeholder to remove.

        Raises:
            StakeholderNotFoundError: If stakeholder does not exist.
        """
        stakeholder = self.db.query(Stakeholder).filter(Stakeholder.id == stakeholder_id).first()
        if not stakeholder:
            raise StakeholderNotFoundError(f"Stakeholder with ID '{stakeholder_id}' not found.")

        deal_id = stakeholder.deal_id
        self.db.delete(stakeholder)
        self.db.commit()

        logger.info(
            f"Deleted stakeholder '{stakeholder_id}' from deal '{deal_id}'.",
            extra={
                "correlation_id": get_correlation_id(),
                "event_type": "stakeholder_deleted",
                "stakeholder_id": stakeholder_id,
                "deal_id": deal_id,
            },
        )

    def log_activity(
        self,
        stakeholder_id: str,
        deal_id: str,
        activity_type: str,
        summary: str,
        occurred_on: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Record an interaction event associated with a mapped stakeholder [FR-007].

        Args:
            stakeholder_id: UUID of mapped stakeholder.
            deal_id: UUID of target deal.
            activity_type: Type of interaction ('Email', 'Meeting', 'Call', 'Note').
            summary: Brief description/summary of interaction.
            occurred_on: Optional timestamp when interaction occurred (defaults to now).

        Returns:
            Dictionary representation of logged activity.

        Raises:
            StakeholderNotFoundError: If stakeholder does not exist.
            StakeholderDealNotFoundError: If specified deal does not exist.
            InvalidActivityError: If activity type or summary is invalid, or stakeholder does not belong to deal.
        """
        if not summary or not summary.strip():
            raise InvalidActivityError("Activity summary cannot be empty.")

        norm_type = activity_type.strip().lower() if activity_type else ""
        if norm_type in ALLOWED_ACTIVITY_TYPES:
            type_str = ALLOWED_ACTIVITY_TYPES[norm_type]
        elif activity_type and activity_type.strip():
            type_str = activity_type.strip().capitalize()
        else:
            raise InvalidActivityError("Activity type cannot be empty.")

        # Confirm deal exists
        deal = self.db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise StakeholderDealNotFoundError(f"Deal with ID '{deal_id}' not found.")

        # Confirm stakeholder exists and matches deal
        stakeholder = self.db.query(Stakeholder).filter(Stakeholder.id == stakeholder_id).first()
        if not stakeholder:
            raise StakeholderNotFoundError(f"Stakeholder with ID '{stakeholder_id}' not found.")
        if stakeholder.deal_id != deal_id:
            raise InvalidActivityError(
                f"Stakeholder '{stakeholder_id}' is not mapped to deal '{deal_id}'."
            )

        event_time = _ensure_utc(occurred_on) or datetime.now(timezone.utc)

        activity = StakeholderActivity(
            id=str(uuid.uuid4()),
            stakeholder_id=stakeholder_id,
            deal_id=deal_id,
            activity_type=type_str,
            summary=summary.strip(),
            occurred_on=event_time,
            created_on=datetime.now(timezone.utc),
        )

        self.db.add(activity)
        self.db.commit()
        self.db.refresh(activity)

        correlation_id = get_correlation_id()
        logger.info(
            f"Logged activity '{activity.id}' ({activity.activity_type}) for stakeholder '{stakeholder_id}' on deal '{deal_id}'.",
            extra={
                "correlation_id": correlation_id,
                "event_type": "stakeholder_activity_logged",
                "activity_id": activity.id,
                "stakeholder_id": stakeholder_id,
                "deal_id": deal_id,
                "activity_type": activity.activity_type,
                "occurred_on": activity.occurred_on.isoformat(),
            },
        )

        activity_dict = activity.to_dict()
        activity_dict["stakeholder_name"] = stakeholder.full_name
        return activity_dict

    def get_activity_feed_for_deal(
        self,
        deal_id: str,
        limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve chronological feed of recent interactions associated with mapped stakeholders for a deal [FR-007].

        Returned records are ordered chronologically by interaction timestamp descending (`occurred_on.desc()`).

        Args:
            deal_id: UUID of target deal.
            limit: Optional maximum number of recent activity records to return.

        Returns:
            List of activity dictionary objects sorted chronologically.
        """
        query = (
            self.db.query(StakeholderActivity)
            .filter(StakeholderActivity.deal_id == deal_id)
            .order_by(StakeholderActivity.occurred_on.desc())
        )

        if limit is not None and limit > 0:
            query = query.limit(limit)

        activities = query.all()
        results: List[Dict[str, Any]] = []

        for act in activities:
            act_dict = act.to_dict()
            if act.stakeholder:
                act_dict["stakeholder_name"] = act.stakeholder.full_name
                act_dict["stakeholder_email"] = act.stakeholder.email
            results.append(act_dict)

        return results

    def get_stakeholders_and_activities(self, deal_id: str) -> Dict[str, Any]:
        """Retrieve mapped stakeholders and chronological activity feed for a deal [FR-006, FR-007].

        Args:
            deal_id: UUID of target deal.

        Returns:
            Dictionary containing 'stakeholders' list and 'recent_activities' chronological feed list.

        Raises:
            StakeholderDealNotFoundError: If specified deal does not exist.
        """
        deal = self.db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise StakeholderDealNotFoundError(f"Deal with ID '{deal_id}' not found.")

        stakeholders = self.list_stakeholders_for_deal(deal_id, active_only=False)
        activities = self.get_activity_feed_for_deal(deal_id)

        return {
            "stakeholders": stakeholders,
            "recent_activities": activities,
        }
