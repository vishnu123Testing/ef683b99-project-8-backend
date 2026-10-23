"""Territory Analytics and Geographic Access Scoping Service.

Provides business logic for territory region filtering, postal code pattern matching,
unassigned region grouping, and multi-region user access scoping [FR-007, FR-008, NFR-002].
"""

from datetime import datetime, timezone
from fnmatch import fnmatch
import logging
from typing import Any, Dict, List, Optional, Set, Union
import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.models.analytics import LeadEvent
from src.models.marketing import Territory

logger = logging.getLogger(__name__)


class TerritoryServiceError(Exception):
    """Base exception for territory service errors."""

    pass


class TerritoryAccessDeniedError(TerritoryServiceError):
    """Raised when a user attempts to access an unauthorized territory."""

    pass


class InvalidFilterError(TerritoryServiceError):
    """Raised when query filters are invalid or malformed."""

    pass


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
        raise InvalidFilterError(f"Invalid UUID value: {val}") from exc


def match_postal_code_pattern(postal_code: str, pattern: str) -> bool:
    """Check if a postal code matches a pattern.

    Supports wildcards '*' and '?', exact matches, and comma/newline separated patterns.

    Args:
        postal_code: The postal code string to test.
        pattern: Pattern string (can contain comma-separated sub-patterns like "90*, 91*").

    Returns:
        True if postal_code matches any sub-pattern, False otherwise.
    """
    if not postal_code or not pattern:
        return False

    code_clean = postal_code.strip().upper()
    sub_patterns = [
        p.strip().upper() for p in pattern.replace("\n", ",").split(",") if p.strip()
    ]

    for pat in sub_patterns:
        if fnmatch(code_clean, pat):
            return True
    return False


def find_matching_territory(
    postal_code: Optional[str],
    territories: List[Territory],
) -> Optional[Territory]:
    """Find the first territory whose postal_code_patterns match the given postal code.

    Args:
        postal_code: The postal code to match.
        territories: List of Territory ORM objects to test.

    Returns:
        Matching Territory ORM object, or None if no match or postal_code is empty.
    """
    if not postal_code or not postal_code.strip():
        return None

    for territory in territories:
        if territory.postal_code_patterns and match_postal_code_pattern(
            postal_code, territory.postal_code_patterns
        ):
            return territory
    return None


def filter_territories_by_access(
    territories: List[Territory],
    user_territory_ids: Optional[List[Union[uuid.UUID, str]]],
) -> List[Territory]:
    """Filter a list of territories based on user access scopes.

    Args:
        territories: Full list of available Territory objects.
        user_territory_ids: List of allowed territory UUIDs for the user.
                            If None, user is unrestricted (e.g. admin/all).

    Returns:
        Subset of territories authorized for the user.
    """
    if user_territory_ids is None:
        return territories

    allowed_set: Set[uuid.UUID] = set()
    for tid in user_territory_ids:
        parsed = _parse_uuid(tid)
        if parsed is not None:
            allowed_set.add(parsed)

    return [t for t in territories if t.id in allowed_set]


def get_territory_heatmap_data(
    db: Session,
    lead_status: Optional[str] = None,
    min_deal_value: Optional[float] = None,
    max_deal_value: Optional[float] = None,
    territory_id: Optional[Union[uuid.UUID, str]] = None,
    user_territory_ids: Optional[List[Union[uuid.UUID, str]]] = None,
    start_date_val: Optional[datetime] = None,
    end_date_val: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Calculate geographic heat map density metrics across territories [FR-007, FR-008, NFR-002].

    Args:
        db: SQLAlchemy DB session.
        lead_status: Filter by lead event stage_name / status.
        min_deal_value: Filter events with deal_value >= min_deal_value.
        max_deal_value: Filter events with deal_value <= max_deal_value.
        territory_id: Specific territory ID requested by caller.
        user_territory_ids: List of territory IDs caller is authorized to view (NFR-002).
        start_date_val: Filter events on/after start timestamp.
        end_date_val: Filter events on/before end timestamp.

    Returns:
        Dictionary containing territory density metrics, unassigned drawer data, and summary totals.

    Raises:
        InvalidFilterError: If filters are malformed (e.g. min_deal_value > max_deal_value).
        TerritoryAccessDeniedError: If requested territory_id is not in user_territory_ids.
    """
    if min_deal_value is not None and max_deal_value is not None:
        if min_deal_value > max_deal_value:
            raise InvalidFilterError("min_deal_value cannot be greater than max_deal_value")

    start_dt = _ensure_utc(start_date_val)
    end_dt = _ensure_utc(end_date_val)
    if start_dt and end_dt and start_dt > end_dt:
        raise InvalidFilterError("start_date_val cannot be after end_date_val")

    target_territory_uuid = _parse_uuid(territory_id)

    # Validate multi-region user access permissions (NFR-002)
    allowed_uuids: Optional[Set[uuid.UUID]] = None
    if user_territory_ids is not None:
        allowed_uuids = set()
        for utid in user_territory_ids:
            parsed = _parse_uuid(utid)
            if parsed is not None:
                allowed_uuids.add(parsed)

        if target_territory_uuid is not None and target_territory_uuid not in allowed_uuids:
            raise TerritoryAccessDeniedError(
                f"User is not authorized to access territory '{target_territory_uuid}'"
            )

    all_territories = db.query(Territory).all()

    active_territories = all_territories
    if allowed_uuids is not None:
        active_territories = [t for t in active_territories if t.id in allowed_uuids]

    if target_territory_uuid is not None:
        active_territories = [t for t in active_territories if t.id == target_territory_uuid]

    query = db.query(LeadEvent)

    if start_dt:
        query = query.filter(LeadEvent.event_timestamp >= start_dt)
    if end_dt:
        query = query.filter(LeadEvent.event_timestamp <= end_dt)

    if lead_status and lead_status.strip():
        query = query.filter(func.lower(LeadEvent.stage_name) == lead_status.strip().lower())

    if min_deal_value is not None:
        query = query.filter(LeadEvent.deal_value >= min_deal_value)
    if max_deal_value is not None:
        query = query.filter(LeadEvent.deal_value <= max_deal_value)

    events: List[LeadEvent] = query.all()

    territory_map: Dict[uuid.UUID, Dict[str, Any]] = {}
    for terr in active_territories:
        territory_map[terr.id] = {
            "territory_id": str(terr.id),
            "territory_code": terr.territory_code,
            "territory_name": terr.territory_name,
            "postal_code_patterns": terr.postal_code_patterns,
            "lead_count": 0,
            "total_deal_value": 0.0,
            "total_revenue": 0.0,
            "avg_deal_value": 0.0,
            "density_score": 0,
        }

    unassigned_events: List[Dict[str, Any]] = []

    for event in events:
        matched_territory_id: Optional[uuid.UUID] = None

        if event.territory_id and event.territory_id in territory_map:
            matched_territory_id = event.territory_id
        elif event.postal_code:
            matching_terr = find_matching_territory(event.postal_code, active_territories)
            if matching_terr and matching_terr.id in territory_map:
                matched_territory_id = matching_terr.id

        deal_val = float(event.deal_value) if event.deal_value is not None else 0.0
        rev_val = float(event.revenue_amount) if event.revenue_amount is not None else 0.0

        if matched_territory_id and matched_territory_id in territory_map:
            bucket = territory_map[matched_territory_id]
            bucket["lead_count"] += 1
            bucket["total_deal_value"] += deal_val
            bucket["total_revenue"] += rev_val
            bucket["density_score"] += 1
        else:
            if target_territory_uuid is None:
                unassigned_events.append({
                    "id": str(event.id),
                    "lead_id": str(event.lead_id),
                    "stage_name": event.stage_name,
                    "quality_score": event.quality_score,
                    "deal_value": deal_val,
                    "revenue_amount": rev_val,
                    "postal_code": event.postal_code,
                    "event_timestamp": (
                        event.event_timestamp.isoformat() if event.event_timestamp else None
                    ),
                })

    territories_result: List[Dict[str, Any]] = []
    total_mapped_leads = 0
    total_mapped_deal_value = 0.0
    total_mapped_revenue = 0.0

    for terr_id, bucket in territory_map.items():
        if bucket["lead_count"] > 0:
            bucket["avg_deal_value"] = round(bucket["total_deal_value"] / bucket["lead_count"], 2)
        bucket["total_deal_value"] = round(bucket["total_deal_value"], 2)
        bucket["total_revenue"] = round(bucket["total_revenue"], 2)

        total_mapped_leads += bucket["lead_count"]
        total_mapped_deal_value += bucket["total_deal_value"]
        total_mapped_revenue += bucket["total_revenue"]

        territories_result.append(bucket)

    unassigned_lead_count = len(unassigned_events)
    unassigned_deal_value = round(sum(e["deal_value"] for e in unassigned_events), 2)
    unassigned_revenue = round(sum(e["revenue_amount"] for e in unassigned_events), 2)

    unassigned_drawer = {
        "lead_count": unassigned_lead_count,
        "total_deal_value": unassigned_deal_value,
        "total_revenue": unassigned_revenue,
        "unassigned_events": unassigned_events,
    }

    summary = {
        "total_mapped_leads": total_mapped_leads,
        "total_unassigned_leads": unassigned_lead_count,
        "total_leads": total_mapped_leads + unassigned_lead_count,
        "total_deal_value": round(total_mapped_deal_value + unassigned_deal_value, 2),
        "total_revenue": round(total_mapped_revenue + unassigned_revenue, 2),
    }

    return {
        "territories": territories_result,
        "unassigned_drawer": unassigned_drawer,
        "summary": summary,
    }
