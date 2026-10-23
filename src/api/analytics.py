"""FastAPI routes for Lead Acquisition & Marketing Analytics [FR-001, FR-002, FR-003, NFR-003, NFR-004]."""

from datetime import datetime
import logging
from typing import Any, Dict, List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from src.lib.logging import get_correlation_id
from src.models.analytics import FunnelThreshold, ReportAuditLog
from src.models.database import get_db
from src.services.analytics.funnel import (
    FunnelServiceError,
    InvalidFunnelDateRangeError,
    StageNotFoundError,
    analyze_conversion_funnel,
)
from src.services.analytics.reporting import (
    CampaignLimitExceededError,
    CampaignNotFoundError,
    ChannelNotFoundError,
    InvalidDateRangeError,
    ReportingService,
    ReportingServiceError,
)
from src.services.marketing.territory import (
    InvalidFilterError,
    TerritoryAccessDeniedError,
    TerritoryServiceError,
    get_territory_heatmap_data,
)
from src.services.support.ticket_service import TicketService

logger = logging.getLogger(__name__)

analytics_router = APIRouter(prefix="/analytics", tags=["Analytics"])
support_analytics_router = APIRouter(prefix="/support", tags=["Analytics"])
router = analytics_router


class ChannelAcquisitionReportItemSchema(BaseModel):
    """Pydantic schema representing a channel acquisition performance metric item [FR-001, FR-002, FR-003]."""

    channel_id: str = Field(..., description="UUID string of acquisition channel")
    channel_name: str = Field(..., description="Name of acquisition channel")
    lead_volume: int = Field(..., ge=0, description="Attributed lead volume count")
    quality_score: float = Field(..., ge=0.0, le=100.0, description="Average lead quality score (0 to 100)")
    spend: float = Field(..., ge=0.0, description="Total spend amount for channel")
    revenue: float = Field(..., ge=0.0, description="Total attributed revenue for channel")
    roi: Optional[float] = Field(None, description="Calculated ROI percentage float or None if zero spend")
    roi_str: str = Field(..., description="Formatted ROI string or 'N/A' if zero spend")
    created_on: Optional[str] = Field(None, description="ISO timestamp of channel creation")


class ChannelAcquisitionQueryParams(BaseModel):
    """Pydantic query parameters model for channel acquisition report endpoint [NFR-004]."""

    start_date_val: Optional[datetime] = Field(None, description="Optional start boundary timestamp filter")
    end_date_val: Optional[datetime] = Field(None, description="Optional end boundary timestamp filter")
    channel_id: Optional[str] = Field(None, description="Optional acquisition channel UUID filter")
    sort_by: str = Field(
        "quality_score",
        description="Sort field ('quality_score', 'lead_volume', 'spend', 'revenue', 'roi', 'channel_name')",
    )
    sort_order: str = Field("desc", description="Sort order direction ('desc' or 'asc')")

    @field_validator("channel_id", mode="before")
    @classmethod
    def sanitize_channel_id(cls, v: Any) -> Optional[str]:
        """Sanitize channel_id string by stripping whitespace [NFR-004]."""
        if isinstance(v, str):
            v_clean = v.strip()
            return v_clean if v_clean else None
        return v

    @field_validator("sort_by", mode="before")
    @classmethod
    def sanitize_and_validate_sort_by(cls, v: Any) -> str:
        """Sanitize and validate sort_by field parameter [NFR-004]."""
        valid_fields = {"quality_score", "lead_volume", "spend", "revenue", "roi", "channel_name"}
        if isinstance(v, str):
            v_clean = v.strip()
            if v_clean not in valid_fields:
                raise ValueError(
                    f"Invalid sort_by field '{v_clean}'. Allowed values: {sorted(list(valid_fields))}"
                )
            return v_clean
        return "quality_score"

    @field_validator("sort_order", mode="before")
    @classmethod
    def sanitize_and_validate_sort_order(cls, v: Any) -> str:
        """Sanitize and validate sort_order parameter [NFR-004]."""
        valid_orders = {"asc", "desc"}
        if isinstance(v, str):
            v_clean = v.strip().lower()
            if v_clean not in valid_orders:
                raise ValueError(f"Invalid sort_order '{v_clean}'. Allowed values: 'asc', 'desc'")
            return v_clean
        return "desc"


class FunnelStageItemSchema(BaseModel):
    """Pydantic schema representing a conversion funnel stage performance item [FR-004, FR-005]."""

    stage_name: str = Field(..., description="Funnel stage name (e.g. 'LEAD', 'MQL', 'SQL', 'OPPORTUNITY', 'CLOSED_WON')")
    stage_count: int = Field(..., ge=0, description="Lead count at this stage")
    conversion_rate: float = Field(..., ge=0.0, le=100.0, description="Stage-to-stage conversion rate percentage")
    dropoff_rate: float = Field(..., ge=0.0, le=100.0, description="Stage-to-stage drop-off rate percentage")
    is_bottleneck: bool = Field(..., description="Flag indicating if drop-off exceeds threshold")
    threshold_percent: float = Field(..., ge=0.0, le=100.0, description="Configured drop-off threshold percentage for stage")
    overall_conversion_rate: float = Field(..., ge=0.0, le=100.0, description="Conversion rate relative to top of funnel")


class CampaignTimeSeriesItemSchema(BaseModel):
    """Pydantic schema representing a campaign daily performance metric data point [FR-006]."""

    date_val: str = Field(..., description="Date string in YYYY-MM-DD format")
    lead_volume: int = Field(..., ge=0, description="Lead volume count on this date")
    cost_per_lead: Optional[float] = Field(None, description="Cost per lead float or None if zero volume")
    cost_per_lead_str: str = Field(..., description="Formatted cost per lead string (e.g. '$50.00' or 'N/A')")
    engagement_rate: float = Field(..., ge=0.0, le=100.0, description="Engagement rate percentage")
    revenue: float = Field(..., ge=0.0, description="Attributed revenue on this date")


class CampaignComparisonItemSchema(BaseModel):
    """Pydantic schema representing a comparative campaign performance summary and trend metrics [FR-006]."""

    campaign_id: str = Field(..., description="UUID string of campaign")
    campaign_name: str = Field(..., description="Name of marketing campaign")
    budget: float = Field(..., ge=0.0, description="Total allocated campaign budget")
    start_date_val: Optional[str] = Field(None, description="ISO timestamp of campaign start date")
    end_date_val: Optional[str] = Field(None, description="ISO timestamp of campaign end date")
    lead_volume: int = Field(..., ge=0, description="Total attributed lead volume")
    relative_volume_pct: float = Field(..., ge=0.0, le=100.0, description="Relative share percentage of total leads across compared campaigns")
    cost_per_lead: Optional[float] = Field(None, description="Overall cost per lead float or None if zero volume")
    cost_per_lead_str: str = Field(..., description="Formatted overall cost per lead string")
    total_revenue: float = Field(..., ge=0.0, description="Total attributed revenue")
    avg_quality_score: float = Field(..., ge=0.0, le=100.0, description="Average lead quality score")
    engagement_rate: float = Field(..., ge=0.0, le=100.0, description="Overall engagement rate percentage")
    engagement_rate_str: str = Field(..., description="Formatted engagement rate string")
    time_series: List[CampaignTimeSeriesItemSchema] = Field(..., description="Time-series daily metric data points")
    trend_points: List[CampaignTimeSeriesItemSchema] = Field(..., description="Trend points daily metric data points (alias of time_series)")


class TerritoryDensityItemSchema(BaseModel):
    """Pydantic schema for territory density heat map item [FR-007, FR-008]."""

    territory_id: str = Field(..., description="UUID string of territory")
    territory_code: str = Field(..., description="Territory code (e.g. 'US-WEST')")
    territory_name: str = Field(..., description="Territory name")
    postal_code_patterns: Optional[str] = Field(None, description="Postal code patterns string")
    lead_count: int = Field(..., ge=0, description="Mapped lead count")
    total_deal_value: float = Field(..., ge=0.0, description="Total deal value in territory")
    total_revenue: float = Field(..., ge=0.0, description="Total revenue in territory")
    avg_deal_value: float = Field(..., ge=0.0, description="Average deal value in territory")
    density_score: int = Field(..., ge=0, description="Relative density score")


class UnassignedEventItemSchema(BaseModel):
    """Pydantic schema for unassigned lead event in drawer [FR-008]."""

    id: str = Field(..., description="UUID string of lead event")
    lead_id: str = Field(..., description="UUID string of lead")
    stage_name: str = Field(..., description="Lead event stage name")
    quality_score: Optional[int] = Field(None, description="Lead quality score")
    deal_value: float = Field(..., ge=0.0, description="Deal value")
    revenue_amount: float = Field(..., ge=0.0, description="Revenue amount")
    postal_code: Optional[str] = Field(None, description="Postal code if any")
    event_timestamp: Optional[str] = Field(None, description="ISO timestamp of event")


class UnassignedDrawerSchema(BaseModel):
    """Pydantic schema for unassigned territory drawer [FR-008]."""

    lead_count: int = Field(..., ge=0, description="Total count of unassigned leads/events")
    total_deal_value: float = Field(..., ge=0.0, description="Total deal value of unassigned leads")
    total_revenue: float = Field(..., ge=0.0, description="Total revenue of unassigned leads")
    unassigned_events: List[UnassignedEventItemSchema] = Field(..., description="List of unassigned lead events")


class TerritoryHeatmapSummarySchema(BaseModel):
    """Pydantic schema for heat map summary totals [FR-007, FR-008]."""

    total_mapped_leads: int = Field(..., ge=0, description="Total leads mapped to territories")
    total_unassigned_leads: int = Field(..., ge=0, description="Total unassigned leads")
    total_leads: int = Field(..., ge=0, description="Total leads overall")
    total_deal_value: float = Field(..., ge=0.0, description="Total deal value overall")
    total_revenue: float = Field(..., ge=0.0, description="Total revenue overall")


class TerritoryHeatmapResponseSchema(BaseModel):
    """Pydantic schema for territory heat map API response [FR-007, FR-008, NFR-002, NFR-004]."""

    territories: List[TerritoryDensityItemSchema] = Field(..., description="List of territory density metrics")
    unassigned_drawer: UnassignedDrawerSchema = Field(..., description="Unassigned territory drawer data")
    summary: TerritoryHeatmapSummarySchema = Field(..., description="Summary totals")


class ReportAuditLogCreateSchema(BaseModel):
    """Pydantic schema for creating a report configuration audit log record [FR-011, NFR-004]."""

    user_id: Optional[str] = Field(
        None,
        description="Optional UUID string of the user making the configuration change. If omitted, a system UUID is generated.",
    )
    action_type: str = Field(
        ...,
        max_length=100,
        description="Action type identifier (e.g. 'UPDATE_THRESHOLD', 'DELETE_CONFIG', 'CREATE_REPORT_CONFIG')",
    )
    config_delta: Optional[str] = Field(
        None,
        description="JSON text or string representation of configuration changes/delta",
    )

    @field_validator("action_type", mode="before")
    @classmethod
    def sanitize_action_type(cls, v: Any) -> str:
        """Sanitize action_type string by stripping whitespace [NFR-004]."""
        if isinstance(v, str):
            v_clean = v.strip()
            if not v_clean:
                raise ValueError("action_type cannot be empty")
            return v_clean
        raise ValueError("action_type must be a valid string")

    @field_validator("user_id", mode="before")
    @classmethod
    def sanitize_user_id(cls, v: Any) -> Optional[str]:
        """Sanitize user_id string by stripping whitespace [NFR-004]."""
        if isinstance(v, str):
            v_clean = v.strip()
            return v_clean if v_clean else None
        return v


class ReportAuditLogItemSchema(BaseModel):
    """Pydantic schema representing a created report audit log item [FR-011]."""

    id: str = Field(..., description="UUID string of report audit log record")
    user_id: str = Field(..., description="UUID string of user who initiated config change")
    action_type: str = Field(..., description="Action type identifier")
    config_delta: Optional[str] = Field(None, description="Configuration delta or diff text")
    created_on: str = Field(..., description="ISO timestamp of audit log creation")


@analytics_router.get(
    "/acquisition",
    response_model=List[ChannelAcquisitionReportItemSchema],
    status_code=status.HTTP_200_OK,
    summary="Get Channel Acquisition Report",
    description=(
        "Aggregate channel acquisition metrics, lead quality scores, spend, revenue, "
        "and zero-spend ROI [FR-001, FR-002, FR-003, NFR-003, NFR-004]."
    ),
)
def get_channel_acquisition_report(
    start_date_val: Optional[datetime] = Query(None, description="Optional start boundary timestamp filter"),
    end_date_val: Optional[datetime] = Query(None, description="Optional end boundary timestamp filter"),
    channel_id: Optional[str] = Query(None, description="Optional acquisition channel UUID filter"),
    sort_by: str = Query(
        "quality_score",
        description="Sort field ('quality_score', 'lead_volume', 'spend', 'revenue', 'roi', 'channel_name')",
    ),
    sort_order: str = Query("desc", description="Sort order direction ('desc' or 'asc')"),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Retrieve aggregated channel acquisition performance report [FR-001, FR-002, FR-003, NFR-003, NFR-004]."""
    # Input sanitization and validation [NFR-004]
    clean_channel_id = channel_id.strip() if channel_id and isinstance(channel_id, str) else None
    if clean_channel_id == "":
        clean_channel_id = None

    clean_sort_by = sort_by.strip() if sort_by and isinstance(sort_by, str) else "quality_score"
    valid_sort_fields = {"quality_score", "lead_volume", "spend", "revenue", "roi", "channel_name"}
    if clean_sort_by not in valid_sort_fields:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort_by field '{clean_sort_by}'. Allowed values: {sorted(list(valid_sort_fields))}",
        )

    clean_sort_order = sort_order.strip().lower() if sort_order and isinstance(sort_order, str) else "desc"
    if clean_sort_order not in {"asc", "desc"}:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid sort_order '{clean_sort_order}'. Allowed values: 'asc', 'desc'",
        )

    logger.info(
        "Processing channel acquisition report request",
        extra={
            "start_date_val": start_date_val.isoformat() if start_date_val else None,
            "end_date_val": end_date_val.isoformat() if end_date_val else None,
            "channel_id": clean_channel_id,
            "sort_by": clean_sort_by,
            "sort_order": clean_sort_order,
        },
    )

    service = ReportingService(db=db)
    try:
        report = service.get_channel_acquisition_report(
            start_date_val=start_date_val,
            end_date_val=end_date_val,
            channel_id=clean_channel_id,
            sort_by=clean_sort_by,
            sort_order=clean_sort_order,
        )
        return report
    except InvalidDateRangeError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )
    except ChannelNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )
    except ReportingServiceError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )


@analytics_router.get(
    "/funnel",
    response_model=List[FunnelStageItemSchema],
    status_code=status.HTTP_200_OK,
    summary="Get Conversion Funnel Report",
    description=(
        "Retrieve conversion funnel stages, stage counts, conversion rates, drop-off rates, "
        "and automated drop-off bottleneck detection flags [FR-004, FR-005, NFR-003, NFR-004]."
    ),
)
def get_conversion_funnel_report(
    funnel_id: Optional[str] = Query(None, description="Optional funnel identifier filter"),
    start_date_val: Optional[datetime] = Query(None, description="Optional start boundary timestamp filter"),
    end_date_val: Optional[datetime] = Query(None, description="Optional end boundary timestamp filter"),
    channel_id: Optional[str] = Query(None, description="Optional acquisition channel UUID filter"),
    campaign_id: Optional[str] = Query(None, description="Optional campaign UUID filter"),
    territory_id: Optional[str] = Query(None, description="Optional territory UUID filter"),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Retrieve conversion funnel stage progression and bottleneck alerts [FR-004, FR-005, NFR-003, NFR-004]."""
    # Input sanitization and validation [NFR-004]
    clean_funnel_id = funnel_id.strip() if funnel_id and isinstance(funnel_id, str) else None
    if clean_funnel_id == "":
        clean_funnel_id = None

    clean_channel_id = channel_id.strip() if channel_id and isinstance(channel_id, str) else None
    if clean_channel_id == "":
        clean_channel_id = None

    clean_campaign_id = campaign_id.strip() if campaign_id and isinstance(campaign_id, str) else None
    if clean_campaign_id == "":
        clean_campaign_id = None

    clean_territory_id = territory_id.strip() if territory_id and isinstance(territory_id, str) else None
    if clean_territory_id == "":
        clean_territory_id = None

    logger.info(
        "Processing conversion funnel report request",
        extra={
            "funnel_id": clean_funnel_id,
            "start_date_val": start_date_val.isoformat() if start_date_val else None,
            "end_date_val": end_date_val.isoformat() if end_date_val else None,
            "channel_id": clean_channel_id,
            "campaign_id": clean_campaign_id,
            "territory_id": clean_territory_id,
        },
    )

    try:
        funnel_report = analyze_conversion_funnel(
            db=db,
            funnel_id=clean_funnel_id,
            start_date_val=start_date_val,
            end_date_val=end_date_val,
            channel_id=clean_channel_id,
            campaign_id=clean_campaign_id,
            territory_id=clean_territory_id,
        )
        return funnel_report
    except InvalidFunnelDateRangeError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )
    except StageNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )
    except FunnelServiceError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )


@analytics_router.get(
    "/campaigns/compare",
    response_model=List[CampaignComparisonItemSchema],
    status_code=status.HTTP_200_OK,
    summary="Get Campaign Comparison Report",
    description=(
        "Retrieve comparative campaign trend analysis, relative lead volume, cost per lead, "
        "and engagement rates over time for up to 5 campaigns [FR-006, NFR-003, NFR-004]."
    ),
)
def get_campaign_comparison_report(
    campaign_ids: Optional[List[str]] = Query(
        None,
        description="List of campaign UUIDs or CSV string of campaign UUIDs (max 5 campaigns)",
    ),
    start_date_val: Optional[datetime] = Query(None, description="Optional start boundary timestamp filter"),
    end_date_val: Optional[datetime] = Query(None, description="Optional end boundary timestamp filter"),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Retrieve comparative campaign trend visualization and performance metrics [FR-006, NFR-003, NFR-004]."""
    # Input sanitization and validation [NFR-004]
    clean_campaign_ids: List[str] = []
    if campaign_ids:
        for item in campaign_ids:
            if isinstance(item, str):
                for sub_item in item.split(","):
                    cleaned = sub_item.strip()
                    if cleaned:
                        clean_campaign_ids.append(cleaned)

    logger.info(
        "Processing campaign comparison report request",
        extra={
            "campaign_ids": clean_campaign_ids,
            "start_date_val": start_date_val.isoformat() if start_date_val else None,
            "end_date_val": end_date_val.isoformat() if end_date_val else None,
        },
    )

    service = ReportingService(db=db)
    try:
        report = service.get_campaign_comparison(
            campaign_ids=clean_campaign_ids,
            start_date_val=start_date_val,
            end_date_val=end_date_val,
        )
        return report
    except CampaignLimitExceededError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )
    except InvalidDateRangeError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )
    except CampaignNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )
    except ReportingServiceError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )


@analytics_router.get(
    "/territory/heatmap",
    response_model=TerritoryHeatmapResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Get Territory Geographic Heat Map",
    description=(
        "Calculate geographic heat map density metrics across territories, enforce territory "
        "authorization scopes, and return unassigned territory drawer data [FR-007, FR-008, NFR-002, NFR-004]."
    ),
)
def get_territory_heatmap(
    lead_status: Optional[str] = Query(None, description="Optional lead status / stage_name filter"),
    min_deal_value: Optional[float] = Query(None, ge=0.0, description="Optional minimum deal value filter"),
    max_deal_value: Optional[float] = Query(None, ge=0.0, description="Optional maximum deal value filter"),
    territory_id: Optional[str] = Query(None, description="Optional territory UUID filter"),
    user_territory_ids: Optional[List[str]] = Query(
        None,
        description="Optional list of authorized territory UUIDs for caller scope or CSV string",
    ),
    start_date_val: Optional[datetime] = Query(None, description="Optional start boundary timestamp filter"),
    end_date_val: Optional[datetime] = Query(None, description="Optional end boundary timestamp filter"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve territory geographic heat map metrics and unassigned drawer [FR-007, FR-008, NFR-002, NFR-004]."""
    # Input sanitization and validation [NFR-004]
    clean_lead_status = lead_status.strip() if lead_status and isinstance(lead_status, str) else None
    if clean_lead_status == "":
        clean_lead_status = None

    clean_territory_id = territory_id.strip() if territory_id and isinstance(territory_id, str) else None
    if clean_territory_id == "":
        clean_territory_id = None

    clean_user_territory_ids: Optional[List[str]] = None
    if user_territory_ids is not None:
        parsed_user_tids: List[str] = []
        for item in user_territory_ids:
            if isinstance(item, str):
                for sub_item in item.split(","):
                    cleaned = sub_item.strip()
                    if cleaned:
                        parsed_user_tids.append(cleaned)
        if parsed_user_tids:
            clean_user_territory_ids = parsed_user_tids

    if min_deal_value is not None and max_deal_value is not None:
        if min_deal_value > max_deal_value:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="min_deal_value cannot be greater than max_deal_value",
            )

    if start_date_val and end_date_val and start_date_val > end_date_val:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_date_val cannot be after end_date_val",
        )

    logger.info(
        "Processing territory heatmap request",
        extra={
            "lead_status": clean_lead_status,
            "min_deal_value": min_deal_value,
            "max_deal_value": max_deal_value,
            "territory_id": clean_territory_id,
            "user_territory_ids": clean_user_territory_ids,
            "start_date_val": start_date_val.isoformat() if start_date_val else None,
            "end_date_val": end_date_val.isoformat() if end_date_val else None,
        },
    )

    try:
        data = get_territory_heatmap_data(
            db=db,
            lead_status=clean_lead_status,
            min_deal_value=min_deal_value,
            max_deal_value=max_deal_value,
            territory_id=clean_territory_id,
            user_territory_ids=clean_user_territory_ids,
            start_date_val=start_date_val,
            end_date_val=end_date_val,
        )
        return data
    except TerritoryAccessDeniedError as err:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(err),
        )
    except InvalidFilterError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )
    except TerritoryServiceError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )


@analytics_router.post(
    "/config/audit",
    response_model=ReportAuditLogItemSchema,
    status_code=status.HTTP_200_OK,
    summary="Create Report Configuration Audit Log",
    description=(
        "Record an immutable audit log entry capturing administrative report configuration "
        "changes, user ID, action type, and configuration delta [FR-011, NFR-003, NFR-004]."
    ),
)
def create_config_audit_log(
    payload: ReportAuditLogCreateSchema,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Create an immutable audit record for administrative configuration changes [FR-011, NFR-003, NFR-004]."""
    user_uuid: uuid.UUID
    if payload.user_id:
        try:
            user_uuid = uuid.UUID(payload.user_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid user_id UUID format '{payload.user_id}'",
            )
    else:
        user_uuid = uuid.uuid4()

    clean_action_type = payload.action_type.strip()
    if not clean_action_type:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="action_type cannot be empty",
        )

    clean_config_delta = (
        payload.config_delta.strip()
        if payload.config_delta and isinstance(payload.config_delta, str)
        else None
    )

    logger.info(
        "Creating report configuration audit log record",
        extra={
            "user_id": str(user_uuid),
            "action_type": clean_action_type,
            "config_delta": clean_config_delta,
        },
    )

    audit_entry = ReportAuditLog(
        user_id=user_uuid,
        action_type=clean_action_type,
        config_delta=clean_config_delta,
    )
    db.add(audit_entry)
    db.commit()
    db.refresh(audit_entry)

    return audit_entry.to_dict()


@analytics_router.delete(
    "/config/{id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Report Configuration",
    description=(
        "Delete a report or metric configuration record (such as a funnel threshold configuration or audit log entry) "
        "by ID and return 204 No Content with an empty response body [FR-011, NFR-003, NFR-004]."
    ),
)
def delete_report_configuration(
    id: str,
    db: Session = Depends(get_db),
) -> Response:
    """Delete a configuration record by ID [FR-011, NFR-003, NFR-004]."""
    clean_id = id.strip() if id and isinstance(id, str) else ""
    if not clean_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Configuration ID cannot be empty",
        )

    try:
        config_uuid = uuid.UUID(clean_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid configuration ID UUID format '{clean_id}'",
        )

    logger.info(
        "Processing configuration delete request",
        extra={"config_id": str(config_uuid)},
    )

    # Check FunnelThreshold table first
    threshold = db.query(FunnelThreshold).filter(FunnelThreshold.id == config_uuid).first()
    if threshold is not None:
        audit_record = ReportAuditLog(
            user_id=uuid.uuid4(),
            action_type="DELETE_CONFIG",
            config_delta=f"Deleted FunnelThreshold {config_uuid} (stage={threshold.stage_name})",
        )
        db.add(audit_record)
        db.delete(threshold)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    # Check ReportAuditLog table next
    audit_log = db.query(ReportAuditLog).filter(ReportAuditLog.id == config_uuid).first()
    if audit_log is not None:
        db.delete(audit_log)
        db.commit()
        return Response(status_code=status.HTTP_204_NO_CONTENT)

    raise HTTPException(
        status_code=status.HTTP_404_NOT_FOUND,
        detail=f"Configuration record with ID '{clean_id}' not found",
    )


class ExportTaskResponseSchema(BaseModel):
    """Pydantic schema representing task response payload for async report export submission [FR-012]."""

    task_id: str = Field(..., description="UUID tracking identifier for background export task")


class ExportReportRequestSchema(BaseModel):
    """Pydantic request body schema for submitting async report export task [FR-012, NFR-004]."""

    report_type: str = Field(
        ...,
        description=(
            "Report dataset identifier ('acquisition', 'funnel', "
            "'campaign_comparison', 'territory_heatmap')"
        ),
    )
    export_format: str = Field("csv", description="Desired export output file format ('csv' or 'pdf')")
    date_filters: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Filter parameters dictionary")
    start_date_val: Optional[datetime] = Field(None, description="Optional start boundary timestamp filter")
    end_date_val: Optional[datetime] = Field(None, description="Optional end boundary timestamp filter")

    @field_validator("report_type", mode="before")
    @classmethod
    def sanitize_and_validate_report_type(cls, v: Any) -> str:
        """Sanitize and validate report_type field [NFR-004]."""
        if isinstance(v, str):
            v_clean = v.strip().lower()
            valid_types = {
                "acquisition",
                "acquisition_report",
                "channel_acquisition",
                "funnel",
                "conversion_funnel",
                "funnel_report",
                "campaign_comparison",
                "campaigns",
                "campaign_report",
                "territory",
                "territory_heatmap",
                "territory_report",
            }
            if not v_clean or v_clean not in valid_types:
                raise ValueError(
                    f"Invalid report_type '{v_clean}'. Allowed values: {sorted(list(valid_types))}"
                )
            return v_clean
        raise ValueError("report_type must be a non-empty string")

    @field_validator("export_format", mode="before")
    @classmethod
    def sanitize_and_validate_export_format(cls, v: Any) -> str:
        """Sanitize and validate export_format field [NFR-004]."""
        if isinstance(v, str):
            v_clean = v.strip().lower()
            valid_formats = {"csv", "pdf"}
            if v_clean not in valid_formats:
                raise ValueError(
                    f"Invalid export_format '{v_clean}'. Allowed values: 'csv', 'pdf'"
                )
            return v_clean
        return "csv"


@analytics_router.post(
    "/exports",
    response_model=ExportTaskResponseSchema,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Submit Async Report Export Task",
    description=(
        "Submit a background task for exporting marketing analytics reports (acquisition, funnel, campaign comparison, "
        "or territory heat map) to downloadable CSV or PDF file format. Returns a task tracking UUID [FR-012, NFR-004]."
    ),
)
def submit_report_export_task(
    payload: ExportReportRequestSchema,
) -> ExportTaskResponseSchema:
    """Submit background export task and return task tracking ID [FR-012, NFR-004]."""
    correlation_id = get_correlation_id() or str(uuid.uuid4())

    filters = dict(payload.date_filters or {})
    if payload.start_date_val is not None and "start_date_val" not in filters:
        filters["start_date_val"] = payload.start_date_val.isoformat()
    if payload.end_date_val is not None and "end_date_val" not in filters:
        filters["end_date_val"] = payload.end_date_val.isoformat()

    logger.info(
        "Submitting async report export task: report_type='%s', export_format='%s', correlation_id='%s'",
        payload.report_type,
        payload.export_format,
        correlation_id,
        extra={
            "correlation_id": correlation_id,
            "report_type": payload.report_type,
            "export_format": payload.export_format,
        },
    )

    export_task_id = str(uuid.uuid4())

    try:
        from src.workers.export_tasks import generate_export_report_task

        async_res = generate_export_report_task.apply_async(
            kwargs={
                "report_type": payload.report_type,
                "export_format": payload.export_format,
                "date_filters": filters,
                "correlation_id": correlation_id,
            },
            task_id=export_task_id,
        )
        if hasattr(async_res, "id") and async_res.id:
            export_task_id = str(async_res.id)
    except Exception as exc:
        logger.warning(
            "Celery export task submission fallback/exception: %s",
            exc,
            extra={"correlation_id": correlation_id},
        )

    return ExportTaskResponseSchema(task_id=export_task_id)


# Support Analytics Pydantic Schemas & Endpoints [FR-8, NFR-2, NFR-3, NFR-4]


class TimeWindowSchema(BaseModel):
    """Pydantic schema representing query time window boundaries [FR-8]."""

    start_time: Optional[str] = Field(None, description="ISO timestamp string of start time boundary")
    end_time: Optional[str] = Field(None, description="ISO timestamp string of end time boundary")


class PriorityBreakdownSchema(BaseModel):
    """Pydantic schema representing ticket counts broken down by priority [FR-8]."""

    P1: int = Field(0, ge=0, description="Count of P1 priority tickets")
    P2: int = Field(0, ge=0, description="Count of P2 priority tickets")
    P3: int = Field(0, ge=0, description="Count of P3 priority tickets")


class SupportAnalyticsResponseSchema(BaseModel):
    """Pydantic schema representing operational support analytics metrics payload [FR-8, NFR-2, NFR-3]."""

    time_window: TimeWindowSchema = Field(..., description="Query time window boundaries")
    total_tickets: int = Field(..., ge=0, description="Total support tickets count in window")
    open_tickets_count: int = Field(..., ge=0, description="Count of open or pending support tickets")
    resolved_tickets_count: int = Field(..., ge=0, description="Count of resolved or closed support tickets")
    average_response_time_minutes: float = Field(..., ge=0.0, description="Average first response time in minutes")
    mean_time_to_resolution_hours: float = Field(..., ge=0.0, description="Mean time to resolution (MTTR) in hours")
    sla_breach_rate_percent: float = Field(..., ge=0.0, le=100.0, description="SLA breach rate percentage (0 to 100)")
    average_csat_score: float = Field(..., ge=0.0, le=5.0, description="Average CSAT score (0.0 to 5.0)")
    csat_response_count: int = Field(..., ge=0, description="Count of submitted CSAT ratings")
    priority_breakdown: PriorityBreakdownSchema = Field(..., description="Breakdown of ticket counts by priority")


@analytics_router.get(
    "/support",
    response_model=SupportAnalyticsResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Get Operational Support Analytics Metrics",
    description=(
        "Aggregate operational support metrics including average response time, "
        "mean time to resolution (MTTR), SLA breach rates, and CSAT scores over a specified "
        "time window [FR-8, NFR-2, NFR-3, NFR-4]."
    ),
)
def get_support_analytics_metrics(
    start_time: Optional[datetime] = Query(None, description="Optional start boundary timestamp filter"),
    end_time: Optional[datetime] = Query(None, description="Optional end boundary timestamp filter"),
    priority: Optional[str] = Query(None, description="Optional ticket priority filter ('P1', 'P2', 'P3')"),
    client_id: Optional[str] = Query(None, description="Optional client UUID filter"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve aggregated operational support metrics for specified filter parameters [FR-8, NFR-2, NFR-3, NFR-4]."""
    correlation_id = get_correlation_id() or str(uuid.uuid4())

    clean_priority: Optional[str] = None
    if priority is not None and str(priority).strip():
        clean_priority = str(priority).upper().strip()
        if clean_priority not in ("P1", "P2", "P3"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid priority filter '{priority}'. Must be 'P1', 'P2', or 'P3'.",
            )

    clean_client_id: Optional[str] = None
    if client_id is not None and str(client_id).strip():
        clean_client_id = str(client_id).strip()
        try:
            uuid.UUID(clean_client_id)
        except ValueError:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Invalid client_id UUID format '{client_id}'.",
            )

    if start_time is not None and end_time is not None and start_time > end_time:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="start_time cannot be after end_time",
        )

    logger.info(
        "Fetching operational support analytics metrics",
        extra={
            "correlation_id": correlation_id,
            "start_time": start_time.isoformat() if start_time else None,
            "end_time": end_time.isoformat() if end_time else None,
            "priority": clean_priority,
            "client_id": clean_client_id,
        },
    )

    ticket_service = TicketService(db)
    metrics = ticket_service.get_support_analytics(
        start_time=start_time,
        end_time=end_time,
        priority=clean_priority,
        client_id=clean_client_id,
    )

    return metrics


@support_analytics_router.get(
    "/analytics",
    response_model=SupportAnalyticsResponseSchema,
    status_code=status.HTTP_200_OK,
    summary="Get Operational Support Analytics Metrics (Alias)",
    description=(
        "Aggregate operational support metrics including average response time, "
        "mean time to resolution (MTTR), SLA breach rates, and CSAT scores over a specified "
        "time window [FR-8, NFR-2, NFR-3, NFR-4]."
    ),
)
def get_support_analytics_metrics_alias(
    start_time: Optional[datetime] = Query(None, description="Optional start boundary timestamp filter"),
    end_time: Optional[datetime] = Query(None, description="Optional end boundary timestamp filter"),
    priority: Optional[str] = Query(None, description="Optional ticket priority filter ('P1', 'P2', 'P3')"),
    client_id: Optional[str] = Query(None, description="Optional client UUID filter"),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve aggregated operational support metrics for specified filter parameters [FR-8, NFR-2, NFR-3, NFR-4]."""
    return get_support_analytics_metrics(
        start_time=start_time,
        end_time=end_time,
        priority=priority,
        client_id=client_id,
        db=db,
    )
