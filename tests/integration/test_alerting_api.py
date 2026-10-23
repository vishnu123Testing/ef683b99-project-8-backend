"""Integration tests for multi-channel alerting and notification API endpoints in backend/src/api/alerting.py [FR-5, FR-6, FR-7, FR-9, FR-10]."""

from datetime import datetime, timezone
from decimal import Decimal
import uuid

from fastapi import status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.api.deps import get_current_user, get_db
from src.api.main import app
from src.models.alerting import AlertTrigger, NotificationLog, NotificationPreference
from src.models.database import Base
from src.models.role import Role
from src.models.tenant import Tenant
from src.models.user import User, hash_password
from src.services.access_control.rbac import assign_role_to_user

# Isolated in-memory database setup for API integration testing
engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def override_get_db():
    """Dependency override providing isolated in-memory DB session."""
    db = TestingSessionLocal()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def setup_test_database():
    """Fixture creating fresh database schema for every test."""
    Base.metadata.create_all(bind=engine)
    app.dependency_overrides[get_db] = override_get_db
    yield
    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def admin_user():
    """Fixture creating a test user with admin:write role assigned."""
    db = TestingSessionLocal()
    tenant = Tenant(name="Alerting Admin Tenant")
    db.add(tenant)
    db.commit()

    user = User(
        email="alert.admin@example.com",
        password_hash=hash_password("Pass123!"),
        tenant_id=tenant.tenant_id,
    )
    db.add(user)
    db.commit()

    role = Role(name="admin:write", description="Admin Write Permission")
    db.add(role)
    db.commit()

    assign_role_to_user(db, user.user_id, "admin:write", tenant.tenant_id)
    db.close()
    return user


@pytest.fixture
def non_admin_user():
    """Fixture creating a test user without admin:write role."""
    db = TestingSessionLocal()
    tenant = Tenant(name="Alerting Standard Tenant")
    db.add(tenant)
    db.commit()

    user = User(
        email="alert.standard@example.com",
        password_hash=hash_password("Pass123!"),
        tenant_id=tenant.tenant_id,
    )
    db.add(user)
    db.commit()

    role = Role(name="ClientViewer", description="Standard Viewer")
    db.add(role)
    db.commit()

    assign_role_to_user(db, user.user_id, "ClientViewer", tenant.tenant_id)
    db.close()
    return user


@pytest.fixture
def admin_client(admin_user):
    """TestClient configured with authenticated user possessing admin:write role."""
    app.dependency_overrides[get_current_user] = lambda: admin_user
    client = TestClient(app)
    return client


@pytest.fixture
def non_admin_client(non_admin_user):
    """TestClient configured with authenticated user lacking admin:write role."""
    app.dependency_overrides[get_current_user] = lambda: non_admin_user
    client = TestClient(app)
    return client


# Tests for Custom Trigger Rules [FR-6, FR-9, FR-10]


def test_create_trigger_rule_success(admin_client):
    """Test creating a custom trigger rule successfully as an admin [FR-6, FR-9]."""
    payload = {
        "rule_name": "High Invoice Past Due",
        "financial_criteria": "past_due_balance_exceeded",
        "threshold_amount": 5000.0,
        "urgency": "High",
        "is_active": True,
    }
    response = admin_client.post("/api/v1/notifications/triggers", json=payload)
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["rule_name"] == "High Invoice Past Due"
    assert data["financial_criteria"] == "past_due_balance_exceeded"
    assert data["threshold_amount"] == 5000.0
    assert data["urgency"] == "High"
    assert data["is_active"] is True
    assert "trigger_id" in data


def test_create_trigger_rule_duplicate_name_conflict(admin_client):
    """Test creating a trigger rule with a duplicate rule_name returns 409 Conflict [FR-6, FR-10]."""
    payload = {
        "rule_name": "Unique Rule Name",
        "financial_criteria": "past_due_balance_exceeded",
        "threshold_amount": 1000.0,
        "urgency": "Medium",
    }
    res1 = admin_client.post("/api/v1/notifications/triggers", json=payload)
    assert res1.status_code == status.HTTP_201_CREATED

    res2 = admin_client.post("/api/v1/notifications/triggers", json=payload)
    assert res2.status_code == status.HTTP_409_CONFLICT
    assert "already exists" in res2.json()["detail"]


def test_create_trigger_rule_unauthorized_forbidden(non_admin_client):
    """Test creating a trigger rule without admin:write role returns 403 Forbidden [FR-9]."""
    payload = {
        "rule_name": "Unauthorized Rule",
        "financial_criteria": "past_due_balance_exceeded",
        "threshold_amount": 500.0,
        "urgency": "Low",
    }
    response = non_admin_client.post("/api/v1/notifications/triggers", json=payload)
    assert response.status_code == status.HTTP_403_FORBIDDEN


def test_list_trigger_rules(admin_client):
    """Test listing all custom trigger rules [FR-6, FR-9]."""
    payload = {
        "rule_name": "Test Trigger 1",
        "financial_criteria": "past_due_balance_exceeded",
        "threshold_amount": 2500.0,
        "urgency": "High",
    }
    admin_client.post("/api/v1/notifications/triggers", json=payload)

    response = admin_client.get("/api/v1/notifications/triggers")
    assert response.status_code == status.HTTP_200_OK
    triggers = response.json()
    assert len(triggers) >= 1
    assert any(t["rule_name"] == "Test Trigger 1" for t in triggers)


def test_delete_trigger_rule_success(admin_client):
    """Test deleting an existing trigger rule returns 204 No Content with empty body [FR-6, FR-9]."""
    create_res = admin_client.post(
        "/api/v1/notifications/triggers",
        json={
            "rule_name": "Trigger To Delete",
            "financial_criteria": "past_due_balance_exceeded",
            "threshold_amount": 100.0,
            "urgency": "Low",
        },
    )
    trigger_id = create_res.json()["trigger_id"]

    delete_res = admin_client.delete(f"/api/v1/notifications/triggers/{trigger_id}")
    assert delete_res.status_code == status.HTTP_204_NO_CONTENT
    assert delete_res.text == ""

    # Verify rule no longer exists
    list_res = admin_client.get("/api/v1/notifications/triggers")
    assert all(t["trigger_id"] != trigger_id for t in list_res.json())


def test_delete_trigger_rule_not_found(admin_client):
    """Test deleting non-existent trigger rule returns 404 Not Found [FR-6]."""
    fake_id = str(uuid.uuid4())
    response = admin_client.delete(f"/api/v1/notifications/triggers/{fake_id}")
    assert response.status_code == status.HTTP_404_NOT_FOUND


# Tests for User Notification Preferences & Optimistic Locking [FR-7, FR-9, FR-10]


def test_update_notification_preference_creation_and_optimistic_locking(admin_client):
    """Test preference creation and optimistic locking version conflicts [FR-7, FR-9, FR-10]."""
    payload = {
        "role_name": "financial_admin",
        "event_type": "invoice_overdue",
        "urgency": "High",
        "channels_json": ["InApp", "Email", "SMS"],
        "version": 1,
    }

    # First update creates record at version 1
    res1 = admin_client.put("/api/v1/notifications/preferences", json=payload)
    assert res1.status_code == status.HTTP_200_OK
    data1 = res1.json()
    assert data1["role_name"] == "financial_admin"
    assert data1["channels_json"] == ["InApp", "Email", "SMS"]
    assert data1["version"] == 1

    # Second update passing version 1 updates to version 2
    payload_v1 = {
        "role_name": "financial_admin",
        "event_type": "invoice_overdue",
        "urgency": "High",
        "channels_json": ["InApp", "Email"],
        "version": 1,
    }
    res2 = admin_client.put("/api/v1/notifications/preferences", json=payload_v1)
    assert res2.status_code == status.HTTP_200_OK
    data2 = res2.json()
    assert data2["channels_json"] == ["InApp", "Email"]
    assert data2["version"] == 2

    # Third update passing stale version 1 returns 409 Conflict
    res3 = admin_client.put("/api/v1/notifications/preferences", json=payload_v1)
    assert res3.status_code == status.HTTP_409_CONFLICT
    assert "Version conflict" in res3.json()["detail"]


def test_list_notification_preferences(admin_client):
    """Test querying list of notification preferences [FR-7, FR-9]."""
    payload = {
        "role_name": "financial_admin",
        "event_type": "quote_expired",
        "urgency": "Medium",
        "channels_json": ["InApp", "Email"],
        "version": 1,
    }
    admin_client.put("/api/v1/notifications/preferences", json=payload)

    response = admin_client.get("/api/v1/notifications/preferences?role_name=financial_admin")
    assert response.status_code == status.HTTP_200_OK
    prefs = response.json()
    assert len(prefs) >= 1
    assert any(p["event_type"] == "quote_expired" for p in prefs)


# Tests for In-App Notifications [FR-5, FR-9, FR-10]


def test_list_in_app_notifications(admin_client, admin_user):
    """Test retrieving in-app notifications and unread_only filtering [FR-5, FR-9, FR-10]."""
    db = TestingSessionLocal()
    now = datetime.now(timezone.utc)

    log1 = NotificationLog(
        recipient_id=admin_user.user_id,
        urgency="High",
        channel="InApp",
        delivery_status="Sent",
        dispatched_at=now,
        correlation_id=str(uuid.uuid4()),
    )
    log2 = NotificationLog(
        recipient_id=admin_user.user_id,
        urgency="Low",
        channel="InApp",
        delivery_status="Read",
        dispatched_at=now - timedelta(minutes=10),
        correlation_id=str(uuid.uuid4()),
    )
    db.add_all([log1, log2])
    db.commit()
    db.close()

    # Query all in-app notifications
    res_all = admin_client.get("/api/v1/notifications/in-app")
    assert res_all.status_code == status.HTTP_200_OK
    logs_all = res_all.json()
    assert len(logs_all) >= 2

    # Query unread_only notifications
    res_unread = admin_client.get("/api/v1/notifications/in-app?unread_only=true")
    assert res_unread.status_code == status.HTTP_200_OK
    logs_unread = res_unread.json()
    assert len(logs_unread) == 1
    assert logs_unread[0]["delivery_status"] == "Sent"
