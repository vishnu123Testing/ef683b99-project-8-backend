"""Unit tests for dashboard status classification and aggregator service (FR-3, FR-4, NFR-2)."""

import logging
import uuid
import pytest

from src.lib.logging import get_correlation_id, reset_correlation_id, set_correlation_id
from src.services.dashboard.aggregator import (
    DashboardAggregationService,
    aggregate_domain_connectors,
    calculate_percentage_change,
    classify_metric_status,
    collate_domain_metrics,
    enrich_metric_with_baseline,
    evaluate_overall_status,
    format_metric_value,
    log_metric_classification,
    lookup_baseline_metric,
    process_metric_classification,
)
from src.services.dashboard.domain_ports import (
    AggregatedSummaryResult,
    AggregationStatus,
    DomainConnectorPort,
    DomainMetricsResult,
    DomainStatus,
    MetricResult,
    MetricStatus,
    MetricUnit,
)


def test_classify_metric_status_valid():
    """Verify classification of non-zero numerical values as VALID."""
    assert classify_metric_status(12500.0) == MetricStatus.VALID
    assert classify_metric_status(1) == MetricStatus.VALID
    assert classify_metric_status(-5.5) == MetricStatus.VALID


def test_classify_metric_status_zero():
    """Verify classification of zero values as ZERO (FR-4)."""
    assert classify_metric_status(0) == MetricStatus.ZERO
    assert classify_metric_status(0.0) == MetricStatus.ZERO


def test_classify_metric_status_unavailable():
    """Verify classification of None or errored values as UNAVAILABLE (FR-3)."""
    assert classify_metric_status(None) == MetricStatus.UNAVAILABLE
    assert classify_metric_status(None, error_detail="Timeout connecting to service") == MetricStatus.UNAVAILABLE
    assert classify_metric_status("not_a_number") == MetricStatus.UNAVAILABLE  # type: ignore


def test_format_metric_value_valid():
    """Verify formatting of valid metrics across currency, count, and percentage units."""
    assert format_metric_value(12500, MetricUnit.CURRENCY, MetricStatus.VALID) == "$12,500"
    assert format_metric_value(12500.50, MetricUnit.CURRENCY, MetricStatus.VALID) == "$12,500.50"
    assert format_metric_value(1250, MetricUnit.COUNT, MetricStatus.VALID) == "1,250"
    assert format_metric_value(15.5, MetricUnit.PERCENTAGE, MetricStatus.VALID) == "15.5%"


def test_format_metric_value_zero():
    """Verify explicit zero metric formatting ("0", "$0") (FR-4)."""
    assert format_metric_value(0, MetricUnit.CURRENCY, MetricStatus.ZERO) == "$0"
    assert format_metric_value(0.0, MetricUnit.CURRENCY, MetricStatus.ZERO) == "$0"
    assert format_metric_value(0, MetricUnit.COUNT, MetricStatus.ZERO) == "0"
    assert format_metric_value(0.0, MetricUnit.PERCENTAGE, MetricStatus.ZERO) == "0.0%"


def test_format_metric_value_unavailable():
    """Verify formatting of unavailable metrics as "Unavailable" (FR-3)."""
    assert format_metric_value(None, MetricUnit.CURRENCY, MetricStatus.UNAVAILABLE) == "Unavailable"
    assert format_metric_value(None, MetricUnit.COUNT, MetricStatus.UNAVAILABLE) == "Unavailable"


def test_process_metric_classification():
    """Verify complete metric processing and DTO construction."""
    result = process_metric_classification(
        domain="financials",
        metric_key="recurring_revenue",
        display_name="Recurring Revenue",
        raw_value=0.0,
        unit=MetricUnit.CURRENCY,
    )
    assert result.domain == "financials"
    assert result.metric_key == "recurring_revenue"
    assert result.status == MetricStatus.ZERO
    assert result.formatted_value == "$0"
    assert result.raw_value == 0.0


def test_log_metric_classification_with_correlation(caplog):
    """Verify structured logging with correlation context (NFR-2)."""
    test_cid = str(uuid.uuid4())
    token = set_correlation_id(test_cid)

    try:
        with caplog.at_level(logging.INFO):
            metric = MetricResult(
                domain="support",
                metric_key="open_tickets",
                display_name="Open Tickets",
                raw_value=0.0,
                formatted_value="0",
                status=MetricStatus.ZERO,
                unit=MetricUnit.COUNT,
            )
            log_metric_classification(metric)

            assert "Metric status classified as ZERO for support:open_tickets" in caplog.text
            assert get_correlation_id() == test_cid
    finally:
        reset_correlation_id(token)


def test_dashboard_aggregation_service_class():
    """Verify DashboardAggregationService class methods."""
    service = DashboardAggregationService()

    status = service.classify_metric_status(100.0)
    assert status == MetricStatus.VALID

    formatted = service.format_metric_value(0.0, MetricUnit.CURRENCY, status=MetricStatus.ZERO)
    assert formatted == "$0"

    metric = service.process_metric(
        domain="lead_gen",
        metric_key="lead_volume",
        display_name="Lead Volume",
        raw_value=None,
        unit=MetricUnit.COUNT,
        error_detail="503 Service Unavailable",
    )
    assert metric.status == MetricStatus.UNAVAILABLE
    assert metric.formatted_value == "Unavailable"
    assert metric.error_detail == "503 Service Unavailable"

    # Evaluate existing MetricResult
    input_result = MetricResult(
        domain="sales_pipeline",
        metric_key="deal_volume",
        display_name="Deal Volume",
        raw_value=0.0,
        unit=MetricUnit.CURRENCY,
    )
    evaluated = service.evaluate_metric_result(input_result)
    assert evaluated.status == MetricStatus.ZERO
    assert evaluated.formatted_value == "$0"


def test_calculate_percentage_change_valid():
    """Verify percentage change calculation with valid numerical values (FR-2)."""
    from src.services.dashboard.aggregator import calculate_percentage_change

    # Positive change: (12500 - 10000) / 10000 * 100 = +25.0%
    assert calculate_percentage_change(12500, 10000) == 25.0

    # Negative change: (7500 - 10000) / 10000 * 100 = -25.0%
    assert calculate_percentage_change(7500, 10000) == -25.0

    # Zero current value: (0 - 100) / 100 * 100 = -100.0%
    assert calculate_percentage_change(0, 100) == -100.0

    # Fractional values
    assert calculate_percentage_change(4.2, 3.5) == 20.0


def test_calculate_percentage_change_edge_cases():
    """Verify percentage change calculation with zero baselines and None values (FR-2)."""
    from src.services.dashboard.aggregator import calculate_percentage_change

    # None inputs
    assert calculate_percentage_change(None, 100) is None
    assert calculate_percentage_change(100, None) is None
    assert calculate_percentage_change(None, None) is None

    # Zero baseline
    assert calculate_percentage_change(0, 0) == 0.0
    assert calculate_percentage_change(100, 0) is None


def test_lookup_baseline_metric_and_enrichment():
    """Verify looking up baseline metric from DB session and enriching MetricResult (FR-2)."""
    from datetime import datetime, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from src.models.database import Base
    from src.models.dashboard import create_metric_snapshot
    from src.services.dashboard.aggregator import (
        enrich_metric_with_baseline,
        lookup_baseline_metric,
        process_metric_classification,
    )

    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()

    try:
        # Create baseline snapshot in DB
        create_metric_snapshot(
            db=session,
            domain_name="financials",
            metric_key="recurring_revenue",
            current_value=10000.0,
            baseline_value=9000.0,
            recorded_at=datetime(2026, 10, 1, 0, 0, 0, tzinfo=timezone.utc),
        )

        # Lookup baseline directly
        base_val = lookup_baseline_metric(session, "financials", "recurring_revenue")
        assert base_val == 10000.0

        # Process metric with DB session lookup and automatic percentage change calculation
        res = process_metric_classification(
            domain="financials",
            metric_key="recurring_revenue",
            display_name="Recurring Revenue",
            raw_value=12500.0,
            unit=MetricUnit.CURRENCY,
            db=session,
        )

        assert res.baseline_value == 10000.0
        assert res.percentage_change == 25.0
        assert res.formatted_value == "$12,500"

        # Enrich explicit MetricResult
        raw_metric = MetricResult(
            domain="financials",
            metric_key="recurring_revenue",
            display_name="Recurring Revenue",
            raw_value=12500.0,
            unit=MetricUnit.CURRENCY,
        )
        enriched = enrich_metric_with_baseline(raw_metric, db=session)
        assert enriched.baseline_value == 10000.0
        assert enriched.percentage_change == 25.0

    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


def test_evaluate_overall_status_complete():
    """Verify overall status evaluation returns COMPLETE when all domain metrics succeed (FR-1)."""
    m1 = MetricResult(domain="lead_gen", metric_key="volume", display_name="Volume", raw_value=100.0, status=MetricStatus.VALID)
    m2 = MetricResult(domain="financials", metric_key="revenue", display_name="Revenue", raw_value=0.0, status=MetricStatus.ZERO)

    dr1 = DomainMetricsResult(domain="lead_gen", status=DomainStatus.COMPLETE, metrics=[m1])
    dr2 = DomainMetricsResult(domain="financials", status=DomainStatus.COMPLETE, metrics=[m2])

    status = evaluate_overall_status([dr1, dr2])
    assert status == AggregationStatus.COMPLETE


def test_evaluate_overall_status_partial():
    """Verify overall status evaluation returns PARTIAL when a domain fails or has UNAVAILABLE metrics (FR-3)."""
    m1 = MetricResult(domain="lead_gen", metric_key="volume", display_name="Volume", raw_value=100.0, status=MetricStatus.VALID)
    m2 = MetricResult(domain="support", metric_key="tickets", display_name="Tickets", raw_value=None, status=MetricStatus.UNAVAILABLE)

    dr1 = DomainMetricsResult(domain="lead_gen", status=DomainStatus.COMPLETE, metrics=[m1])
    dr2 = DomainMetricsResult(domain="support", status=DomainStatus.UNAVAILABLE, metrics=[m2])

    status = evaluate_overall_status([dr1, dr2])
    assert status == AggregationStatus.PARTIAL


def test_evaluate_overall_status_failed():
    """Verify overall status evaluation returns FAILED when all domain metrics are UNAVAILABLE (FR-3)."""
    m1 = MetricResult(domain="lead_gen", metric_key="volume", display_name="Volume", raw_value=None, status=MetricStatus.UNAVAILABLE)
    m2 = MetricResult(domain="support", metric_key="tickets", display_name="Tickets", raw_value=None, status=MetricStatus.UNAVAILABLE)

    dr1 = DomainMetricsResult(domain="lead_gen", status=DomainStatus.UNAVAILABLE, metrics=[m1])
    dr2 = DomainMetricsResult(domain="support", status=DomainStatus.UNAVAILABLE, metrics=[m2])

    assert evaluate_overall_status([dr1, dr2]) == AggregationStatus.FAILED
    assert evaluate_overall_status([]) == AggregationStatus.FAILED


def test_collate_domain_metrics():
    """Verify collating domain metrics into a unified AggregatedSummaryResult DTO (FR-1, FR-3)."""
    m1 = MetricResult(domain="sales_pipeline", metric_key="deal_volume", display_name="Deal Volume", raw_value=1200.0, unit=MetricUnit.CURRENCY, status=MetricStatus.VALID)
    m2 = MetricResult(domain="support", metric_key="open_tickets", display_name="Open Tickets", raw_value=None, unit=MetricUnit.COUNT, status=MetricStatus.UNAVAILABLE, error_detail="Timeout")

    dr1 = DomainMetricsResult(domain="sales_pipeline", status=DomainStatus.COMPLETE, metrics=[m1])
    dr2 = DomainMetricsResult(domain="support", status=DomainStatus.UNAVAILABLE, metrics=[m2])

    summary = collate_domain_metrics([dr1, dr2])

    assert summary.status == AggregationStatus.PARTIAL
    assert summary.generated_at is not None
    assert len(summary.metrics) == 2
    assert summary.metrics[0].domain == "sales_pipeline"
    assert summary.metrics[0].formatted_value == "$1,200"
    assert summary.metrics[1].domain == "support"
    assert summary.metrics[1].formatted_value == "Unavailable"


def test_aggregate_domain_connectors():
    """Verify executing metric aggregation across domain connector port adapters (FR-1, FR-3)."""

    class MockSuccessConnector(DomainConnectorPort):
        @property
        def domain_id(self) -> str:
            return "lead_gen"

        def fetch_metrics(self, timeframe: str = "30d") -> DomainMetricsResult:
            m = MetricResult(
                domain="lead_gen",
                metric_key="leads",
                display_name="Leads",
                raw_value=50.0,
                unit=MetricUnit.COUNT,
                status=MetricStatus.VALID,
            )
            return DomainMetricsResult(domain="lead_gen", status=DomainStatus.COMPLETE, metrics=[m])

    class MockFailingConnector(DomainConnectorPort):
        @property
        def domain_id(self) -> str:
            return "support"

        def fetch_metrics(self, timeframe: str = "30d") -> DomainMetricsResult:
            raise RuntimeError("503 Service Unavailable")

    connectors = [MockSuccessConnector(), MockFailingConnector()]
    summary = aggregate_domain_connectors(connectors=connectors, timeframe="30d")

    assert summary.status == AggregationStatus.PARTIAL
    assert len(summary.metrics) == 1
    assert summary.metrics[0].domain == "lead_gen"
    assert summary.metrics[0].formatted_value == "50"


def test_dashboard_aggregation_service_collation_methods():
    """Verify DashboardAggregationService class wrapper methods for collation and status evaluation."""
    service = DashboardAggregationService()

    m1 = MetricResult(domain="financials", metric_key="mrr", display_name="MRR", raw_value=10000.0, unit=MetricUnit.CURRENCY, status=MetricStatus.VALID)
    dr1 = DomainMetricsResult(domain="financials", status=DomainStatus.COMPLETE, metrics=[m1])

    status = service.evaluate_overall_status([dr1])
    assert status == AggregationStatus.COMPLETE

    summary = service.collate_domain_metrics([dr1])
    assert summary.status == AggregationStatus.COMPLETE
    assert len(summary.metrics) == 1
    assert summary.metrics[0].formatted_value == "$10,000"

