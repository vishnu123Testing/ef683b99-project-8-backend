"""Integration tests for financial transparency API endpoints in backend/src/api/financial.py [FR-1, FR-2, FR-3, FR-4, FR-9, FR-10]."""

from datetime import datetime, timedelta, timezone
from decimal import Decimal
import uuid

from fastapi import status
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.api.deps import get_current_user, get_db
from src.api.main import app
from src.models.database import Base, get_db
from src.models.financial import Invoice, Payment, Quote
from src.models.role import Role, UserRole
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
def financial_user():
    """Fixture creating a test user with financial:read role assigned."""
    db = TestingSessionLocal()
    tenant = Tenant(name="Financial Test Tenant")
    db.add(tenant)
    db.commit()

    user = User(
        email="financial.reader@example.com",
        password_hash=hash_password("Pass123!"),
        tenant_id=tenant.tenant_id,
    )
    db.add(user)
    db.commit()

    role = Role(name="financial:read", description="Financial Read Permission")
    db.add(role)
    db.commit()

    assign_role_to_user(db, user.user_id, "financial:read", tenant.tenant_id)
    db.close()
    return user


@pytest.fixture
def non_financial_user():
    """Fixture creating a test user without financial:read role."""
    db = TestingSessionLocal()
    tenant = Tenant(name="Standard Tenant")
    db.add(tenant)
    db.commit()

    user = User(
        email="standard.user@example.com",
        password_hash=hash_password("Pass123!"),
        tenant_id=tenant.tenant_id,
    )
    db.add(user)
    db.commit()

    role = Role(name="ClientViewer", description="Standard Client Viewer")
    db.add(role)
    db.commit()

    assign_role_to_user(db, user.user_id, "ClientViewer", tenant.tenant_id)
    db.close()
    return user


@pytest.fixture
def auth_client(financial_user):
    """TestClient configured with authenticated user possessing financial:read role."""
    app.dependency_overrides[get_current_user] = lambda: financial_user
    client = TestClient(app)
    return client


@pytest.fixture
def forbidden_client(non_financial_user):
    """TestClient configured with user lacking financial:read role."""
    app.dependency_overrides[get_current_user] = lambda: non_financial_user
    client = TestClient(app)
    return client


@pytest.fixture
def unauth_client():
    """TestClient without authentication dependency overrides."""
    if get_current_user in app.dependency_overrides:
        del app.dependency_overrides[get_current_user]
    return TestClient(app)


def test_unauthenticated_requests_return_401(unauth_client):
    """Verify endpoints reject unauthenticated callers with HTTP 401 Unauthorized [FR-9]."""
    routes = [
        "/api/v1/financial/quotes",
        "/api/v1/financial/invoices",
        "/api/v1/financial/payments",
        "/api/v1/financial/ar-aging",
    ]
    for route in routes:
        res = unauth_client.get(route)
        assert res.status_code == status.HTTP_401_UNAUTHORIZED, f"Failed for route {route}"


def test_unauthorized_user_returns_403(forbidden_client):
    """Verify endpoints reject users missing 'financial:read' role with HTTP 403 Forbidden [FR-9]."""
    routes = [
        "/api/v1/financial/quotes",
        "/api/v1/financial/invoices",
        "/api/v1/financial/payments",
        "/api/v1/financial/ar-aging",
    ]
    for route in routes:
        res = forbidden_client.get(route)
        assert res.status_code == status.HTTP_403_FORBIDDEN, f"Failed for route {route}"


def test_list_quotes_and_filters(auth_client):
    """Verify GET /api/v1/financial/quotes retrieves quotes with status and customer filtering [FR-1, FR-10]."""
    db = TestingSessionLocal()
    cust_a = uuid.uuid4()
    cust_b = uuid.uuid4()
    now = datetime.now(timezone.utc)

    q1 = Quote(
        customer_id=cust_a,
        approval_state="Draft",
        expiration_date_time=now + timedelta(days=7),
        total_amount=Decimal("1500.00"),
    )
    q2 = Quote(
        customer_id=cust_a,
        approval_state="Approved",
        expiration_date_time=now + timedelta(days=14),
        total_amount=Decimal("3000.00"),
    )
    q3 = Quote(
        customer_id=cust_b,
        approval_state="Expired",
        expiration_date_time=now - timedelta(days=1),
        total_amount=Decimal("500.00"),
    )
    db.add_all([q1, q2, q3])
    db.commit()

    # List all quotes
    res = auth_client.get("/api/v1/financial/quotes")
    assert res.status_code == status.HTTP_200_OK
    items = res.json()
    assert len(items) == 3

    # Filter by approval_state
    res_approved = auth_client.get("/api/v1/financial/quotes?approval_state=Approved")
    assert res_approved.status_code == status.HTTP_200_OK
    approved_items = res_approved.json()
    assert len(approved_items) == 1
    assert approved_items[0]["quote_id"] == str(q2.quote_id)

    # Filter by customer_id
    res_cust_a = auth_client.get(f"/api/v1/financial/quotes?customer_id={cust_a}")
    assert res_cust_a.status_code == status.HTTP_200_OK
    cust_a_items = res_cust_a.json()
    assert len(cust_a_items) == 2

    db.close()


def test_list_invoices_and_filters(auth_client):
    """Verify GET /api/v1/financial/invoices retrieves invoices with status filtering [FR-2, FR-10]."""
    db = TestingSessionLocal()
    cust_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    inv1 = Invoice(
        customer_id=cust_id,
        status="Current",
        issue_date_time=now,
        due_date_time=now + timedelta(days=30),
        total_amount=Decimal("2000.00"),
        outstanding_amount=Decimal("2000.00"),
    )
    inv2 = Invoice(
        customer_id=cust_id,
        status="Paid",
        issue_date_time=now - timedelta(days=40),
        due_date_time=now - timedelta(days=10),
        total_amount=Decimal("1000.00"),
        outstanding_amount=Decimal("0.00"),
    )
    db.add_all([inv1, inv2])
    db.commit()

    # Get all invoices
    res = auth_client.get("/api/v1/financial/invoices")
    assert res.status_code == status.HTTP_200_OK
    assert len(res.json()) == 2

    # Filter by status = Paid
    res_paid = auth_client.get("/api/v1/financial/invoices?status=Paid")
    assert res_paid.status_code == status.HTTP_200_OK
    paid_items = res_paid.json()
    assert len(paid_items) == 1
    assert paid_items[0]["invoice_id"] == str(inv2.invoice_id)

    db.close()


def test_list_payments_and_filters(auth_client):
    """Verify GET /api/v1/financial/payments retrieves payments linked to invoices [FR-3, FR-10]."""
    db = TestingSessionLocal()
    cust_id = uuid.uuid4()
    now = datetime.now(timezone.utc)

    inv = Invoice(
        customer_id=cust_id,
        status="Current",
        issue_date_time=now - timedelta(days=10),
        due_date_time=now + timedelta(days=20),
        total_amount=Decimal("5000.00"),
        outstanding_amount=Decimal("3000.00"),
    )
    db.add(inv)
    db.commit()

    pay1 = Payment(
        invoice_id=inv.invoice_id,
        payment_date_time=now - timedelta(days=5),
        amount_paid=Decimal("2000.00"),
        payment_method="Credit_Card",
    )
    db.add(pay1)
    db.commit()

    # Filter payments by invoice_id
    res = auth_client.get(f"/api/v1/financial/payments?invoice_id={inv.invoice_id}")
    assert res.status_code == status.HTTP_200_OK
    payments = res.json()
    assert len(payments) == 1
    assert payments[0]["payment_id"] == str(pay1.payment_id)
    assert payments[0]["amount_paid"] == 2000.0

    db.close()


def test_get_ar_aging_metrics_success_and_empty_state(auth_client):
    """Verify GET /api/v1/financial/ar-aging calculates buckets and handles empty state [FR-4, FR-10]."""
    db = TestingSessionLocal()
    cust_a = uuid.uuid4()
    cust_b = uuid.uuid4()
    now = datetime.now(timezone.utc)

    # Invoices for cust_a across duration buckets
    inv_0_30 = Invoice(
        customer_id=cust_a,
        status="Past_Due",
        issue_date_time=now - timedelta(days=35),
        due_date_time=now - timedelta(days=15),  # 15 days overdue -> bucket_0_30
        total_amount=Decimal("1000.00"),
        outstanding_amount=Decimal("1000.00"),
    )
    inv_31_60 = Invoice(
        customer_id=cust_a,
        status="Past_Due",
        issue_date_time=now - timedelta(days=60),
        due_date_time=now - timedelta(days=45),  # 45 days overdue -> bucket_31_60
        total_amount=Decimal("2500.00"),
        outstanding_amount=Decimal("2500.00"),
    )
    inv_90_plus = Invoice(
        customer_id=cust_a,
        status="Past_Due",
        issue_date_time=now - timedelta(days=120),
        due_date_time=now - timedelta(days=100),  # 100 days overdue -> bucket_90_plus
        total_amount=Decimal("4000.00"),
        outstanding_amount=Decimal("4000.00"),
    )
    db.add_all([inv_0_30, inv_31_60, inv_90_plus])
    db.commit()

    # Call AR aging for cust_a
    res = auth_client.get(f"/api/v1/financial/ar-aging?customer_id={cust_a}")
    assert res.status_code == status.HTTP_200_OK
    data = res.json()

    assert data["bucket_0_30"]["total_outstanding"] == 1000.0
    assert data["bucket_0_30"]["invoice_count"] == 1
    assert data["bucket_31_60"]["total_outstanding"] == 2500.0
    assert data["bucket_31_60"]["invoice_count"] == 1
    assert data["bucket_61_90"]["total_outstanding"] == 0.0
    assert data["bucket_61_90"]["invoice_count"] == 0
    assert data["bucket_90_plus"]["total_outstanding"] == 4000.0
    assert data["bucket_90_plus"]["invoice_count"] == 1

    # Call AR aging for cust_b (empty state)
    res_empty = auth_client.get(f"/api/v1/financial/ar-aging?customer_id={cust_b}")
    assert res_empty.status_code == status.HTTP_200_OK
    empty_data = res_empty.json()
    assert empty_data["bucket_0_30"]["total_outstanding"] == 0.0
    assert empty_data["bucket_0_30"]["invoice_count"] == 0
    assert empty_data["bucket_31_60"]["total_outstanding"] == 0.0
    assert empty_data["bucket_31_60"]["invoice_count"] == 0
    assert empty_data["bucket_61_90"]["total_outstanding"] == 0.0
    assert empty_data["bucket_61_90"]["invoice_count"] == 0
    assert empty_data["bucket_90_plus"]["total_outstanding"] == 0.0
    assert empty_data["bucket_90_plus"]["invoice_count"] == 0

    db.close()


def test_invalid_uuid_query_parameter_returns_400(auth_client):
    """Verify endpoints return HTTP 400 Bad Request when supplied invalid UUID filter string [FR-10]."""
    res_quotes = auth_client.get("/api/v1/financial/quotes?customer_id=not-a-valid-uuid")
    assert res_quotes.status_code == status.HTTP_400_BAD_REQUEST

    res_invoices = auth_client.get("/api/v1/financial/invoices?customer_id=not-a-valid-uuid")
    assert res_invoices.status_code == status.HTTP_400_BAD_REQUEST

    res_payments = auth_client.get("/api/v1/financial/payments?invoice_id=not-a-valid-uuid")
    assert res_payments.status_code == status.HTTP_400_BAD_REQUEST

    res_ar = auth_client.get("/api/v1/financial/ar-aging?customer_id=not-a-valid-uuid")
    assert res_ar.status_code == status.HTTP_400_BAD_REQUEST
