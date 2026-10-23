"""SMTP email delivery connector implementing the Email port interface [FR-8, NFR-3].

Provides concrete SMTPEmailConnector adapter implementation for sending email notifications
via standard SMTP protocol using application settings or explicit connection parameters.
"""

from email.message import EmailMessage
import logging
import socket
import smtplib
from datetime import datetime, timezone
from typing import Any, Callable, Dict, Optional
import uuid

from src.lib.config import get_settings
from src.services.alerting.ports import (
    DispatchResult,
    EmailDispatcherPort,
    EmailDispatchError,
    EmailPayload,
)

logger = logging.getLogger(__name__)


class SMTPEmailConnector(EmailDispatcherPort):
    """SMTP email delivery connector adapter implementing EmailDispatcherPort [FR-8, NFR-3].

    Handles email formatting, TLS configuration, SMTP authentication, and error trapping
    for multi-channel notification dispatch.
    """

    def __init__(
        self,
        smtp_host: Optional[str] = None,
        smtp_port: Optional[int] = None,
        smtp_user: Optional[str] = None,
        smtp_password: Optional[str] = None,
        smtp_from_email: Optional[str] = None,
        smtp_use_tls: Optional[bool] = None,
        timeout: float = 10.0,
        dry_run: bool = False,
        smtp_factory: Optional[Callable[..., Any]] = None,
    ) -> None:
        """Initialize SMTPEmailConnector with optional configuration parameters.

        Args:
            smtp_host: SMTP server hostname or IP address.
            smtp_port: SMTP server port number (default 587).
            smtp_user: Optional username for SMTP authentication.
            smtp_password: Optional password for SMTP authentication.
            smtp_from_email: Sender email address ("From" header).
            smtp_use_tls: Flag enabling TLS encryption via STARTTLS.
            timeout: Socket connection timeout in seconds.
            dry_run: If True, simulates dispatch without initiating network connection.
            smtp_factory: Optional factory callable for injecting custom/mock SMTP connections.
        """
        app_settings = get_settings()
        self.smtp_host = smtp_host if smtp_host is not None else app_settings.smtp_host
        self.smtp_port = smtp_port if smtp_port is not None else app_settings.smtp_port
        self.smtp_user = smtp_user if smtp_user is not None else app_settings.smtp_user
        self.smtp_password = (
            smtp_password if smtp_password is not None else app_settings.smtp_password
        )
        self.smtp_from_email = (
            smtp_from_email if smtp_from_email is not None else app_settings.smtp_from_email
        )
        self.smtp_use_tls = (
            smtp_use_tls if smtp_use_tls is not None else app_settings.smtp_use_tls
        )
        self.timeout = timeout
        self.dry_run = dry_run
        self.smtp_factory = smtp_factory

    def send_email(self, payload: EmailPayload) -> DispatchResult:
        """Dispatch an email message payload over SMTP [FR-8, NFR-3].

        Args:
            payload: EmailPayload object containing recipient_email, subject, body_text,
                body_html, urgency, and correlation metadata.

        Returns:
            DispatchResult containing delivery confirmation, timestamp, and metadata.

        Raises:
            EmailDispatchError: If payload validation fails, SMTP configuration is missing,
                or network/authentication delivery errors occur.
        """
        if not isinstance(payload, EmailPayload):
            raise EmailDispatchError("Invalid payload type provided to SMTPEmailConnector.")

        if not payload.recipient_email or not payload.recipient_email.strip():
            raise EmailDispatchError("Recipient email address is required for email dispatch.")

        if not payload.body_text and not payload.body_html:
            raise EmailDispatchError("Email body text or HTML content is required.")

        dispatched_at = datetime.now(timezone.utc)
        message_id = f"email-{uuid.uuid4()}"

        if self.dry_run:
            logger.info(
                "Simulated dry-run email dispatch to %s (subject=%s, correlation_id=%s)",
                payload.recipient_email,
                payload.subject,
                payload.correlation_id,
            )
            return DispatchResult(
                success=True,
                channel="Email",
                recipient=payload.recipient_email,
                dispatched_at=dispatched_at,
                correlation_id=payload.correlation_id,
                message_id=message_id,
                details={
                    "dry_run": True,
                    "subject": payload.subject,
                    "urgency": payload.urgency,
                    "smtp_host": self.smtp_host,
                    "smtp_port": self.smtp_port,
                    "from_email": self.smtp_from_email,
                },
            )

        if not self.smtp_host or not str(self.smtp_host).strip():
            raise EmailDispatchError(
                "SMTP host is not configured. Provide SMTP_HOST environment variable (NFR-3)."
            )

        if not self.smtp_from_email or not str(self.smtp_from_email).strip():
            raise EmailDispatchError(
                "SMTP sender email (smtp_from_email) is not configured (NFR-3)."
            )

        # Construct Email Message
        msg = EmailMessage()
        msg["Subject"] = payload.subject or ""
        msg["From"] = self.smtp_from_email
        msg["To"] = payload.recipient_email
        if payload.correlation_id:
            msg["X-Correlation-ID"] = payload.correlation_id
        if payload.urgency:
            msg["X-Urgency"] = payload.urgency

        msg.set_content(payload.body_text or "")
        if payload.body_html:
            msg.add_alternative(payload.body_html, subtype="html")

        # Attempt SMTP delivery
        try:
            if self.smtp_factory:
                smtp_conn = self.smtp_factory(self.smtp_host, self.smtp_port, timeout=self.timeout)
            else:
                smtp_conn = smtplib.SMTP(self.smtp_host, self.smtp_port, timeout=self.timeout)

            with smtp_conn as server:
                if self.smtp_use_tls:
                    server.starttls()

                if self.smtp_user or self.smtp_password:
                    if not (self.smtp_user and self.smtp_password):
                        raise EmailDispatchError(
                            "Incomplete SMTP credentials provided: both smtp_user and "
                            "smtp_password are required when authentication is enabled (NFR-3)."
                        )
                    server.login(self.smtp_user, self.smtp_password)

                server.send_message(msg)

        except EmailDispatchError:
            raise
        except (smtplib.SMTPException, socket.error, OSError, TimeoutError, Exception) as exc:
            logger.error(
                "Failed to dispatch email via SMTP to %s (correlation_id=%s): %s",
                payload.recipient_email,
                payload.correlation_id,
                str(exc),
                exc_info=True,
            )
            raise EmailDispatchError(
                f"SMTP email delivery failed to {payload.recipient_email}: {exc}"
            ) from exc

        logger.info(
            "Successfully dispatched email via SMTP to %s (message_id=%s, correlation_id=%s)",
            payload.recipient_email,
            message_id,
            payload.correlation_id,
        )

        return DispatchResult(
            success=True,
            channel="Email",
            recipient=payload.recipient_email,
            dispatched_at=dispatched_at,
            correlation_id=payload.correlation_id,
            message_id=message_id,
            details={
                "smtp_host": self.smtp_host,
                "smtp_port": self.smtp_port,
                "urgency": payload.urgency,
                "subject": payload.subject,
                "from_email": self.smtp_from_email,
            },
        )


# Connector aliases for flexible importing across the codebase
EmailConnector = SMTPEmailConnector
EmailAdapter = SMTPEmailConnector
SMTPEmailAdapter = SMTPEmailConnector
