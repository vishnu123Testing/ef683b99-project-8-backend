"""SMS gateway connector implementing the SMS port interface [FR-8, NFR-2, NFR-3].

Provides concrete SMSGatewayConnector adapter implementation for dispatching
SMS notifications via HTTP gateway API with structured warning logging on
delivery failure.
"""

from datetime import datetime, timezone
import logging
from typing import Any, Callable, Optional
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

from src.lib.config import get_settings
from src.lib.logging import get_correlation_id
from src.services.alerting.ports import (
    DispatchResult,
    SMSDispatcherPort,
    SMSDispatchError,
    SMSPayload,
)

logger = logging.getLogger(__name__)


class SMSGatewayConnector(SMSDispatcherPort):
    """SMS gateway connector adapter implementing SMSDispatcherPort [FR-8, NFR-2, NFR-3].

    Handles SMS payload validation, gateway configuration, HTTP POST dispatch,
    and structured warning logging on delivery failure.
    """

    def __init__(
        self,
        gateway_url: Optional[str] = None,
        api_key: Optional[str] = None,
        sender_id: Optional[str] = None,
        timeout: float = 10.0,
        dry_run: bool = False,
        http_client: Optional[Any] = None,
        request_factory: Optional[Callable[..., Any]] = None,
        simulate_failure: bool = False,
        simulate_timeout: bool = False,
        simulate_invalid_phone: bool = False,
    ) -> None:
        """Initialize SMSGatewayConnector with optional configuration overrides.

        Args:
            gateway_url: Upstream SMS gateway HTTP URL.
            api_key: Upstream SMS gateway authentication API key.
            sender_id: Sender ID or outbound phone number.
            timeout: Network connection timeout in seconds.
            dry_run: If True, simulates dispatch without initiating network call.
            http_client: Optional custom HTTP client instance (e.g. httpx.Client or
                mock).
            request_factory: Optional factory callable for mocking HTTP requests in
                tests.
            simulate_failure: Testing flag to simulate gateway delivery failure.
            simulate_timeout: Testing flag to simulate gateway timeout.
            simulate_invalid_phone: Testing flag to simulate invalid recipient
                phone number.
        """
        app_settings = get_settings()
        self.gateway_url = (
            gateway_url if gateway_url is not None else app_settings.sms_gateway_url
        )
        self.api_key = (
            api_key if api_key is not None else app_settings.sms_api_key
        )
        self.sender_id = (
            sender_id if sender_id is not None else app_settings.sms_sender_id
        )
        self.timeout = timeout
        self.dry_run = dry_run
        self.http_client = http_client
        self.request_factory = request_factory
        self.simulate_failure = simulate_failure
        self.simulate_timeout = simulate_timeout
        self.simulate_invalid_phone = simulate_invalid_phone

    def send_sms(self, payload: SMSPayload) -> DispatchResult:
        """Dispatch an SMS payload via configured gateway [FR-8, NFR-2, NFR-3].

        Args:
            payload: SMSPayload object containing recipient_phone, message_text,
                urgency, correlation_id, and metadata.

        Returns:
            DispatchResult containing delivery status, timestamp, and metadata.

        Raises:
            SMSDispatchError: If payload validation fails, gateway configuration is
                missing, or network/gateway delivery errors occur.
        """
        if not isinstance(payload, SMSPayload):
            raise SMSDispatchError(
                "Invalid payload type provided to SMSGatewayConnector."
            )

        cid = payload.correlation_id or get_correlation_id() or str(uuid.uuid4())

        if not payload.recipient_phone or not str(payload.recipient_phone).strip():
            logger.warning(
                "SMS dispatch failed due to missing recipient phone number "
                "(correlation_id=%s)",
                cid,
                extra={
                    "correlation_id": cid,
                    "recipient_phone": "",
                    "urgency": payload.urgency,
                    "channel": "SMS",
                    "error_detail": "Missing recipient phone number",
                },
            )
            raise SMSDispatchError(
                "Recipient phone number is required for SMS dispatch."
            )

        if self.simulate_invalid_phone:
            logger.warning(
                "SMS gateway delivery failure: invalid recipient phone number %s "
                "(correlation_id=%s)",
                payload.recipient_phone,
                cid,
                extra={
                    "correlation_id": cid,
                    "recipient_phone": payload.recipient_phone,
                    "urgency": payload.urgency,
                    "channel": "SMS",
                    "error_detail": "Invalid recipient phone number",
                },
            )
            raise SMSDispatchError(
                "SMS delivery failed: invalid recipient phone number "
                f"{payload.recipient_phone}"
            )

        if not payload.message_text or not str(payload.message_text).strip():
            logger.warning(
                "SMS dispatch failed due to missing message text for recipient %s "
                "(correlation_id=%s)",
                payload.recipient_phone,
                cid,
                extra={
                    "correlation_id": cid,
                    "recipient_phone": payload.recipient_phone,
                    "urgency": payload.urgency,
                    "channel": "SMS",
                    "error_detail": "Missing message text",
                },
            )
            raise SMSDispatchError("SMS message text is required for dispatch.")

        dispatched_at = datetime.now(timezone.utc)
        message_id = f"sms-{uuid.uuid4()}"

        if self.dry_run:
            logger.info(
                "Simulated dry-run SMS dispatch to %s (correlation_id=%s)",
                payload.recipient_phone,
                cid,
            )
            return DispatchResult(
                success=True,
                channel="SMS",
                recipient=payload.recipient_phone,
                dispatched_at=dispatched_at,
                correlation_id=cid,
                message_id=message_id,
                details={
                    "dry_run": True,
                    "sender_id": self.sender_id,
                    "gateway_url": self.gateway_url,
                    "urgency": payload.urgency,
                },
            )

        if self.simulate_timeout:
            logger.warning(
                "SMS gateway delivery timeout for recipient %s (correlation_id=%s)",
                payload.recipient_phone,
                cid,
                extra={
                    "correlation_id": cid,
                    "recipient_phone": payload.recipient_phone,
                    "urgency": payload.urgency,
                    "channel": "SMS",
                    "error_detail": "Gateway request timeout",
                },
            )
            raise SMSDispatchError(
                f"SMS gateway timeout sending to {payload.recipient_phone} "
                "(simulated timeout)"
            )

        if self.simulate_failure:
            logger.warning(
                "SMS gateway delivery failure for recipient %s "
                "(correlation_id=%s): Simulated gateway error",
                payload.recipient_phone,
                cid,
                extra={
                    "correlation_id": cid,
                    "recipient_phone": payload.recipient_phone,
                    "urgency": payload.urgency,
                    "channel": "SMS",
                    "error_detail": "Simulated gateway error",
                },
            )
            raise SMSDispatchError(
                f"SMS gateway delivery failed to {payload.recipient_phone}: "
                "Simulated gateway error"
            )

        if not self.gateway_url or not str(self.gateway_url).strip():
            logger.warning(
                "SMS dispatch failed: SMS gateway URL is not configured (NFR-3) "
                "(correlation_id=%s)",
                cid,
                extra={
                    "correlation_id": cid,
                    "recipient_phone": payload.recipient_phone,
                    "urgency": payload.urgency,
                    "channel": "SMS",
                    "error_detail": "SMS gateway URL is not configured",
                },
            )
            raise SMSDispatchError(
                "SMS gateway URL is not configured. Provide SMS_GATEWAY_URL "
                "environment variable (NFR-3)."
            )

        if not self.api_key or not str(self.api_key).strip():
            logger.warning(
                "SMS dispatch failed: SMS API key is not configured (NFR-3) "
                "(correlation_id=%s)",
                cid,
                extra={
                    "correlation_id": cid,
                    "recipient_phone": payload.recipient_phone,
                    "urgency": payload.urgency,
                    "channel": "SMS",
                    "error_detail": "SMS API key is not configured",
                },
            )
            raise SMSDispatchError(
                "SMS API key is not configured. Provide SMS_API_KEY environment "
                "variable (NFR-3)."
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "X-Correlation-ID": cid,
        }
        body = {
            "to": payload.recipient_phone,
            "message": payload.message_text,
            "sender_id": self.sender_id,
            "urgency": payload.urgency,
            "correlation_id": cid,
        }

        try:
            if self.request_factory:
                response = self.request_factory(
                    self.gateway_url,
                    headers=headers,
                    json=body,
                    timeout=self.timeout,
                )
            elif self.http_client:
                if hasattr(self.http_client, "post"):
                    response = self.http_client.post(
                        self.gateway_url,
                        headers=headers,
                        json=body,
                        timeout=self.timeout,
                    )
                else:
                    response = self.http_client(
                        self.gateway_url,
                        headers=headers,
                        json=body,
                        timeout=self.timeout,
                    )
            else:
                with httpx.Client(timeout=self.timeout) as client:
                    response = client.post(
                        self.gateway_url,
                        headers=headers,
                        json=body,
                    )

            status_code = getattr(response, "status_code", 200)
            if status_code >= 400:
                resp_text = getattr(response, "text", str(response))
                logger.warning(
                    "SMS gateway delivery failure for recipient %s "
                    "(correlation_id=%s): HTTP %s - %s",
                    payload.recipient_phone,
                    cid,
                    status_code,
                    resp_text,
                    extra={
                        "correlation_id": cid,
                        "recipient_phone": payload.recipient_phone,
                        "urgency": payload.urgency,
                        "channel": "SMS",
                        "error_detail": f"HTTP {status_code}: {resp_text}",
                    },
                )
                raise SMSDispatchError(
                    f"SMS gateway returned HTTP {status_code}: {resp_text}"
                )

        except SMSDispatchError:
            raise
        except Exception as exc:
            logger.warning(
                "SMS gateway delivery failure for recipient %s "
                "(correlation_id=%s): %s",
                payload.recipient_phone,
                cid,
                str(exc),
                extra={
                    "correlation_id": cid,
                    "recipient_phone": payload.recipient_phone,
                    "urgency": payload.urgency,
                    "channel": "SMS",
                    "error_detail": str(exc),
                },
                exc_info=True,
            )
            raise SMSDispatchError(
                f"SMS gateway delivery failed to {payload.recipient_phone}: {exc}"
            ) from exc

        logger.info(
            "Successfully dispatched SMS via gateway to %s (message_id=%s, "
            "correlation_id=%s)",
            payload.recipient_phone,
            message_id,
            cid,
        )

        return DispatchResult(
            success=True,
            channel="SMS",
            recipient=payload.recipient_phone,
            dispatched_at=dispatched_at,
            correlation_id=cid,
            message_id=message_id,
            details={
                "gateway_url": self.gateway_url,
                "sender_id": self.sender_id,
                "urgency": payload.urgency,
            },
        )


# Connector aliases for flexible importing across the codebase
SMSConnector = SMSGatewayConnector
SMSAdapter = SMSGatewayConnector
SMSGatewayAdapter = SMSGatewayConnector
