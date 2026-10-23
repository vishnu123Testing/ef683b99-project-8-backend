"""FastAPI REST endpoints for multi-channel alerting and notification hub [FR-5, FR-6, FR-7, FR-9, FR-10].

Provides HTTP endpoints for custom trigger rules management, optimistic-locked user
notification preferences, and in-app alert viewing.
"""

from datetime import datetime, timezone
from decimal import Decimal
import logging
from typing import List, Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from src.api.deps import RequireRole, get_current_access_context
from src.models.alerting import AlertTrigger, NotificationLog, NotificationPreference
from src.models.database import get_db
from src.services.access_control.rbac import UserAccessContext

logger = logging.getLogger(__name__)

alerting_router = APIRouter(prefix="/notifications", tags=["Alerting & Notifications"])
router = alerting_router


# Pydantic Schemas for Request & Response validation [FR-10]


class TriggerCreateSchema(BaseModel):
    """Schema for creating a custom financial alert trigger rule [FR-6, FR-10]."""

    rule_name: str = Field(
        ...,
        min_length=1,
        max_length=100,
        description="Unique name for the alert trigger rule",
    )
    financial_criteria: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Financial criteria identifier (e.g., past_due_balance_exceeded)",
    )
    threshold_amount: float = Field(
        ...,
        ge=0.0,
        description="Monetary threshold amount triggering the alert",
    )
    urgency: str = Field(
        "Medium",
        description="Alert urgency level (Low, Medium, High)",
    )
    is_active: bool = Field(
        True,
        description="Whether the alert trigger rule is active",
    )


class TriggerResponseSchema(BaseModel):
    """Response schema for a custom alert trigger rule [FR-6, FR-10]."""

    trigger_id: uuid.UUID = Field(..., description="Unique UUID for trigger rule")
    rule_name: str = Field(..., description="Unique name of trigger rule")
    financial_criteria: str = Field(..., description="Financial criteria identifier")
    threshold_amount: float = Field(..., description="Monetary threshold amount")
    urgency: str = Field(..., description="Urgency level (Low, Medium, High)")
    is_active: bool = Field(..., description="Active status indicator")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last updated timestamp")

    model_config = ConfigDict(from_attributes=True)


class PreferenceUpdateSchema(BaseModel):
    """Schema for updating role-based notification channel preferences with optimistic locking [FR-7, FR-10]."""

    role_name: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Target role name (e.g., financial_admin, ClientViewer)",
    )
    event_type: str = Field(
        ...,
        min_length=1,
        max_length=50,
        description="Event type identifier (e.g., invoice_overdue, quote_expired)",
    )
    urgency: str = Field(
        "Medium",
        description="Urgency level (Low, Medium, High)",
    )
    channels_json: List[str] = Field(
        ...,
        description="List of enabled notification channels (e.g., ['InApp', 'Email', 'SMS'])",
    )
    version: int = Field(
        1,
        ge=1,
        description="Expected version integer for optimistic concurrency control",
    )


class PreferenceResponseSchema(BaseModel):
    """Response schema for a notification preference record [FR-7, FR-10]."""

    preference_id: uuid.UUID = Field(..., description="Unique UUID for preference record")
    role_name: str = Field(..., description="Target role name")
    event_type: str = Field(..., description="Event type identifier")
    urgency: str = Field(..., description="Urgency level (Low, Medium, High)")
    channels_json: List[str] = Field(..., description="Configured notification channels list")
    version: int = Field(..., description="Record version integer for optimistic locking")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last updated timestamp")

    model_config = ConfigDict(from_attributes=True)


class InAppNotificationResponseSchema(BaseModel):
    """Response schema for in-app notifications in the centralized notification hub [FR-5, FR-10]."""

    notification_id: uuid.UUID = Field(..., description="Unique notification log UUID")
    recipient_id: uuid.UUID = Field(..., description="Recipient user UUID")
    trigger_id: Optional[uuid.UUID] = Field(None, description="Linked trigger rule UUID if applicable")
    urgency: str = Field(..., description="Notification urgency level (Low, Medium, High)")
    channel: str = Field("InApp", description="Notification delivery channel")
    delivery_status: str = Field(..., description="Delivery status (Sent, Failed, Fallback)")
    dispatched_at: datetime = Field(..., description="Dispatch timestamp")
    correlation_id: str = Field(..., description="Correlation ID for audit tracking")
    message: Optional[str] = Field(None, description="Notification message summary")
    read_status: bool = Field(False, description="Whether notification has been read")

    model_config = ConfigDict(from_attributes=True)


# Route Handlers


@alerting_router.get(
    "/in-app",
    response_model=List[InAppNotificationResponseSchema],
    summary="List in-app system alerts for the notification hub",
    description="Retrieve in-app system alerts filtered optionally by unread status [FR-5, FR-9, FR-10].",
)
def list_in_app_notifications(
    unread_only: bool = Query(False, description="Filter unread notifications only"),
    db: Session = Depends(get_db),
    access_context: UserAccessContext = Depends(get_current_access_context),
) -> List[InAppNotificationResponseSchema]:
    """Retrieve in-app notifications from NotificationLog records [FR-5, FR-9, FR-10]."""
    query = db.query(NotificationLog).filter(NotificationLog.channel == "InApp")

    # Scope recipient to current user if not admin
    if access_context.user_id and not access_context.has_role("admin:write"):
        query = query.filter(NotificationLog.recipient_id == access_context.user_id)

    if unread_only:
        # Sent status represents unread notifications
        query = query.filter(NotificationLog.delivery_status == "Sent")

    logs = query.order_by(NotificationLog.dispatched_at.desc()).all()

    results = []
    for log in logs:
        rule_name = log.trigger.rule_name if log.trigger else None
        summary_msg = (
            f"Alert trigger '{rule_name}' evaluated ({log.urgency} urgency)"
            if rule_name
            else f"In-App Notification ({log.urgency} urgency)"
        )
        results.append(
            InAppNotificationResponseSchema(
                notification_id=log.notification_id,
                recipient_id=log.recipient_id,
                trigger_id=log.trigger_id,
                urgency=log.urgency,
                channel=log.channel,
                delivery_status=log.delivery_status,
                dispatched_at=log.dispatched_at,
                correlation_id=log.correlation_id,
                message=summary_msg,
                read_status=(log.delivery_status != "Sent"),
            )
        )
    return results


@alerting_router.get(
    "/triggers",
    response_model=List[TriggerResponseSchema],
    summary="List custom financial alert trigger rules",
    description="Retrieve all configured custom financial alert trigger rules [FR-6, FR-9].",
)
def list_trigger_rules(
    db: Session = Depends(get_db),
    _access_context: UserAccessContext = Depends(RequireRole(["admin:write", "financial:read"])),
) -> List[TriggerResponseSchema]:
    """List all custom alert trigger rules [FR-6, FR-9]."""
    triggers = db.query(AlertTrigger).order_by(AlertTrigger.created_at.desc()).all()
    return [TriggerResponseSchema.model_validate(t) for t in triggers]


@alerting_router.post(
    "/triggers",
    response_model=TriggerResponseSchema,
    status_code=status.HTTP_201_CREATED,
    summary="Create a custom financial alert trigger rule",
    description="Create a new custom financial alert trigger rule [FR-6, FR-9, FR-10].",
)
def create_trigger_rule(
    payload: TriggerCreateSchema,
    db: Session = Depends(get_db),
    _access_context: UserAccessContext = Depends(RequireRole("admin:write")),
) -> TriggerResponseSchema:
    """Create a new custom alert trigger rule [FR-6, FR-9, FR-10]."""
    existing = (
        db.query(AlertTrigger)
        .filter(AlertTrigger.rule_name == payload.rule_name)
        .first()
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Trigger rule with name '{payload.rule_name}' already exists",
        )

    trigger = AlertTrigger(
        rule_name=payload.rule_name,
        financial_criteria=payload.financial_criteria,
        threshold_amount=Decimal(str(payload.threshold_amount)),
        urgency=payload.urgency,
        is_active=payload.is_active,
    )
    db.add(trigger)
    db.commit()
    db.refresh(trigger)
    return TriggerResponseSchema.model_validate(trigger)


@alerting_router.delete(
    "/triggers/{trigger_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    summary="Delete a custom financial alert trigger rule",
    description="Delete an existing alert trigger rule by its UUID [FR-6, FR-9, FR-10].",
)
def delete_trigger_rule(
    trigger_id: uuid.UUID,
    db: Session = Depends(get_db),
    _access_context: UserAccessContext = Depends(RequireRole("admin:write")),
) -> Response:
    """Delete an existing alert trigger rule [FR-6, FR-9, FR-10]."""
    trigger = (
        db.query(AlertTrigger)
        .filter(AlertTrigger.trigger_id == trigger_id)
        .first()
    )
    if not trigger:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Alert trigger with ID {trigger_id} not found",
        )

    db.delete(trigger)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@alerting_router.get(
    "/preferences",
    response_model=List[PreferenceResponseSchema],
    summary="List role-based notification preferences",
    description="Retrieve notification preferences filtered optionally by role_name or event_type [FR-7, FR-9].",
)
def list_notification_preferences(
    role_name: Optional[str] = Query(None, description="Filter preferences by role name"),
    event_type: Optional[str] = Query(None, description="Filter preferences by event type"),
    db: Session = Depends(get_db),
    _access_context: UserAccessContext = Depends(RequireRole(["admin:write", "financial:read"])),
) -> List[PreferenceResponseSchema]:
    """List role-based notification preferences [FR-7, FR-9]."""
    query = db.query(NotificationPreference)
    if role_name:
        query = query.filter(NotificationPreference.role_name == role_name)
    if event_type:
        query = query.filter(NotificationPreference.event_type == event_type)
    preferences = query.all()
    return [PreferenceResponseSchema.model_validate(p) for p in preferences]


@alerting_router.put(
    "/preferences",
    response_model=PreferenceResponseSchema,
    summary="Update role-based notification preferences with optimistic locking",
    description="Update or create notification channel preferences using optimistic locking version check [FR-7, FR-9, FR-10].",
)
def update_notification_preference(
    payload: PreferenceUpdateSchema,
    db: Session = Depends(get_db),
    _access_context: UserAccessContext = Depends(RequireRole("admin:write")),
) -> PreferenceResponseSchema:
    """Update role-based notification channel preferences with optimistic locking [FR-7, FR-9, FR-10]."""
    existing_pref = (
        db.query(NotificationPreference)
        .filter(
            NotificationPreference.role_name == payload.role_name,
            NotificationPreference.event_type == payload.event_type,
            NotificationPreference.urgency == payload.urgency,
        )
        .first()
    )

    if existing_pref is None:
        # Create new preference record starting at version 1
        new_pref = NotificationPreference(
            role_name=payload.role_name,
            event_type=payload.event_type,
            urgency=payload.urgency,
            channels_json=payload.channels_json,
            version=1,
        )
        db.add(new_pref)
        db.commit()
        db.refresh(new_pref)
        return PreferenceResponseSchema.model_validate(new_pref)

    # Optimistic locking check: verify supplied version matches DB record version
    if existing_pref.version != payload.version:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"Version conflict: Current preference version is {existing_pref.version}, "
                f"supplied version was {payload.version}. Please re-fetch and retry."
            ),
        )

    # Increment version upon modification
    existing_pref.channels_json = payload.channels_json
    existing_pref.version = existing_pref.version + 1
    existing_pref.updated_at = datetime.now(timezone.utc)

    db.commit()
    db.refresh(existing_pref)
    return PreferenceResponseSchema.model_validate(existing_pref)
