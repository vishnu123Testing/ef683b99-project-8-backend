"""Trigger evaluation engine, alert deduplication hashing, and role/urgency channel matrix routing [FR-6, FR-7, FR-8, FR-11].

Provides domain business logic for evaluating financial criteria against custom alert triggers,
calculating deterministic deduplication hashes for burst alert suppression, looking up or defaulting
role-based notification channel routing matrices, and constructing dispatch payloads.
"""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union
import uuid

from sqlalchemy.orm import Session

from src.lib.config import get_settings
from src.models.alerting import AlertSuppression, AlertTrigger, NotificationPreference
from src.services.alerting.ports import (
    DispatchResult,
    EmailDispatcherPort,
    EmailPayload,
    InAppDispatcherPort,
    InAppPayload,
    SMSDispatcherPort,
    SMSPayload,
)

logger = logging.getLogger(__name__)

DEFAULT_CHANNEL_MATRIX: Dict[str, List[str]] = {
    "High": ["InApp", "Email", "SMS"],
    "Medium": ["InApp", "Email"],
    "Low": ["InApp", "Email"],
}


class EvaluatorError(Exception):
    """Base exception for alert trigger evaluation operations."""

    pass


class TriggerNotFoundError(EvaluatorError):
    """Raised when an expected trigger rule is missing."""

    pass


@dataclass
class EvaluationEvent:
    """Dataclass holding financial event payload parameters for trigger evaluation [FR-6]."""

    financial_criteria: str
    value: Union[Decimal, float, int]
    entity_id: str
    event_type: str
    role_name: str
    recipient_id: Optional[Union[uuid.UUID, str]] = None
    recipient_email: Optional[str] = None
    recipient_phone: Optional[str] = None
    correlation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert EvaluationEvent to dictionary representation."""
        return {
            "financial_criteria": self.financial_criteria,
            "value": float(self.value),
            "entity_id": self.entity_id,
            "event_type": self.event_type,
            "role_name": self.role_name,
            "recipient_id": str(self.recipient_id) if self.recipient_id else None,
            "recipient_email": self.recipient_email,
            "recipient_phone": self.recipient_phone,
            "correlation_id": self.correlation_id,
            "details": self.details,
        }


@dataclass
class RoutedAlert:
    """Dataclass representing the evaluated and channel-routed alert result [FR-6, FR-7, FR-8, FR-11]."""

    rule_name: str
    financial_criteria: str
    threshold_amount: float
    urgency: str
    dedup_hash: str
    is_suppressed: bool
    channels: List[str]
    event: EvaluationEvent
    correlation_id: str
    trigger_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert RoutedAlert to dictionary representation."""
        return {
            "rule_name": self.rule_name,
            "financial_criteria": self.financial_criteria,
            "threshold_amount": self.threshold_amount,
            "urgency": self.urgency,
            "dedup_hash": self.dedup_hash,
            "is_suppressed": self.is_suppressed,
            "channels": self.channels,
            "event": self.event.to_dict(),
            "correlation_id": self.correlation_id,
            "trigger_id": self.trigger_id,
        }


def compute_dedup_hash(
    rule_name_or_trigger_id: str,
    event_type: str,
    entity_id: str,
    recipient_or_role: str = "",
) -> str:
    """Calculate a deterministic SHA-256 deduplication hash string [FR-11].

    :param rule_name_or_trigger_id: Rule name or trigger ID string
    :param event_type: Event type classifier (e.g. 'invoice_overdue')
    :param entity_id: Financial entity UUID/identifier
    :param recipient_or_role: Recipient ID or role name string
    :return: 64-character SHA-256 hex digest string
    """
    raw_key = f"{rule_name_or_trigger_id}:{event_type}:{entity_id}:{recipient_or_role}"
    return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


def _get_utc_now() -> datetime:
    """Return current UTC datetime with timezone info."""
    return datetime.now(timezone.utc)


def is_alert_suppressed(session: Session, dedup_hash: str) -> bool:
    """Check if an alert deduplication hash is currently active in the suppression window [FR-11].

    :param session: Synchronous SQLAlchemy session
    :param dedup_hash: SHA-256 deduplication hash string
    :return: True if active unexpired suppression exists, False otherwise
    """
    now = _get_utc_now()
    existing = (
        session.query(AlertSuppression)
        .filter(
            AlertSuppression.dedup_hash == dedup_hash,
            AlertSuppression.expires_at > now,
        )
        .first()
    )
    return existing is not None


def record_suppression(
    session: Session,
    dedup_hash: str,
    suppression_window_seconds: Optional[int] = None,
) -> AlertSuppression:
    """Record or renew an alert deduplication suppression entry in the database [FR-11].

    :param session: Synchronous SQLAlchemy session
    :param dedup_hash: SHA-256 deduplication hash string
    :param suppression_window_seconds: Duration in seconds for suppression window (defaults to config setting)
    :return: Saved or updated AlertSuppression ORM instance
    """
    settings = get_settings()
    if suppression_window_seconds is None:
        suppression_window_seconds = settings.alert_suppression_window_seconds

    now = _get_utc_now()
    expires_at = now + timedelta(seconds=suppression_window_seconds)

    existing = (
        session.query(AlertSuppression)
        .filter(AlertSuppression.dedup_hash == dedup_hash)
        .first()
    )

    if existing:
        existing.last_triggered_at = now
        existing.expires_at = expires_at
        session.add(existing)
        session.flush()
        return existing

    suppression = AlertSuppression(
        dedup_hash=dedup_hash,
        last_triggered_at=now,
        expires_at=expires_at,
    )
    session.add(suppression)
    session.flush()
    return suppression


def get_routed_channels(
    session: Session,
    role_name: str,
    event_type: str,
    urgency: str,
) -> List[str]:
    """Look up notification preference channels for role/event/urgency or default to urgency matrix [FR-7, FR-8].

    :param session: Synchronous SQLAlchemy session
    :param role_name: Target user role name
    :param event_type: Event type identifier
    :param urgency: Event urgency level ('Low', 'Medium', 'High')
    :return: List of target channel names (e.g. ['InApp', 'Email', 'SMS'])
    """
    normalized_urgency = urgency.capitalize() if urgency else "Low"

    pref = (
        session.query(NotificationPreference)
        .filter(
            NotificationPreference.role_name == role_name.strip(),
            NotificationPreference.event_type == event_type.strip(),
            NotificationPreference.urgency == normalized_urgency,
        )
        .first()
    )

    if pref and pref.channels_json and isinstance(pref.channels_json, list):
        # Return unique channels preserving order
        unique_channels: List[str] = []
        for ch in pref.channels_json:
            if isinstance(ch, str) and ch not in unique_channels:
                unique_channels.append(ch)
        if unique_channels:
            return unique_channels

    # Fallback to default channel matrix
    return DEFAULT_CHANNEL_MATRIX.get(normalized_urgency, ["InApp", "Email"])


def evaluate_trigger_rule(
    trigger: AlertTrigger,
    value: Union[Decimal, float, int],
) -> bool:
    """Evaluate whether a numeric financial value satisfies an active AlertTrigger rule [FR-6].

    :param trigger: AlertTrigger ORM model instance
    :param value: Observed numeric value
    :return: True if trigger is active and value meets or exceeds threshold_amount
    """
    if not trigger.is_active:
        return False

    val_decimal = Decimal(str(value))
    return val_decimal >= trigger.threshold_amount


def evaluate_active_triggers(
    session: Session,
    financial_criteria: str,
    value: Union[Decimal, float, int],
) -> List[AlertTrigger]:
    """Retrieve and evaluate all active alert triggers matching a financial criteria [FR-6].

    :param session: Synchronous SQLAlchemy session
    :param financial_criteria: Financial criteria identifier (e.g. 'past_due_balance_exceeded')
    :param value: Observed numeric value to test
    :return: List of active AlertTrigger instances whose thresholds are met or exceeded
    """
    triggers = (
        session.query(AlertTrigger)
        .filter(
            AlertTrigger.is_active == True,  # noqa: E712
            AlertTrigger.financial_criteria == financial_criteria.strip(),
        )
        .all()
    )

    matched: List[AlertTrigger] = []
    for trigger in triggers:
        if evaluate_trigger_rule(trigger, value):
            matched.append(trigger)

    return matched


def build_dispatch_payloads(
    event: EvaluationEvent,
    trigger_rule_name: str,
    urgency: str,
    channels: List[str],
    correlation_id: str,
    trigger_id: Optional[Union[uuid.UUID, str]] = None,
) -> Dict[str, Any]:
    """Construct multi-channel message dispatch payloads based on routed channels and event data [FR-8].

    :param event: EvaluationEvent instance
    :param trigger_rule_name: Rule name string
    :param urgency: Urgency level ('Low', 'Medium', 'High')
    :param channels: List of routed channel strings
    :param correlation_id: Context correlation ID
    :param trigger_id: Optional trigger UUID or string
    :return: Dictionary mapping channel name ('InApp', 'Email', 'SMS') to payload dataclass
    """
    payloads: Dict[str, Any] = {}
    title_text = f"Financial Alert: {trigger_rule_name}"
    body_text = (
        f"Financial alert '{trigger_rule_name}' triggered for {event.financial_criteria} "
        f"with value {event.value} (Entity: {event.entity_id})."
    )

    trig_id_str = str(trigger_id) if trigger_id else None

    if "InApp" in channels and event.recipient_id:
        payloads["InApp"] = InAppPayload(
            recipient_id=event.recipient_id,
            title=title_text,
            message_text=body_text,
            urgency=urgency,
            correlation_id=correlation_id,
            trigger_id=trig_id_str,
            metadata={
                "event_type": event.event_type,
                "entity_id": event.entity_id,
                "role_name": event.role_name,
                **event.details,
            },
        )

    if "Email" in channels and event.recipient_email:
        payloads["Email"] = EmailPayload(
            recipient_email=event.recipient_email,
            subject=title_text,
            body_text=body_text,
            body_html=f"<h3>{title_text}</h3><p>{body_text}</p>",
            urgency=urgency,
            correlation_id=correlation_id,
            recipient_id=event.recipient_id,
            trigger_id=trig_id_str,
            metadata={
                "event_type": event.event_type,
                "entity_id": event.entity_id,
                "role_name": event.role_name,
                **event.details,
            },
        )

    if "SMS" in channels and event.recipient_phone:
        payloads["SMS"] = SMSPayload(
            recipient_phone=event.recipient_phone,
            message_text=f"{title_text}: {body_text}"[:160],
            urgency=urgency,
            correlation_id=correlation_id,
            recipient_id=event.recipient_id,
            trigger_id=trig_id_str,
            metadata={
                "event_type": event.event_type,
                "entity_id": event.entity_id,
                "role_name": event.role_name,
                **event.details,
            },
        )

    return payloads


class AlertEvaluatorEngine:
    """Domain service engine coordinating alert trigger evaluation, deduplication, and channel routing [FR-6, FR-7, FR-8, FR-11]."""

    def __init__(
        self,
        session: Session,
        email_port: Optional[EmailDispatcherPort] = None,
        sms_port: Optional[SMSDispatcherPort] = None,
        in_app_port: Optional[InAppDispatcherPort] = None,
    ) -> None:
        """Initialize the AlertEvaluatorEngine.

        :param session: Synchronous SQLAlchemy database session
        :param email_port: Optional email dispatcher port adapter
        :param sms_port: Optional SMS dispatcher port adapter
        :param in_app_port: Optional in-app dispatcher port adapter
        """
        self.session = session
        self.email_port = email_port
        self.sms_port = sms_port
        self.in_app_port = in_app_port

    def evaluate_event(
        self,
        event: EvaluationEvent,
        auto_suppress: bool = True,
        suppression_window_seconds: Optional[int] = None,
    ) -> List[RoutedAlert]:
        """Evaluate a financial event against active trigger rules, apply deduplication, and route channels [FR-6, FR-7, FR-8, FR-11].

        :param event: EvaluationEvent instance describing financial criteria, value, and recipient/role
        :param auto_suppress: If True, record/renew suppression entry when an unsuppressed alert fires
        :param suppression_window_seconds: Optional custom suppression window duration
        :return: List of RoutedAlert objects corresponding to triggered rules
        """
        matched_triggers = evaluate_active_triggers(
            self.session,
            event.financial_criteria,
            event.value,
        )

        routed_alerts: List[RoutedAlert] = []

        for trigger in matched_triggers:
            recipient_key = (
                str(event.recipient_id)
                if event.recipient_id
                else event.role_name
            )
            dedup_hash = compute_dedup_hash(
                rule_name_or_trigger_id=trigger.rule_name,
                event_type=event.event_type,
                entity_id=event.entity_id,
                recipient_or_role=recipient_key,
            )

            suppressed = is_alert_suppressed(self.session, dedup_hash)

            if not suppressed and auto_suppress:
                record_suppression(
                    self.session,
                    dedup_hash,
                    suppression_window_seconds=suppression_window_seconds,
                )

            channels = get_routed_channels(
                self.session,
                event.role_name,
                event.event_type,
                trigger.urgency,
            )

            routed_alert = RoutedAlert(
                trigger_id=str(trigger.trigger_id) if trigger.trigger_id else None,
                rule_name=trigger.rule_name,
                financial_criteria=trigger.financial_criteria,
                threshold_amount=float(trigger.threshold_amount),
                urgency=trigger.urgency,
                dedup_hash=dedup_hash,
                is_suppressed=suppressed,
                channels=channels,
                event=event,
                correlation_id=event.correlation_id,
            )

            routed_alerts.append(routed_alert)

        return routed_alerts

    def dispatch_routed_alert(
        self,
        routed_alert: RoutedAlert,
    ) -> Dict[str, DispatchResult]:
        """Dispatch a non-suppressed routed alert across configured channel ports [FR-8].

        :param routed_alert: RoutedAlert instance
        :return: Dict mapping channel names to DispatchResult instances
        """
        if routed_alert.is_suppressed:
            logger.info(
                f"Skipping dispatch for suppressed alert dedup_hash={routed_alert.dedup_hash} "
                f"correlation_id={routed_alert.correlation_id}"
            )
            return {}

        payloads = build_dispatch_payloads(
            event=routed_alert.event,
            trigger_rule_name=routed_alert.rule_name,
            urgency=routed_alert.urgency,
            channels=routed_alert.channels,
            correlation_id=routed_alert.correlation_id,
            trigger_id=routed_alert.trigger_id,
        )

        results: Dict[str, DispatchResult] = {}

        if "InApp" in payloads and self.in_app_port:
            try:
                res = self.in_app_port.send_in_app(payloads["InApp"])
                results["InApp"] = res
            except Exception as exc:
                logger.error(
                    f"InApp dispatch failed correlation_id={routed_alert.correlation_id}: {exc}"
                )
                results["InApp"] = DispatchResult(
                    success=False,
                    channel="InApp",
                    recipient=str(routed_alert.event.recipient_id or ""),
                    dispatched_at=_get_utc_now(),
                    correlation_id=routed_alert.correlation_id,
                    error_message=str(exc),
                )

        if "Email" in payloads and self.email_port:
            try:
                res = self.email_port.send_email(payloads["Email"])
                results["Email"] = res
            except Exception as exc:
                logger.error(
                    f"Email dispatch failed correlation_id={routed_alert.correlation_id}: {exc}"
                )
                results["Email"] = DispatchResult(
                    success=False,
                    channel="Email",
                    recipient=routed_alert.event.recipient_email or "",
                    dispatched_at=_get_utc_now(),
                    correlation_id=routed_alert.correlation_id,
                    error_message=str(exc),
                )

        if "SMS" in payloads and self.sms_port:
            try:
                res = self.sms_port.send_sms(payloads["SMS"])
                results["SMS"] = res
            except Exception as exc:
                logger.error(
                    f"SMS dispatch failed correlation_id={routed_alert.correlation_id}: {exc}"
                )
                results["SMS"] = DispatchResult(
                    success=False,
                    channel="SMS",
                    recipient=routed_alert.event.recipient_phone or "",
                    dispatched_at=_get_utc_now(),
                    correlation_id=routed_alert.correlation_id,
                    error_message=str(exc),
                )

        return results
