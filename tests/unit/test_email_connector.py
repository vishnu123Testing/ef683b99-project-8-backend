"""Unit tests for SMTP email connector in backend/src/connectors/email.py [FR-8, NFR-3]."""

from datetime import datetime, timezone
from unittest.mock import MagicMock
import pytest

from src.connectors.email import (
    EmailAdapter,
    EmailConnector,
    SMTPEmailAdapter,
    SMTPEmailConnector,
)
from src.services.alerting.ports import (
    EmailDispatcherPort,
    EmailDispatchError,
    EmailPayload,
)


class MockSMTPServer:
    """Mock SMTP server for testing SMTP connection and message delivery."""

    def __init__(self, should_fail: bool = False, fail_stage: str = "send") -> None:
        self.should_fail = should_fail
        self.fail_stage = fail_stage
        self.starttls_called = False
        self.login_called = False
        self.login_credentials = None
        self.sent_messages = []

    def __enter__(self):
        if self.should_fail and self.fail_stage == "connect":
            raise OSError("Connection refused")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False

    def starttls(self):
        if self.should_fail and self.fail_stage == "starttls":
            raise OSError("TLS negotiation failed")
        self.starttls_called = True

    def login(self, user, password):
        if self.should_fail and self.fail_stage == "login":
            raise OSError("Authentication failed")
        self.login_called = True
        self.login_credentials = (user, password)

    def send_message(self, msg):
        if self.should_fail and self.fail_stage == "send":
            raise OSError("SMTP write error")
        self.sent_messages.append(msg)


def test_smtp_email_connector_class_aliases():
    """Verify class aliases for SMTPEmailConnector."""
    assert EmailConnector is SMTPEmailConnector
    assert EmailAdapter is SMTPEmailConnector
    assert SMTPEmailAdapter is SMTPEmailConnector
    assert issubclass(SMTPEmailConnector, EmailDispatcherPort)


def test_smtp_email_connector_init_overrides():
    """Verify initialization parameter overrides."""
    connector = SMTPEmailConnector(
        smtp_host="mail.example.com",
        smtp_port=2525,
        smtp_user="user@example.com",
        smtp_password="secure-password",
        smtp_from_email="alerts@example.com",
        smtp_use_tls=False,
        timeout=5.0,
        dry_run=True,
    )
    assert connector.smtp_host == "mail.example.com"
    assert connector.smtp_port == 2525
    assert connector.smtp_user == "user@example.com"
    assert connector.smtp_password == "secure-password"
    assert connector.smtp_from_email == "alerts@example.com"
    assert connector.smtp_use_tls is False
    assert connector.timeout == 5.0
    assert connector.dry_run is True


def test_send_email_dry_run():
    """Verify send_email in dry_run mode without network execution [FR-8]."""
    connector = SMTPEmailConnector(
        smtp_host="mail.example.com",
        smtp_from_email="noreply@example.com",
        dry_run=True,
    )
    payload = EmailPayload(
        recipient_email="finance@example.com",
        subject="Invoice Past Due Alert",
        body_text="Invoice #1001 is past due.",
        urgency="High",
        correlation_id="corr-123",
    )

    result = connector.send_email(payload)
    assert result.success is True
    assert result.channel == "Email"
    assert result.recipient == "finance@example.com"
    assert result.correlation_id == "corr-123"
    assert result.message_id.startswith("email-")
    assert result.details["dry_run"] is True


def test_convenience_aliases():
    """Verify convenience alias methods dispatch and send."""
    connector = SMTPEmailConnector(
        smtp_host="mail.example.com",
        smtp_from_email="noreply@example.com",
        dry_run=True,
    )
    payload = EmailPayload(
        recipient_email="finance@example.com",
        subject="Test Alias",
        body_text="Testing aliases.",
    )

    result1 = connector.dispatch(payload)
    result2 = connector.send(payload)

    assert result1.success is True
    assert result2.success is True


def test_send_email_validation_failures():
    """Verify error checking for invalid payloads and missing configuration."""
    connector = SMTPEmailConnector(
        smtp_host="mail.example.com",
        smtp_from_email="noreply@example.com",
        dry_run=True,
    )

    # Invalid payload type
    with pytest.raises(EmailDispatchError, match="Invalid payload type"):
        connector.send_email("invalid-payload")  # type: ignore

    # Missing recipient email
    payload_no_recipient = EmailPayload(
        recipient_email="",
        subject="Test",
        body_text="Test Body",
    )
    with pytest.raises(EmailDispatchError, match="Recipient email address is required"):
        connector.send_email(payload_no_recipient)

    # Missing body content
    payload_no_body = EmailPayload(
        recipient_email="user@example.com",
        subject="Test",
        body_text="",
        body_html="",
    )
    with pytest.raises(EmailDispatchError, match="Email body text or HTML content is required"):
        connector.send_email(payload_no_body)


def test_send_email_missing_host_or_sender():
    """Verify EmailDispatchError raised when host or sender email is missing [NFR-3]."""
    connector_no_host = SMTPEmailConnector(
        smtp_host="",
        smtp_from_email="sender@example.com",
        dry_run=False,
    )
    payload = EmailPayload(
        recipient_email="user@example.com",
        subject="Test",
        body_text="Body text",
    )
    with pytest.raises(EmailDispatchError, match="SMTP host is not configured"):
        connector_no_host.send_email(payload)

    connector_no_sender = SMTPEmailConnector(
        smtp_host="mail.example.com",
        smtp_from_email="",
        dry_run=False,
    )
    with pytest.raises(EmailDispatchError, match="SMTP sender email"):
        connector_no_sender.send_email(payload)


def test_send_email_incomplete_credentials():
    """Verify error raised when incomplete credentials are provided [NFR-3]."""
    mock_server = MockSMTPServer()
    connector = SMTPEmailConnector(
        smtp_host="mail.example.com",
        smtp_from_email="sender@example.com",
        smtp_user="user@example.com",
        smtp_password="",  # Missing password
        smtp_factory=lambda host, port, timeout: mock_server,
    )
    payload = EmailPayload(
        recipient_email="recipient@example.com",
        subject="Credential Check",
        body_text="Checking credentials.",
    )
    with pytest.raises(EmailDispatchError, match="Incomplete SMTP credentials provided"):
        connector.send_email(payload)


def test_send_email_smtp_happy_path_with_auth_and_tls():
    """Verify happy path SMTP dispatch with TLS, auth, and HTML body [FR-8]."""
    mock_server = MockSMTPServer()
    connector = SMTPEmailConnector(
        smtp_host="smtp.example.com",
        smtp_port=587,
        smtp_user="auth_user",
        smtp_password="auth_password",
        smtp_from_email="alerts@example.com",
        smtp_use_tls=True,
        smtp_factory=lambda host, port, timeout: mock_server,
    )

    payload = EmailPayload(
        recipient_email="client@example.com",
        subject="Payment Received",
        body_text="Thank you for your payment.",
        body_html="<p>Thank you for your payment.</p>",
        urgency="High",
        correlation_id="corr-999",
    )

    result = connector.send_email(payload)

    assert result.success is True
    assert result.channel == "Email"
    assert result.recipient == "client@example.com"
    assert result.correlation_id == "corr-999"

    assert mock_server.starttls_called is True
    assert mock_server.login_called is True
    assert mock_server.login_credentials == ("auth_user", "auth_password")
    assert len(mock_server.sent_messages) == 1

    sent_msg = mock_server.sent_messages[0]
    assert sent_msg["Subject"] == "Payment Received"
    assert sent_msg["From"] == "alerts@example.com"
    assert sent_msg["To"] == "client@example.com"
    assert sent_msg["X-Correlation-ID"] == "corr-999"
    assert sent_msg["X-Urgency"] == "High"


def test_send_email_smtp_happy_path_no_tls_no_auth():
    """Verify happy path SMTP dispatch without TLS or auth."""
    mock_server = MockSMTPServer()
    connector = SMTPEmailConnector(
        smtp_host="smtp.internal",
        smtp_port=25,
        smtp_user=None,
        smtp_password=None,
        smtp_from_email="noreply@internal",
        smtp_use_tls=False,
        smtp_factory=lambda host, port, timeout: mock_server,
    )

    payload = EmailPayload(
        recipient_email="internal@example.com",
        subject="Internal Notification",
        body_text="Internal body text.",
    )

    result = connector.send_email(payload)

    assert result.success is True
    assert mock_server.starttls_called is False
    assert mock_server.login_called is False
    assert len(mock_server.sent_messages) == 1


def test_send_email_smtp_network_failure():
    """Verify network or connection error raises EmailDispatchError."""
    mock_server = MockSMTPServer(should_fail=True, fail_stage="connect")
    connector = SMTPEmailConnector(
        smtp_host="unreachable.example.com",
        smtp_from_email="alerts@example.com",
        smtp_factory=lambda host, port, timeout: mock_server,
    )

    payload = EmailPayload(
        recipient_email="recipient@example.com",
        subject="Connection Test",
        body_text="Testing connection error.",
    )

    with pytest.raises(EmailDispatchError, match="SMTP email delivery failed"):
        connector.send_email(payload)
