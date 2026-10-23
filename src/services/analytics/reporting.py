"""Analytics Reporting Domain Service for channel acquisition aggregation and campaign trend analysis.

Provides business logic for aggregating channel lead volume, quality scores, spend, revenue,
and zero-spend ROI division safety [FR-001, FR-002, FR-003].
"""

from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional, Tuple
import uuid

from sqlalchemy.orm import Session

from src.models.analytics import AcquisitionChannel, Campaign, LeadEvent

logger = logging.getLogger(__name__)


class ReportingServiceError(Exception):
    """Base exception class for analytics reporting service errors."""

    pass


class ChannelNotFoundError(ReportingServiceError):
    """Raised when a specified acquisition channel is not found."""

    pass


class InvalidDateRangeError(ReportingServiceError):
    """Raised when date range filter parameters are invalid."""

    pass


class CampaignNotFoundError(ReportingServiceError):
    """Raised when a specified marketing campaign is not found."""

    pass


class CampaignLimitExceededError(ReportingServiceError):
    """Raised when attempting to compare more than 5 campaigns simultaneously."""

    pass


# Compatibility alias
MaxCampaignLimitExceededError = CampaignLimitExceededError


def _ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    """Ensure a datetime object is timezone-aware and set to UTC."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def calculate_channel_roi(spend: float, revenue: float) -> Tuple[Optional[float], str]:
    """Calculate channel ROI percentage and formatted string with zero-spend division safety [FR-003].

    Args:
        spend: Total channel spend amount.
        revenue: Total attributed revenue amount.

    Returns:
        Tuple of (roi_value_float_or_none, roi_str).
        If spend is zero or negative, returns (None, "N/A").
    """
    if spend is None or spend <= 0:
        return None, "N/A"

    roi_val = round(((revenue - spend) / spend) * 100, 2)
    roi_str = f"{roi_val:.2f}%"
    return roi_val, roi_str


def compute_lead_quality_score(
    stage_name: str,
    deal_value: float = 0.0,
    revenue_amount: float = 0.0,
    raw_score: Optional[int] = None,
) -> int:
    """Compute rule-based lead quality score or validate raw score [FR-002].

    Args:
        stage_name: Progression stage name of the lead.
        deal_value: Numeric deal value associated with the lead.
        revenue_amount: Attributed revenue amount.
        raw_score: Optional explicit quality score from external CRM ingestion.

    Returns:
        Integer quality score bounded between 0 and 100.
    """
    if raw_score is not None:
        return max(0, min(100, int(raw_score)))

    score = 50
    normalized_stage = stage_name.upper() if stage_name else ""

    if "WON" in normalized_stage or "CLOSED" in normalized_stage or "REVENUE" in normalized_stage:
        score += 30
    elif "QUALIFIED" in normalized_stage or "SQL" in normalized_stage or "OPPORTUNITY" in normalized_stage:
        score += 20
    elif "MQL" in normalized_stage or "LEAD" in normalized_stage:
        score += 10

    if deal_value > 10000:
        score += 10
    elif deal_value > 1000:
        score += 5

    if revenue_amount > 0:
        score += 10

    return max(0, min(100, score))


def calculate_campaign_trends(
    campaigns: List[Campaign],
    events_by_campaign: Dict[uuid.UUID, List[LeadEvent]],
    start_date_val: Optional[datetime] = None,
    end_date_val: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Calculate comparative performance metrics and time-series trends across campaigns [FR-006].

    Args:
        campaigns: List of Campaign ORM objects (up to 5).
        events_by_campaign: Mapping of campaign UUID to list of filtered LeadEvent objects.
        start_date_val: Optional start boundary timestamp filter.
        end_date_val: Optional end boundary timestamp filter.

    Returns:
        List of campaign performance summary and trend metrics dictionaries.

    Raises:
        CampaignLimitExceededError: If more than 5 campaigns are provided.
    """
    if len(campaigns) > 5:
        raise CampaignLimitExceededError("Maximum 5 campaigns can be compared simultaneously.")

    # Calculate total unique lead volume across all compared campaigns for relative volume percentage
    campaign_lead_counts: Dict[uuid.UUID, int] = {}
    for campaign in campaigns:
        events = events_by_campaign.get(campaign.id, [])
        unique_leads = {e.lead_id for e in events}
        campaign_lead_counts[campaign.id] = len(unique_leads)

    total_leads_all = sum(campaign_lead_counts.values())

    comparison_results: List[Dict[str, Any]] = []

    for campaign in campaigns:
        events = events_by_campaign.get(campaign.id, [])
        budget = round(float(campaign.budget) if campaign.budget is not None else 0.0, 2)
        lead_volume = campaign_lead_counts[campaign.id]

        relative_volume_pct = round((lead_volume / total_leads_all) * 100, 2) if total_leads_all > 0 else 0.0

        if lead_volume > 0:
            if budget > 0:
                cost_per_lead = round(budget / lead_volume, 2)
                cost_per_lead_str = f"${cost_per_lead:.2f}"
            else:
                cost_per_lead = 0.0
                cost_per_lead_str = "$0.00"
        else:
            cost_per_lead = None
            cost_per_lead_str = "N/A"

        total_revenue = round(sum(float(e.revenue_amount) for e in events if e.revenue_amount is not None), 2)
        avg_quality_score = round(sum(e.quality_score for e in events) / len(events), 2) if events else 0.0

        engaged_leads = len({e.lead_id for e in events if e.quality_score >= 50})
        engagement_rate = round((engaged_leads / lead_volume) * 100, 2) if lead_volume > 0 else 0.0
        engagement_rate_str = f"{engagement_rate:.2f}%"

        # Time series grouping by YYYY-MM-DD
        events_by_date: Dict[str, List[LeadEvent]] = {}
        for event in events:
            if event.event_timestamp:
                d_str = event.event_timestamp.strftime("%Y-%m-%d")
                events_by_date.setdefault(d_str, []).append(event)

        time_series: List[Dict[str, Any]] = []
        for d_str in sorted(events_by_date.keys()):
            day_events = events_by_date[d_str]
            day_leads = len({e.lead_id for e in day_events})
            day_rev = round(sum(float(e.revenue_amount) for e in day_events if e.revenue_amount is not None), 2)
            day_engaged = len({e.lead_id for e in day_events if e.quality_score >= 50})
            day_eng_rate = round((day_engaged / day_leads) * 100, 2) if day_leads > 0 else 0.0

            if day_leads > 0:
                if budget > 0:
                    day_cpl = round(budget / day_leads, 2)
                    day_cpl_str = f"${day_cpl:.2f}"
                else:
                    day_cpl = 0.0
                    day_cpl_str = "$0.00"
            else:
                day_cpl = None
                day_cpl_str = "N/A"

            time_series.append({
                "date_val": d_str,
                "lead_volume": day_leads,
                "cost_per_lead": day_cpl,
                "cost_per_lead_str": day_cpl_str,
                "engagement_rate": day_eng_rate,
                "revenue": day_rev,
            })

        campaign_item = {
            "campaign_id": str(campaign.id),
            "campaign_name": campaign.campaign_name,
            "budget": budget,
            "start_date_val": campaign.start_date_val.isoformat() if campaign.start_date_val else None,
            "end_date_val": campaign.end_date_val.isoformat() if campaign.end_date_val else None,
            "lead_volume": lead_volume,
            "relative_volume_pct": relative_volume_pct,
            "cost_per_lead": cost_per_lead,
            "cost_per_lead_str": cost_per_lead_str,
            "total_revenue": total_revenue,
            "avg_quality_score": avg_quality_score,
            "engagement_rate": engagement_rate,
            "engagement_rate_str": engagement_rate_str,
            "time_series": time_series,
            "trend_points": time_series,
        }
        comparison_results.append(campaign_item)

    return comparison_results


class ReportingService:
    """Domain service encapsulating channel acquisition aggregation and analytics reporting."""

    def __init__(self, db: Session) -> None:
        """Initialize ReportingService with a synchronous database session.

        Args:
            db: Synchronous SQLAlchemy database session.
        """
        self.db = db

    def get_channel_acquisition_report(
        self,
        start_date_val: Optional[datetime] = None,
        end_date_val: Optional[datetime] = None,
        channel_id: Optional[str] = None,
        sort_by: str = "quality_score",
        sort_order: str = "desc",
    ) -> List[Dict[str, Any]]:
        """Aggregate channel acquisition metrics, lead quality scores, and zero-spend ROI [FR-001, FR-002, FR-003].

        Args:
            start_date_val: Optional start boundary timestamp filter.
            end_date_val: Optional end boundary timestamp filter.
            channel_id: Optional specific acquisition channel UUID filter.
            sort_by: Field to sort report items by (quality_score, lead_volume, spend, revenue, roi, channel_name).
            sort_order: Sort direction ('desc' or 'asc').

        Returns:
            List of aggregated channel performance dictionaries.

        Raises:
            InvalidDateRangeError: If start_date_val is after end_date_val.
            ChannelNotFoundError: If specified channel_id is invalid or not found.
        """
        start_dt = _ensure_utc(start_date_val)
        end_dt = _ensure_utc(end_date_val)

        if start_dt and end_dt and start_dt > end_dt:
            raise InvalidDateRangeError("start_date_val must be before or equal to end_date_val.")

        query = self.db.query(AcquisitionChannel)

        if channel_id:
            try:
                target_uuid = uuid.UUID(channel_id) if isinstance(channel_id, str) else channel_id
            except ValueError:
                raise ChannelNotFoundError(f"Invalid channel_id format: '{channel_id}'")

            query = query.filter(AcquisitionChannel.id == target_uuid)

        channels = query.all()

        if channel_id and not channels:
            raise ChannelNotFoundError(f"Acquisition channel with id '{channel_id}' not found.")

        report_items: List[Dict[str, Any]] = []

        for channel in channels:
            events_query = self.db.query(LeadEvent).filter(LeadEvent.channel_id == channel.id)

            if start_dt:
                events_query = events_query.filter(LeadEvent.event_timestamp >= start_dt)
            if end_dt:
                events_query = events_query.filter(LeadEvent.event_timestamp <= end_dt)

            events = events_query.all()

            # Attributed unique lead volume
            distinct_lead_ids = {e.lead_id for e in events}
            lead_volume = len(distinct_lead_ids)

            # Average lead quality score
            if events:
                avg_quality_score = round(sum(e.quality_score for e in events) / len(events), 2)
            else:
                avg_quality_score = 0.0

            spend = round(float(channel.spend_amount) if channel.spend_amount is not None else 0.0, 2)
            revenue = round(sum(float(e.revenue_amount) for e in events if e.revenue_amount is not None), 2)

            roi_val, roi_str = calculate_channel_roi(spend, revenue)

            report_items.append({
                "channel_id": str(channel.id),
                "channel_name": channel.channel_name,
                "lead_volume": lead_volume,
                "quality_score": avg_quality_score,
                "spend": spend,
                "revenue": revenue,
                "roi": roi_val,
                "roi_str": roi_str,
                "created_on": channel.created_on.isoformat() if channel.created_on else None,
            })

        # Sorting logic
        valid_sort_fields = {"quality_score", "lead_volume", "spend", "revenue", "roi", "channel_name"}
        sort_field = sort_by if sort_by in valid_sort_fields else "quality_score"
        is_desc = (sort_order.lower() == "desc")

        def _sort_key(item: Dict[str, Any]) -> Any:
            val = item.get(sort_field)
            if sort_field == "roi":
                if val is None:
                    return -float("inf") if is_desc else float("inf")
                return val
            if val is None:
                return 0 if isinstance(item.get("revenue"), (int, float)) else ""
            return val

        report_items.sort(key=_sort_key, reverse=is_desc)
        return report_items

    def get_campaign_comparison(
        self,
        campaign_ids: List[str],
        start_date_val: Optional[datetime] = None,
        end_date_val: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Retrieve comparative campaign trend analysis for up to 5 campaigns [FR-006].

        Args:
            campaign_ids: List of campaign UUID strings to compare (max 5).
            start_date_val: Optional start timestamp boundary filter.
            end_date_val: Optional end timestamp boundary filter.

        Returns:
            List of comparative campaign metrics and time-series trends.

        Raises:
            CampaignLimitExceededError: If more than 5 campaign IDs are specified.
            InvalidDateRangeError: If start_date_val is after end_date_val.
            CampaignNotFoundError: If a campaign_id is invalid or not found.
        """
        if campaign_ids is None:
            campaign_ids = []

        clean_ids = [str(cid).strip() for cid in campaign_ids if cid and str(cid).strip()]

        if len(clean_ids) > 5:
            raise CampaignLimitExceededError("Maximum 5 campaigns can be compared simultaneously.")

        start_dt = _ensure_utc(start_date_val)
        end_dt = _ensure_utc(end_date_val)

        if start_dt and end_dt and start_dt > end_dt:
            raise InvalidDateRangeError("start_date_val must be before or equal to end_date_val.")

        if not clean_ids:
            return []

        campaigns: List[Campaign] = []
        events_by_campaign: Dict[uuid.UUID, List[LeadEvent]] = {}

        for cid in clean_ids:
            try:
                target_uuid = uuid.UUID(cid)
            except ValueError:
                raise CampaignNotFoundError(f"Invalid campaign_id format: '{cid}'")

            campaign = self.db.query(Campaign).filter(Campaign.id == target_uuid).first()
            if not campaign:
                raise CampaignNotFoundError(f"Campaign with id '{cid}' not found.")

            campaigns.append(campaign)

            events_query = self.db.query(LeadEvent).filter(LeadEvent.campaign_id == campaign.id)
            if start_dt:
                events_query = events_query.filter(LeadEvent.event_timestamp >= start_dt)
            if end_dt:
                events_query = events_query.filter(LeadEvent.event_timestamp <= end_dt)

            events_by_campaign[campaign.id] = events_query.all()

        return calculate_campaign_trends(
            campaigns=campaigns,
            events_by_campaign=events_by_campaign,
            start_date_val=start_dt,
            end_date_val=end_dt,
        )

    def calculate_campaign_trends(
        self,
        campaign_ids: List[str],
        start_date_val: Optional[datetime] = None,
        end_date_val: Optional[datetime] = None,
    ) -> List[Dict[str, Any]]:
        """Alias method for get_campaign_comparison [FR-006]."""
        return self.get_campaign_comparison(
            campaign_ids=campaign_ids,
            start_date_val=start_date_val,
            end_date_val=end_date_val,
        )
