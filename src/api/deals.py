"""FastAPI routes for Visual Deal Pipeline management, stage transitions, action items, stakeholder management, and AI forecasting [FR-001, FR-002, FR-003, FR-004, FR-005, FR-006, FR-007, FR-008, FR-009, NFR-003]."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from src.models.database import get_db
from src.services.action_items.service import (
    ActionItemDealNotFoundError,
    ActionItemNotFoundError,
    ActionItemService,
    ActionItemServiceError,
    InvalidActionItemError,
)
from src.services.deals.service import (
    DealNotFoundError,
    DealService,
    InvalidDealStageError,
)
from src.services.forecasting.service import (
    ForecastDealNotFoundError,
    ForecastingService,
)
from src.services.stakeholders.service import (
    InvalidActivityError,
    InvalidStakeholderError,
    StakeholderDealNotFoundError,
    StakeholderNotFoundError,
    StakeholderService,
)

deals_router = APIRouter(prefix="/deals", tags=["Deals"])
router = deals_router


class CreateDealSchema(BaseModel):
    """Schema for creating a new deal record [FR-001, FR-002, NFR-003]."""

    title: str = Field(..., min_length=1, max_length=255, description="Title of the deal")
    deal_value: float = Field(..., ge=0.0, description="Monetary value of the deal")
    milestone_stage: str = Field(..., min_length=1, max_length=100, description="Initial milestone stage")
    win_probability: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Optional win probability override (0.0 to 1.0)"
    )

    @field_validator("title", "milestone_stage", mode="before")
    @classmethod
    def sanitize_string_fields(cls, v: Any) -> Any:
        """Sanitize string inputs by stripping leading/trailing whitespace [NFR-003]."""
        if isinstance(v, str):
            v_clean = v.strip()
            if not v_clean:
                raise ValueError("String fields cannot be blank or empty.")
            return v_clean
        return v


class UpdateDealStageSchema(BaseModel):
    """Schema for updating a deal's milestone stage [FR-003, NFR-003]."""

    milestone_stage: str = Field(..., min_length=1, max_length=100, description="Target milestone stage name")
    win_probability: Optional[float] = Field(
        None, ge=0.0, le=1.0, description="Optional win probability override (0.0 to 1.0)"
    )

    @field_validator("milestone_stage", mode="before")
    @classmethod
    def sanitize_stage(cls, v: Any) -> Any:
        """Sanitize stage input by stripping whitespace and validating non-empty [NFR-003]."""
        if isinstance(v, str):
            v_clean = v.strip()
            if not v_clean:
                raise ValueError("Milestone stage name cannot be blank or empty.")
            return v_clean
        return v


class CreateActionItemSchema(BaseModel):
    """Schema for creating a new action item [FR-004, NFR-003]."""

    title: str = Field(..., min_length=1, max_length=255, description="Title of the action item")
    assigned_owner_id: str = Field(..., min_length=1, max_length=255, description="Owner ID or username")
    target_due_date: datetime = Field(..., description="Target completion deadline")
    sla_threshold_hours: Optional[int] = Field(None, gt=0, description="Optional custom SLA threshold in hours")

    @field_validator("title", "assigned_owner_id", mode="before")
    @classmethod
    def sanitize_string_fields(cls, v: Any) -> Any:
        """Sanitize string inputs by stripping whitespace and rejecting blank strings [NFR-003]."""
        if isinstance(v, str):
            v_clean = v.strip()
            if not v_clean:
                raise ValueError("String fields cannot be blank or empty.")
            return v_clean
        return v


class CompleteActionItemSchema(BaseModel):
    """Schema for marking an action item complete [FR-005, NFR-003]."""

    completed_on: Optional[datetime] = Field(None, description="Optional completion timestamp")


class CreateStakeholderSchema(BaseModel):
    """Schema for mapping a new stakeholder contact to a deal [FR-006, NFR-003]."""

    full_name: str = Field(..., min_length=1, max_length=255, description="Full name of stakeholder")
    email: str = Field(..., min_length=3, max_length=255, description="Email address of stakeholder")
    org_role: str = Field(..., min_length=1, max_length=255, description="Organizational role or title")
    influence_level: str = Field(..., min_length=1, max_length=50, description="Influence level ('High', 'Medium', 'Low')")

    @field_validator("full_name", "org_role", "influence_level", mode="before")
    @classmethod
    def sanitize_string_fields(cls, v: Any) -> Any:
        """Sanitize string inputs by stripping leading/trailing whitespace [NFR-003]."""
        if isinstance(v, str):
            v_clean = v.strip()
            if not v_clean:
                raise ValueError("String fields cannot be blank or empty.")
            return v_clean
        return v

    @field_validator("email", mode="before")
    @classmethod
    def validate_and_sanitize_email(cls, v: Any) -> Any:
        """Sanitize email string and validate email format [NFR-003]."""
        if isinstance(v, str):
            v_clean = v.strip().lower()
            if not v_clean or "@" not in v_clean:
                raise ValueError("A valid email address with '@' must be provided.")
            return v_clean
        return v


class CreateStakeholderActivitySchema(BaseModel):
    """Schema for logging an interaction activity for a stakeholder [FR-007, NFR-003]."""

    activity_type: str = Field(..., min_length=1, max_length=100, description="Activity type ('Email', 'Meeting', 'Call', 'Note')")
    summary: str = Field(..., min_length=1, max_length=2000, description="Summary or description of interaction")
    occurred_on: Optional[datetime] = Field(None, description="Optional timestamp when interaction occurred")

    @field_validator("activity_type", "summary", mode="before")
    @classmethod
    def sanitize_string_fields(cls, v: Any) -> Any:
        """Sanitize string inputs by stripping whitespace and rejecting blank strings [NFR-003]."""
        if isinstance(v, str):
            v_clean = v.strip()
            if not v_clean:
                raise ValueError("String fields cannot be blank or empty.")
            return v_clean
        return v


@deals_router.get(
    "/pipeline",
    response_model=List[Dict[str, Any]],
    status_code=status.HTTP_200_OK,
    summary="Get Deal Pipeline Board",
    description="Retrieve deals grouped or filterable by milestone stage for the interactive visual board [FR-001, FR-002].",
)
def get_pipeline_deals(
    stage: Optional[str] = Query(None, description="Optional milestone stage filter"),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Retrieve all deals or filter deals by stage for the pipeline board."""
    service = DealService(db=db)
    clean_stage = stage.strip() if stage and isinstance(stage, str) else None
    return service.get_pipeline_deals(stage=clean_stage)


@deals_router.post(
    "",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Create New Deal",
    description="Create a new deal record in the visual pipeline board [FR-001, FR-002, NFR-003].",
)
def create_deal(
    payload: CreateDealSchema,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Create a new deal with validated inputs."""
    service = DealService(db=db)
    return service.create_deal(
        title=payload.title,
        deal_value=payload.deal_value,
        milestone_stage=payload.milestone_stage,
        win_probability=payload.win_probability,
    )


@deals_router.get(
    "/{deal_id}",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Get Deal Summary",
    description="Retrieve comprehensive summary for a single deal by ID [FR-001, FR-002].",
)
def get_deal_summary(
    deal_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve deal detail summary or 404 if not found."""
    service = DealService(db=db)
    try:
        return service.get_deal_summary(deal_id)
    except DealNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )


@deals_router.patch(
    "/{deal_id}/stage",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Update Deal Milestone Stage",
    description="Update deal milestone stage progression and trigger recalculations [FR-003, NFR-003].",
)
def update_deal_stage(
    deal_id: str,
    payload: UpdateDealStageSchema,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Update milestone stage for a deal."""
    service = DealService(db=db)
    try:
        return service.update_deal_stage(
            deal_id=deal_id,
            new_stage=payload.milestone_stage,
            win_probability=payload.win_probability,
        )
    except DealNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )
    except InvalidDealStageError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )


@deals_router.get(
    "/{deal_id}/action-items",
    response_model=List[Dict[str, Any]],
    status_code=status.HTTP_200_OK,
    summary="List Action Items for Deal",
    description="Retrieve all action items associated with a specific deal [FR-004].",
)
def list_action_items(
    deal_id: str,
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """List all action items for a deal or return 404 if deal not found."""
    deal_service = DealService(db=db)
    try:
        deal_service.get_deal_summary(deal_id)
    except DealNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )
    service = ActionItemService(db=db)
    return service.list_action_items_for_deal(deal_id)


@deals_router.post(
    "/{deal_id}/action-items",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Create Action Item for Deal",
    description="Assign a new action item with designated owner and target due date to a deal [FR-004, NFR-003].",
)
def create_action_item(
    deal_id: str,
    payload: CreateActionItemSchema,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Create and assign a new action item to a deal."""
    service = ActionItemService(db=db)
    try:
        return service.create_action_item(
            deal_id=deal_id,
            title=payload.title,
            assigned_owner_id=payload.assigned_owner_id,
            target_due_date=payload.target_due_date,
            sla_threshold_hours=payload.sla_threshold_hours,
        )
    except ActionItemDealNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )
    except InvalidActionItemError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )


@deals_router.patch(
    "/{deal_id}/action-items/{item_id}/complete",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Complete Action Item",
    description="Mark an action item as completed and clear its escalation state [FR-005].",
)
def complete_action_item(
    deal_id: str,
    item_id: str,
    payload: Optional[CompleteActionItemSchema] = None,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Mark an action item as completed and clear escalation flags."""
    service = ActionItemService(db=db)
    try:
        completed_on = payload.completed_on if payload else None
        return service.complete_action_item(
            item_id=item_id,
            completed_on=completed_on,
        )
    except ActionItemNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )
    except ActionItemServiceError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )


@deals_router.delete(
    "/{deal_id}/action-items/{item_id}",
    response_model=None,
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete Action Item",
    description="Delete an action item record from a deal [FR-004].",
)
def delete_action_item(
    deal_id: str,
    item_id: str,
    db: Session = Depends(get_db),
) -> Response:
    """Delete an action item record."""
    service = ActionItemService(db=db)
    try:
        service.delete_action_item(item_id=item_id)
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    except ActionItemNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )


@deals_router.get(
    "/{deal_id}/stakeholders",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Get Stakeholders and Activity Feed for Deal",
    description=(
        "Retrieve mapped stakeholders and chronological interaction feed for a deal [FR-006, FR-007]."
    ),
)
def get_deal_stakeholders(
    deal_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve mapped stakeholders and chronological interaction feed for a deal."""
    service = StakeholderService(db=db)
    try:
        return service.get_stakeholders_and_activities(deal_id)
    except StakeholderDealNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )


@deals_router.post(
    "/{deal_id}/stakeholders",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Map Stakeholder to Deal",
    description=(
        "Map a new stakeholder contact to a deal capturing contact details, role, and influence level [FR-006, NFR-003]."
    ),
)
def create_stakeholder(
    deal_id: str,
    payload: CreateStakeholderSchema,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Map a new stakeholder contact to a deal."""
    service = StakeholderService(db=db)
    try:
        return service.create_stakeholder(
            deal_id=deal_id,
            full_name=payload.full_name,
            email=payload.email,
            org_role=payload.org_role,
            influence_level=payload.influence_level,
        )
    except StakeholderDealNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )
    except InvalidStakeholderError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )


@deals_router.post(
    "/{deal_id}/stakeholders/{stakeholder_id}/activities",
    response_model=Dict[str, Any],
    status_code=status.HTTP_201_CREATED,
    summary="Log Stakeholder Activity for Deal",
    description=(
        "Record an interaction event associated with a mapped stakeholder for a deal [FR-007, NFR-003]."
    ),
)
def log_stakeholder_activity(
    deal_id: str,
    stakeholder_id: str,
    payload: CreateStakeholderActivitySchema,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Record an interaction event associated with a mapped stakeholder."""
    service = StakeholderService(db=db)
    try:
        return service.log_activity(
            stakeholder_id=stakeholder_id,
            deal_id=deal_id,
            activity_type=payload.activity_type,
            summary=payload.summary,
            occurred_on=payload.occurred_on,
        )
    except (StakeholderNotFoundError, StakeholderDealNotFoundError) as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )
    except InvalidActivityError as err:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(err),
        )


@deals_router.get(
    "/{deal_id}/forecast",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Get AI Deal Completion Forecast",
    description=(
        "Retrieve AI-driven estimated completion timeline and percentage-based confidence score for a deal [FR-008, FR-009]."
    ),
)
def get_deal_forecast(
    deal_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Retrieve current AI completion forecast and confidence score for a deal or return 404 if deal not found."""
    service = ForecastingService(db=db)
    try:
        return service.get_forecast(deal_id)
    except ForecastDealNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )


@deals_router.post(
    "/{deal_id}/forecast/recalculate",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Recalculate AI Completion Forecast for Deal",
    description=(
        "Trigger a recalculation of the AI completion timeline forecast and confidence score for a deal [FR-008, FR-009, FR-010]."
    ),
)
def recalculate_deal_forecast(
    deal_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Recalculate AI completion forecast and confidence score for a deal."""
    service = ForecastingService(db=db)
    try:
        return service.recalculate_forecast(deal_id=deal_id, trigger_event="MANUAL_REQUEST")
    except ForecastDealNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )


@deals_router.post(
    "/{deal_id}/forecast",
    response_model=Dict[str, Any],
    status_code=status.HTTP_200_OK,
    summary="Recalculate AI Completion Forecast for Deal (POST Alias)",
    description=(
        "Trigger a recalculation of the AI completion timeline forecast and confidence score for a deal [FR-008, FR-009, FR-010]."
    ),
)
def recalculate_deal_forecast_alias(
    deal_id: str,
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Recalculate AI completion forecast and confidence score for a deal (POST alias)."""
    service = ForecastingService(db=db)
    try:
        return service.recalculate_forecast(deal_id=deal_id, trigger_event="MANUAL_REQUEST")
    except ForecastDealNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )


@deals_router.get(
    "/{deal_id}/forecast/logs",
    response_model=List[Dict[str, Any]],
    status_code=status.HTTP_200_OK,
    summary="Get AI Forecast Evaluation Logs for Deal",
    description=(
        "Retrieve historical AI forecast evaluation and recalculation audit log records for a deal [FR-010, NFR-002]."
    ),
)
def get_forecast_evaluation_logs(
    deal_id: str,
    limit: int = Query(50, ge=1, le=500, description="Maximum log records to return"),
    db: Session = Depends(get_db),
) -> List[Dict[str, Any]]:
    """Retrieve historical evaluation audit logs for a deal's AI completion forecasts."""
    service = ForecastingService(db=db)
    try:
        return service.get_evaluation_logs(deal_id=deal_id, limit=limit)
    except ForecastDealNotFoundError as err:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(err),
        )
