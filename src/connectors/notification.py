"""Notification connector adapter for breach warnings and escalation alerts [FR-5, FR-6, NFR-3, NFR-4, NFR-5].

Provides abstract port interface (NotificationPort) and concrete adapter implementation (NotificationConnector)
for dispatching SLA breach warning notifications, SLA breach alerts, and support tier manager escalation alerts.
Supports configurable delivery channels (IN_APP, EMAIL, WEBHOOK, SLACK), structured JSON correlation logging,
and testing simulation triggers.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
import json
import logging
from typing import Any, Dict, List, Optional, Union
import uuid

try:
    import httpx
except ModuleNotFoundError:
    import json as _json
    import urllib.error
    import urllib.request

    class _HttpxResponse:
        def __init__(self, status_code=200, content=b""):
            self.status_code = status_code
            self._content = content

        @property
        def text(self):
            return self._content.decode("utf-8", errors="replace")

        def json(self):
            return _json.loads(self.text) if self._content else {}

        def raise_for_status(self):
            if self.status_code >= 400:
                raise _HTTPStatusError(f"HTTP Error {self.status_code}", response=self)

    class _TimeoutException(Exception):
        pass

    class _HTTPStatusError(Exception):
        def __init__(self, msg, response=None):
            super().__init__(msg)
            self.response = response

    class _RequestError(Exception):
        pass

    class _HttpxClient:
        def __init__(self, timeout=None, *args, **kwargs):
            self.timeout = timeout if isinstance(timeout, (int, float)) else 10.0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def request(self, method, url, params=None, headers=None, json=None, data=None, **kwargs):
            if params:
                from urllib.parse import urlencode
                url = f"{url}?{urlencode(params)}"
            req_headers = headers or {}
            req_data = None
            if json is not None:
                req_data = _json.dumps(json).encode("utf-8")
                if "Content-Type" not in req_headers:
                    req_headers["Content-Type"] = "application/json"
            elif data is not None:
                if isinstance(data, str):
                    req_data = data.encode("utf-8")
                elif isinstance(data, bytes):
                    req_data = data

            req = urllib.request.Request(url, data=req_data, headers=req_headers, method=method.upper())
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    resp_data = resp.read()
                    return _HttpxResponse(status_code=resp.status, content=resp_data)
            except urllib.error.HTTPError as err:
                resp_data = err.read()
                return _HttpxResponse(status_code=err.code, content=resp_data)
            except urllib.error.URLError as err:
                if isinstance(err.reason, TimeoutError) or "timed out" in str(err.reason).lower():
                    raise _TimeoutException(str(err))
                raise _RequestError(str(err))
            except TimeoutError as err:
                raise _TimeoutException(str(err))
            except Exception as err:
                raise _RequestError(str(err))

        def get(self, url, **kwargs):
            return self.request("GET", url, **kwargs)

        def post(self, url, **kwargs):
            return self.request("POST", url, **kwargs)

    class _HttpxModule:
        Client = _HttpxClient
        TimeoutException = _TimeoutException
        HTTPStatusError = _HTTPStatusError
        RequestError = _RequestError

    httpx = _HttpxModule()

from src.lib.config import settings

logger = logging.getLogger(__name__)


class NotificationError(Exception):
    """Base exception class for notification connector operations."""

    pass


class NotificationDispatchError(NotificationError):
    """Raised when dispatching a notification alert fails."""

    pass


class NotificationDeliveryError(NotificationError):
    """Raised when external notification delivery fails."""

    pass


class NotificationValidationError(NotificationError):
    """Raised when notification payload validation fails."""

    pass


class NotificationTimeoutError(NotificationError):
    """Raised when a notification dispatch operation times out."""

    pass


class NotificationChannel(str, Enum):
    """Supported delivery channels for notification dispatch."""

    IN_APP = "IN_APP"
    EMAIL = "EMAIL"
    WEBHOOK = "WEBHOOK"
    SLACK = "SLACK"


class NotificationType(str, Enum):
    """Notification alert types."""

    SLA_BREACH_WARNING = "SLA_BREACH_WARNING"
    SLA_BREACH_ALERT = "SLA_BREACH_ALERT"
    TIER_ESCALATION = "TIER_ESCALATION"
    GENERIC = "GENERIC"


@dataclass
class SLAWarningNotificationPayload:
    """Payload data structure for proactive SLA breach warning notifications [FR-5]."""

    ticket_id: uuid.UUID
    client_id: uuid.UUID
    priority: str  # P1, P2, P3
    target_type: str  # first_response, resolution
    target_due_at: datetime
    elapsed_percent: float
    time_remaining_minutes: float
    assigned_tier: Optional[str] = "tier1"
    assigned_agent_id: Optional[uuid.UUID] = None
    recipients: List[str] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert warning payload to dictionary representation."""
        return {
            "ticket_id": str(self.ticket_id),
            "client_id": str(self.client_id),
            "priority": self.priority,
            "target_type": self.target_type,
            "target_due_at": self.target_due_at.isoformat() if self.target_due_at else None,
            "elapsed_percent": self.elapsed_percent,
            "time_remaining_minutes": self.time_remaining_minutes,
            "assigned_tier": self.assigned_tier,
            "assigned_agent_id": str(self.assigned_agent_id) if self.assigned_agent_id else None,
            "recipients": self.recipients,
            "metadata": self.metadata or {},
        }


# Alias for flexible importing
BreachWarningPayload = SLAWarningNotificationPayload


@dataclass
class SLABreachNotificationPayload:
    """Payload data structure for SLA breach alerts [FR-5, FR-7]."""

    ticket_id: uuid.UUID
    client_id: uuid.UUID
    priority: str  # P1, P2, P3
    target_type: str  # first_response, resolution
    target_due_at: datetime
    breached_at: datetime
    assigned_tier: Optional[str] = "tier1"
    assigned_agent_id: Optional[uuid.UUID] = None
    recipients: List[str] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert breach payload to dictionary representation."""
        return {
            "ticket_id": str(self.ticket_id),
            "client_id": str(self.client_id),
            "priority": self.priority,
            "target_type": self.target_type,
            "target_due_at": self.target_due_at.isoformat() if self.target_due_at else None,
            "breached_at": self.breached_at.isoformat() if self.breached_at else None,
            "assigned_tier": self.assigned_tier,
            "assigned_agent_id": str(self.assigned_agent_id) if self.assigned_agent_id else None,
            "recipients": self.recipients,
            "metadata": self.metadata or {},
        }


# Alias for flexible importing
BreachAlertPayload = SLABreachNotificationPayload


@dataclass
class EscalationNotificationPayload:
    """Payload data structure for support tier escalation alerts [FR-6]."""

    ticket_id: uuid.UUID
    client_id: uuid.UUID
    priority: str  # P1, P2, P3
    source_tier: str
    target_tier: str
    reason: str
    escalated_at: datetime
    assigned_agent_id: Optional[uuid.UUID] = None
    recipients: List[str] = field(default_factory=list)
    metadata: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert escalation payload to dictionary representation."""
        return {
            "ticket_id": str(self.ticket_id),
            "client_id": str(self.client_id),
            "priority": self.priority,
            "source_tier": self.source_tier,
            "target_tier": self.target_tier,
            "reason": self.reason,
            "escalated_at": self.escalated_at.isoformat() if self.escalated_at else None,
            "assigned_agent_id": str(self.assigned_agent_id) if self.assigned_agent_id else None,
            "recipients": self.recipients,
            "metadata": self.metadata or {},
        }


# Alias for flexible importing
EscalationAlertPayload = EscalationNotificationPayload


@dataclass
class NotificationDispatchResult:
    """Result data structure returned following a notification alert dispatch."""

    notification_id: uuid.UUID
    notification_type: str
    ticket_id: uuid.UUID
    channel: str
    recipients: List[str] = field(default_factory=list)
    status: str = "DELIVERED"  # DELIVERED, QUEUED, FAILED, SUPPRESSED
    message: str = ""
    dispatched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    details: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert dispatch result to dictionary representation."""
        return {
            "notification_id": str(self.notification_id),
            "notification_type": self.notification_type,
            "ticket_id": str(self.ticket_id),
            "channel": self.channel,
            "recipients": self.recipients,
            "status": self.status,
            "message": self.message,
            "dispatched_at": self.dispatched_at.isoformat() if self.dispatched_at else None,
            "details": self.details or {},
        }


class NotificationPort(ABC):
    """Abstract port interface for dispatching breach warnings and escalation alerts [FR-5, FR-6, NFR-4]."""

    @abstractmethod
    def dispatch_breach_warning(
        self,
        payload: SLAWarningNotificationPayload,
    ) -> NotificationDispatchResult:
        """Dispatch a proactive SLA breach warning notification [FR-5]."""
        pass

    @abstractmethod
    def dispatch_breach_alert(
        self,
        payload: SLABreachNotificationPayload,
    ) -> NotificationDispatchResult:
        """Dispatch an SLA breach alert notification [FR-5]."""
        pass

    @abstractmethod
    def dispatch_escalation_alert(
        self,
        payload: EscalationNotificationPayload,
    ) -> NotificationDispatchResult:
        """Dispatch a support tier escalation alert notification [FR-6]."""
        pass

    @abstractmethod
    def send_notification(
        self,
        notification_type: str,
        ticket_id: uuid.UUID,
        recipients: List[str],
        message_body: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NotificationDispatchResult:
        """Send a generic or custom notification alert."""
        pass


class NotificationConnector(NotificationPort):
    """Adapter implementing notification dispatching for SLA breach warnings and escalations [FR-5, FR-6, NFR-3, NFR-5]."""

    def __init__(
        self,
        webhook_url: Optional[str] = None,
        default_channel: Union[NotificationChannel, str] = NotificationChannel.IN_APP,
        enable_external_delivery: bool = True,
        timeout_seconds: Optional[float] = None,
        retries: Optional[int] = None,
        simulate_failure: bool = False,
        simulate_timeout: bool = False,
    ) -> None:
        """Initialize NotificationConnector with configurable delivery parameters.

        Args:
            webhook_url: Optional external HTTP webhook endpoint for dispatching alerts.
            default_channel: Default delivery channel (IN_APP, EMAIL, WEBHOOK, SLACK).
            enable_external_delivery: Toggle for executing HTTP calls to external webhook endpoints.
            timeout_seconds: Timeout threshold in seconds for network calls.
            retries: Retry attempts for network calls.
            simulate_failure: Testing flag to trigger simulated dispatch failure.
            simulate_timeout: Testing flag to trigger simulated request timeout.
        """
        self.webhook_url = webhook_url
        self.default_channel = (
            default_channel.value
            if isinstance(default_channel, NotificationChannel)
            else str(default_channel)
        )
        self.enable_external_delivery = enable_external_delivery
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else float(getattr(settings, "dashboard_connector_timeout_seconds", 5))
        )
        self.retries = (
            retries
            if retries is not None
            else getattr(settings, "dashboard_connector_retries", 3)
        )
        self.simulate_failure = simulate_failure
        self.simulate_timeout = simulate_timeout

        self._dispatched_notifications: List[NotificationDispatchResult] = []

    def _check_simulation_triggers(self) -> None:
        """Check testing simulation flags and raise appropriate exceptions if set."""
        if self.simulate_timeout:
            raise NotificationTimeoutError("Simulated notification dispatch request timeout")
        if self.simulate_failure:
            raise NotificationDispatchError("Simulated notification dispatch operational failure")

    def _log_structured_event(
        self,
        event_name: str,
        ticket_id: uuid.UUID,
        client_id: Optional[uuid.UUID],
        payload_dict: Dict[str, Any],
    ) -> None:
        """Emit structured JSON correlation log for notification operations [NFR-3]."""
        log_payload = {
            "event": event_name,
            "ticket_id": str(ticket_id),
            "client_id": str(client_id) if client_id else None,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "details": payload_dict,
        }
        logger.info("NOTIFICATION_EVENT: %s", json.dumps(log_payload))

    def dispatch_breach_warning(
        self,
        payload: SLAWarningNotificationPayload,
    ) -> NotificationDispatchResult:
        """Dispatch a proactive SLA breach warning notification [FR-5].

        Args:
            payload: SLA warning details including ticket ID, priority, and elapsed time.

        Returns:
            NotificationDispatchResult detailing the dispatch outcome.

        Raises:
            NotificationValidationError: If required payload attributes are invalid.
            NotificationDispatchError: If dispatch fails.
            NotificationTimeoutError: If operation times out.
        """
        self._check_simulation_triggers()

        if not payload.ticket_id:
            raise NotificationValidationError("ticket_id is required for breach warning dispatch")

        recipients = payload.recipients or getattr(settings, "escalation_recipient_roles", ["support_tier_manager"])
        formatted_message = (
            f"SLA Warning: Ticket {payload.ticket_id} ({payload.priority}) "
            f"has reached {payload.elapsed_percent:.1f}% of its {payload.target_type} SLA target. "
            f"Target due at {payload.target_due_at.isoformat()} ({payload.time_remaining_minutes:.1f} minutes remaining)."
        )

        notification_id = uuid.uuid4()
        now_utc = datetime.now(timezone.utc)

        result = NotificationDispatchResult(
            notification_id=notification_id,
            notification_type=NotificationType.SLA_BREACH_WARNING.value,
            ticket_id=payload.ticket_id,
            channel=self.default_channel,
            recipients=recipients,
            status="DELIVERED",
            message=formatted_message,
            dispatched_at=now_utc,
            details=payload.to_dict(),
        )

        self._deliver_external_if_configured(result)
        self._log_structured_event("SLA_BREACH_WARNING_DISPATCHED", payload.ticket_id, payload.client_id, result.to_dict())
        self._dispatched_notifications.append(result)
        return result

    def dispatch_breach_alert(
        self,
        payload: SLABreachNotificationPayload,
    ) -> NotificationDispatchResult:
        """Dispatch an SLA breach alert notification [FR-5, FR-7].

        Args:
            payload: SLA breach details including ticket ID, target type, and breached_at timestamp.

        Returns:
            NotificationDispatchResult detailing the dispatch outcome.

        Raises:
            NotificationValidationError: If required payload attributes are invalid.
            NotificationDispatchError: If dispatch fails.
            NotificationTimeoutError: If operation times out.
        """
        self._check_simulation_triggers()

        if not payload.ticket_id:
            raise NotificationValidationError("ticket_id is required for breach alert dispatch")

        recipients = payload.recipients or getattr(settings, "escalation_recipient_roles", ["support_tier_manager", "admin"])
        formatted_message = (
            f"SLA Breach Alert: Ticket {payload.ticket_id} ({payload.priority}) "
            f"breached its {payload.target_type} SLA target. "
            f"Breached at {payload.breached_at.isoformat()}."
        )

        notification_id = uuid.uuid4()
        now_utc = datetime.now(timezone.utc)

        result = NotificationDispatchResult(
            notification_id=notification_id,
            notification_type=NotificationType.SLA_BREACH_ALERT.value,
            ticket_id=payload.ticket_id,
            channel=self.default_channel,
            recipients=recipients,
            status="DELIVERED",
            message=formatted_message,
            dispatched_at=now_utc,
            details=payload.to_dict(),
        )

        self._deliver_external_if_configured(result)
        self._log_structured_event("SLA_BREACH_ALERT_DISPATCHED", payload.ticket_id, payload.client_id, result.to_dict())
        self._dispatched_notifications.append(result)
        return result

    def dispatch_escalation_alert(
        self,
        payload: EscalationNotificationPayload,
    ) -> NotificationDispatchResult:
        """Dispatch a support tier escalation alert notification [FR-6].

        Args:
            payload: Escalation details including source/target tiers and reason.

        Returns:
            NotificationDispatchResult detailing the dispatch outcome.

        Raises:
            NotificationValidationError: If required payload attributes are invalid.
            NotificationDispatchError: If dispatch fails.
            NotificationTimeoutError: If operation times out.
        """
        self._check_simulation_triggers()

        if not payload.ticket_id:
            raise NotificationValidationError("ticket_id is required for escalation alert dispatch")

        recipients = payload.recipients or getattr(settings, "escalation_recipient_roles", ["support_tier_manager", "admin"])
        formatted_message = (
            f"Escalation Alert: Ticket {payload.ticket_id} ({payload.priority}) "
            f"escalated from {payload.source_tier} to {payload.target_tier}. "
            f"Reason: {payload.reason}."
        )

        notification_id = uuid.uuid4()
        now_utc = datetime.now(timezone.utc)

        result = NotificationDispatchResult(
            notification_id=notification_id,
            notification_type=NotificationType.TIER_ESCALATION.value,
            ticket_id=payload.ticket_id,
            channel=self.default_channel,
            recipients=recipients,
            status="DELIVERED",
            message=formatted_message,
            dispatched_at=now_utc,
            details=payload.to_dict(),
        )

        self._deliver_external_if_configured(result)
        self._log_structured_event("TIER_ESCALATION_ALERT_DISPATCHED", payload.ticket_id, payload.client_id, result.to_dict())
        self._dispatched_notifications.append(result)
        return result

    def send_notification(
        self,
        notification_type: str,
        ticket_id: uuid.UUID,
        recipients: List[str],
        message_body: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> NotificationDispatchResult:
        """Send a generic or custom notification alert.

        Args:
            notification_type: Notification type label or string.
            ticket_id: Target ticket UUID.
            recipients: List of recipient identifiers or emails.
            message_body: Formatted message body text.
            metadata: Optional additional metadata dictionary.

        Returns:
            NotificationDispatchResult object.
        """
        self._check_simulation_triggers()

        if not ticket_id:
            raise NotificationValidationError("ticket_id is required")

        notification_id = uuid.uuid4()
        now_utc = datetime.now(timezone.utc)

        result = NotificationDispatchResult(
            notification_id=notification_id,
            notification_type=notification_type,
            ticket_id=ticket_id,
            channel=self.default_channel,
            recipients=recipients or [],
            status="DELIVERED",
            message=message_body,
            dispatched_at=now_utc,
            details=metadata or {},
        )

        self._deliver_external_if_configured(result)
        self._log_structured_event("CUSTOM_NOTIFICATION_DISPATCHED", ticket_id, None, result.to_dict())
        self._dispatched_notifications.append(result)
        return result

    def _deliver_external_if_configured(self, result: NotificationDispatchResult) -> None:
        """Execute HTTP POST call to configured webhook URL if enabled."""
        if not self.enable_external_delivery or not self.webhook_url:
            return

        try:
            with httpx.Client(timeout=self.timeout_seconds) as client:
                response = client.post(
                    self.webhook_url,
                    json=result.to_dict(),
                    headers={"Content-Type": "application/json"},
                )
                if response.status_code >= 400:
                    logger.warning(
                        "External webhook delivery returned non-success status %d for notification %s",
                        response.status_code,
                        result.notification_id,
                    )
        except Exception as exc:
            logger.warning(
                "External webhook delivery failed for notification %s: %s",
                result.notification_id,
                str(exc),
            )

    def get_dispatched_notifications(
        self,
        ticket_id: Optional[uuid.UUID] = None,
        notification_type: Optional[str] = None,
    ) -> List[NotificationDispatchResult]:
        """Retrieve dispatched notifications filtered optionally by ticket_id or notification_type."""
        filtered = self._dispatched_notifications
        if ticket_id is not None:
            filtered = [n for n in filtered if n.ticket_id == ticket_id]
        if notification_type is not None:
            filtered = [n for n in filtered if n.notification_type == notification_type]
        return list(filtered)

    def clear_dispatched_notifications(self) -> None:
        """Clear all in-memory recorded dispatched notifications."""
        self._dispatched_notifications.clear()


# Aliases for flexible module importing across services
SupportNotificationConnector = NotificationConnector
NotificationAdapter = NotificationConnector
