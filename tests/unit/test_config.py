"""Unit tests for backend.src.lib.config application settings."""

from src.lib.config import Settings, get_settings, settings


def test_default_settings():
    """Test that default settings values are initialized correctly."""
    app_settings = Settings()
    assert app_settings.app_name == "FDE Platform Access Control System Foundation"
    assert app_settings.environment == "development"
    assert app_settings.debug is False
    assert app_settings.log_level == "INFO"
    assert "app.db" in app_settings.database_url
    assert app_settings.algorithm == "HS256"
    assert app_settings.access_token_expire_minutes == 60
    assert app_settings.dashboard_connector_timeout_seconds == 5
    assert app_settings.dashboard_connector_retries == 3
    assert app_settings.dashboard_refresh_polling_interval_seconds == 30
    assert app_settings.dashboard_connector_timeout == 5
    assert app_settings.dashboard_refresh_polling_interval == 30
    assert app_settings.storage_root_dir == "./storage/contracts"
    assert app_settings.contract_storage_root == "./storage/contracts"
    assert app_settings.storage_root == "./storage/contracts"
    assert app_settings.max_file_size_mb == 50
    assert app_settings.max_upload_size_mb == 50
    assert app_settings.max_upload_size_bytes == 52428800
    assert app_settings.renewal_alert_thresholds_days == [90, 60, 30]
    assert app_settings.renewal_alert_thresholds == [90, 60, 30]
    assert app_settings.alert_notification_enabled is True
    # Action Item SLA & Escalation Settings
    assert app_settings.action_item_sla_threshold_hours == 24
    assert app_settings.action_item_sla_hours == 24
    assert app_settings.escalation_alert_notification_enabled is True
    assert app_settings.escalation_recipient_roles == ["sales_manager", "admin"]
    # AI Forecasting Settings
    assert app_settings.forecast_confidence_threshold_pct == 50.0
    assert app_settings.forecast_min_history_records == 3
    assert app_settings.forecast_min_records == 3
    assert app_settings.forecast_recalculation_async is True
    assert app_settings.crm_connector_timeout_seconds == 5
    assert app_settings.forecast_provider_timeout_seconds == 5
    # Celery Worker Schedule & Queue Settings
    assert app_settings.escalation_scan_interval_seconds == 300
    assert app_settings.escalation_scan_interval == 300
    assert app_settings.escalation_worker_queue == "escalations"
    assert app_settings.forecast_recalculation_queue == "forecasting"
    # Marketing Analytics & Export Settings
    assert app_settings.export_output_dir == "./storage/exports"
    assert app_settings.export_storage_root == "./storage/exports"
    assert app_settings.export_dir == "./storage/exports"
    assert app_settings.funnel_bottleneck_threshold_percent == 30.0
    assert app_settings.funnel_default_dropoff_threshold_percent == 30.0
    assert app_settings.funnel_bottleneck_threshold == 30.0
    assert app_settings.funnel_dropoff_threshold == 30.0
    assert app_settings.campaign_max_comparison_limit == 5
    assert app_settings.export_worker_queue == "exports"
    # Support Operations & SLA Management Settings
    assert app_settings.sla_p1_response_hours == 1
    assert app_settings.sla_p1_resolution_hours == 4
    assert app_settings.sla_p2_response_hours == 4
    assert app_settings.sla_p2_resolution_hours == 24
    assert app_settings.sla_p3_response_hours == 24
    assert app_settings.sla_p3_resolution_hours == 72
    assert app_settings.sla_warning_threshold_percent == 80.0
    assert app_settings.attachment_max_size_mb == 25
    assert app_settings.attachment_max_bytes == 25 * 1024 * 1024
    assert "image/jpeg" in app_settings.allowed_attachment_mime_types
    assert ".pdf" in app_settings.allowed_attachment_extensions
    assert app_settings.support_attachment_storage_dir == "./storage/attachments"
    assert app_settings.support_storage_dir == "./storage/attachments"
    assert app_settings.sla_scan_interval_seconds == 60
    assert app_settings.sla_worker_queue == "sla_management"
    # Financial Transparency & Multi-Channel Alerting Settings
    assert app_settings.smtp_host is None
    assert app_settings.smtp_port == 587
    assert app_settings.smtp_user is None
    assert app_settings.smtp_password is None
    assert app_settings.smtp_from_email == "noreply@example.com"
    assert app_settings.smtp_use_tls is True
    assert app_settings.sms_gateway_url is None
    assert app_settings.sms_api_key is None
    assert app_settings.sms_sender_id is None
    assert app_settings.alert_suppression_window_minutes == 15
    assert app_settings.alert_suppression_window_seconds == 900
    assert app_settings.alert_suppression_minutes == 15
    assert app_settings.alert_suppression_seconds == 900
    assert app_settings.alert_worker_queue == "notifications"
    # Knowledge Repository & Onboarding Resources Settings
    assert app_settings.knowledge_storage_dir == "./storage/knowledge"
    assert app_settings.knowledge_storage_root == "./storage/knowledge"
    assert app_settings.knowledge_dir == "./storage/knowledge"
    assert app_settings.knowledge_max_attachment_size_mb == 50
    assert app_settings.knowledge_max_bytes == 50 * 1024 * 1024
    assert app_settings.cdn_provider_url is None
    assert app_settings.cdn_provider_api_key is None
    assert app_settings.cdn_provider_secret_key is None
    assert app_settings.cdn_provider_timeout_seconds == 5
    assert app_settings.cdn_timeout == 5
    assert app_settings.video_streaming_token_ttl_seconds == 3600


def test_environment_override(monkeypatch):
    """Test that environment variables override default settings."""
    monkeypatch.setenv("LOG_LEVEL", "DEBUG")
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("SECRET_KEY", "custom-production-secret")
    monkeypatch.setenv("DASHBOARD_CONNECTOR_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("DASHBOARD_CONNECTOR_RETRIES", "5")
    monkeypatch.setenv("DASHBOARD_REFRESH_POLLING_INTERVAL_SECONDS", "60")
    monkeypatch.setenv("STORAGE_ROOT_DIR", "/var/app/storage")
    monkeypatch.setenv("CONTRACT_STORAGE_ROOT", "/var/app/contracts")
    monkeypatch.setenv("MAX_FILE_SIZE_MB", "100")
    monkeypatch.setenv("MAX_UPLOAD_SIZE_BYTES", "104857600")
    monkeypatch.setenv("RENEWAL_ALERT_THRESHOLDS_DAYS", "[90, 60, 30, 15]")
    monkeypatch.setenv("ALERT_NOTIFICATION_ENABLED", "false")
    monkeypatch.setenv("ACTION_ITEM_SLA_THRESHOLD_HOURS", "48")
    monkeypatch.setenv("ESCALATION_ALERT_NOTIFICATION_ENABLED", "false")
    monkeypatch.setenv("ESCALATION_RECIPIENT_ROLES", '["director", "admin"]')
    monkeypatch.setenv("FORECAST_CONFIDENCE_THRESHOLD_PCT", "60.0")
    monkeypatch.setenv("FORECAST_MIN_HISTORY_RECORDS", "5")
    monkeypatch.setenv("FORECAST_RECALCULATION_ASYNC", "false")
    monkeypatch.setenv("CRM_CONNECTOR_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("FORECAST_PROVIDER_TIMEOUT_SECONDS", "12")
    monkeypatch.setenv("ESCALATION_SCAN_INTERVAL_SECONDS", "600")
    monkeypatch.setenv("ESCALATION_WORKER_QUEUE", "custom_escalations")
    monkeypatch.setenv("FORECAST_RECALCULATION_QUEUE", "custom_forecasting")
    monkeypatch.setenv("EXPORT_OUTPUT_DIR", "/var/app/exports")
    monkeypatch.setenv("EXPORT_STORAGE_ROOT", "/var/app/exports")
    monkeypatch.setenv("FUNNEL_BOTTLENECK_THRESHOLD_PERCENT", "25.0")
    monkeypatch.setenv("FUNNEL_DEFAULT_DROPOFF_THRESHOLD_PERCENT", "25.0")
    monkeypatch.setenv("CAMPAIGN_MAX_COMPARISON_LIMIT", "10")
    monkeypatch.setenv("EXPORT_WORKER_QUEUE", "custom_exports")
    monkeypatch.setenv("SLA_P1_RESPONSE_HOURS", "2")
    monkeypatch.setenv("SLA_P1_RESOLUTION_HOURS", "6")
    monkeypatch.setenv("ATTACHMENT_MAX_SIZE_MB", "50")
    monkeypatch.setenv("SUPPORT_ATTACHMENT_STORAGE_DIR", "/var/app/attachments")
    monkeypatch.setenv("SLA_SCAN_INTERVAL_SECONDS", "120")
    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_PORT", "465")
    monkeypatch.setenv("SMTP_USER", "smtp_user")
    monkeypatch.setenv("SMTP_PASSWORD", "smtp_pass")
    monkeypatch.setenv("SMTP_FROM_EMAIL", "alerts@example.com")
    monkeypatch.setenv("SMTP_USE_TLS", "false")
    monkeypatch.setenv("SMS_GATEWAY_URL", "https://sms.example.com/api")
    monkeypatch.setenv("SMS_API_KEY", "secret_sms_key")
    monkeypatch.setenv("SMS_SENDER_ID", "FDEAlerts")
    monkeypatch.setenv("ALERT_SUPPRESSION_WINDOW_MINUTES", "30")
    monkeypatch.setenv("ALERT_SUPPRESSION_WINDOW_SECONDS", "1800")
    monkeypatch.setenv("ALERT_WORKER_QUEUE", "custom_notifications")
    monkeypatch.setenv("KNOWLEDGE_STORAGE_DIR", "/var/app/knowledge")
    monkeypatch.setenv("KNOWLEDGE_STORAGE_ROOT", "/var/app/knowledge")
    monkeypatch.setenv("KNOWLEDGE_MAX_ATTACHMENT_SIZE_MB", "100")
    monkeypatch.setenv("CDN_PROVIDER_URL", "https://cdn.example.com")
    monkeypatch.setenv("CDN_PROVIDER_API_KEY", "cdn_key_123")
    monkeypatch.setenv("CDN_PROVIDER_SECRET_KEY", "cdn_secret_456")
    monkeypatch.setenv("CDN_PROVIDER_TIMEOUT_SECONDS", "10")
    monkeypatch.setenv("VIDEO_STREAMING_TOKEN_TTL_SECONDS", "7200")

    custom_settings = Settings()
    assert custom_settings.log_level == "DEBUG"
    assert custom_settings.environment == "production"
    assert custom_settings.secret_key == "custom-production-secret"
    assert custom_settings.dashboard_connector_timeout_seconds == 10
    assert custom_settings.dashboard_connector_retries == 5
    assert custom_settings.dashboard_refresh_polling_interval_seconds == 60
    assert custom_settings.dashboard_connector_timeout == 10
    assert custom_settings.dashboard_refresh_polling_interval == 60
    assert custom_settings.storage_root_dir == "/var/app/storage"
    assert custom_settings.contract_storage_root == "/var/app/contracts"
    assert custom_settings.storage_root == "/var/app/storage"
    assert custom_settings.max_file_size_mb == 100
    assert custom_settings.max_upload_size_mb == 100
    assert custom_settings.max_upload_size_bytes == 104857600
    assert custom_settings.renewal_alert_thresholds_days == [90, 60, 30, 15]
    assert custom_settings.renewal_alert_thresholds == [90, 60, 30, 15]
    assert custom_settings.alert_notification_enabled is False
    assert custom_settings.action_item_sla_threshold_hours == 48
    assert custom_settings.action_item_sla_hours == 48
    assert custom_settings.escalation_alert_notification_enabled is False
    assert custom_settings.escalation_recipient_roles == ["director", "admin"]
    assert custom_settings.forecast_confidence_threshold_pct == 60.0
    assert custom_settings.forecast_min_history_records == 5
    assert custom_settings.forecast_min_records == 5
    assert custom_settings.forecast_recalculation_async is False
    assert custom_settings.crm_connector_timeout_seconds == 10
    assert custom_settings.forecast_provider_timeout_seconds == 12
    assert custom_settings.escalation_scan_interval_seconds == 600
    assert custom_settings.escalation_scan_interval == 600
    assert custom_settings.escalation_worker_queue == "custom_escalations"
    assert custom_settings.forecast_recalculation_queue == "custom_forecasting"
    assert custom_settings.export_output_dir == "/var/app/exports"
    assert custom_settings.export_storage_root == "/var/app/exports"
    assert custom_settings.export_dir == "/var/app/exports"
    assert custom_settings.funnel_bottleneck_threshold_percent == 25.0
    assert custom_settings.funnel_default_dropoff_threshold_percent == 25.0
    assert custom_settings.funnel_bottleneck_threshold == 25.0
    assert custom_settings.funnel_dropoff_threshold == 25.0
    assert custom_settings.campaign_max_comparison_limit == 10
    assert custom_settings.export_worker_queue == "custom_exports"
    assert custom_settings.sla_p1_response_hours == 2
    assert custom_settings.sla_p1_resolution_hours == 6
    assert custom_settings.attachment_max_size_mb == 50
    assert custom_settings.attachment_max_bytes == 50 * 1024 * 1024
    assert custom_settings.support_attachment_storage_dir == "/var/app/attachments"
    assert custom_settings.support_storage_dir == "/var/app/attachments"
    assert custom_settings.sla_scan_interval_seconds == 120
    assert custom_settings.smtp_host == "smtp.example.com"
    assert custom_settings.smtp_port == 465
    assert custom_settings.smtp_user == "smtp_user"
    assert custom_settings.smtp_password == "smtp_pass"
    assert custom_settings.smtp_from_email == "alerts@example.com"
    assert custom_settings.smtp_use_tls is False
    assert custom_settings.sms_gateway_url == "https://sms.example.com/api"
    assert custom_settings.sms_api_key == "secret_sms_key"
    assert custom_settings.sms_sender_id == "FDEAlerts"
    assert custom_settings.alert_suppression_window_minutes == 30
    assert custom_settings.alert_suppression_window_seconds == 1800
    assert custom_settings.alert_suppression_minutes == 30
    assert custom_settings.alert_suppression_seconds == 1800
    assert custom_settings.alert_worker_queue == "custom_notifications"
    assert custom_settings.knowledge_storage_dir == "/var/app/knowledge"
    assert custom_settings.knowledge_storage_root == "/var/app/knowledge"
    assert custom_settings.knowledge_dir == "/var/app/knowledge"
    assert custom_settings.knowledge_max_attachment_size_mb == 100
    assert custom_settings.knowledge_max_bytes == 100 * 1024 * 1024
    assert custom_settings.cdn_provider_url == "https://cdn.example.com"
    assert custom_settings.cdn_provider_api_key == "cdn_key_123"
    assert custom_settings.cdn_provider_secret_key == "cdn_secret_456"
    assert custom_settings.cdn_provider_timeout_seconds == 10
    assert custom_settings.cdn_timeout == 10
    assert custom_settings.video_streaming_token_ttl_seconds == 7200


def test_get_settings_cached():
    """Test that get_settings returns a cached instance."""
    s1 = get_settings()
    s2 = get_settings()
    assert s1 is s2
    assert s1 is settings
