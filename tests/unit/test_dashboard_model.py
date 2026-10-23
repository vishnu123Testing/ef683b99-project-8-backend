"""Unit tests for MetricSnapshot ORM model in backend/src/models/dashboard.py."""

from datetime import datetime, timezone

import pytest
from sqlalchemy import Index, UniqueConstraint, create_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from src.models.database import Base
from src.models.dashboard import (
    STATUS_UNAVAILABLE,
    STATUS_VALID,
    STATUS_ZERO,
    MetricSnapshot,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory database session for testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_metric_snapshot_model_defaults():
    """Test MetricSnapshot model initialization with default values and representations."""
    now = datetime.now(timezone.utc)
    snapshot = MetricSnapshot(
        domain_name="financials",
        metric_key="recurring_revenue",
        current_value=12500.0,
        baseline_value=10000.0,
        status_code=STATUS_VALID,
        error_detail=None,
        recorded_at=now,
    )

    assert snapshot.domain_name == "financials"
    assert snapshot.metric_key == "recurring_revenue"
    assert snapshot.current_value == 12500.0
    assert snapshot.baseline_value == 10000.0
    assert snapshot.status_code == STATUS_VALID
    assert snapshot.error_detail is None
    assert snapshot.recorded_at == now

    # Test representation and dictionary conversion
    repr_str = repr(snapshot)
    assert "financials" in repr_str
    assert "recurring_revenue" in repr_str
    assert STATUS_VALID in repr_str

    d = snapshot.to_dict()
    assert d["domain_name"] == "financials"
    assert d["metric_key"] == "recurring_revenue"
    assert d["current_value"] == 12500.0
    assert d["baseline_value"] == 10000.0
    assert d["status_code"] == STATUS_VALID
    assert d["recorded_at"] == now.isoformat()


def test_metric_snapshot_db_persistence(db_session: Session):
    """Test inserting and querying a MetricSnapshot in database session."""
    snapshot = MetricSnapshot(
        domain_name="lead_gen",
        metric_key="conversion_rate",
        current_value=3.5,
        baseline_value=3.0,
        status_code=STATUS_VALID,
        error_detail=None,
    )
    db_session.add(snapshot)
    db_session.commit()
    db_session.refresh(snapshot)

    assert snapshot.id is not None
    assert snapshot.id > 0
    assert isinstance(snapshot.recorded_at, datetime)

    # Query back from DB
    queried = (
        db_session.query(MetricSnapshot)
        .filter_by(domain_name="lead_gen", metric_key="conversion_rate")
        .first()
    )
    assert queried is not None
    assert queried.id == snapshot.id
    assert queried.current_value == 3.5
    assert queried.baseline_value == 3.0
    assert queried.status_code == STATUS_VALID


def test_metric_snapshot_nullable_fields(db_session: Session):
    """Test persisting MetricSnapshot with null current_value and baseline_value on error."""
    snapshot = MetricSnapshot(
        domain_name="support",
        metric_key="open_tickets",
        current_value=None,
        baseline_value=None,
        status_code=STATUS_UNAVAILABLE,
        error_detail="Timeout connecting to support service",
    )
    db_session.add(snapshot)
    db_session.commit()
    db_session.refresh(snapshot)

    assert snapshot.id is not None
    assert snapshot.current_value is None
    assert snapshot.baseline_value is None
    assert snapshot.status_code == STATUS_UNAVAILABLE
    assert snapshot.error_detail == "Timeout connecting to support service"


def test_metric_snapshot_zero_value(db_session: Session):
    """Test persisting MetricSnapshot with zero current_value."""
    snapshot = MetricSnapshot(
        domain_name="support",
        metric_key="unassigned_tickets",
        current_value=0.0,
        baseline_value=5.0,
        status_code=STATUS_ZERO,
        error_detail=None,
    )
    db_session.add(snapshot)
    db_session.commit()
    db_session.refresh(snapshot)

    assert snapshot.id is not None
    assert snapshot.current_value == 0.0
    assert snapshot.status_code == STATUS_ZERO


def test_metric_snapshot_composite_unique_constraint(db_session: Session):
    """Test that duplicate (domain_name, metric_key, recorded_at) raises IntegrityError."""
    timestamp = datetime(2026, 10, 6, 8, 0, 0, tzinfo=timezone.utc)
    snapshot1 = MetricSnapshot(
        domain_name="sales_pipeline",
        metric_key="deal_count",
        current_value=10.0,
        baseline_value=8.0,
        status_code=STATUS_VALID,
        recorded_at=timestamp,
    )
    snapshot2 = MetricSnapshot(
        domain_name="sales_pipeline",
        metric_key="deal_count",
        current_value=12.0,
        baseline_value=8.0,
        status_code=STATUS_VALID,
        recorded_at=timestamp,
    )

    db_session.add(snapshot1)
    db_session.commit()

    db_session.add(snapshot2)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_metric_snapshot_table_constraints_metadata():
    """Verify table metadata unique constraints and index configurations."""
    table = MetricSnapshot.__table__

    # Check table name
    assert table.name == "metric_snapshots"

    # Check unique constraint names or columns
    unique_constraints = [c for c in table.constraints if isinstance(c, UniqueConstraint)]
    assert len(unique_constraints) >= 1
    uc = unique_constraints[0]
    uc_col_names = [col.name for col in uc.columns]
    assert uc_col_names == ["domain_name", "metric_key", "recorded_at"]

    # Check composite index
    indexes = list(table.indexes)
    composite_index = next(
        (idx for idx in indexes if idx.name == "ix_metric_snapshots_domain_recorded"), None
    )
    assert composite_index is not None
    idx_col_names = [col.name for col in composite_index.columns]
    assert idx_col_names == ["domain_name", "recorded_at"]


def test_create_and_get_latest_metric_snapshot_helpers(db_session: Session):
    """Test create_metric_snapshot and get_latest_metric_snapshot helper functions."""
    from src.models.dashboard import create_metric_snapshot, get_latest_metric_snapshot

    # Initially none exists
    latest = get_latest_metric_snapshot(db_session, "financials", "recurring_revenue")
    assert latest is None

    # Create older snapshot
    t_old = datetime(2026, 10, 1, 10, 0, 0, tzinfo=timezone.utc)
    create_metric_snapshot(
        db=db_session,
        domain_name="financials",
        metric_key="recurring_revenue",
        current_value=10000.0,
        baseline_value=9000.0,
        recorded_at=t_old,
    )

    # Create newer snapshot
    t_new = datetime(2026, 10, 6, 10, 0, 0, tzinfo=timezone.utc)
    snapshot_new = create_metric_snapshot(
        db=db_session,
        domain_name="financials",
        metric_key="recurring_revenue",
        current_value=12500.0,
        baseline_value=10000.0,
        recorded_at=t_new,
    )

    latest = get_latest_metric_snapshot(db_session, "financials", "recurring_revenue")
    assert latest is not None
    assert latest.id == snapshot_new.id
    assert latest.current_value == 12500.0
    assert latest.baseline_value == 10000.0


def test_get_metric_snapshots_filtering(db_session: Session):
    """Test get_metric_snapshots filtering by domain and metric key."""
    from src.models.dashboard import create_metric_snapshot, get_metric_snapshots

    create_metric_snapshot(db_session, "lead_gen", "lead_volume", 100.0)
    create_metric_snapshot(db_session, "lead_gen", "conversion_rate", 3.0)
    create_metric_snapshot(db_session, "financials", "recurring_revenue", 5000.0)

    all_snaps = get_metric_snapshots(db_session)
    assert len(all_snaps) == 3

    lead_snaps = get_metric_snapshots(db_session, domain_name="lead_gen")
    assert len(lead_snaps) == 2

    mrr_snaps = get_metric_snapshots(db_session, domain_name="financials", metric_key="recurring_revenue")
    assert len(mrr_snaps) == 1
    assert mrr_snaps[0].current_value == 5000.0

