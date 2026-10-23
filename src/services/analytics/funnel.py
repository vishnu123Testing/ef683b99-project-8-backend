"""Conversion Funnel Analytics Domain Service.

Provides business logic for conversion funnel stage progression analysis,
drop-off rate calculations, and automated bottleneck detection [FR-004, FR-005].
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Union
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.lib.config import get_settings
from src.models.analytics import FunnelThreshold, LeadEvent

logger = logging.getLogger(__name__)


class FunnelServiceError(Exception):
    """Base exception class for conversion funnel service errors."""

    pass


class InvalidFunnelDateRangeError(FunnelServiceError):
    """Raised when start_date_val is after end_date_val."""

    pass


class StageNotFoundError(FunnelServiceError):
    """Raised when a specified funnel stage is not found or invalid."""

    pass


DEFAULT_FUNNEL_STAGES: List[str] = ["LEAD", "MQL", "SQL", "OPPORTUNITY", "CLOSED_WON"]

CANONICAL_STAGE_ORDER: Dict[str, int] = {
    "LEAD": 1,
    "MQL": 2,
    "SQL": 3,
    "OPPORTUNITY": 4,
    "CLOSED_WON": 5,
    "CLOSED WON": 5,
    "WON": 5,
}


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime object is timezone-aware and set to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _parse_uuid(val: Optional[Union[uuid.UUID, str]]) -> Optional[uuid.UUID]:
    """Parse a string or UUID into a UUID object."""
    if val is None:
        return None
    if isinstance(val, uuid.UUID):
        return val
    try:
        return uuid.UUID(str(val))
    except (ValueError, TypeError) as exc:
        raise FunnelServiceError(f"Invalid UUID value: {val}") from exc


def get_funnel_thresholds(db: Session) -> Dict[str, float]:
    """Fetch all configured funnel stage drop-off thresholds.

    Args:
        db: SQLAlchemy DB session.

    Returns:
        Dictionary mapping stage_name -> max_dropoff_percent.
    """
    records = db.query(FunnelThreshold).all()
    return {rec.stage_name: float(rec.max_dropoff_percent) for rec in records}


def set_funnel_threshold(
    db: Session,
    stage_name: str,
    max_dropoff_percent: float,
) -> FunnelThreshold:
    """Create or update a bottleneck detection drop-off threshold for a stage.

    Args:
        db: SQLAlchemy DB session.
        stage_name: Target funnel stage name.
        max_dropoff_percent: Maximum allowed drop-off percentage before flagging a bottleneck.

    Returns:
        Created or updated FunnelThreshold model instance.
    """
    if not stage_name or not stage_name.strip():
        raise FunnelServiceError("stage_name cannot be empty")

    normalized_stage = stage_name.strip()
    existing = (
        db.query(FunnelThreshold)
        .filter(func.upper(FunnelThreshold.stage_name) == normalized_stage.upper())
        .first()
    )

    now = datetime.now(timezone.utc)
    if existing:
        existing.max_dropoff_percent = float(max_dropoff_percent)
        existing.updated_on = now
        record = existing
    else:
        record = FunnelThreshold(
            stage_name=normalized_stage,
            max_dropoff_percent=float(max_dropoff_percent),
            updated_on=now,
        )
        db.add(record)

    db.commit()
    db.refresh(record)
    return record


def delete_funnel_threshold(db: Session, stage_name: str) -> bool:
    """Delete a configured funnel threshold for a stage.

    Args:
        db: SQLAlchemy DB session.
        stage_name: Target funnel stage name.

    Returns:
        True if deleted, False if record did not exist.
    """
    if not stage_name or not stage_name.strip():
        return False

    record = (
        db.query(FunnelThreshold)
        .filter(func.upper(FunnelThreshold.stage_name) == stage_name.strip().upper())
        .first()
    )

    if record:
        db.delete(record)
        db.commit()
        return True
    return False


def get_funnel_stage_names(
    db: Session,
    start_date_val: Optional[datetime] = None,
    end_date_val: Optional[datetime] = None,
    channel_id: Optional[Union[uuid.UUID, str]] = None,
    campaign_id: Optional[Union[uuid.UUID, str]] = None,
    territory_id: Optional[Union[uuid.UUID, str]] = None,
) -> List[str]:
    """Retrieve distinct funnel stage names present in lead events.

    Args:
        db: SQLAlchemy DB session.
        start_date_val: Optional start timestamp filter.
        end_date_val: Optional end timestamp filter.
        channel_id: Optional acquisition channel ID filter.
        campaign_id: Optional campaign ID filter.
        territory_id: Optional territory ID filter.

    Returns:
        Ordered list of stage names.
    """
    start_dt = _ensure_utc(start_date_val)
    end_dt = _ensure_utc(end_date_val)

    query = db.query(LeadEvent.stage_name).distinct()

    if start_dt:
        query = query.filter(LeadEvent.event_timestamp >= start_dt)
    if end_dt:
        query = query.filter(LeadEvent.event_timestamp <= end_dt)

    c_id = _parse_uuid(channel_id)
    if c_id:
        query = query.filter(LeadEvent.channel_id == c_id)

    cmp_id = _parse_uuid(campaign_id)
    if cmp_id:
        query = query.filter(LeadEvent.campaign_id == cmp_id)

    t_id = _parse_uuid(territory_id)
    if t_id:
        query = query.filter(LeadEvent.territory_id == t_id)

    rows = query.all()
    distinct_stages = [r[0] for r in rows if r[0]]

    if not distinct_stages:
        return list(DEFAULT_FUNNEL_STAGES)

    sorted_stages = sorted(
        distinct_stages,
        key=lambda s: CANONICAL_STAGE_ORDER.get(s.upper(), 99),
    )
    return sorted_stages


def analyze_conversion_funnel(
    db: Session,
    funnel_id: Optional[str] = None,
    start_date_val: Optional[datetime] = None,
    end_date_val: Optional[datetime] = None,
    channel_id: Optional[Union[uuid.UUID, str]] = None,
    campaign_id: Optional[Union[uuid.UUID, str]] = None,
    territory_id: Optional[Union[uuid.UUID, str]] = None,
    stage_order: Optional[List[str]] = None,
    default_threshold_percent: Optional[float] = None,
) -> List[Dict[str, Any]]:
    """Analyze conversion funnel stage progression and detect drop-off rate bottlenecks [FR-004, FR-005].

    Args:
        db: SQLAlchemy DB session.
        funnel_id: Optional funnel identifier.
        start_date_val: Start date for lead event filtering.
        end_date_val: End date for lead event filtering.
        channel_id: Optional channel ID filter.
        campaign_id: Optional campaign ID filter.
        territory_id: Optional territory ID filter.
        stage_order: Custom ordered list of stage names. If None, stage names are auto-discovered or defaulted.
        default_threshold_percent: Optional threshold override for bottleneck detection.

    Returns:
        List of dictionaries representing each stage in the funnel:
        [
            {
                "stage_name": str,
                "stage_count": int,
                "conversion_rate": float,
                "dropoff_rate": float,
                "is_bottleneck": bool,
                "threshold_percent": float,
                "overall_conversion_rate": float,
            },
            ...
        ]
    """
    start_dt = _ensure_utc(start_date_val)
    end_dt = _ensure_utc(end_date_val)

    if start_dt and end_dt and start_dt > end_dt:
        raise InvalidFunnelDateRangeError("start_date_val cannot be after end_date_val")

    # Load configured stage thresholds
    threshold_map = get_funnel_thresholds(db)

    # Fallback threshold from settings if not explicitly passed
    settings = get_settings()
    fallback_threshold = (
        default_threshold_percent
        if default_threshold_percent is not None
        else settings.funnel_bottleneck_threshold
    )

    # Determine stages to analyze
    if stage_order and len(stage_order) > 0:
        stages = [s for s in stage_order if s]
    else:
        stages = get_funnel_stage_names(
            db=db,
            start_date_val=start_dt,
            end_date_val=end_dt,
            channel_id=channel_id,
            campaign_id=campaign_id,
            territory_id=territory_id,
        )

    # Build base filter query
    c_id = _parse_uuid(channel_id)
    cmp_id = _parse_uuid(campaign_id)
    t_id = _parse_uuid(territory_id)

    query = db.query(
        LeadEvent.stage_name,
        func.count(func.distinct(LeadEvent.lead_id)).label("stage_count"),
    )

    if start_dt:
        query = query.filter(LeadEvent.event_timestamp >= start_dt)
    if end_dt:
        query = query.filter(LeadEvent.event_timestamp <= end_dt)
    if c_id:
        query = query.filter(LeadEvent.channel_id == c_id)
    if cmp_id:
        query = query.filter(LeadEvent.campaign_id == cmp_id)
    if t_id:
        query = query.filter(LeadEvent.territory_id == t_id)

    query = query.group_by(LeadEvent.stage_name)
    grouped_counts = query.all()

    # Create mapping of stage_name -> count with case-insensitive fallback
    counts_by_stage: Dict[str, int] = {}
    for row in grouped_counts:
        stage_key = row.stage_name
        counts_by_stage[stage_key] = row.stage_count
        counts_by_stage[stage_key.upper()] = row.stage_count

    top_count: Optional[int] = None
    prev_count: Optional[int] = None
    funnel_results: List[Dict[str, Any]] = []

    for idx, stage in enumerate(stages):
        stage_count = counts_by_stage.get(stage, counts_by_stage.get(stage.upper(), 0))

        if idx == 0:
            top_count = stage_count
            conversion_rate = 100.0 if stage_count > 0 else 0.0
            dropoff_rate = 0.0
            is_bottleneck = False
            overall_conversion_rate = 100.0 if stage_count > 0 else 0.0
            stage_threshold = fallback_threshold
        else:
            if prev_count is not None and prev_count > 0:
                conversion_rate = round((stage_count / prev_count) * 100.0, 2)
                raw_dropoff = ((prev_count - stage_count) / prev_count) * 100.0
                dropoff_rate = round(max(0.0, raw_dropoff), 2)
            else:
                conversion_rate = 0.0
                dropoff_rate = 0.0

            if top_count is not None and top_count > 0:
                overall_conversion_rate = round((stage_count / top_count) * 100.0, 2)
            else:
                overall_conversion_rate = 0.0

            stage_threshold = threshold_map.get(
                stage,
                threshold_map.get(stage.upper(), fallback_threshold),
            )

            if prev_count is not None and prev_count > 0 and dropoff_rate > stage_threshold:
                is_bottleneck = True
            else:
                is_bottleneck = False

        prev_count = stage_count

        funnel_results.append(
            {
                "stage_name": stage,
                "stage_count": stage_count,
                "conversion_rate": conversion_rate,
                "dropoff_rate": dropoff_rate,
                "is_bottleneck": is_bottleneck,
                "threshold_percent": float(stage_threshold),
                "overall_conversion_rate": overall_conversion_rate,
            }
        )

    return funnel_results
