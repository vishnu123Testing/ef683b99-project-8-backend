"""Unit tests for FastAPI application entrypoint in backend/src/api/main.py."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from src.api.main import app, create_application, lifespan, parse_allowed_origins
from src.lib.config import settings
from src.models.database import Base


def test_parse_allowed_origins_list():
    """Test parse_allowed_origins when input is a list of strings."""
    origins = ["http://localhost:3000", "http://example.com"]
    result = parse_allowed_origins(origins)
    assert result == ["http://localhost:3000", "http://example.com"]


def test_parse_allowed_origins_string():
    """Test parse_allowed_origins when input is a comma-separated string."""
    origins_str = "http://localhost:3000, http://example.com, "
    result = parse_allowed_origins(origins_str)
    assert result == ["http://localhost:3000", "http://example.com"]


def test_app_instance():
    """Test that app instance is created with correct title and settings."""
    test_app = create_application()
    assert isinstance(test_app, FastAPI)
    assert test_app.title == settings.app_name


def test_root_health_endpoint():
    """Test GET /health returns 200 OK and expected app status."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["app"] == settings.app_name


def test_api_v1_health_endpoint():
    """Test GET /api/v1/health returns 200 OK and version info."""
    client = TestClient(app)
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["version"] == "v1"


def test_correlation_id_middleware_applied():
    """Test that CorrelationIdMiddleware attaches X-Correlation-ID header to responses."""
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    assert "X-Correlation-ID" in response.headers


@pytest.mark.asyncio
async def test_lifespan_context():
    """Test that lifespan startup handler executes without errors and registers metric_snapshots table."""
    dummy_app = FastAPI()
    async with lifespan(dummy_app):
        # Application startup complete inside context block
        assert "metric_snapshots" in Base.metadata.tables


def test_dashboard_routes_registered():
    """Test that dashboard endpoints are registered under /api/v1/dashboard."""
    route_paths = [route.path for route in app.routes]
    assert "/api/v1/dashboard/summary" in route_paths
    assert "/api/v1/dashboard/domains/{domain_id}" in route_paths
