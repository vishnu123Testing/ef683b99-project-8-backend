"""Unit tests for backend.src.lib.logging JSON formatter and correlation middleware."""

import json
import logging
import sys
from typing import Any, Dict
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.lib.logging import (
    CorrelationIdMiddleware,
    JSONFormatter,
    get_correlation_id,
    mask_sensitive_data,
    reset_correlation_id,
    set_correlation_id,
    setup_logging,
)


def test_correlation_id_context():
    """Test getting, setting, and resetting correlation ID in context."""
    assert get_correlation_id() is None

    test_id = str(uuid.uuid4())
    token = set_correlation_id(test_id)
    assert get_correlation_id() == test_id

    reset_correlation_id(token)
    assert get_correlation_id() is None


def test_mask_sensitive_data():
    """Test recursive masking of sensitive keys in nested data structures."""
    payload = {
        "username": "alice",
        "password": "secret-password-123",
        "nested": {
            "access_token": "bearer-token-xyz",
            "normal_key": "visible_value",
            "api_key": "key-999",
        },
        "list_data": [
            {"secret": "hidden_value", "public": "open_value"},
        ],
    }

    masked = mask_sensitive_data(payload)

    assert masked["username"] == "alice"
    assert masked["password"] == "***MASKED***"
    assert masked["nested"]["access_token"] == "***MASKED***"
    assert masked["nested"]["normal_key"] == "visible_value"
    assert masked["nested"]["api_key"] == "***MASKED***"
    assert masked["list_data"][0]["secret"] == "***MASKED***"
    assert masked["list_data"][0]["public"] == "open_value"


def test_json_formatter_basic():
    """Test JSONFormatter produces structured JSON with expected attributes."""
    formatter = JSONFormatter()

    cid = "test-correlation-123"
    token = set_correlation_id(cid)

    try:
        record = logging.LogRecord(
            name="test_logger",
            level=logging.INFO,
            pathname="test.py",
            lineno=10,
            msg="User %s logged in successfully",
            args=("admin",),
            exc_info=None,
        )

        formatted_str = formatter.format(record)
        data = json.loads(formatted_str)

        assert data["level"] == "INFO"
        assert data["logger"] == "test_logger"
        assert data["message"] == "User admin logged in successfully"
        assert data["correlation_id"] == cid
        assert "timestamp" in data
        assert "logged_at" in data
        assert "environment" in data
    finally:
        reset_correlation_id(token)


def test_json_formatter_sensitive_extra_fields():
    """Test JSONFormatter masks sensitive extra attributes attached to LogRecord."""
    formatter = JSONFormatter()

    record = logging.LogRecord(
        name="auth_logger",
        level=logging.WARNING,
        pathname="auth.py",
        lineno=42,
        msg="Authentication attempt failed",
        args=(),
        exc_info=None,
    )
    record.user_email = "user@example.com"
    record.password = "my_plain_password"
    record.authorization = "Bearer secrettoken"

    formatted_str = formatter.format(record)
    data = json.loads(formatted_str)

    assert data["user_email"] == "user@example.com"
    assert data["password"] == "***MASKED***"
    assert data["authorization"] == "***MASKED***"


def test_json_formatter_exception_info():
    """Test JSONFormatter formats exception traces cleanly."""
    formatter = JSONFormatter()

    try:
        raise ValueError("Simulated validation error")
    except ValueError:
        exc_info = sys.exc_info()

    record = logging.LogRecord(
        name="error_logger",
        level=logging.ERROR,
        pathname="error.py",
        lineno=100,
        msg="An unhandled exception occurred",
        args=(),
        exc_info=exc_info,
    )

    formatted_str = formatter.format(record)
    data = json.loads(formatted_str)

    assert data["level"] == "ERROR"
    assert "exception" in data
    assert "ValueError: Simulated validation error" in data["exception"]


def test_correlation_middleware_with_provided_header():
    """Test middleware extracts X-Correlation-ID from request and echoes in response."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/test")
    def test_endpoint():
        return {"active_cid": get_correlation_id()}

    client = TestClient(app)
    custom_cid = "client-provided-correlation-id-99"

    response = client.get("/test", headers={"X-Correlation-ID": custom_cid})

    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID") == custom_cid
    assert response.json()["active_cid"] == custom_cid

    # Verify contextvar was cleared after request execution
    assert get_correlation_id() is None


def test_correlation_middleware_with_x_request_id_header():
    """Test middleware extracts X-Request-ID when X-Correlation-ID is missing."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/test")
    def test_endpoint():
        return {"active_cid": get_correlation_id()}

    client = TestClient(app)
    custom_req_id = "request-id-header-55"

    response = client.get("/test", headers={"X-Request-ID": custom_req_id})

    assert response.status_code == 200
    assert response.headers.get("X-Correlation-ID") == custom_req_id
    assert response.json()["active_cid"] == custom_req_id


def test_correlation_middleware_generates_uuid_when_missing():
    """Test middleware generates new UUID4 correlation ID if no header provided."""
    app = FastAPI()
    app.add_middleware(CorrelationIdMiddleware)

    @app.get("/test")
    def test_endpoint():
        return {"active_cid": get_correlation_id()}

    client = TestClient(app)
    response = client.get("/test")

    assert response.status_code == 200
    response_cid = response.headers.get("X-Correlation-ID")
    assert response_cid is not None
    # Validate it's a valid UUID
    uuid_obj = uuid.UUID(response_cid)
    assert str(uuid_obj) == response_cid
    assert response.json()["active_cid"] == response_cid


def test_setup_logging_configuration():
    """Test setup_logging attaches StreamHandler with JSONFormatter to root logger."""
    setup_logging(log_level="DEBUG")

    root_logger = logging.getLogger()
    assert root_logger.level == logging.DEBUG
    assert len(root_logger.handlers) >= 1

    stream_handler = root_logger.handlers[0]
    assert isinstance(stream_handler, logging.StreamHandler)
    assert isinstance(stream_handler.formatter, JSONFormatter)
