"""Abstract port interfaces for multi-channel message dispatchers.

Defines port contracts and standardized message payloads for Email, SMS,
and In-App notification dispatchers [FR-8].
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional, Union
import uuid


class AlertingPortError(Exception):
    """Base exception for all alerting and notification port operations."""

    pass


class EmailDispatchError(AlertingPortError):
    """Raised when email message dispatch fails."""

    pass


class SMSDispatchError(AlertingPortError):
    """Raised when SMS message dispatch fails."""

    pass


class InAppDispatchError(AlertingPortError):
    """Raised when in-app notification creation or persistence fails."""

    pass


@dataclass
class EmailPayload:
    """Dataclass encapsulating parameters required for email notification dispatch [FR-8]."""

    recipient_email: str
    subject: str
    body_text: str
    body_html: Optional[str] = None
    urgency: str = "Low"  # Values: Low, Medium, High
    correlation_id: str = ""
    recipient_id: Optional[Union[uuid.UUID, str]] = None
    trigger_id: Optional[Union[uuid.UUID, str]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert EmailPayload to dictionary representation."""
        return {
            "recipient_email": self.recipient_email,
            "subject": self.subject,
            "body_text": self.body_text,
            "body_html": self.body_html,
            "urgency": self.urgency,
            "correlation_id": self.correlation_id,
            "recipient_id": str(self.recipient_id) if self.recipient_id else None,
            "trigger_id": str(self.trigger_id) if self.trigger_id else None,
            "metadata": self.metadata,
        }


@dataclass
class SMSPayload:
    """Dataclass encapsulating parameters required for SMS notification dispatch [FR-8]."""

    recipient_phone: str
    message_text: str
    urgency: str = "High"  # Values: Low, Medium, High
    correlation_id: str = ""
    recipient_id: Optional[Union[uuid.UUID, str]] = None
    trigger_id: Optional[Union[uuid.UUID, str]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert SMSPayload to dictionary representation."""
        return {
            "recipient_phone": self.recipient_phone,
            "message_text": self.message_text,
            "urgency": self.urgency,
            "correlation_id": self.correlation_id,
            "recipient_id": str(self.recipient_id) if self.recipient_id else None,
            "trigger_id": str(self.trigger_id) if self.trigger_id else None,
            "metadata": self.metadata,
        }


@dataclass
class InAppPayload:
    """Dataclass encapsulating parameters required for In-App notification dispatch [FR-8]."""

    recipient_id: Union[uuid.UUID, str]
    title: str
    message_text: str
    urgency: str = "Low"  # Values: Low, Medium, High
    correlation_id: str = ""
    trigger_id: Optional[Union[uuid.UUID, str]] = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert InAppPayload to dictionary representation."""
        return {
            "recipient_id": str(self.recipient_id),
            "title": self.title,
            "message_text": self.message_text,
            "urgency": self.urgency,
            "correlation_id": self.correlation_id,
            "trigger_id": str(self.trigger_id) if self.trigger_id else None,
            "metadata": self.metadata,
        }


@dataclass
class DispatchResult:
    """Dataclass holding metadata resulting from a notification message dispatch attempt [FR-8]."""

    success: bool
    channel: str  # Values: Email, SMS, InApp
    recipient: str
    dispatched_at: datetime
    correlation_id: str
    message_id: Optional[str] = None
    error_message: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert DispatchResult to dictionary representation."""
        return {
            "success": self.success,
            "channel": self.channel,
            "recipient": self.recipient,
            "dispatched_at": self.dispatched_at.isoformat(),
            "correlation_id": self.correlation_id,
            "message_id": self.message_id,
            "error_message": self.error_message,
            "details": self.details,
        }


class EmailDispatcherPort(ABC):
    """Abstract port interface for Email message dispatchers [FR-8].

    Abstracts underlying SMTP delivery, third-party email APIs, or fake test mailers
    from domain business rules and evaluator logic.
    """

    @abstractmethod
    def send_email(self, payload: EmailPayload) -> DispatchResult:
        """Dispatch an email message payload.

        Args:
            payload: EmailPayload object containing recipient, subject, body, urgency,
                and correlation metadata.

        Returns:
            DispatchResult containing dispatch status, timestamps, and message details.

        Raises:
            EmailDispatchError: If email dispatch fails due to network, auth, or provider error.
        """
        pass

    def dispatch(self, payload: EmailPayload) -> DispatchResult:
        """Convenience alias method for dispatching an email payload."""
        return self.send_email(payload)

    def send(self, payload: EmailPayload) -> DispatchResult:
        """Convenience alias method for sending an email payload."""
        return self.send_email(payload)


class SMSDispatcherPort(ABC):
    """Abstract port interface for SMS message dispatchers [FR-8].

    Abstracts underlying SMS gateway HTTP providers, cellular network APIs, or mock test gateways
    from domain business rules and evaluator logic.
    """

    @abstractmethod
    def send_sms(self, payload: SMSPayload) -> DispatchResult:
        """Dispatch an SMS message payload.

        Args:
            payload: SMSPayload object containing recipient phone number, text body, urgency,
                and correlation metadata.

        Returns:
            DispatchResult containing dispatch status, timestamps, and message details.

        Raises:
            SMSDispatchError: If SMS dispatch fails due to gateway error, phone invalidity, or network error.
        """
        pass

    def dispatch(self, payload: SMSPayload) -> DispatchResult:
        """Convenience alias method for dispatching an SMS payload."""
        return self.send_sms(payload)

    def send(self, payload: SMSPayload) -> DispatchResult:
        """Convenience alias method for sending an SMS payload."""
        return self.send_sms(payload)


class InAppDispatcherPort(ABC):
    """Abstract port interface for In-App message dispatchers [FR-8].

    Abstracts underlying database persistence and real-time in-app notification publishing
    from domain business rules and evaluator logic.
    """

    @abstractmethod
    def send_in_app(self, payload: InAppPayload) -> DispatchResult:
        """Dispatch and persist an in-app notification payload.

        Args:
            payload: InAppPayload object containing recipient ID, title, text body, urgency,
                and correlation metadata.

        Returns:
            DispatchResult containing dispatch status, timestamps, and notification details.

        Raises:
            InAppDispatchError: If in-app notification creation or persistence fails.
        """
        pass

    def dispatch(self, payload: InAppPayload) -> DispatchResult:
        """Convenience alias method for dispatching an in-app payload."""
        return self.send_in_app(payload)

    def send(self, payload: InAppPayload) -> DispatchResult:
        """Convenience alias method for sending an in-app payload."""
        return self.send_in_app(payload)


# Convenient port aliases
EmailPort = EmailDispatcherPort
SMSPort = SMSDispatcherPort
InAppPort = InAppDispatcherPort
