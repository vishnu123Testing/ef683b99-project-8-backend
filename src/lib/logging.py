"""Structured JSON logging formatter and correlation middleware.

Provides structured JSON logging output to stdout, context-managed correlation IDs,
and FastAPI middleware for correlation ID propagation across request lifecycles.
"""

from contextvars import ContextVar, Token
from datetime import datetime, timezone
import json
import logging
import sys
from typing import Any, Dict, Optional, Set
import uuid

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from src.lib.config import settings

# Context variable holding request correlation ID
correlation_id_ctx: ContextVar[Optional[str]] = ContextVar("correlation_id", default=None)

# Standard log record attributes to exclude when extracting extra fields
STANDARD_LOG_RECORD_ATTRS: Set[str] = {
    "args",
    "asctime",
    "created",
    "exc_info",
    "exc_text",
    "filename",
    "funcName",
    "levelname",
    "levelno",
    "lineno",
    "message",
    "module",
    "msecs",
    "msg",
    "name",
    "pathname",
    "process",
    "processName",
    "relativeCreated",
    "stack_info",
    "thread",
    "threadName",
    "correlation_id",
}

# Sensitive key names or substrings to mask in logs
SENSITIVE_KEYS: Set[str] = {
    "password",
    "token",
    "access_token",
    "refresh_token",
    "secret",
    "secret_key",
    "authorization",
    "api_key",
    "credentials",
    "private_key",
}


def get_correlation_id() -> Optional[str]:
    """Retrieve the current correlation ID from context."""
    return correlation_id_ctx.get()


def set_correlation_id(cid: Optional[str]) -> Token[Optional[str]]:
    """Set the correlation ID in context and return token."""
    return correlation_id_ctx.set(cid)


def reset_correlation_id(token: Token[Optional[str]]) -> None:
    """Reset the correlation ID in context using token."""
    correlation_id_ctx.reset(token)


def mask_sensitive_data(data: Any) -> Any:
    """Recursively mask sensitive keys in dictionaries, lists, and tuples."""
    if isinstance(data, dict):
        masked_dict: Dict[str, Any] = {}
        for key, value in data.items():
            key_lower = str(key).lower()
            if any(s_key in key_lower for s_key in SENSITIVE_KEYS):
                masked_dict[str(key)] = "***MASKED***"
            else:
                masked_dict[str(key)] = mask_sensitive_data(value)
        return masked_dict
    elif isinstance(data, list):
        return [mask_sensitive_data(item) for item in data]
    elif isinstance(data, tuple):
        return tuple(mask_sensitive_data(item) for item in data)
    return data


class JSONFormatter(logging.Formatter):
    """Formatter that outputs structured JSON with correlation context and sensitive data masking."""

    def __init__(self) -> None:
        super().__init__()

    def format(self, record: logging.LogRecord) -> str:
        """Format the log record into a structured JSON string."""
        timestamp_str = datetime.now(timezone.utc).isoformat()

        # Resolve correlation ID from record or contextvar
        cid = getattr(record, "correlation_id", None) or get_correlation_id()

        log_payload: Dict[str, Any] = {
            "timestamp": timestamp_str,
            "logged_at": timestamp_str,
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "correlation_id": cid,
            "environment": getattr(settings, "environment", "development"),
        }

        # Handle exception info if present
        if record.exc_info:
            log_payload["exception"] = self.formatException(record.exc_info)

        # Include extra attributes attached to record
        extra_data: Dict[str, Any] = {}
        for key, val in record.__dict__.items():
            if key not in STANDARD_LOG_RECORD_ATTRS and not key.startswith("_"):
                extra_data[key] = val

        if extra_data:
            masked_extra = mask_sensitive_data(extra_data)
            log_payload.update(masked_extra)

        # Mask payload if message or other fields contained sensitive nested data
        masked_payload = mask_sensitive_data(log_payload)

        try:
            return json.dumps(masked_payload, default=str, ensure_ascii=False)
        except Exception as err:
            # Fallback for unexpected serialization failures
            fallback_payload = {
                "timestamp": timestamp_str,
                "logged_at": timestamp_str,
                "level": record.levelname,
                "logger": record.name,
                "message": str(record.msg),
                "correlation_id": cid,
                "formatting_error": str(err),
            }
            return json.dumps(fallback_payload, default=str)


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Middleware that injects and propagates correlation IDs across HTTP request lifecycles."""

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        header_cid = (
            request.headers.get("x-correlation-id")
            or request.headers.get("x-request-id")
        )

        if header_cid and header_cid.strip():
            cid = header_cid.strip()
        else:
            cid = str(uuid.uuid4())

        token = set_correlation_id(cid)
        try:
            response = await call_next(request)
            response.headers["X-Correlation-ID"] = cid
            return response
        finally:
            reset_correlation_id(token)


def setup_logging(log_level: Optional[str] = None) -> None:
    """Configure stdout stream handler with JSONFormatter and specified log level."""
    level_name = log_level or settings.log_level
    numeric_level = getattr(logging, level_name.upper(), logging.INFO)

    root_logger = logging.getLogger()
    root_logger.setLevel(numeric_level)

    # Clear existing handlers to prevent duplicate logs
    for handler in root_logger.handlers[:]:
        root_logger.removeHandler(handler)

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(JSONFormatter())
    root_logger.addHandler(stream_handler)

    # Ensure standard uvicorn and fastapi loggers propagate or use JSON formatting
    for logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error", "fastapi"):
        uv_logger = logging.getLogger(logger_name)
        uv_logger.handlers = []
        uv_logger.propagate = True
