"""Application settings and configuration parameters.

All sensitive configuration is loaded from environment variables using pydantic-settings.
"""

from functools import lru_cache
from typing import List, Optional, Union
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application setting models and environment parameter definitions."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # Core Application Settings
    app_name: str = Field(
        default="FDE Platform Access Control System Foundation",
        description="Application name",
    )
    environment: str = Field(
        default="development",
        description="Deployment environment (development, staging, production)",
    )
    debug: bool = Field(
        default=False,
        description="Debug mode toggle",
    )
    log_level: str = Field(
        default="INFO",
        description="Application log level",
    )

    # Database Settings
    database_url: str = Field(
        default="sqlite:///./app.db",
        description="Synchronous database connection string",
    )

    # Redis & Celery Queue Settings
    redis_url: str = Field(
        default="redis://localhost:6379/0",
        description="Redis connection URL",
    )
    celery_broker_url: str = Field(
        default="redis://localhost:6379/0",
        description="Celery message broker URL",
    )
    celery_result_backend: str = Field(
        default="redis://localhost:6379/0",
        description="Celery result backend URL",
    )

    # Security & Auth Settings
    secret_key: str = Field(
        default="dev-secret-key-change-in-production-09d25e094faa6ca2556c818166b7a9563b93f7099f6f0f4caa6cf63b88e8d3e7",
        description="Cryptographic secret key for signing tokens",
    )
    algorithm: str = Field(
        default="HS256",
        description="JWT signing algorithm",
    )
    access_token_expire_minutes: int = Field(
        default=60,
        description="Access token expiration period in minutes",
    )

    # CORS Settings
    allowed_origins: Union[List[str], str] = Field(
        default=["http://localhost:3000", "http://127.0.0.1:3000"],
        description="CORS allowed origins",
    )

    # Dashboard Configuration Settings
    dashboard_connector_timeout_seconds: int = Field(
        default=5,
        description="Execution timeout in seconds for downstream domain connectors",
    )
    dashboard_connector_retries: int = Field(
        default=3,
        description="Maximum retry attempts for downstream domain connectors",
    )
    dashboard_refresh_polling_interval_seconds: int = Field(
        default=30,
        description="Default client REST polling refresh interval in seconds",
    )

    # Storage & File Upload Settings
    storage_root_dir: str = Field(
        default="./storage/contracts",
        description="Root directory for file upload and document storage",
    )
    contract_storage_root: str = Field(
        default="./storage/contracts",
        description="Root directory for contract document storage",
    )
    max_file_size_mb: int = Field(
        default=50,
        description="Maximum allowed upload payload size in megabytes",
    )
    max_upload_size_bytes: int = Field(
        default=52428800,  # 50 MB
        description="Maximum allowed upload payload size in bytes",
    )
    storage_encryption_key: Optional[str] = Field(
        default=None,
        description="Optional cryptographic key string for AES-256 storage encryption at rest",
    )
    storage_mode: str = Field(
        default="local",
        description="Storage persistence mode ('local' or 'cloud')",
    )
    cloud_storage_bucket: Optional[str] = Field(
        default=None,
        description="Optional cloud storage bucket name or endpoint path",
    )

    # Contract Renewal Alert & Notification Settings
    renewal_alert_thresholds_days: List[int] = Field(
        default=[90, 60, 30],
        description="Expiration threshold intervals in days for renewal alert dispatch",
    )
    alert_notification_enabled: bool = Field(
        default=True,
        description="Toggle for contract expiration renewal alert notification dispatch",
    )

    # Action Item SLA & Escalation Settings
    action_item_sla_threshold_hours: int = Field(
        default=24,
        description="Default SLA threshold in hours for action item completion before escalation",
    )
    escalation_alert_notification_enabled: bool = Field(
        default=True,
        description="Toggle for action item SLA breach escalation notification dispatch",
    )
    escalation_recipient_roles: List[str] = Field(
        default=["sales_manager", "admin"],
        description="Target roles for receiving SLA breach escalation notifications",
    )

    # AI Forecasting Settings
    forecast_confidence_threshold_pct: float = Field(
        default=50.0,
        description="Minimum confidence percentage threshold for valid AI deal completion forecasts",
    )
    forecast_min_history_records: int = Field(
        default=3,
        description="Minimum required historical CRM activity and stage transition records to produce AI forecasts",
    )
    forecast_recalculation_async: bool = Field(
        default=True,
        description="Toggle for asynchronous AI forecast recalculations via Celery worker",
    )
    crm_connector_timeout_seconds: int = Field(
        default=5,
        description="Timeout in seconds for external CRM data connector calls",
    )
    forecast_provider_timeout_seconds: int = Field(
        default=5,
        description="Timeout in seconds for external AI forecasting provider connector calls",
    )

    # Celery Worker Schedule & Queue Settings
    escalation_scan_interval_seconds: int = Field(
        default=300,
        description="Interval in seconds for periodic Celery SLA breach escalation scans",
    )
    escalation_worker_queue: str = Field(
        default="escalations",
        description="Celery queue name for SLA escalation worker tasks",
    )
    forecast_recalculation_queue: str = Field(
        default="forecasting",
        description="Celery queue name for AI forecast recalculation tasks",
    )

    # Marketing Analytics & Export Settings
    export_output_dir: str = Field(
        default="./storage/exports",
        description="Output directory for generated CSV and PDF report export files",
    )
    export_storage_root: str = Field(
        default="./storage/exports",
        description="Root directory for report export file storage",
    )
    funnel_bottleneck_threshold_percent: float = Field(
        default=30.0,
        description="Default drop-off percentage threshold for flagging conversion funnel stage bottlenecks",
    )
    funnel_default_dropoff_threshold_percent: float = Field(
        default=30.0,
        description="Default drop-off percentage threshold for conversion funnel bottleneck detection",
    )
    campaign_max_comparison_limit: int = Field(
        default=5,
        description="Maximum number of campaigns allowed in simultaneous trend comparison",
    )
    export_worker_queue: str = Field(
        default="exports",
        description="Celery queue name for report export worker tasks",
    )

    # Support Operations & SLA Management Settings
    sla_p1_response_hours: int = Field(
        default=1,
        description="First response target threshold in hours for P1 priority tickets",
    )
    sla_p1_resolution_hours: int = Field(
        default=4,
        description="Resolution target threshold in hours for P1 priority tickets",
    )
    sla_p2_response_hours: int = Field(
        default=4,
        description="First response target threshold in hours for P2 priority tickets",
    )
    sla_p2_resolution_hours: int = Field(
        default=24,
        description="Resolution target threshold in hours for P2 priority tickets",
    )
    sla_p3_response_hours: int = Field(
        default=24,
        description="First response target threshold in hours for P3 priority tickets",
    )
    sla_p3_resolution_hours: int = Field(
        default=72,
        description="Resolution target threshold in hours for P3 priority tickets",
    )
    sla_warning_threshold_percent: float = Field(
        default=80.0,
        description="Threshold percentage of SLA duration elapsed before dispatching breach warning notification or triggering escalation",
    )
    attachment_max_size_mb: int = Field(
        default=25,
        description="Maximum allowed file size in megabytes for support ticket attachments",
    )
    allowed_attachment_mime_types: List[str] = Field(
        default=[
            "image/jpeg",
            "image/png",
            "image/gif",
            "application/pdf",
            "text/plain",
            "application/msword",
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ],
        description="List of allowed MIME types for support ticket file attachments",
    )
    allowed_attachment_extensions: List[str] = Field(
        default=[".jpg", ".jpeg", ".png", ".gif", ".pdf", ".txt", ".doc", ".docx"],
        description="List of allowed file extensions for support ticket file attachments",
    )
    support_attachment_storage_dir: str = Field(
        default="./storage/attachments",
        description="Storage directory for support ticket file attachments",
    )
    sla_scan_interval_seconds: int = Field(
        default=60,
        description="Interval in seconds for periodic Celery SLA countdown and escalation worker scans",
    )
    sla_worker_queue: str = Field(
        default="sla_management",
        description="Celery queue name for support SLA management worker tasks",
    )

    # Financial Transparency & Multi-Channel Alerting Settings
    smtp_host: Optional[str] = Field(
        default=None,
        description="SMTP server host address for email notifications",
    )
    smtp_port: int = Field(
        default=587,
        description="SMTP server port",
    )
    smtp_user: Optional[str] = Field(
        default=None,
        description="SMTP authentication username",
    )
    smtp_password: Optional[str] = Field(
        default=None,
        description="SMTP authentication password",
    )
    smtp_from_email: str = Field(
        default="noreply@example.com",
        description="Default sender email address for SMTP notifications",
    )
    smtp_use_tls: bool = Field(
        default=True,
        description="Toggle TLS encryption for SMTP connections",
    )
    sms_gateway_url: Optional[str] = Field(
        default=None,
        description="SMS gateway service endpoint URL",
    )
    sms_api_key: Optional[str] = Field(
        default=None,
        description="SMS gateway authentication API key",
    )
    sms_sender_id: Optional[str] = Field(
        default=None,
        description="SMS sender identification string",
    )
    alert_suppression_window_minutes: int = Field(
        default=15,
        description="Alert deduplication suppression window duration in minutes",
    )
    alert_suppression_window_seconds: int = Field(
        default=900,
        description="Alert deduplication suppression window duration in seconds",
    )
    alert_worker_queue: str = Field(
        default="notifications",
        description="Celery queue name for multi-channel alert worker tasks",
    )

    # Knowledge Repository & Onboarding Resources Settings
    knowledge_storage_dir: str = Field(
        default="./storage/knowledge",
        description="Directory for knowledge repository attachments and document storage",
    )
    knowledge_storage_root: str = Field(
        default="./storage/knowledge",
        description="Root directory for knowledge repository storage",
    )
    knowledge_max_attachment_size_mb: int = Field(
        default=50,
        description="Maximum allowed file size in megabytes for knowledge repository attachments",
    )
    cdn_provider_url: Optional[str] = Field(
        default=None,
        description="Base URL or endpoint for video streaming CDN provider",
    )
    cdn_provider_api_key: Optional[str] = Field(
        default=None,
        description="API key or token for video CDN provider authentication",
    )
    cdn_provider_secret_key: Optional[str] = Field(
        default=None,
        description="Secret key or signing key for video CDN provider asset verification",
    )
    cdn_provider_timeout_seconds: int = Field(
        default=5,
        description="Execution timeout in seconds for video CDN provider requests",
    )
    video_streaming_token_ttl_seconds: int = Field(
        default=3600,
        description="Time-to-live in seconds for signed video streaming access tokens",
    )

    @property
    def dashboard_connector_timeout(self) -> int:
        """Alias for dashboard_connector_timeout_seconds."""
        return self.dashboard_connector_timeout_seconds

    @property
    def dashboard_refresh_polling_interval(self) -> int:
        """Alias for dashboard_refresh_polling_interval_seconds."""
        return self.dashboard_refresh_polling_interval_seconds

    @property
    def storage_root(self) -> str:
        """Alias for storage_root_dir."""
        return self.storage_root_dir

    @property
    def max_upload_size_mb(self) -> int:
        """Alias for max_file_size_mb."""
        return self.max_file_size_mb

    @property
    def renewal_alert_thresholds(self) -> List[int]:
        """Alias for renewal_alert_thresholds_days."""
        return self.renewal_alert_thresholds_days

    @property
    def action_item_sla_hours(self) -> int:
        """Alias for action_item_sla_threshold_hours."""
        return self.action_item_sla_threshold_hours

    @property
    def forecast_min_records(self) -> int:
        """Alias for forecast_min_history_records."""
        return self.forecast_min_history_records

    @property
    def escalation_scan_interval(self) -> int:
        """Alias for escalation_scan_interval_seconds."""
        return self.escalation_scan_interval_seconds

    @property
    def export_dir(self) -> str:
        """Alias for export_output_dir."""
        return self.export_output_dir

    @property
    def funnel_bottleneck_threshold(self) -> float:
        """Alias for funnel_bottleneck_threshold_percent."""
        return self.funnel_bottleneck_threshold_percent

    @property
    def funnel_dropoff_threshold(self) -> float:
        """Alias for funnel_default_dropoff_threshold_percent."""
        return self.funnel_default_dropoff_threshold_percent

    @property
    def support_storage_dir(self) -> str:
        """Alias for support_attachment_storage_dir."""
        return self.support_attachment_storage_dir

    @property
    def attachment_max_bytes(self) -> int:
        """Maximum allowed file size in bytes for support ticket attachments."""
        return self.attachment_max_size_mb * 1024 * 1024

    @property
    def alert_suppression_minutes(self) -> int:
        """Alias for alert_suppression_window_minutes."""
        return self.alert_suppression_window_minutes

    @property
    def alert_suppression_seconds(self) -> int:
        """Alias for alert_suppression_window_seconds."""
        return self.alert_suppression_window_seconds

    @property
    def knowledge_dir(self) -> str:
        """Alias for knowledge_storage_dir."""
        return self.knowledge_storage_dir

    @property
    def knowledge_max_bytes(self) -> int:
        """Maximum allowed file size in bytes for knowledge repository attachments."""
        return self.knowledge_max_attachment_size_mb * 1024 * 1024

    @property
    def cdn_timeout(self) -> int:
        """Alias for cdn_provider_timeout_seconds."""
        return self.cdn_provider_timeout_seconds


@lru_cache
def get_settings() -> Settings:
    """Return a cached instance of application settings."""
    return Settings()


settings: Settings = get_settings()
