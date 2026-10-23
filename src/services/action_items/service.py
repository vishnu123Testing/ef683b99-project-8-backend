"""Action Item Service for creation, assignment, SLA breach scanning, completion, and escalation clearing [FR-004, FR-005, NFR-004].

Provides domain business logic for deal action item management, automated SLA breach detection,
alert suppression for previously escalated items, task completion with escalation state reset,
and structured JSON logging with context-managed correlation IDs.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional
import uuid

from sqlalchemy.orm import Session

from src.lib.config import settings
from src.lib.logging import get_correlation_id
from src.models.deal import ActionItem, Deal, Stakeholder, StakeholderActivity

logger = logging.getLogger(__name__)


class ActionItemServiceError(Exception):
    """Base exception for action item domain service errors."""

    pass


class ActionItemNotFoundError(ActionItemServiceError):
    """Raised when a requested action item record is not found."""

    pass


class ActionItemDealNotFoundError(ActionItemServiceError):
    """Raised when the specified deal for an action item does not exist."""

    pass


class InvalidActionItemError(ActionItemServiceError):
    """Raised when action item parameters fail validation checks."""

    pass


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime object is timezone-aware and set to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


class ActionItemService:
    """Domain service encapsulating action item lifecycle, SLA breach scanning, and escalation clearing."""

    def __init__(
        self,
        db: Session,
        notification_port: Optional[Any] = None,
    ) -> None:
        """Initialize ActionItemService with a database session and optional notification port.

        Args:
            db: Synchronous SQLAlchemy database session.
            notification_port: Optional notification dispatch adapter/port for escalation alerts.
        """
        self.db = db
        self._notification_port = notification_port

    def create_action_item(
        self,
        deal_id: str,
        title: str,
        assigned_owner_id: str,
        target_due_date: datetime,
        sla_threshold_hours: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Create and assign a new action item to a deal [FR-004].

        Args:
            deal_id: UUID of the target deal.
            title: Descriptive title of the action item.
            assigned_owner_id: ID or username of designated owner.
            target_due_date: Target completion deadline timestamp.
            sla_threshold_hours: Optional custom SLA threshold in hours (defaults to settings).

        Returns:
            Dictionary representation of the created action item.

        Raises:
            ActionItemDealNotFoundError: If specified deal ID does not exist.
            InvalidActionItemError: If title or assigned owner ID is empty/blank.
        """
        if not title or not title.strip():
            raise InvalidActionItemError("Action item title cannot be empty.")
        if not assigned_owner_id or not assigned_owner_id.strip():
            raise InvalidActionItemError("Assigned owner ID cannot be empty.")

        # Confirm target deal exists
        deal = self.db.query(Deal).filter(Deal.id == deal_id).first()
        if not deal:
            raise ActionItemDealNotFoundError(f"Deal with ID '{deal_id}' not found.")

        due_dt = _ensure_utc(target_due_date)
        if due_dt is None:
            raise InvalidActionItemError("Target due date must be a valid datetime.")

        sla_hours = (
            sla_threshold_hours
            if (sla_threshold_hours is not None and sla_threshold_hours > 0)
            else settings.action_item_sla_threshold_hours
        )

        item = ActionItem(
            id=str(uuid.uuid4()),
            deal_id=deal_id,
            title=title.strip(),
            assigned_owner_id=assigned_owner_id.strip(),
            target_due_date=due_dt,
            sla_threshold_hours=sla_hours,
            is_completed=False,
            is_escalated=False,
            created_on=datetime.now(timezone.utc),
        )

        self.db.add(item)
        self.db.commit()
        self.db.refresh(item)

        correlation_id = get_correlation_id()
        logger.info(
            f"Created action item '{item.id}' assigned to '{item.assigned_owner_id}' for deal '{deal_id}'.",
            extra={
                "correlation_id": correlation_id,
                "event_type": "action_item_created",
                "action_item_id": item.id,
                "deal_id": deal_id,
                "assigned_owner_id": item.assigned_owner_id,
                "target_due_date": item.target_due_date.isoformat(),
                "sla_threshold_hours": item.sla_threshold_hours,
            },
        )

        return item.to_dict()

    def assign_action_item(
        self,
        item_id: str,
        assigned_owner_id: str,
        target_due_date: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Reassign an action item to a new owner or update its target due date [FR-004].

        Args:
            item_id: UUID of action item to reassign.
            assigned_owner_id: New owner ID/username.
            target_due_date: Optional new target due date.

        Returns:
            Dictionary representation of updated action item.

        Raises:
            ActionItemNotFoundError: If action item record is missing.
            InvalidActionItemError: If assigned owner ID is invalid.
        """
        if not assigned_owner_id or not assigned_owner_id.strip():
            raise InvalidActionItemError("Assigned owner ID cannot be empty.")

        item = self.db.query(ActionItem).filter(ActionItem.id == item_id).first()
        if not item:
            raise ActionItemNotFoundError(f"Action item with ID '{item_id}' not found.")

        item.assigned_owner_id = assigned_owner_id.strip()
        if target_due_date is not None:
            due_dt = _ensure_utc(target_due_date)
            if due_dt:
                item.target_due_date = due_dt

        self.db.commit()
        self.db.refresh(item)

        logger.info(
            f"Reassigned action item '{item.id}' to owner '{item.assigned_owner_id}'.",
            extra={
                "correlation_id": get_correlation_id(),
                "event_type": "action_item_reassigned",
                "action_item_id": item.id,
                "deal_id": item.deal_id,
                "assigned_owner_id": item.assigned_owner_id,
            },
        )

        return item.to_dict()

    def get_action_item(self, item_id: str) -> Dict[str, Any]:
        """Retrieve an action item by ID.

        Args:
            item_id: UUID of action item.

        Returns:
            Dictionary representation of action item.

        Raises:
            ActionItemNotFoundError: If item is missing.
        """
        item = self.db.query(ActionItem).filter(ActionItem.id == item_id).first()
        if not item:
            raise ActionItemNotFoundError(f"Action item with ID '{item_id}' not found.")
        return item.to_dict()

    def list_action_items_for_deal(self, deal_id: str) -> List[Dict[str, Any]]:
        """Retrieve all action items associated with a specific deal [FR-004].

        Args:
            deal_id: UUID of deal.

        Returns:
            List of action item dictionary objects.
        """
        items = (
            self.db.query(ActionItem)
            .filter(ActionItem.deal_id == deal_id)
            .order_by(ActionItem.created_on.desc())
            .all()
        )
        return [item.to_dict() for item in items]

    def complete_action_item(
        self,
        item_id: str,
        completed_on: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Mark an action item as completed and clear its escalation state [FR-005].

        Completion clears active escalation flags (`is_escalated=False`) and records
        a completion event in the deal activity log if stakeholders exist.

        Args:
            item_id: UUID of action item.
            completed_on: Optional completion timestamp (defaults to current UTC time).

        Returns:
            Updated action item dictionary.

        Raises:
            ActionItemNotFoundError: If action item record is missing.
        """
        item = self.db.query(ActionItem).filter(ActionItem.id == item_id).first()
        if not item:
            raise ActionItemNotFoundError(f"Action item with ID '{item_id}' not found.")

        finish_time = _ensure_utc(completed_on) or datetime.now(timezone.utc)
        item.is_completed = True
        item.completed_on = finish_time

        # Clear escalation state upon task completion [FR-005]
        was_escalated = item.is_escalated
        item.is_escalated = False
        item.escalated_on = None

        # Attempt to record completion in deal activity feed if a stakeholder exists
        stakeholder = (
            self.db.query(Stakeholder)
            .filter(Stakeholder.deal_id == item.deal_id)
            .first()
        )
        if stakeholder:
            activity = StakeholderActivity(
                id=str(uuid.uuid4()),
                stakeholder_id=stakeholder.id,
                deal_id=item.deal_id,
                activity_type="Note",
                summary=f"Action item completed: '{item.title}' (Owner: {item.assigned_owner_id})",
                occurred_on=finish_time,
                created_on=datetime.now(timezone.utc),
            )
            self.db.add(activity)

        self.db.commit()
        self.db.refresh(item)

        correlation_id = get_correlation_id()
        logger.info(
            f"Completed action item '{item.id}' for deal '{item.deal_id}' (escalation cleared: {was_escalated}).",
            extra={
                "correlation_id": correlation_id,
                "event_type": "action_item_completed",
                "action_item_id": item.id,
                "deal_id": item.deal_id,
                "assigned_owner_id": item.assigned_owner_id,
                "completed_on": item.completed_on.isoformat() if item.completed_on else None,
                "was_escalated": was_escalated,
            },
        )

        return item.to_dict()

    def clear_escalation(self, item_id: str) -> Dict[str, Any]:
        """Explicitly clear the escalation state of an action item [FR-005].

        Args:
            item_id: UUID of action item.

        Returns:
            Updated action item dictionary.

        Raises:
            ActionItemNotFoundError: If action item record is missing.
        """
        item = self.db.query(ActionItem).filter(ActionItem.id == item_id).first()
        if not item:
            raise ActionItemNotFoundError(f"Action item with ID '{item_id}' not found.")

        item.is_escalated = False
        item.escalated_on = None

        self.db.commit()
        self.db.refresh(item)

        logger.info(
            f"Cleared escalation flag for action item '{item.id}'.",
            extra={
                "correlation_id": get_correlation_id(),
                "event_type": "action_item_escalation_cleared",
                "action_item_id": item.id,
                "deal_id": item.deal_id,
            },
        )

        return item.to_dict()

    def scan_and_escalate_sla_breaches(
        self,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Scan all open action items for SLA threshold breaches and dispatch escalation alerts [FR-005, NFR-004].

        Suppresses duplicate escalation notifications if an item is already flagged as escalated.
        Emits structured JSON logs containing correlation IDs for all escalation rule executions.

        Args:
            current_time: Optional evaluation timestamp (defaults to UTC now).

        Returns:
            Dictionary containing scan statistics and list of newly escalated items.
        """
        eval_time = _ensure_utc(current_time) or datetime.now(timezone.utc)

        incomplete_items = (
            self.db.query(ActionItem)
            .filter(ActionItem.is_completed == False)  # noqa: E712
            .all()
        )

        scanned_count = len(incomplete_items)
        escalated_count = 0
        suppressed_count = 0
        escalated_items: List[Dict[str, Any]] = []

        for item in incomplete_items:
            item_due = _ensure_utc(item.target_due_date)
            item_created = _ensure_utc(item.created_on)

            sla_cutoff = (
                item_created + timedelta(hours=item.sla_threshold_hours)
                if item_created
                else eval_time
            )

            # SLA breach occurs if evaluation time exceeds target due date or creation + SLA threshold
            is_due_breached = item_due is not None and eval_time > item_due
            is_sla_hours_breached = eval_time > sla_cutoff
            is_breached = is_due_breached or is_sla_hours_breached

            if not is_breached:
                continue

            correlation_id = get_correlation_id()

            if item.is_escalated:
                # Duplicate alert suppression: already escalated items are skipped [FR-005, NFR-004]
                suppressed_count += 1
                logger.info(
                    f"Suppressed duplicate SLA escalation alert for action item '{item.id}' on deal '{item.deal_id}'.",
                    extra={
                        "correlation_id": correlation_id,
                        "event_type": "action_item_escalation_suppressed",
                        "action_item_id": item.id,
                        "deal_id": item.deal_id,
                        "assigned_owner_id": item.assigned_owner_id,
                        "is_escalated": True,
                        "target_due_date": item_due.isoformat() if item_due else None,
                        "sla_threshold_hours": item.sla_threshold_hours,
                    },
                )
            else:
                # Transition item to escalated state
                item.is_escalated = True
                item.escalated_on = eval_time
                escalated_count += 1

                logger.warning(
                    f"SLA breach detected: Escalated action item '{item.id}' on deal '{item.deal_id}'.",
                    extra={
                        "correlation_id": correlation_id,
                        "event_type": "action_item_sla_escalation",
                        "action_item_id": item.id,
                        "deal_id": item.deal_id,
                        "assigned_owner_id": item.assigned_owner_id,
                        "sla_threshold_hours": item.sla_threshold_hours,
                        "target_due_date": item_due.isoformat() if item_due else None,
                        "escalated_on": eval_time.isoformat(),
                        "recipient_roles": settings.escalation_recipient_roles,
                    },
                )

                # Dispatch notification if notification port is configured
                if self._notification_port and hasattr(self._notification_port, "send_escalation_alert"):
                    try:
                        self._notification_port.send_escalation_alert(
                            action_item_id=item.id,
                            deal_id=item.deal_id,
                            assigned_owner_id=item.assigned_owner_id,
                            escalated_on=eval_time,
                        )
                    except Exception as exc:
                        logger.error(
                            f"Failed to dispatch escalation notification for action item '{item.id}': {exc}",
                            extra={
                                "correlation_id": correlation_id,
                                "event_type": "action_item_escalation_dispatch_failed",
                                "action_item_id": item.id,
                                "error": str(exc),
                            },
                        )

                escalated_items.append(item.to_dict())

        self.db.commit()

        return {
            "scanned_count": scanned_count,
            "escalated_count": escalated_count,
            "suppressed_count": suppressed_count,
            "escalated_items": escalated_items,
        }

    def delete_action_item(self, item_id: str) -> None:
        """Delete an action item record.

        Args:
            item_id: UUID of action item to remove.

        Raises:
            ActionItemNotFoundError: If action item record is missing.
        """
        item = self.db.query(ActionItem).filter(ActionItem.id == item_id).first()
        if not item:
            raise ActionItemNotFoundError(f"Action item with ID '{item_id}' not found.")

        deal_id = item.deal_id
        self.db.delete(item)
        self.db.commit()

        logger.info(
            f"Deleted action item '{item_id}' from deal '{deal_id}'.",
            extra={
                "correlation_id": get_correlation_id(),
                "event_type": "action_item_deleted",
                "action_item_id": item_id,
                "deal_id": deal_id,
            },
        )
