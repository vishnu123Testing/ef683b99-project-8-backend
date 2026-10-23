"""API v1 router registry for FDE Platform Access Control System Foundation."""

from fastapi import APIRouter

from src.api.alerting import alerting_router
from src.api.analytics import analytics_router, support_analytics_router
from src.api.contracts import contracts_router, signatures_router
from src.api.dashboard import router as dashboard_router
from src.api.deals import deals_router
from src.api.financial import financial_router
from src.api.knowledge import knowledge_router
from src.api.marketing import marketing_router
from src.api.tickets import tickets_router
from src.api.v1.endpoints.auth import router as auth_router
from src.api.v1.endpoints.navigation import router as navigation_router

api_v1_router = APIRouter(prefix="/api/v1")
api_v1_router.include_router(auth_router)
api_v1_router.include_router(navigation_router)
api_v1_router.include_router(dashboard_router)
api_v1_router.include_router(contracts_router)
api_v1_router.include_router(signatures_router)
api_v1_router.include_router(deals_router)
api_v1_router.include_router(financial_router)
api_v1_router.include_router(alerting_router)
api_v1_router.include_router(analytics_router)
api_v1_router.include_router(support_analytics_router)
api_v1_router.include_router(marketing_router)
api_v1_router.include_router(tickets_router)
api_v1_router.include_router(knowledge_router)


@api_v1_router.get("/health", tags=["Health"])
def api_v1_health() -> dict[str, str]:
    """Health status endpoint for API v1."""
    return {"status": "ok", "version": "v1"}

