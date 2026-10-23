"""In-App persistence connector implementing the In-App port interface [FR-5, FR-8].

Provides concrete InAppPersistenceConnector adapter implementation for persisting
in-app notification alerts into the database and tracking in-app notification dispatches.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Callable, Dict, List, Optional, Union
import uuid

from sqlalchemy.orm import Session

from src.lib.logging import get_correlation_id
from src.models.alerting import NotificationLog
from src.services.alerting.ports import (
    DispatchResult,
    InAppDispatcherPort,
    InAppDispatchError,
    InAppPayload,
)

logger = logging.getLogger(__name__)


class InAppPersistenceConnector(InAppDispatcherPort):
    """In-App persistence connector adapter implementing InAppDispatcherPort [FR-5, FR-8].

    Handles in-app notification payload validation, database persistence using NotificationLog ORM model,
    in-memory notification tracking, and error trapping for notification dispatches.
    """

    def __init__(
        self,
        session: Optional[Session] = None,
        db_session: Optional[Session] = None,
        session_factory: Optional[Callable[[], Session]] = None,
        dry_run: bool = False,
        auto_commit: bool = True,
        simulate_failure: bool = False,
    ) -> None:
        """Initialize InAppPersistenceConnector with database session or configuration options.

        Args:
            session: Optional SQLAlchemy Session instance for DB operations.
            db_session: Alias for session parameter.
            session_factory: Optional callable returning a new SQLAlchemy Session.
            dry_run: If True, simulates dispatch without writing to the database.
            auto_commit: If True, automatically commits the DB session after adding record.
            simulate_failure: Testing flag to simulate persistence failure.
        """
        self.session = session if session is not None else db_session
        self.session_factory = session_factory
        self.dry_run = dry_run
        self.auto_commit = auto_commit
        self.simulate_failure = simulate_failure
        self.sent_notifications: List[Dict[str, Any]] = []

    def send_in_app(self, payload: InAppPayload) -> DispatchResult:
        """Dispatch and persist an in-app notification payload [FR-5, FR-8].

        Args:
            payload: InAppPayload object containing recipient_id, title, message_text,
                urgency, correlation_id, trigger_id, and metadata.

        Returns:
            DispatchResult containing dispatch status, timestamps, and notification details.

        Raises:
            InAppDispatchError: If payload validation fails, UUID parsing fails, or
                database persistence fails.
        """
        if not isinstance(payload, InAppPayload):
            raise InAppDispatchError(
                "Invalid payload type provided to InAppPersistenceConnector."
            )

        if not payload.recipient_id or not str(payload.recipient_id).strip():
            raise InAppDispatchError(
                "Recipient ID is required for in-app notification dispatch."
            )

        title = payload.title.strip() if payload.title else ""
        message_text = payload.message_text.strip() if payload.message_text else ""
        if not title and not message_text:
            raise InAppDispatchError(
                "In-app notification title or message text is required."
            )

        if self.simulate_failure:
            raise InAppDispatchError(
                "Simulated in-app notification persistence failure."
            )

        # Parse recipient_id into UUID
        recipient_str = str(payload.recipient_id).strip()
        try:
            recipient_uuid = (
                payload.recipient_id
                if isinstance(payload.recipient_id, uuid.UUID)
                else uuid.UUID(recipient_str)
            )
        except (ValueError, TypeError) as exc:
            raise InAppDispatchError(
                f"Invalid recipient_id UUID format: '{recipient_str}'"
            ) from exc

        # Parse trigger_id into UUID if present
        trigger_uuid: Optional[uuid.UUID] = None
        if payload.trigger_id and str(payload.trigger_id).strip():
            trigger_str = str(payload.trigger_id).strip()
            try:
                trigger_uuid = (
                    payload.trigger_id
                    if isinstance(payload.trigger_id, uuid.UUID)
                    else uuid.UUID(trigger_str)
                )
            except (ValueError, TypeError) as exc:
                raise InAppDispatchError(
                    f"Invalid trigger_id UUID format: '{trigger_str}'"
                ) from exc

        dispatched_at = datetime.now(timezone.utc)
        correlation_id = (
            payload.correlation_id
            or get_correlation_id()
            or f"corr-{uuid.uuid4()}"
        )
        message_id = f"inapp-{uuid.uuid4()}"

        details: Dict[str, Any] = {
            "title": payload.title,
            "message_text": payload.message_text,
            "urgency": payload.urgency,
            "trigger_id": str(trigger_uuid) if trigger_uuid else None,
            "recipient_id": str(recipient_uuid),
            "metadata": payload.metadata or {},
            "dry_run": self.dry_run,
        }

        # Track in memory
        notification_entry = {
            "message_id": message_id,
            "recipient_id": str(recipient_uuid),
            "title": payload.title,
            "message_text": payload.message_text,
            "urgency": payload.urgency or "Low",
            "trigger_id": str(trigger_uuid) if trigger_uuid else None,
            "dispatched_at": dispatched_at.isoformat(),
            "correlation_id": correlation_id,
            "metadata": payload.metadata or {},
            "read": False,
        }
        self.sent_notifications.append(notification_entry)

        if self.dry_run:
            logger.info(
                f"[DRY RUN] In-App notification created for recipient={recipient_uuid}, "
                f"title={payload.title!r}, correlation_id={correlation_id}"
            )
            return DispatchResult(
                success=True,
                channel="InApp",
                recipient=str(recipient_uuid),
                dispatched_at=dispatched_at,
                correlation_id=correlation_id,
                message_id=message_id,
                details=details,
            )

        # Database persistence
        current_session: Optional[Session] = self.session
        close_session_on_exit = False
        if current_session is None and self.session_factory is not None:
            current_session = self.session_factory()
            close_session_on_exit = True

        if current_session is not None:
            try:
                log_entry = NotificationLog(
                    notification_id=uuid.uuid4(),
                    recipient_id=recipient_uuid,
                    trigger_id=trigger_uuid,
                    urgency=payload.urgency or "Low",
                    channel="InApp",
                    delivery_status="Sent",
                    dispatched_at=dispatched_at,
                    correlation_id=correlation_id,
                )
                current_session.add(log_entry)
                if self.auto_commit:
                    current_session.commit()
                else:
                    current_session.flush()
                details["notification_id"] = str(log_entry.notification_id)
            except Exception as exc:
                if self.auto_commit:
                    current_session.rollback()
                logger.error(
                    f"Failed to persist in-app notification record to database for recipient={recipient_uuid}: {exc}",
                    exc_info=True,
                )
                raise InAppDispatchError(
                    f"Database error persisting in-app notification: {exc}"
                ) from exc
            finally:
                if close_session_on_exit and current_session is not None:
                    current_session.close()

        logger.info(
            f"Successfully persisted in-app notification for recipient={recipient_uuid}, "
            f"title={payload.title!r}, correlation_id={correlation_id}"
        )

        return DispatchResult(
            success=True,
            channel="InApp",
            recipient=str(recipient_uuid),
            dispatched_at=dispatched_at,
            correlation_id=correlation_id,
            message_id=message_id,
            details=details,
        )

    def get_in_app_notifications(
        self, recipient_id: Optional[Union[uuid.UUID, str]] = None
    ) -> List[Dict[str, Any]]:
        """Retrieve sent in-app notifications from in-memory tracking or session.

        Args:
            recipient_id: Optional filter for recipient UUID.

        Returns:
            List of dictionary items representing in-app notifications.
        """
        if recipient_id is None:
            return list(self.sent_notifications)

        target_str = str(recipient_id).strip()
        return [
            n for n in self.sent_notifications if n["recipient_id"] == target_str
        ]

    def clear(self) -> None:
        """Clear recorded in-memory notifications (primarily for testing)."""
        self.sent_notifications.clear()


# Convenient class aliases
InAppConnector = InAppPersistenceConnector
InAppAdapter = InAppPersistenceConnector
InAppPersistenceAdapter = InAppPersistenceConnector
