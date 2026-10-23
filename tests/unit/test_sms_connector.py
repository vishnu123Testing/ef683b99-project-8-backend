"""Unit tests for SMS gateway connector in backend/src/connectors/sms.py [FR-8, NFR-2, NFR-3]."""

import logging
from unittest.mock import MagicMock

import pytest

from src.connectors.sms import (
    SMSAdapter,
    SMSConnector,
    SMSGatewayAdapter,
    SMSGatewayConnector,
)
from src.services.alerting.ports import (
    DispatchResult,
    SMSDispatcherPort,
    SMSDispatchError,
    SMSPayload,
)


class MockHTTPResponse:
    """Mock HTTP response object for testing Gateway HTTP calls."""

    def __init__(
        self, status_code: int = 200, text: str = '{"status": "queued"}'
    ) -> None:
        self.status_code = status_code
        self.text = text

    def json(self):
        return {"status": "queued"}


def test_sms_connector_class_aliases():
    """Verify class aliases for SMSGatewayConnector [FR-8]."""
    assert SMSConnector is SMSGatewayConnector
    assert SMSAdapter is SMSGatewayConnector
    assert SMSGatewayAdapter is SMSGatewayConnector
    assert issubclass(SMSGatewayConnector, SMSDispatcherPort)


def test_sms_connector_init_overrides():
    """Verify initialization parameter overrides."""
    connector = SMSGatewayConnector(
        gateway_url="https://sms.example.com/v1/send",
        api_key="secret-sms-key",
        sender_id="NOTIFY",
        timeout=5.0,
        dry_run=True,
    )
    assert connector.gateway_url == "https://sms.example.com/v1/send"
    assert connector.api_key == "secret-sms-key"
    assert connector.sender_id == "NOTIFY"
    assert connector.timeout == 5.0
    assert connector.dry_run is True


def test_send_sms_dry_run():
    """Verify send_sms in dry_run mode returns successful DispatchResult [FR-8]."""
    connector = SMSGatewayConnector(
        gateway_url="https://sms.example.com/v1/send",
        api_key="secret-sms-key",
        sender_id="ALERT",
        dry_run=True,
    )
    payload = SMSPayload(
        recipient_phone="+15551234567",
        message_text="High priority alert: Invoice #1001 past due",
        urgency="High",
        correlation_id="corr-dry-run-123",
    )
    result = connector.send_sms(payload)

    assert isinstance(result, DispatchResult)
    assert result.success is True
    assert result.channel == "SMS"
    assert result.recipient == "+15551234567"
    assert result.correlation_id == "corr-dry-run-123"
    assert result.details["dry_run"] is True
    assert result.details["sender_id"] == "ALERT"


def test_send_sms_invalid_payload_type():
    """Verify send_sms raises SMSDispatchError when provided invalid payload type."""
    connector = SMSGatewayConnector(dry_run=True)
    with pytest.raises(SMSDispatchError, match="Invalid payload type"):
        connector.send_sms("invalid-payload")  # type: ignore


def test_send_sms_missing_recipient_phone(caplog):
    """Verify send_sms raises SMSDispatchError when phone is missing [NFR-2]."""
    connector = SMSGatewayConnector(dry_run=True)
    payload = SMSPayload(
        recipient_phone="",
        message_text="Alert text",
        correlation_id="corr-missing-phone",
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(
            SMSDispatchError, match="Recipient phone number is required"
        ):
            connector.send_sms(payload)

    assert "missing recipient phone number" in caplog.text
    assert "corr-missing-phone" in caplog.text


def test_send_sms_missing_message_text(caplog):
    """Verify send_sms raises SMSDispatchError when message text is missing [NFR-2]."""
    connector = SMSGatewayConnector(dry_run=True)
    payload = SMSPayload(
        recipient_phone="+15551234567",
        message_text="",
        correlation_id="corr-missing-text",
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(SMSDispatchError, match="SMS message text is required"):
            connector.send_sms(payload)

    assert "missing message text" in caplog.text
    assert "corr-missing-text" in caplog.text


def test_send_sms_missing_gateway_url_config(caplog):
    """Verify send_sms raises error when SMS_GATEWAY_URL is unconfigured (NFR-3)."""
    connector = SMSGatewayConnector(
        gateway_url="",
        api_key="secret-key",
        dry_run=False,
    )
    payload = SMSPayload(
        recipient_phone="+15551234567",
        message_text="Alert text",
        correlation_id="corr-no-url",
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(
            SMSDispatchError, match="SMS gateway URL is not configured"
        ):
            connector.send_sms(payload)

    assert "SMS gateway URL is not configured" in caplog.text
    assert "corr-no-url" in caplog.text


def test_send_sms_missing_api_key_config(caplog):
    """Verify send_sms raises error when SMS_API_KEY is unconfigured (NFR-3)."""
    connector = SMSGatewayConnector(
        gateway_url="https://sms.example.com/v1/send",
        api_key="",
        dry_run=False,
    )
    payload = SMSPayload(
        recipient_phone="+15551234567",
        message_text="Alert text",
        correlation_id="corr-no-key",
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(
            SMSDispatchError, match="SMS API key is not configured"
        ):
            connector.send_sms(payload)

    assert "SMS API key is not configured" in caplog.text
    assert "corr-no-key" in caplog.text


def test_send_sms_success_with_mock_factory():
    """Verify successful SMS dispatch using mock request factory."""
    mock_factory = MagicMock(return_value=MockHTTPResponse(status_code=200))
    connector = SMSGatewayConnector(
        gateway_url="https://sms.example.com/v1/send",
        api_key="test-key-123",
        sender_id="SYSTEM",
        request_factory=mock_factory,
    )
    payload = SMSPayload(
        recipient_phone="+15559876543",
        message_text="Urgent financial threshold breach",
        urgency="High",
        correlation_id="corr-success-999",
    )

    result = connector.send_sms(payload)

    assert result.success is True
    assert result.channel == "SMS"
    assert result.recipient == "+15559876543"
    assert result.correlation_id == "corr-success-999"
    mock_factory.assert_called_once_with(
        "https://sms.example.com/v1/send",
        headers={
            "Authorization": "Bearer test-key-123",
            "Content-Type": "application/json",
            "X-Correlation-ID": "corr-success-999",
        },
        json={
            "to": "+15559876543",
            "message": "Urgent financial threshold breach",
            "sender_id": "SYSTEM",
            "urgency": "High",
            "correlation_id": "corr-success-999",
        },
        timeout=10.0,
    )


def test_send_sms_http_error_response(caplog):
    """Verify send_sms handles HTTP error code, logs warning, and raises error [NFR-2]."""
    mock_factory = MagicMock(
        return_value=MockHTTPResponse(status_code=500, text="Internal Server Error")
    )
    connector = SMSGatewayConnector(
        gateway_url="https://sms.example.com/v1/send",
        api_key="test-key-123",
        request_factory=mock_factory,
    )
    payload = SMSPayload(
        recipient_phone="+15559876543",
        message_text="Urgent threshold breach",
        correlation_id="corr-http-500",
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(SMSDispatchError, match="HTTP 500"):
            connector.send_sms(payload)

    assert "SMS gateway delivery failure" in caplog.text
    assert "corr-http-500" in caplog.text


def test_send_sms_exception_logging(caplog):
    """Verify network exceptions trigger warning log with correlation ID [NFR-2]."""
    mock_factory = MagicMock(side_effect=RuntimeError("Connection reset by peer"))
    connector = SMSGatewayConnector(
        gateway_url="https://sms.example.com/v1/send",
        api_key="test-key-123",
        request_factory=mock_factory,
    )
    payload = SMSPayload(
        recipient_phone="+15559876543",
        message_text="Urgent threshold breach",
        correlation_id="corr-network-err",
    )

    with caplog.at_level(logging.WARNING):
        with pytest.raises(SMSDispatchError, match="Connection reset by peer"):
            connector.send_sms(payload)

    assert "SMS gateway delivery failure" in caplog.text
    assert "corr-network-err" in caplog.text
    assert "+15559876543" in caplog.text


def test_send_sms_simulated_flags(caplog):
    """Verify simulation flags for failure, timeout, and invalid phone number."""
    payload = SMSPayload(
        recipient_phone="+15550000000",
        message_text="Test message",
        correlation_id="corr-sim",
    )

    # Simulated invalid phone
    c1 = SMSGatewayConnector(simulate_invalid_phone=True)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(SMSDispatchError, match="invalid recipient phone number"):
            c1.send_sms(payload)
    assert "invalid recipient phone number" in caplog.text

    # Simulated timeout
    caplog.clear()
    c2 = SMSGatewayConnector(simulate_timeout=True)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(SMSDispatchError, match="simulated timeout"):
            c2.send_sms(payload)
    assert "SMS gateway delivery timeout" in caplog.text

    # Simulated failure
    caplog.clear()
    c3 = SMSGatewayConnector(simulate_failure=True)
    with caplog.at_level(logging.WARNING):
        with pytest.raises(SMSDispatchError, match="Simulated gateway error"):
            c3.send_sms(payload)
    assert "Simulated gateway error" in caplog.text


def test_dispatch_and_send_aliases():
    """Verify dispatch() and send() alias methods on SMSGatewayConnector."""
    connector = SMSGatewayConnector(dry_run=True)
    payload = SMSPayload(
        recipient_phone="+15551234567",
        message_text="Alias test message",
    )

    r1 = connector.dispatch(payload)
    assert r1.success is True

    r2 = connector.send(payload)
    assert r2.success is True
