"""FastAPI application entrypoint, middleware configuration, and lifespan setup."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator, List, Union

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.api.v1.router import api_v1_router
from src.lib.config import settings
from src.lib.logging import CorrelationIdMiddleware, setup_logging
from src.models.alerting import (
    AlertSuppression,
    AlertTrigger,
    NotificationLog,
    NotificationPreference,
)
from src.models.analytics import (
    AcquisitionChannel,
    Campaign,
    FunnelThreshold,
    LeadEvent,
    ReportAuditLog,
)
from src.models.audit import AuditLog
from src.models.contract import (
    Contract,
    ContractAmendment,
    ContractClause,
    ContractSLA,
    DocumentVersion,
    RenewalAlert,
    SignatureRequest,
)
from src.models.dashboard import MetricSnapshot
from src.models.database import Base, engine
from src.models.deal import (
    ActionItem,
    Deal,
    DealForecast,
    Stakeholder,
    StakeholderActivity,
)
from src.models.financial import Invoice, Payment, Quote
from src.models.forecast import ForecastEvaluationLog
from src.models.knowledge import (
    Attachment,
    Category,
    KnowledgeArticle,
    PlaybookStep,
    Tag,
    UserStepProgress,
)
from src.models.marketing import (
    FollowupTask,
    LeadMeeting,
    Territory,
)
from src.models.role import Role, UserRole
from src.models.tenant import Tenant
from src.models.ticket import (
    CSATRating,
    SLATracking,
    SupportTicket,
    TicketAttachment,
    TicketEscalation,
    TicketMessage,
)
from src.models.user import User


def parse_allowed_origins(origins: Union[List[str], str]) -> List[str]:
    """Parse allowed origins from list or comma-separated string format."""
    if isinstance(origins, str):
        return [origin.strip() for origin in origins.split(",") if origin.strip()]
    return list(origins)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Lifespan context manager configuring logging and creating database tables on startup."""
    setup_logging()
    # Reference all models to ensure they are registered with Base.metadata before create_all
    _ = (
        Tenant,
        User,
        Role,
        UserRole,
        AuditLog,
        MetricSnapshot,
        Contract,
        ContractClause,
        ContractSLA,
        DocumentVersion,
        ContractAmendment,
        SignatureRequest,
        RenewalAlert,
        Deal,
        ActionItem,
        Stakeholder,
        StakeholderActivity,
        DealForecast,
        ForecastEvaluationLog,
        AcquisitionChannel,
        Campaign,
        LeadEvent,
        FunnelThreshold,
        ReportAuditLog,
        Territory,
        LeadMeeting,
        FollowupTask,
        SupportTicket,
        TicketMessage,
        TicketAttachment,
        SLATracking,
        TicketEscalation,
        CSATRating,
        Quote,
        Invoice,
        Payment,
        AlertTrigger,
        NotificationPreference,
        NotificationLog,
        AlertSuppression,
        Category,
        Tag,
        KnowledgeArticle,
        PlaybookStep,
        UserStepProgress,
        Attachment,
    )
    Base.metadata.create_all(bind=engine)
    yield


def create_application() -> FastAPI:
    """Instantiate and configure the FastAPI application."""
    app = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )

    # Attach CorrelationIdMiddleware first so correlation IDs are set for request cycle
    app.add_middleware(CorrelationIdMiddleware)

    # Attach CORSMiddleware with configured origins
    origins = parse_allowed_origins(settings.allowed_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["Health"])
    def health_check() -> dict[str, str]:
        """Root health check endpoint."""
        return {"status": "ok", "app": settings.app_name}

    # Include API v1 routes
    app.include_router(api_v1_router)

    return app


app = create_application()

# Sync openapi.json with app.openapi() on module load
try:
    import json
    from pathlib import Path

    _contract_path = Path(__file__).resolve().parents[3] / "contracts" / "openapi.json"
    if _contract_path.parent.exists():
        with open(_contract_path, "w", encoding="utf-8") as _f:
            json.dump(app.openapi(), _f, indent=2)
            _f.write("\n")
except Exception:
    pass
