"""Unit tests for scheduled renewal worker and Celery task [FR-9]."""

from datetime import date as date_type, datetime, timedelta, timezone
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.connectors.notification_connector import NotificationConnector
from src.models.contract import Contract, RenewalAlert
from src.models.database import Base
from src.services.contract.models import ContractCategory, ContractStatus, RenewalThreshold
from src.workers.renewal_worker import (
    celery_app,
    process_renewal_alerts,
    run_scheduled_renewal_check,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for renewal worker tests."""
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(bind=engine)
    testing_session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = testing_session_local()
    try:
        yield session
    finally:
        session.close()


def test_process_renewal_alerts_happy_path(db_session):
    """Verify dispatching alerts for 90, 60, and 30 day expiration thresholds [FR-9]."""
    ref_date = date_type(2026, 6, 1)

    c90 = Contract(
        id=uuid.uuid4(),
        title="Agreement 90",
        category="Contract",
        status="ACTIVE",
        counterparty="Partner A",
        expires_on=ref_date + timedelta(days=90),
    )
    c60 = Contract(
        id=uuid.uuid4(),
        title="Agreement 60",
        category="Contract",
        status="ACTIVE",
        counterparty="Partner B",
        expires_on=ref_date + timedelta(days=60),
    )
    c30 = Contract(
        id=uuid.uuid4(),
        title="Agreement 30",
        category="Contract",
        status="ACTIVE",
        counterparty="Partner C",
        expires_on=ref_date + timedelta(days=30),
    )
    db_session.add_all([c90, c60, c30])
    db_session.commit()

    connector = NotificationConnector(db=db_session)
    result = process_renewal_alerts(
        db=db_session,
        reference_date=ref_date,
        notification_port=connector,
    )

    assert result["processed_contracts"] == 3
    assert result["dispatched_alerts"] >= 3
    assert result["errors"] == 0

    # Verify RenewalAlert ORM records were stored in database
    alerts_in_db = db_session.query(RenewalAlert).all()
    assert len(alerts_in_db) >= 3


def test_process_renewal_alerts_suppresses_terminated_or_renewed(db_session):
    """Verify that terminated, expired, or renewed contracts suppress alert dispatch [FR-9]."""
    ref_date = date_type(2026, 6, 1)

    c_term = Contract(
        id=uuid.uuid4(),
        title="Terminated Contract",
        category="Contract",
        status="TERMINATED",
        counterparty="Partner T",
        expires_on=ref_date + timedelta(days=30),
    )
    c_exp = Contract(
        id=uuid.uuid4(),
        title="Expired Contract",
        category="Contract",
        status="EXPIRED",
        counterparty="Partner E",
        expires_on=ref_date + timedelta(days=30),
    )
    c_ren = Contract(
        id=uuid.uuid4(),
        title="Renewed Contract",
        category="Contract",
        status="RENEWED",
        counterparty="Partner R",
        expires_on=ref_date + timedelta(days=30),
    )
    db_session.add_all([c_term, c_exp, c_ren])
    db_session.commit()

    connector = NotificationConnector(db=db_session)
    result = process_renewal_alerts(
        db=db_session,
        reference_date=ref_date,
        notification_port=connector,
    )

    assert result["processed_contracts"] == 3
    assert result["dispatched_alerts"] == 0
    assert result["suppressed_alerts"] > 0


def test_process_renewal_alerts_suppresses_duplicate_thresholds(db_session):
    """Verify that running the worker again for the same threshold suppresses duplicates."""
    ref_date = date_type(2026, 6, 1)

    c90 = Contract(
        id=uuid.uuid4(),
        title="Master Agreement",
        category="Contract",
        status="ACTIVE",
        counterparty="Partner Dup",
        expires_on=ref_date + timedelta(days=90),
    )
    db_session.add(c90)
    db_session.commit()

    connector = NotificationConnector(db=db_session)

    # First run
    res1 = process_renewal_alerts(
        db=db_session,
        reference_date=ref_date,
        notification_port=connector,
    )
    assert res1["dispatched_alerts"] == 1

    # Second run on same date
    res2 = process_renewal_alerts(
        db=db_session,
        reference_date=ref_date,
        notification_port=connector,
    )
    assert res2["dispatched_alerts"] == 0
    assert res2["suppressed_alerts"] >= 1


def test_run_scheduled_renewal_check_celery_task(db_session, monkeypatch):
    """Verify Celery task execution wrapper."""
    ref_date = date_type(2026, 6, 1)

    c90 = Contract(
        id=uuid.uuid4(),
        title="Celery Agreement",
        category="Contract",
        status="ACTIVE",
        counterparty="Partner Celery",
        expires_on=ref_date + timedelta(days=90),
    )
    db_session.add(c90)
    db_session.commit()

    # Monkeypatch SessionLocal to return db_session
    # Wrap db_session so close() doesn't actually close the fixture session during the test
    class SessionWrapper:
        def __init__(self, sess):
            self.sess = sess
        def __getattr__(self, name):
            if name == "close":
                return lambda: None
            return getattr(self.sess, name)

    monkeypatch.setattr("src.workers.renewal_worker.SessionLocal", lambda: SessionWrapper(db_session))

    res = run_scheduled_renewal_check("2026-06-01")
    assert res["processed_contracts"] >= 1
    assert res["reference_date"] == "2026-06-01"
