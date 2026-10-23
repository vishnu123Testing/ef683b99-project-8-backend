"""SQLAlchemy ORM models for AI forecast evaluation and recalculation logging.

Defines database entities for logging AI completion timeline forecasts, confidence
scores, trigger events, and correlation IDs.
"""

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Optional
import uuid

from sqlalchemy import DateTime, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from src.models.database import Base

if TYPE_CHECKING:
    from src.models.deal import Deal, DealForecast


class ForecastEvaluationLog(Base):
    """ORM entity representing an AI forecast evaluation or recalculation log entry.

    Records historical AI completion date estimates, percentage confidence scores,
    trigger events, and correlation IDs for audit and observability.
    """

    __tablename__ = "forecast_evaluation_logs"

    id: Mapped[str] = mapped_column(
        String(36),
        primary_key=True,
        default=lambda: str(uuid.uuid4()),
    )
    deal_id: Mapped[str] = mapped_column(
        String(36),
        ForeignKey("deals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    forecast_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("deal_forecasts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    estimated_completion_date: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    confidence_score_pct: Mapped[Optional[float]] = mapped_column(
        Float,
        nullable=True,
    )
    status_code: Mapped[str] = mapped_column(
        String(50),
        nullable=False,
        default="COMPUTING",
    )
    trigger_event: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
        default="RECALCULATION",
    )
    correlation_id: Mapped[Optional[str]] = mapped_column(
        String(64),
        nullable=True,
        index=True,
    )
    model_metadata: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    error_detail: Mapped[Optional[str]] = mapped_column(
        Text,
        nullable=True,
    )
    calculated_on: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        index=True,
    )

    # Relationships
    deal: Mapped["Deal"] = relationship(
        "Deal",
    )
    forecast: Mapped[Optional["DealForecast"]] = relationship(
        "DealForecast",
    )

    def to_dict(self) -> dict:
        """Convert ForecastEvaluationLog instance into a dictionary representation."""
        return {
            "id": self.id,
            "deal_id": self.deal_id,
            "forecast_id": self.forecast_id,
            "estimated_completion_date": (
                self.estimated_completion_date.isoformat()
                if self.estimated_completion_date
                else None
            ),
            "confidence_score_pct": self.confidence_score_pct,
            "status_code": self.status_code,
            "trigger_event": self.trigger_event,
            "correlation_id": self.correlation_id,
            "model_metadata": self.model_metadata,
            "error_detail": self.error_detail,
            "calculated_on": (
                self.calculated_on.isoformat() if self.calculated_on else None
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<ForecastEvaluationLog(id={self.id!r}, "
            f"deal_id={self.deal_id!r}, "
            f"status_code={self.status_code!r}, "
            f"confidence_score_pct={self.confidence_score_pct}, "
            f"trigger_event={self.trigger_event!r}, "
            f"correlation_id={self.correlation_id!r})>"
        )


# Type aliases for alternative naming conventions
ForecastRecalculationLog = ForecastEvaluationLog
ForecastLog = ForecastEvaluationLog
