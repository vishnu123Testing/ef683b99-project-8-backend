"""Unit tests for authentication endpoints in backend/src/api/v1/endpoints/auth.py."""

import uuid

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from src.api.deps import get_db
from src.api.main import app
from src.models.database import Base
from src.models.role import ROLE_CLIENT_VIEWER, Role
from src.models.tenant import Tenant
from src.models.user import User, hash_password


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


def test_register_user_success(test_client):
    """Test successful user registration assigns default ClientViewer role and returns 201 Created."""
    client, SessionLocal = test_client

    payload = {
        "email": "newuser@example.com",
        "password": "SecurePassword123!",
    }
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()

    assert data["email"] == "newuser@example.com"
    assert "user_id" in data
    assert "tenant_id" in data
    assert ROLE_CLIENT_VIEWER in data["roles"]

    # Verify user saved in DB
    db = SessionLocal()
    user = db.query(User).filter_by(email="newuser@example.com").first()
    assert user is not None
    assert user.check_password("SecurePassword123!") is True
    db.close()


def test_register_user_duplicate_email_returns_409(test_client):
    """Test user registration with existing email returns HTTP 409 Conflict."""
    client, _ = test_client

    payload = {
        "email": "duplicate@example.com",
        "password": "SecurePassword123!",
    }
    resp1 = client.post("/api/v1/auth/register", json=payload)
    assert resp1.status_code == 201

    resp2 = client.post("/api/v1/auth/register", json=payload)
    assert resp2.status_code == 409
    assert "already exists" in resp2.json()["detail"]


def test_register_user_with_specific_tenant_id(test_client):
    """Test registering a user with an explicitly provided tenant_id."""
    client, SessionLocal = test_client

    db = SessionLocal()
    tenant = Tenant(name="Custom Tenant")
    db.add(tenant)
    db.commit()
    tenant_id_str = str(tenant.tenant_id)
    db.close()

    payload = {
        "email": "tenantuser@example.com",
        "password": "SecurePassword123!",
        "tenant_id": tenant_id_str,
    }
    response = client.post("/api/v1/auth/register", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["tenant_id"] == tenant_id_str


def test_login_success(test_client):
    """Test successful user login returns HTTP 200 OK with bearer token."""
    client, _ = test_client

    # 1. Register user
    reg_payload = {
        "email": "loginuser@example.com",
        "password": "MySecretPassword1!",
    }
    reg_resp = client.post("/api/v1/auth/register", json=reg_payload)
    assert reg_resp.status_code == 201

    # 2. Login
    login_payload = {
        "email": "loginuser@example.com",
        "password": "MySecretPassword1!",
    }
    login_resp = client.post("/api/v1/auth/login", json=login_payload)
    assert login_resp.status_code == 200
    data = login_resp.json()

    assert "access_token" in data
    assert data["token_type"] == "bearer"
    assert "user_id" in data
    assert "tenant_id" in data


def test_login_invalid_credentials_returns_401(test_client):
    """Test login with incorrect password returns HTTP 401 Unauthorized."""
    client, _ = test_client

    # Register user first
    reg_payload = {
        "email": "wrongpass@example.com",
        "password": "CorrectPassword123!",
    }
    client.post("/api/v1/auth/register", json=reg_payload)

    # Attempt login with wrong password
    login_payload = {
        "email": "wrongpass@example.com",
        "password": "WrongPassword123!",
    }
    resp = client.post("/api/v1/auth/login", json=login_payload)
    assert resp.status_code == 401
    assert "Invalid email or password" in resp.json()["detail"]


def test_login_nonexistent_user_returns_401(test_client):
    """Test login with email that does not exist returns HTTP 401 Unauthorized."""
    client, _ = test_client

    login_payload = {
        "email": "nonexistent@example.com",
        "password": "SomePassword123!",
    }
    resp = client.post("/api/v1/auth/login", json=login_payload)
    assert resp.status_code == 401
    assert "Invalid email or password" in resp.json()["detail"]


def test_get_active_identity_me_success(test_client):
    """Test GET /api/v1/auth/me returns identity details for authenticated user."""
    client, _ = test_client

    # Register and login
    email = "me_user@example.com"
    password = "MyPassword123!"
    client.post("/api/v1/auth/register", json={"email": email, "password": password})
    login_resp = client.post("/api/v1/auth/login", json={"email": email, "password": password})
    token = login_resp.json()["access_token"]

    # Request /me with Bearer token
    headers = {"Authorization": f"Bearer {token}"}
    me_resp = client.get("/api/v1/auth/me", headers=headers)
    assert me_resp.status_code == 200
    me_data = me_resp.json()

    assert me_data["email"] == email
    assert ROLE_CLIENT_VIEWER in me_data["roles"]
    assert "user_id" in me_data
    assert "tenant_id" in me_data


def test_get_active_identity_me_unauthorized(test_client):
    """Test GET /api/v1/auth/me without token returns HTTP 401 Unauthorized."""
    client, _ = test_client

    me_resp = client.get("/api/v1/auth/me")
    assert me_resp.status_code == 401
