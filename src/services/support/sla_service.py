"""SLA evaluation logic, target deadline calculation, breach detection, and priority recalculation service [FR-4, FR-5, FR-6, FR-7, NFR-1, NFR-3, NFR-4].

Provides business logic for calculating first response and resolution SLA target deadlines,
evaluating countdown statuses, detecting threshold warnings and breach conditions,
recalculating SLA targets on ticket priority updates, and executing support tier escalations.
"""

from datetime import datetime, timedelta, timezone
import logging
from typing import Any, Dict, List, Optional, Union
import uuid

from sqlalchemy.orm import Session

from src.connectors.notification import (
    EscalationNotificationPayload,
    NotificationPort,
    SLABreachNotificationPayload,
    SLAWarningNotificationPayload,
)
from src.lib.config import settings
from src.lib.logging import get_correlation_id
from src.models.ticket import SLATracking, SupportTicket, TicketEscalation

logger = logging.getLogger(__name__)


def calculate_target_due_at(
    priority: str,
    target_type: str,
    start_time: Optional[datetime] = None,
) -> datetime:
    """Calculate SLA target due datetime based on ticket priority and target type [FR-4].

    Args:
        priority: Ticket priority level ('P1', 'P2', 'P3').
        target_type: SLA target type ('first_response' or 'resolution').
        start_time: Baseline datetime for calculation. Defaults to current UTC time.

    Returns:
        Timezone-aware UTC datetime indicating when the SLA target is due.
    """
    p = priority.upper().strip() if priority else "P3"
    t_type = target_type.lower().strip() if target_type else "resolution"

    if start_time is None:
        base_time = datetime.now(timezone.utc)
    elif start_time.tzinfo is None:
        base_time = start_time.replace(tzinfo=timezone.utc)
    else:
        base_time = start_time.astimezone(timezone.utc)

    # Determine configured threshold hours from application settings
    if p == "P1":
        hours = (
            settings.sla_p1_response_hours
            if t_type == "first_response"
            else settings.sla_p1_resolution_hours
        )
    elif p == "P2":
        hours = (
            settings.sla_p2_response_hours
            if t_type == "first_response"
            else settings.sla_p2_resolution_hours
        )
    else:  # P3 or fallback
        hours = (
            settings.sla_p3_response_hours
            if t_type == "first_response"
            else settings.sla_p3_resolution_hours
        )

    return base_time + timedelta(hours=hours)


def calculate_remaining_time(
    target_due_at: datetime,
    current_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Calculate real-time remaining countdown metrics and formatted time string [FR-4, NFR-1].

    Args:
        target_due_at: Target SLA due datetime.
        current_time: Optional current time baseline. Defaults to UTC now.

    Returns:
        Dictionary containing remaining seconds, minutes, hours, breach status, and formatted string.
    """
    if current_time is None:
        now = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        now = current_time.replace(tzinfo=timezone.utc)
    else:
        now = current_time.astimezone(timezone.utc)

    if target_due_at.tzinfo is None:
        due = target_due_at.replace(tzinfo=timezone.utc)
    else:
        due = target_due_at.astimezone(timezone.utc)

    remaining_delta = (due - now).total_seconds()
    is_breached = remaining_delta < 0

    abs_sec = abs(int(remaining_delta))
    hours = abs_sec // 3600
    minutes = (abs_sec % 3600) // 60
    seconds = abs_sec % 60

    if is_breached:
        formatted = f"-{hours:02d}h {minutes:02d}m {seconds:02d}s"
    else:
        formatted = f"{hours:02d}h {minutes:02d}m {seconds:02d}s"

    return {
        "target_due_at": due.isoformat(),
        "current_time": now.isoformat(),
        "time_remaining_seconds": remaining_delta,
        "time_remaining_minutes": round(remaining_delta / 60.0, 2),
        "time_remaining_hours": round(remaining_delta / 3600.0, 2),
        "is_breached": is_breached,
        "formatted_countdown": formatted,
    }


def initialize_sla_tracking(
    db: Session,
    ticket: SupportTicket,
    current_time: Optional[datetime] = None,
) -> List[SLATracking]:
    """Initialize SLA countdown tracking records for first response and resolution targets [FR-4].

    Args:
        db: Synchronous database session.
        ticket: SupportTicket ORM entity.
        current_time: Optional reference time for start calculation.

    Returns:
        List of initialized or existing SLATracking ORM records for the ticket.
    """
    base_time = ticket.created_at or current_time or datetime.now(timezone.utc)
    if base_time.tzinfo is None:
        base_time = base_time.replace(tzinfo=timezone.utc)

    existing_trackings = {t.target_type: t for t in (ticket.sla_trackings or [])}
    result_trackings: List[SLATracking] = []

    for target_type in ["first_response", "resolution"]:
        due_at = calculate_target_due_at(ticket.priority, target_type, base_time)

        if target_type in existing_trackings:
            tracking = existing_trackings[target_type]
            tracking.target_due_at = due_at
        else:
            tracking = SLATracking(
                ticket_id=ticket.id,
                target_type=target_type,
                target_due_at=due_at,
                is_breached=False,
            )
            db.add(tracking)

        result_trackings.append(tracking)

    db.flush()

    logger.info(
        "Initialized SLA tracking records for support ticket",
        extra={
            "correlation_id": get_correlation_id(),
            "ticket_id": str(ticket.id),
            "priority": ticket.priority,
            "first_response_due_at": result_trackings[0].target_due_at.isoformat(),
            "resolution_due_at": result_trackings[1].target_due_at.isoformat(),
        },
    )

    return result_trackings


def recalculate_sla_targets(
    db: Session,
    ticket: SupportTicket,
    new_priority: str,
    current_time: Optional[datetime] = None,
) -> List[SLATracking]:
    """Recalculate SLA targets when ticket priority is updated [FR-4].

    Args:
        db: Synchronous database session.
        ticket: SupportTicket ORM entity.
        new_priority: New priority string ('P1', 'P2', 'P3').
        current_time: Optional current time baseline.

    Returns:
        Updated list of SLATracking ORM records.
    """
    normalized_priority = new_priority.upper().strip()
    if normalized_priority not in ("P1", "P2", "P3"):
        raise ValueError(f"Invalid ticket priority: {new_priority}. Must be P1, P2, or P3.")

    old_priority = ticket.priority
    ticket.priority = normalized_priority

    if current_time is None:
        now = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        now = current_time.replace(tzinfo=timezone.utc)
    else:
        now = current_time.astimezone(timezone.utc)

    base_time = ticket.created_at or now
    if base_time.tzinfo is None:
        base_time = base_time.replace(tzinfo=timezone.utc)

    trackings = initialize_sla_tracking(db, ticket, base_time)

    for tracking in trackings:
        if tracking.target_type == "first_response":
            if ticket.first_responded_at is None:
                new_due_at = calculate_target_due_at(normalized_priority, "first_response", base_time)
                tracking.target_due_at = new_due_at
                if now > new_due_at:
                    tracking.is_breached = True
                    tracking.breached_at = tracking.breached_at or now
                else:
                    tracking.is_breached = False
                    tracking.breached_at = None
                    tracking.warning_sent_at = None

        elif tracking.target_type == "resolution":
            if ticket.status not in ("resolved", "closed") and ticket.resolved_at is None:
                new_due_at = calculate_target_due_at(normalized_priority, "resolution", base_time)
                tracking.target_due_at = new_due_at
                if now > new_due_at:
                    tracking.is_breached = True
                    tracking.breached_at = tracking.breached_at or now
                else:
                    tracking.is_breached = False
                    tracking.breached_at = None
                    tracking.warning_sent_at = None

    db.flush()

    logger.info(
        "Recalculated SLA targets following priority modification",
        extra={
            "correlation_id": get_correlation_id(),
            "ticket_id": str(ticket.id),
            "old_priority": old_priority,
            "new_priority": normalized_priority,
        },
    )

    return trackings


def evaluate_ticket_slas(
    db: Session,
    ticket: SupportTicket,
    notification_connector: Optional[Any] = None,
    current_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Evaluate countdown timers, breach conditions, and warning thresholds for a ticket [FR-4, FR-5, NFR-3].

    Args:
        db: Synchronous database session.
        ticket: SupportTicket ORM entity.
        notification_connector: Optional notification connector instance for dispatching alerts.
        current_time: Optional datetime baseline for evaluation.

    Returns:
        Dictionary summarizing evaluation results including warning and breach triggers.
    """
    if current_time is None:
        now = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        now = current_time.replace(tzinfo=timezone.utc)
    else:
        now = current_time.astimezone(timezone.utc)

    base_time = ticket.created_at or now
    if base_time.tzinfo is None:
        base_time = base_time.replace(tzinfo=timezone.utc)

    trackings = initialize_sla_tracking(db, ticket, base_time)
    warning_triggered = False
    breach_triggered = False

    for tracking in trackings:
        due_at = tracking.target_due_at
        if due_at.tzinfo is None:
            due_at = due_at.replace(tzinfo=timezone.utc)

        total_sec = max(1.0, (due_at - base_time).total_seconds())
        elapsed_sec = (now - base_time).total_seconds()
        elapsed_percent = min(100.0, max(0.0, (elapsed_sec / total_sec) * 100.0))
        time_remaining_mins = max(0.0, (due_at - now).total_seconds() / 60.0)

        if tracking.target_type == "first_response":
            if ticket.first_responded_at is not None:
                responded_at = ticket.first_responded_at
                if responded_at.tzinfo is None:
                    responded_at = responded_at.replace(tzinfo=timezone.utc)
                if responded_at > due_at:
                    tracking.is_breached = True
                    tracking.breached_at = tracking.breached_at or responded_at
                else:
                    tracking.is_breached = False
            else:
                if now > due_at:
                    if not tracking.is_breached:
                        tracking.is_breached = True
                        tracking.breached_at = now
                        breach_triggered = True

                        logger.warning(
                            "SLA breach detected for first response target",
                            extra={
                                "correlation_id": get_correlation_id(),
                                "ticket_id": str(ticket.id),
                                "target_type": "first_response",
                                "target_due_at": due_at.isoformat(),
                                "breached_at": now.isoformat(),
                            },
                        )

                        if notification_connector and hasattr(notification_connector, "dispatch_breach_alert"):
                            try:
                                payload = SLABreachNotificationPayload(
                                    ticket_id=ticket.id,
                                    client_id=ticket.client_id,
                                    priority=ticket.priority,
                                    target_type="first_response",
                                    target_due_at=due_at,
                                    breached_at=now,
                                    assigned_tier=ticket.assigned_tier,
                                    assigned_agent_id=ticket.assigned_agent_id,
                                )
                                notification_connector.dispatch_breach_alert(payload)
                            except Exception as exc:
                                logger.error(
                                    "Failed to dispatch SLA breach alert notification",
                                    extra={"ticket_id": str(ticket.id), "error": str(exc)},
                                )

                elif not tracking.is_breached and tracking.warning_sent_at is None:
                    if elapsed_percent >= settings.sla_warning_threshold_percent:
                        tracking.warning_sent_at = now
                        warning_triggered = True

                        logger.info(
                            "SLA warning threshold crossed for first response target",
                            extra={
                                "correlation_id": get_correlation_id(),
                                "ticket_id": str(ticket.id),
                                "target_type": "first_response",
                                "elapsed_percent": elapsed_percent,
                            },
                        )

                        if notification_connector and hasattr(notification_connector, "dispatch_breach_warning"):
                            try:
                                payload = SLAWarningNotificationPayload(
                                    ticket_id=ticket.id,
                                    client_id=ticket.client_id,
                                    priority=ticket.priority,
                                    target_type="first_response",
                                    target_due_at=due_at,
                                    elapsed_percent=round(elapsed_percent, 2),
                                    time_remaining_minutes=round(time_remaining_mins, 2),
                                    assigned_tier=ticket.assigned_tier,
                                    assigned_agent_id=ticket.assigned_agent_id,
                                )
                                notification_connector.dispatch_breach_warning(payload)
                            except Exception as exc:
                                logger.error(
                                    "Failed to dispatch SLA breach warning notification",
                                    extra={"ticket_id": str(ticket.id), "error": str(exc)},
                                )

        elif tracking.target_type == "resolution":
            if ticket.status in ("resolved", "closed") or ticket.resolved_at is not None:
                res_at = ticket.resolved_at or now
                if res_at.tzinfo is None:
                    res_at = res_at.replace(tzinfo=timezone.utc)
                if res_at > due_at:
                    tracking.is_breached = True
                    tracking.breached_at = tracking.breached_at or res_at
                else:
                    tracking.is_breached = False
            else:
                if now > due_at:
                    if not tracking.is_breached:
                        tracking.is_breached = True
                        tracking.breached_at = now
                        breach_triggered = True

                        logger.warning(
                            "SLA breach detected for resolution target",
                            extra={
                                "correlation_id": get_correlation_id(),
                                "ticket_id": str(ticket.id),
                                "target_type": "resolution",
                                "target_due_at": due_at.isoformat(),
                                "breached_at": now.isoformat(),
                            },
                        )

                        if notification_connector and hasattr(notification_connector, "dispatch_breach_alert"):
                            try:
                                payload = SLABreachNotificationPayload(
                                    ticket_id=ticket.id,
                                    client_id=ticket.client_id,
                                    priority=ticket.priority,
                                    target_type="resolution",
                                    target_due_at=due_at,
                                    breached_at=now,
                                    assigned_tier=ticket.assigned_tier,
                                    assigned_agent_id=ticket.assigned_agent_id,
                                )
                                notification_connector.dispatch_breach_alert(payload)
                            except Exception as exc:
                                logger.error(
                                    "Failed to dispatch SLA breach alert notification",
                                    extra={"ticket_id": str(ticket.id), "error": str(exc)},
                                )

                elif not tracking.is_breached and tracking.warning_sent_at is None:
                    if elapsed_percent >= settings.sla_warning_threshold_percent:
                        tracking.warning_sent_at = now
                        warning_triggered = True

                        logger.info(
                            "SLA warning threshold crossed for resolution target",
                            extra={
                                "correlation_id": get_correlation_id(),
                                "ticket_id": str(ticket.id),
                                "target_type": "resolution",
                                "elapsed_percent": elapsed_percent,
                            },
                        )

                        if notification_connector and hasattr(notification_connector, "dispatch_breach_warning"):
                            try:
                                payload = SLAWarningNotificationPayload(
                                    ticket_id=ticket.id,
                                    client_id=ticket.client_id,
                                    priority=ticket.priority,
                                    target_type="resolution",
                                    target_due_at=due_at,
                                    elapsed_percent=round(elapsed_percent, 2),
                                    time_remaining_minutes=round(time_remaining_mins, 2),
                                    assigned_tier=ticket.assigned_tier,
                                    assigned_agent_id=ticket.assigned_agent_id,
                                )
                                notification_connector.dispatch_breach_warning(payload)
                            except Exception as exc:
                                logger.error(
                                    "Failed to dispatch SLA breach warning notification",
                                    extra={"ticket_id": str(ticket.id), "error": str(exc)},
                                )

    db.flush()

    return {
        "ticket_id": str(ticket.id),
        "status": ticket.status,
        "priority": ticket.priority,
        "warning_triggered": warning_triggered,
        "breach_triggered": breach_triggered,
    }


def execute_ticket_escalation(
    db: Session,
    ticket: SupportTicket,
    reason: str,
    target_tier: Optional[str] = None,
    notification_connector: Optional[Any] = None,
    current_time: Optional[datetime] = None,
) -> TicketEscalation:
    """Execute support tier escalation re-assignment and emit audit log [FR-6, NFR-3].

    Args:
        db: Synchronous database session.
        ticket: SupportTicket ORM entity.
        reason: Text justification for escalation trigger.
        target_tier: Optional target support tier string. Defaults to next sequence tier.
        notification_connector: Optional notification connector instance for dispatch.
        current_time: Optional datetime baseline for escalation timestamp.

    Returns:
        Created TicketEscalation ORM audit log record.
    """
    if current_time is None:
        now = datetime.now(timezone.utc)
    elif current_time.tzinfo is None:
        now = current_time.replace(tzinfo=timezone.utc)
    else:
        now = current_time.astimezone(timezone.utc)

    source_tier = ticket.assigned_tier or "tier1"

    if not target_tier:
        if source_tier == "tier1":
            target_tier = "tier2"
        elif source_tier == "tier2":
            target_tier = "tier3"
        else:
            target_tier = "tier3"

    ticket.assigned_tier = target_tier

    escalation = TicketEscalation(
        ticket_id=ticket.id,
        source_tier=source_tier,
        target_tier=target_tier,
        escalated_at=now,
        reason=reason,
    )
    db.add(escalation)
    db.flush()

    logger.warning(
        "Executed support ticket tier escalation",
        extra={
            "correlation_id": get_correlation_id(),
            "ticket_id": str(ticket.id),
            "source_tier": source_tier,
            "target_tier": target_tier,
            "reason": reason,
            "escalated_at": now.isoformat(),
        },
    )

    if notification_connector and hasattr(notification_connector, "dispatch_escalation_alert"):
        try:
            payload = EscalationNotificationPayload(
                ticket_id=ticket.id,
                client_id=ticket.client_id,
                priority=ticket.priority,
                source_tier=source_tier,
                target_tier=target_tier,
                reason=reason,
                escalated_at=now,
                assigned_agent_id=ticket.assigned_agent_id,
            )
            notification_connector.dispatch_escalation_alert(payload)
        except Exception as exc:
            logger.error(
                "Failed to dispatch escalation alert notification",
                extra={"ticket_id": str(ticket.id), "error": str(exc)},
            )

    return escalation


def check_and_escalate_ticket(
    db: Session,
    ticket: SupportTicket,
    notification_connector: Optional[Any] = None,
    current_time: Optional[datetime] = None,
) -> Optional[TicketEscalation]:
    """Check SLA threshold conditions and execute automatic escalation if rules trigger [FR-5, FR-6].

    Args:
        db: Synchronous database session.
        ticket: SupportTicket ORM entity.
        notification_connector: Optional notification connector for dispatch.
        current_time: Optional reference datetime baseline.

    Returns:
        Created TicketEscalation record if escalated, or None.
    """
    if ticket.status in ("resolved", "closed"):
        return None

    resolution_tracking = None
    for tracking in ticket.sla_trackings or []:
        if tracking.target_type == "resolution":
            resolution_tracking = tracking
            break

    if not resolution_tracking:
        return None

    # Check rule: If resolution SLA warning threshold is reached or breached
    if resolution_tracking.is_breached or resolution_tracking.warning_sent_at is not None:
        source_tier = ticket.assigned_tier or "tier1"

        if source_tier == "tier1":
            reason = "Automated escalation: SLA threshold warning or breach on tier1 ticket"
            return execute_ticket_escalation(
                db, ticket, reason, "tier2", notification_connector, current_time
            )
        elif source_tier == "tier2" and resolution_tracking.is_breached:
            reason = "Automated escalation: SLA breach on tier2 ticket"
            return execute_ticket_escalation(
                db, ticket, reason, "tier3", notification_connector, current_time
            )

    return None


def evaluate_all_active_slas(
    db: Session,
    notification_connector: Optional[Any] = None,
    current_time: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Scan and evaluate all active open/pending support tickets for SLA warnings and breaches [FR-4, FR-5, FR-6].

    Args:
        db: Synchronous database session.
        notification_connector: Optional notification connector instance.
        current_time: Optional reference datetime baseline.

    Returns:
        Summary metrics dictionary of evaluated count, warnings, breaches, and escalations.
    """
    active_tickets = (
        db.query(SupportTicket)
        .filter(SupportTicket.status.in_(["open", "pending"]))
        .all()
    )

    evaluated_count = 0
    warnings_count = 0
    breaches_count = 0
    escalations_count = 0

    for ticket in active_tickets:
        eval_result = evaluate_ticket_slas(db, ticket, notification_connector, current_time)
        evaluated_count += 1
        if eval_result.get("warning_triggered"):
            warnings_count += 1
        if eval_result.get("breach_triggered"):
            breaches_count += 1

        escalation_result = check_and_escalate_ticket(db, ticket, notification_connector, current_time)
        if escalation_result:
            escalations_count += 1

    db.commit()

    logger.info(
        "Completed periodic SLA countdown and escalation evaluation scan",
        extra={
            "correlation_id": get_correlation_id(),
            "evaluated_count": evaluated_count,
            "warnings_count": warnings_count,
            "breaches_count": breaches_count,
            "escalations_count": escalations_count,
        },
    )

    return {
        "evaluated_count": evaluated_count,
        "warnings_count": warnings_count,
        "breaches_count": breaches_count,
        "escalations_count": escalations_count,
    }


class SLAService:
    """Class wrapper providing explicit dependency injection for SLA operations [NFR-4]."""

    def __init__(
        self,
        db: Optional[Session] = None,
        notification_connector: Optional[Any] = None,
    ) -> None:
        """Initialize SLAService instance.

        Args:
            db: Optional database session.
            notification_connector: Optional notification connector instance.
        """
        self.db = db
        self.notification_connector = notification_connector

    def calculate_target_due_at(
        self,
        priority: str,
        target_type: str,
        start_time: Optional[datetime] = None,
    ) -> datetime:
        """Calculate target due datetime for priority and target type."""
        return calculate_target_due_at(priority, target_type, start_time)

    def calculate_remaining_time(
        self,
        target_due_at: datetime,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Calculate countdown remaining time metrics."""
        return calculate_remaining_time(target_due_at, current_time)

    def initialize_sla_tracking(
        self,
        ticket: SupportTicket,
        current_time: Optional[datetime] = None,
    ) -> List[SLATracking]:
        """Initialize SLA tracking records for a support ticket."""
        if not self.db:
            raise ValueError("Database session is required for initialize_sla_tracking")
        return initialize_sla_tracking(self.db, ticket, current_time)

    def recalculate_sla_targets(
        self,
        ticket: SupportTicket,
        new_priority: str,
        current_time: Optional[datetime] = None,
    ) -> List[SLATracking]:
        """Recalculate SLA targets following priority modification."""
        if not self.db:
            raise ValueError("Database session is required for recalculate_sla_targets")
        return recalculate_sla_targets(self.db, ticket, new_priority, current_time)

    def evaluate_ticket_slas(
        self,
        ticket: SupportTicket,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Evaluate ticket countdown state and dispatch breach/warning notifications."""
        if not self.db:
            raise ValueError("Database session is required for evaluate_ticket_slas")
        return evaluate_ticket_slas(
            self.db, ticket, self.notification_connector, current_time
        )

    def execute_ticket_escalation(
        self,
        ticket: SupportTicket,
        reason: str,
        target_tier: Optional[str] = None,
        current_time: Optional[datetime] = None,
    ) -> TicketEscalation:
        """Execute support tier escalation re-assignment."""
        if not self.db:
            raise ValueError("Database session is required for execute_ticket_escalation")
        return execute_ticket_escalation(
            self.db, ticket, reason, target_tier, self.notification_connector, current_time
        )

    def check_and_escalate_ticket(
        self,
        ticket: SupportTicket,
        current_time: Optional[datetime] = None,
    ) -> Optional[TicketEscalation]:
        """Check escalation conditions and execute tier escalation if triggered."""
        if not self.db:
            raise ValueError("Database session is required for check_and_escalate_ticket")
        return check_and_escalate_ticket(
            self.db, ticket, self.notification_connector, current_time
        )

    def evaluate_all_active_slas(
        self,
        current_time: Optional[datetime] = None,
    ) -> Dict[str, Any]:
        """Scan and evaluate all active support ticket SLAs."""
        if not self.db:
            raise ValueError("Database session is required for evaluate_all_active_slas")
        return evaluate_all_active_slas(
            self.db, self.notification_connector, current_time
        )
