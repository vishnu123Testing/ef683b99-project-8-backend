"""Navigation endpoints returning authorized system routes based on caller RBAC permissions."""

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from src.api.deps import get_current_access_context
from src.models.database import get_db
from src.services.access_control.audit import AuditService
from src.services.access_control.rbac import (
    UserAccessContext,
    check_route_access,
)

router = APIRouter(prefix="/navigation", tags=["Navigation"])
audit_service = AuditService()

DEFAULT_NAVIGATION_ITEMS: list[dict[str, str]] = [
    {
        "title": "Dashboard",
        "path": "/dashboard",
        "icon_id": "dashboard",
    },
    {
        "title": "Performance",
        "path": "/performance",
        "icon_id": "performance",
    },
    {
        "title": "Contracts",
        "path": "/contracts",
        "icon_id": "contracts",
    },
    {
        "title": "Support",
        "path": "/support",
        "icon_id": "support",
    },
    {
        "title": "Financials",
        "path": "/financials",
        "icon_id": "financials",
    },
    {
        "title": "Knowledge Base",
        "path": "/knowledge",
        "icon_id": "knowledge",
    },
]


class NavigationItemResponse(BaseModel):
    """Schema for an individual navigation route item."""

    title: str = Field(..., description="Display title for navigation link")
    path: str = Field(..., description="Target route path")
    is_accessible: bool = Field(..., description="Whether route is accessible to caller")
    icon_id: str = Field(..., description="Identifier for navigation icon")


class NavigationMenuResponse(BaseModel):
    """Response payload schema for authorized navigation menu items."""

    items: list[NavigationItemResponse] = Field(
        default_factory=list,
        description="List of authorized navigation menu items",
    )


@router.get(
    "/menu",
    response_model=NavigationMenuResponse,
    status_code=status.HTTP_200_OK,
    summary="Get authorized navigation routes for current user",
)
def get_navigation_menu(
    request: Request,
    context: UserAccessContext = Depends(get_current_access_context),
    db: Session = Depends(get_db),
) -> NavigationMenuResponse:
    """Evaluate caller roles and return list of authorized navigation routes.

    Checks caller's assigned server-side roles against default domain routes.
    Emits a structured JSON audit event upon evaluation.
    Requires bearer token authentication (401 Unauthorized if unauthenticated).
    """
    client_ip = request.client.host if (request and request.client) else None

    authorized_items: list[NavigationItemResponse] = []
    for item in DEFAULT_NAVIGATION_ITEMS:
        path = item["path"]
        is_accessible = check_route_access(context.roles, path)
        if is_accessible:
            authorized_items.append(
                NavigationItemResponse(
                    title=item["title"],
                    path=path,
                    is_accessible=True,
                    icon_id=item["icon_id"],
                )
            )

    # Emit non-blocking audit event for reading navigation menu
    audit_service.log_event(
        action="READ_NAVIGATION_MENU",
        outcome_status="SUCCESS",
        user_id=context.user_id,
        assigned_role=",".join(context.roles) if context.roles else None,
        tenant_id=context.tenant_id,
        resource_id="/api/v1/navigation/menu",
        client_ip=client_ip,
        db=db,
    )

    return NavigationMenuResponse(items=authorized_items)
