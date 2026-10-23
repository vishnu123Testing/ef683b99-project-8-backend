"""Celery background worker tasks for multi-channel alert dispatching, audit log recording, and SMS fallback execution [FR-8, FR-12, NFR-2, NFR-4].

Provides background execution for dispatching multi-channel notifications (InApp, Email, SMS),
recording structured audit log entries into NotificationLog, and executing automatic fallback
dispatches when SMS delivery fails. Emits structured JSON logs with correlation IDs.
"""

from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import Any, Dict, List, Optional, Union
import uuid

from celery import Celery
from sqlalchemy.orm import Session

from src.connectors.email import SMTPEmailConnector
from src.connectors.in_app import InAppPersistenceConnector
from src.connectors.sms import SMSGatewayConnector
from src.lib.config import get_settings, settings
from src.lib.logging import get_correlation_id, set_correlation_id
from src.models.alerting import NotificationLog
from src.models.database import SessionLocal
from src.services.alerting.ports import (
    DispatchResult,
    EmailDispatcherPort,
    EmailDispatchError,
    EmailPayload,
    InAppDispatcherPort,
    InAppDispatchError,
    InAppPayload,
    SMSDispatcherPort,
    SMSDispatchError,
    SMSPayload,
)

logger = logging.getLogger(__name__)

# Initialize Celery worker application
celery_app = Celery(
    "notification_tasks",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_default_queue=getattr(settings, "alert_worker_queue", "notifications"),
)


def record_notification_audit_log(
    recipient_id: Union[uuid.UUID, str],
    trigger_id: Optional[Union[uuid.UUID, str]] = None,
    urgency: str = "Low",
    channel: str = "InApp",
    delivery_status: str = "Sent",
    correlation_id: Optional[str] = None,
    dispatched_at: Optional[datetime] = None,
    db: Optional[Session] = None,
) -> Dict[str, Any]:
    """Record a structured notification audit log entry into the database [FR-12, NFR-2, NFR-4].

    Args:
        recipient_id: Recipient user UUID or string.
        trigger_id: Optional alert trigger rule UUID or string.
        urgency: Urgency level ('Low', 'Medium', 'High').
        channel: Notification channel ('InApp', 'Email', 'SMS').
        delivery_status: Delivery outcome ('Sent', 'Failed', 'Fallback').
        correlation_id: Optional context correlation ID.
        dispatched_at: Optional timestamp of dispatch attempt.
        db: Optional SQLAlchemy Session instance.

    Returns:
        Dictionary representation of the recorded NotificationLog entry.
    """
    cid = correlation_id or get_correlation_id() or f"corr-{uuid.uuid4()}"
    token = set_correlation_id(cid)

    # Parse recipient_id UUID
    recipient_str = str(recipient_id).strip()
    try:
        recipient_uuid = (
            recipient_id
            if isinstance(recipient_id, uuid.UUID)
            else uuid.UUID(recipient_str)
        )
    except (ValueError, TypeError):
        recipient_uuid = uuid.uuid4()

    # Parse trigger_id UUID
    trigger_uuid: Optional[uuid.UUID] = None
    if trigger_id and str(trigger_id).strip():
        try:
            trigger_uuid = (
                trigger_id
                if isinstance(trigger_id, uuid.UUID)
                else uuid.UUID(str(trigger_id).strip())
            )
        except (ValueError, TypeError):
            trigger_uuid = None

    now_utc = dispatched_at or datetime.now(timezone.utc)
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)

    close_session = False
    if db is None:
        db = SessionLocal()
        close_session = True

    try:
        log_entry = NotificationLog(
            notification_id=uuid.uuid4(),
            recipient_id=recipient_uuid,
            trigger_id=trigger_uuid,
            urgency=urgency,
            channel=channel,
            delivery_status=delivery_status,
            dispatched_at=now_utc,
            correlation_id=cid,
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)

        log_dict = log_entry.to_dict()

        logger.info(
            f"Recorded notification audit log: channel={channel}, status={delivery_status}, "
            f"recipient={recipient_uuid}, correlation_id={cid}",
            extra={
                "correlation_id": cid,
                "event_type": "notification_audit_logged",
                "notification_id": str(log_entry.notification_id),
                "recipient_id": str(recipient_uuid),
                "trigger_id": str(trigger_uuid) if trigger_uuid else None,
                "urgency": urgency,
                "channel": channel,
                "delivery_status": delivery_status,
                "dispatched_at": now_utc.isoformat(),
            },
        )

        return log_dict

    except Exception as exc:
        if close_session and db is not None:
            db.rollback()
        logger.error(
            f"Failed to record notification audit log for recipient {recipient_id}: {exc}",
            extra={
                "correlation_id": cid,
                "event_type": "notification_audit_log_failed",
                "recipient_id": str(recipient_id),
                "channel": channel,
                "delivery_status": delivery_status,
                "error": str(exc),
            },
        )
        return {
            "notification_id": None,
            "recipient_id": str(recipient_id),
            "trigger_id": str(trigger_id) if trigger_id else None,
            "urgency": urgency,
            "channel": channel,
            "delivery_status": delivery_status,
            "correlation_id": cid,
            "error": str(exc),
        }
    finally:
        if close_session and db is not None:
            db.close()


def execute_sms_fallback(
    recipient_id: Union[uuid.UUID, str],
    recipient_email: Optional[str] = None,
    trigger_id: Optional[Union[uuid.UUID, str]] = None,
    urgency: str = "High",
    title: str = "Notification Alert",
    message_text: str = "",
    correlation_id: Optional[str] = None,
    db: Optional[Session] = None,
    email_connector: Optional[EmailDispatcherPort] = None,
    in_app_connector: Optional[InAppDispatcherPort] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute fallback notification dispatches to Email and In-App channels when SMS delivery fails [FR-8, FR-12, NFR-2, NFR-4].

    Args:
        recipient_id: Recipient user UUID or string identifier.
        recipient_email: Optional recipient email address.
        trigger_id: Optional alert trigger rule UUID or string.
        urgency: Urgency level string.
        title: Notification title string.
        message_text: Notification body text string.
        correlation_id: Context correlation ID.
        db: Optional SQLAlchemy Session.
        email_connector: Optional Email port adapter override.
        in_app_connector: Optional In-App port adapter override.
        metadata: Optional additional event metadata dictionary.

    Returns:
        Dictionary summarizing Email and In-App fallback execution outcomes.
    """
    cid = correlation_id or get_correlation_id() or f"corr-{uuid.uuid4()}"
    token = set_correlation_id(cid)

    logger.warning(
        f"SMS delivery failed for recipient '{recipient_id}' (correlation_id={cid}). "
        f"Executing fallback notification dispatches to Email and In-App channels.",
        extra={
            "correlation_id": cid,
            "event_type": "sms_fallback_triggered",
            "recipient_id": str(recipient_id),
            "trigger_id": str(trigger_id) if trigger_id else None,
            "urgency": urgency,
        },
    )

    close_session = False
    if db is None:
        db = SessionLocal()
        close_session = True

    try:
        if email_connector is None:
            email_connector = SMTPEmailConnector()
        if in_app_connector is None:
            in_app_connector = InAppPersistenceConnector(session=db, auto_commit=True)

        fallback_summary: Dict[str, Any] = {
            "sms_status": "failed",
            "fallback_triggered": True,
            "email_fallback": None,
            "in_app_fallback": None,
            "correlation_id": cid,
        }

        # 1. Fallback dispatch via Email if recipient email is available
        if recipient_email and recipient_email.strip():
            email_payload = EmailPayload(
                recipient_email=recipient_email,
                subject=f"[FALLBACK] {title}",
                body_text=f"Notice: Delivered via Email fallback following SMS delivery failure.\n\n{message_text}",
                body_html=f"<p><em>Notice: Delivered via Email fallback following SMS delivery failure.</em></p><p>{message_text}</p>",
                urgency=urgency,
                correlation_id=cid,
                recipient_id=recipient_id,
                trigger_id=trigger_id,
                metadata=metadata or {},
            )
            try:
                email_result = email_connector.send_email(email_payload)
                fallback_summary["email_fallback"] = {
                    "success": email_result.success,
                    "channel": "Email",
                    "delivery_status": "Fallback" if email_result.success else "Failed",
                    "details": email_result.to_dict(),
                }
                status_to_record = "Fallback" if email_result.success else "Failed"
                record_notification_audit_log(
                    recipient_id=recipient_id,
                    trigger_id=trigger_id,
                    urgency=urgency,
                    channel="Email",
                    delivery_status=status_to_record,
                    correlation_id=cid,
                    db=db,
                )
            except Exception as exc:
                logger.error(
                    f"Email fallback dispatch failed for recipient '{recipient_id}' correlation_id={cid}: {exc}"
                )
                fallback_summary["email_fallback"] = {
                    "success": False,
                    "channel": "Email",
                    "delivery_status": "Failed",
                    "error": str(exc),
                }
                record_notification_audit_log(
                    recipient_id=recipient_id,
                    trigger_id=trigger_id,
                    urgency=urgency,
                    channel="Email",
                    delivery_status="Failed",
                    correlation_id=cid,
                    db=db,
                )

        # 2. Fallback dispatch via In-App
        if recipient_id:
            in_app_payload = InAppPayload(
                recipient_id=recipient_id,
                title=f"[FALLBACK] {title}",
                message_text=f"Fallback Notice: {message_text}",
                urgency=urgency,
                correlation_id=cid,
                trigger_id=trigger_id,
                metadata=metadata or {},
            )
            try:
                in_app_result = in_app_connector.send_in_app(in_app_payload)
                fallback_summary["in_app_fallback"] = {
                    "success": in_app_result.success,
                    "channel": "InApp",
                    "delivery_status": "Fallback" if in_app_result.success else "Failed",
                    "details": in_app_result.to_dict(),
                }
                status_to_record = "Fallback" if in_app_result.success else "Failed"
                record_notification_audit_log(
                    recipient_id=recipient_id,
                    trigger_id=trigger_id,
                    urgency=urgency,
                    channel="InApp",
                    delivery_status=status_to_record,
                    correlation_id=cid,
                    db=db,
                )
            except Exception as exc:
                logger.error(
                    f"In-App fallback dispatch failed for recipient '{recipient_id}' correlation_id={cid}: {exc}"
                )
                fallback_summary["in_app_fallback"] = {
                    "success": False,
                    "channel": "InApp",
                    "delivery_status": "Failed",
                    "error": str(exc),
                }
                record_notification_audit_log(
                    recipient_id=recipient_id,
                    trigger_id=trigger_id,
                    urgency=urgency,
                    channel="InApp",
                    delivery_status="Failed",
                    correlation_id=cid,
                    db=db,
                )

        return fallback_summary

    finally:
        if close_session and db is not None:
            db.close()


def process_multi_channel_dispatch(
    recipient_id: Union[uuid.UUID, str],
    recipient_email: Optional[str] = None,
    recipient_phone: Optional[str] = None,
    trigger_id: Optional[Union[uuid.UUID, str]] = None,
    urgency: str = "Low",
    channels: Optional[List[str]] = None,
    title: str = "Notification Alert",
    message_text: str = "",
    subject: Optional[str] = None,
    event_type: str = "financial_alert",
    financial_criteria: Optional[str] = None,
    entity_id: Optional[str] = None,
    value: Optional[Union[float, int, Decimal]] = None,
    correlation_id: Optional[str] = None,
    db: Optional[Session] = None,
    email_connector: Optional[EmailDispatcherPort] = None,
    sms_connector: Optional[SMSDispatcherPort] = None,
    in_app_connector: Optional[InAppDispatcherPort] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Execute multi-channel notification dispatching, record audit logs, and trigger SMS fallbacks [FR-8, FR-12, NFR-2, NFR-4].

    Args:
        recipient_id: Recipient user UUID or string identifier.
        recipient_email: Optional recipient email address.
        recipient_phone: Optional recipient phone number.
        trigger_id: Optional alert trigger rule UUID or string.
        urgency: Event urgency level ('Low', 'Medium', 'High').
        channels: Optional list of target channels (e.g. ['InApp', 'Email', 'SMS']).
        title: Title/rule_name string.
        message_text: Text message body.
        subject: Optional email subject line override (defaults to title).
        event_type: Event classification string.
        financial_criteria: Optional financial criteria string.
        entity_id: Optional financial entity ID string.
        value: Optional financial numeric value.
        correlation_id: Context correlation ID.
        db: Optional SQLAlchemy Session instance.
        email_connector: Optional Email port adapter override.
        sms_connector: Optional SMS port adapter override.
        in_app_connector: Optional In-App port adapter override.
        metadata: Optional additional event metadata dictionary.

    Returns:
        Summary dictionary containing status per channel and correlation ID.
    """
    cid = correlation_id or get_correlation_id() or f"corr-{uuid.uuid4()}"
    token = set_correlation_id(cid)

    # Normalize channels if not explicitly provided
    if not channels:
        if urgency and urgency.capitalize() == "High":
            channels = ["InApp", "Email", "SMS"]
        else:
            channels = ["InApp", "Email"]

    close_session = False
    if db is None:
        db = SessionLocal()
        close_session = True

    try:
        if email_connector is None:
            email_connector = SMTPEmailConnector()
        if sms_connector is None:
            sms_connector = SMSGatewayConnector()
        if in_app_connector is None:
            in_app_connector = InAppPersistenceConnector(session=db, auto_commit=True)

        meta = metadata.copy() if metadata else {}
        if financial_criteria:
            meta["financial_criteria"] = financial_criteria
        if entity_id:
            meta["entity_id"] = entity_id
        if value is not None:
            meta["value"] = float(value)

        channel_results: Dict[str, Any] = {}
        successful_channels: List[str] = []

        # 1. Dispatch In-App Channel
        if "InApp" in channels and recipient_id:
            in_app_payload = InAppPayload(
                recipient_id=recipient_id,
                title=title,
                message_text=message_text,
                urgency=urgency,
                correlation_id=cid,
                trigger_id=trigger_id,
                metadata=meta,
            )
            try:
                res = in_app_connector.send_in_app(in_app_payload)
                if res.success:
                    channel_results["InApp"] = {"status": "success", "details": res.to_dict()}
                    successful_channels.append("InApp")
                else:
                    channel_results["InApp"] = {"status": "failed", "error": res.error_message}
                    record_notification_audit_log(
                        recipient_id=recipient_id,
                        trigger_id=trigger_id,
                        urgency=urgency,
                        channel="InApp",
                        delivery_status="Failed",
                        correlation_id=cid,
                        db=db,
                    )
            except Exception as exc:
                logger.error(f"In-App dispatch failed for recipient '{recipient_id}' correlation_id={cid}: {exc}")
                channel_results["InApp"] = {"status": "failed", "error": str(exc)}
                record_notification_audit_log(
                    recipient_id=recipient_id,
                    trigger_id=trigger_id,
                    urgency=urgency,
                    channel="InApp",
                    delivery_status="Failed",
                    correlation_id=cid,
                    db=db,
                )

        # 2. Dispatch Email Channel
        if "Email" in channels and recipient_email and recipient_email.strip():
            email_payload = EmailPayload(
                recipient_email=recipient_email,
                subject=subject or title,
                body_text=message_text,
                body_html=f"<h3>{title}</h3><p>{message_text}</p>",
                urgency=urgency,
                correlation_id=cid,
                recipient_id=recipient_id,
                trigger_id=trigger_id,
                metadata=meta,
            )
            try:
                res = email_connector.send_email(email_payload)
                if res.success:
                    channel_results["Email"] = {"status": "success", "details": res.to_dict()}
                    successful_channels.append("Email")
                    record_notification_audit_log(
                        recipient_id=recipient_id,
                        trigger_id=trigger_id,
                        urgency=urgency,
                        channel="Email",
                        delivery_status="Sent",
                        correlation_id=cid,
                        db=db,
                    )
                else:
                    channel_results["Email"] = {"status": "failed", "error": res.error_message}
                    record_notification_audit_log(
                        recipient_id=recipient_id,
                        trigger_id=trigger_id,
                        urgency=urgency,
                        channel="Email",
                        delivery_status="Failed",
                        correlation_id=cid,
                        db=db,
                    )
            except Exception as exc:
                logger.error(f"Email dispatch failed for recipient '{recipient_id}' correlation_id={cid}: {exc}")
                channel_results["Email"] = {"status": "failed", "error": str(exc)}
                record_notification_audit_log(
                    recipient_id=recipient_id,
                    trigger_id=trigger_id,
                    urgency=urgency,
                    channel="Email",
                    delivery_status="Failed",
                    correlation_id=cid,
                    db=db,
                )

        # 3. Dispatch SMS Channel with Fallback Execution
        if "SMS" in channels:
            sms_success = False
            if recipient_phone and recipient_phone.strip():
                sms_payload = SMSPayload(
                    recipient_phone=recipient_phone,
                    message_text=f"{title}: {message_text}"[:160],
                    urgency=urgency,
                    correlation_id=cid,
                    recipient_id=recipient_id,
                    trigger_id=trigger_id,
                    metadata=meta,
                )
                try:
                    res = sms_connector.send_sms(sms_payload)
                    if res.success:
                        sms_success = True
                        channel_results["SMS"] = {"status": "success", "details": res.to_dict()}
                        successful_channels.append("SMS")
                        record_notification_audit_log(
                            recipient_id=recipient_id,
                            trigger_id=trigger_id,
                            urgency=urgency,
                            channel="SMS",
                            delivery_status="Sent",
                            correlation_id=cid,
                            db=db,
                        )
                    else:
                        channel_results["SMS"] = {"status": "failed", "error": res.error_message}
                        record_notification_audit_log(
                            recipient_id=recipient_id,
                            trigger_id=trigger_id,
                            urgency=urgency,
                            channel="SMS",
                            delivery_status="Failed",
                            correlation_id=cid,
                            db=db,
                        )
                except Exception as exc:
                    logger.error(f"SMS dispatch failed for recipient '{recipient_id}' correlation_id={cid}: {exc}")
                    channel_results["SMS"] = {"status": "failed", "error": str(exc)}
                    record_notification_audit_log(
                        recipient_id=recipient_id,
                        trigger_id=trigger_id,
                        urgency=urgency,
                        channel="SMS",
                        delivery_status="Failed",
                        correlation_id=cid,
                        db=db,
                    )
            else:
                channel_results["SMS"] = {"status": "failed", "error": "Missing recipient phone number"}
                record_notification_audit_log(
                    recipient_id=recipient_id,
                    trigger_id=trigger_id,
                    urgency=urgency,
                    channel="SMS",
                    delivery_status="Failed",
                    correlation_id=cid,
                    db=db,
                )

            # Trigger SMS Fallback execution if SMS failed
            if not sms_success:
                fallback_res = execute_sms_fallback(
                    recipient_id=recipient_id,
                    recipient_email=recipient_email,
                    trigger_id=trigger_id,
                    urgency=urgency,
                    title=title,
                    message_text=message_text,
                    correlation_id=cid,
                    db=db,
                    email_connector=email_connector,
                    in_app_connector=in_app_connector,
                    metadata=meta,
                )
                channel_results["SMS_Fallback"] = fallback_res

        summary = {
            "status": "success" if successful_channels or "SMS_Fallback" in channel_results else "failed",
            "recipient_id": str(recipient_id),
            "trigger_id": str(trigger_id) if trigger_id else None,
            "urgency": urgency,
            "channels": channels,
            "successful_channels": successful_channels,
            "results": channel_results,
            "correlation_id": cid,
        }

        logger.info(
            f"Multi-channel notification dispatch completed for recipient '{recipient_id}': "
            f"successful_channels={successful_channels}, correlation_id={cid}",
            extra={
                "correlation_id": cid,
                "event_type": "multi_channel_dispatch_completed",
                "recipient_id": str(recipient_id),
                "trigger_id": str(trigger_id) if trigger_id else None,
                "urgency": urgency,
                "successful_channels": successful_channels,
            },
        )

        return summary

    except Exception as exc:
        logger.error(
            f"Failed multi-channel notification dispatch for recipient '{recipient_id}' correlation_id={cid}: {exc}",
            extra={
                "correlation_id": cid,
                "event_type": "multi_channel_dispatch_failed",
                "recipient_id": str(recipient_id),
                "error": str(exc),
            },
        )
        return {
            "status": "failed",
            "recipient_id": str(recipient_id),
            "trigger_id": str(trigger_id) if trigger_id else None,
            "urgency": urgency,
            "channels": channels or [],
            "error": str(exc),
            "correlation_id": cid,
        }
    finally:
        if close_session and db is not None:
            db.close()


# Alias functions for caller flexibility
dispatch_multi_channel_alert = process_multi_channel_dispatch
record_audit_log = record_notification_audit_log


# Celery Tasks
@celery_app.task(name="record_notification_audit_log", bind=True)
def run_record_notification_audit_log(
    self,
    recipient_id: str,
    trigger_id: Optional[str] = None,
    urgency: str = "Low",
    channel: str = "InApp",
    delivery_status: str = "Sent",
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Celery background task for recording structured notification audit log entries [FR-12, NFR-2, NFR-4]."""
    return record_notification_audit_log(
        recipient_id=recipient_id,
        trigger_id=trigger_id,
        urgency=urgency,
        channel=channel,
        delivery_status=delivery_status,
        correlation_id=correlation_id,
    )


@celery_app.task(name="dispatch_multi_channel_alert", bind=True)
def run_dispatch_multi_channel_alert(
    self,
    recipient_id: str,
    recipient_email: Optional[str] = None,
    recipient_phone: Optional[str] = None,
    trigger_id: Optional[str] = None,
    urgency: str = "Low",
    channels: Optional[List[str]] = None,
    title: str = "Notification Alert",
    message_text: str = "",
    subject: Optional[str] = None,
    event_type: str = "financial_alert",
    financial_criteria: Optional[str] = None,
    entity_id: Optional[str] = None,
    value: Optional[float] = None,
    correlation_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Celery background task for multi-channel alert dispatching with SMS fallback [FR-8, FR-12, NFR-2, NFR-4]."""
    return process_multi_channel_dispatch(
        recipient_id=recipient_id,
        recipient_email=recipient_email,
        recipient_phone=recipient_phone,
        trigger_id=trigger_id,
        urgency=urgency,
        channels=channels,
        title=title,
        message_text=message_text,
        subject=subject,
        event_type=event_type,
        financial_criteria=financial_criteria,
        entity_id=entity_id,
        value=value,
        correlation_id=correlation_id,
        metadata=metadata,
    )


@celery_app.task(name="execute_sms_fallback", bind=True)
def run_execute_sms_fallback(
    self,
    recipient_id: str,
    recipient_email: Optional[str] = None,
    trigger_id: Optional[str] = None,
    urgency: str = "High",
    title: str = "Notification Alert",
    message_text: str = "",
    correlation_id: Optional[str] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Celery background task for executing fallback dispatches to Email and In-App when SMS fails [FR-8, FR-12]."""
    return execute_sms_fallback(
        recipient_id=recipient_id,
        recipient_email=recipient_email,
        trigger_id=trigger_id,
        urgency=urgency,
        title=title,
        message_text=message_text,
        correlation_id=correlation_id,
        metadata=metadata,
    )


# Additional aliases for task execution
dispatch_multi_channel_alert_task = run_dispatch_multi_channel_alert
record_notification_audit_log_task = run_record_notification_audit_log
execute_sms_fallback_task = run_execute_sms_fallback
