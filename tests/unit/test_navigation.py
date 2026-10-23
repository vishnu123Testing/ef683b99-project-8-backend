"""Unit tests for navigation endpoints in backend/src/api/v1/endpoints/navigation.py."""

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.api.deps import create_access_token, get_db
from src.api.main import app
from src.models.database import Base
from src.models.role import ROLE_CLIENT_VIEWER, Role
from src.models.tenant import Tenant
from src.models.user import User
from src.services.access_control.rbac import assign_role_to_user


@pytest.fixture
def test_client():
    """Fixture providing a FastAPI TestClient with an in-memory SQLite database session override."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})

    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    # Seed system roles
    db = TestingSessionLocal()
    if not db.query(Role).filter_by(name=ROLE_CLIENT_VIEWER).first():
        db.add(Role(name=ROLE_CLIENT_VIEWER, description="ClientViewer default role"))
        db.commit()
    db.close()

    def override_get_db():
        session = TestingSessionLocal()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as client:
        yield client, TestingSessionLocal

    app.dependency_overrides.clear()


def test_navigation_menu_unauthenticated_returns_401(test_client):
    """Test accessing GET /api/v1/navigation/menu without bearer token returns 401 Unauthorized."""
    client, _ = test_client

    response = client.get("/api/v1/navigation/menu")
    assert response.status_code == 401
    assert "Authentication token required" in response.json()["detail"]


def test_navigation_menu_invalid_token_returns_401(test_client):
    """Test accessing GET /api/v1/navigation/menu with invalid token returns 401 Unauthorized."""
    client, _ = test_client

    headers = {"Authorization": "Bearer invalid_token_value"}
    response = client.get("/api/v1/navigation/menu", headers=headers)
    assert response.status_code == 401


def test_navigation_menu_authorized_user_returns_routes(test_client):
    """Test authenticated user with ClientViewer role gets all 6 default domain routes."""
    client, SessionLocal = test_client

    # Create user and assign role
    db = SessionLocal()
    tenant = Tenant(name="Nav Test Tenant")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    user = User(
        email="navuser@example.com",
        tenant_id=tenant.tenant_id,
    )
    user.set_password("SecurePassword123!")
    db.add(user)
    db.commit()
    db.refresh(user)

    assign_role_to_user(
        db,
        user_id=user.user_id,
        role_name=ROLE_CLIENT_VIEWER,
        tenant_id=tenant.tenant_id,
    )

    token = create_access_token(
        data={"sub": str(user.user_id), "tenant_id": str(tenant.tenant_id)}
    )
    db.close()

    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/v1/navigation/menu", headers=headers)
    assert response.status_code == 200
    data = response.json()

    assert "items" in data
    items = data["items"]
    assert len(items) == 6

    expected_paths = [
        "/dashboard",
        "/performance",
        "/contracts",
        "/support",
        "/financials",
        "/knowledge",
    ]
    returned_paths = [item["path"] for item in items]
    assert returned_paths == expected_paths

    for item in items:
        assert item["is_accessible"] is True
        assert "title" in item
        assert "icon_id" in item


def test_navigation_menu_user_without_roles_returns_empty_items(test_client):
    """Test authenticated user without any assigned role receives empty items list."""
    client, SessionLocal = test_client

    db = SessionLocal()
    tenant = Tenant(name="No Role Tenant")
    db.add(tenant)
    db.commit()
    db.refresh(tenant)

    user = User(
        email="noroleuser@example.com",
        tenant_id=tenant.tenant_id,
    )
    user.set_password("SecurePassword123!")
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token(
        data={"sub": str(user.user_id), "tenant_id": str(tenant.tenant_id)}
    )
    db.close()

    headers = {"Authorization": f"Bearer {token}"}
    response = client.get("/api/v1/navigation/menu", headers=headers)
    assert response.status_code == 200
    data = response.json()

    assert "items" in data
    assert len(data["items"]) == 0
