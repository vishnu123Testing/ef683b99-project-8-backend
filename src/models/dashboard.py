"""MetricSnapshot ORM model and data access queries for storing executive KPI metric baselines and snapshots."""

from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy import DateTime, Float, Index, Integer, String, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column, Session

from src.models.database import Base

STATUS_VALID = "VALID"
STATUS_ZERO = "ZERO"
STATUS_UNAVAILABLE = "UNAVAILABLE"


class MetricSnapshot(Base):
    """MetricSnapshot ORM model representing historical and baseline KPI metric records."""

    __tablename__ = "metric_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "domain_name",
            "metric_key",
            "recorded_at",
            name="uq_metric_snapshots_domain_key_recorded",
        ),
        Index(
            "ix_metric_snapshots_domain_recorded",
            "domain_name",
            "recorded_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        autoincrement=True,
    )
    domain_name: Mapped[str] = mapped_column(
        String(50),
        index=True,
        nullable=False,
    )
    metric_key: Mapped[str] = mapped_column(
        String(100),
        index=True,
        nullable=False,
    )
    current_value: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    baseline_value: Mapped[float | None] = mapped_column(
        Float,
        nullable=True,
    )
    status_code: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    error_detail: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    def to_dict(self) -> dict:
        """Convert MetricSnapshot model instance into a dictionary representation."""
        return {
            "id": self.id,
            "domain_name": self.domain_name,
            "metric_key": self.metric_key,
            "current_value": self.current_value,
            "baseline_value": self.baseline_value,
            "status_code": self.status_code,
            "error_detail": self.error_detail,
            "recorded_at": self.recorded_at.isoformat() if self.recorded_at else None,
        }

    def __repr__(self) -> str:
        return (
            f"<MetricSnapshot(id={self.id}, "
            f"domain_name={self.domain_name!r}, "
            f"metric_key={self.metric_key!r}, "
            f"status_code={self.status_code!r}, "
            f"recorded_at={self.recorded_at})>"
        )


def get_latest_metric_snapshot(
    db: Session,
    domain_name: str,
    metric_key: str,
) -> Optional[MetricSnapshot]:
    """Retrieve the most recent MetricSnapshot for a domain and metric key.

    Args:
        db: Synchronous SQLAlchemy database session.
        domain_name: Operational domain identifier string.
        metric_key: Unique metric key identifier.

    Returns:
        Optional[MetricSnapshot]: Most recent snapshot record if found, else None.
    """
    stmt = (
        select(MetricSnapshot)
        .where(
            MetricSnapshot.domain_name == domain_name,
            MetricSnapshot.metric_key == metric_key,
        )
        .order_by(MetricSnapshot.recorded_at.desc())
    )
    return db.scalars(stmt).first()


def create_metric_snapshot(
    db: Session,
    domain_name: str,
    metric_key: str,
    current_value: Optional[float] = None,
    baseline_value: Optional[float] = None,
    status_code: str = STATUS_VALID,
    error_detail: Optional[str] = None,
    recorded_at: Optional[datetime] = None,
) -> MetricSnapshot:
    """Create and persist a new MetricSnapshot record in the database.

    Args:
        db: Synchronous SQLAlchemy database session.
        domain_name: Operational domain identifier string.
        metric_key: Unique metric key identifier.
        current_value: Current numerical value.
        baseline_value: Baseline historical numerical value.
        status_code: Status classification code (VALID, ZERO, UNAVAILABLE).
        error_detail: Optional exception/error detail message.
        recorded_at: Optional explicit timestamp override.

    Returns:
        MetricSnapshot: The persisted model instance.
    """
    snapshot = MetricSnapshot(
        domain_name=domain_name,
        metric_key=metric_key,
        current_value=current_value,
        baseline_value=baseline_value,
        status_code=status_code,
        error_detail=error_detail,
    )
    if recorded_at is not None:
        snapshot.recorded_at = recorded_at

    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def get_metric_snapshots(
    db: Session,
    domain_name: Optional[str] = None,
    metric_key: Optional[str] = None,
    limit: int = 100,
) -> List[MetricSnapshot]:
    """Retrieve a list of MetricSnapshot records with optional filtering.

    Args:
        db: Synchronous SQLAlchemy database session.
        domain_name: Optional domain identifier filter.
        metric_key: Optional metric key filter.
        limit: Maximum number of records to return (default 100).

    Returns:
        List[MetricSnapshot]: List of matching snapshot records ordered by recorded_at desc.
    """
    stmt = select(MetricSnapshot)
    if domain_name is not None:
        stmt = stmt.where(MetricSnapshot.domain_name == domain_name)
    if metric_key is not None:
        stmt = stmt.where(MetricSnapshot.metric_key == metric_key)
    stmt = stmt.order_by(MetricSnapshot.recorded_at.desc()).limit(limit)
    return list(db.scalars(stmt).all())

