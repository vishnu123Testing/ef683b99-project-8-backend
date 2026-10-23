"""Dashboard aggregation and metric classification service.

Provides metric status classification (VALID, ZERO, UNAVAILABLE), value formatting,
baseline metric lookup, percentage change calculation, and structured logging
with correlation context for consolidated executive metrics (FR-2, FR-3, FR-4, NFR-2).
"""

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Union

from sqlalchemy.orm import Session

from src.lib.logging import get_correlation_id, mask_sensitive_data
from src.models.dashboard import get_latest_metric_snapshot
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

logger = logging.getLogger(__name__)


def classify_metric_status(
    raw_value: Optional[Union[float, int]] = None,
    error_detail: Optional[str] = None,
) -> MetricStatus:
    """Classify the operational status of a metric based on raw value and error state.

    Args:
        raw_value: Raw numerical value of the metric; None if unavailable.
        error_detail: Optional error description string if data acquisition failed.

    Returns:
        MetricStatus: VALID, ZERO, or UNAVAILABLE status code.
    """
    if error_detail is not None and raw_value is None:
        return MetricStatus.UNAVAILABLE

    if raw_value is None:
        return MetricStatus.UNAVAILABLE

    try:
        val_float = float(raw_value)
    except (ValueError, TypeError):
        return MetricStatus.UNAVAILABLE

    if val_float == 0.0:
        return MetricStatus.ZERO

    return MetricStatus.VALID


def format_metric_value(
    raw_value: Optional[Union[float, int]],
    unit: MetricUnit = MetricUnit.COUNT,
    status: Optional[MetricStatus] = None,
) -> str:
    """Format raw numerical metric value into human-readable string based on unit and status.

    Args:
        raw_value: Raw numerical value; None if unavailable.
        unit: Measurement unit (CURRENCY, COUNT, PERCENTAGE).
        status: Optional MetricStatus override; if not provided, status is computed.

    Returns:
        str: Formatted string for UI display (e.g. "$12,500", "0", "$0", "Unavailable").
    """
    if status is None:
        status = classify_metric_status(raw_value)

    if status == MetricStatus.UNAVAILABLE or raw_value is None:
        return "Unavailable"

    try:
        val = float(raw_value)
    except (ValueError, TypeError):
        return "Unavailable"

    if status == MetricStatus.ZERO or val == 0.0:
        if unit == MetricUnit.CURRENCY:
            return "$0"
        elif unit == MetricUnit.PERCENTAGE:
            return "0.0%"
        else:
            return "0"

    if unit == MetricUnit.CURRENCY:
        if val.is_integer():
            return f"${int(val):,}"
        else:
            return f"${val:,.2f}"
    elif unit == MetricUnit.PERCENTAGE:
        return f"{val:.1f}%"
    elif unit == MetricUnit.COUNT:
        if val.is_integer():
            return f"{int(val):,}"
        else:
            return f"{val:,}"
    else:
        return str(val)


def calculate_percentage_change(
    current_value: Optional[Union[float, int]],
    baseline_value: Optional[Union[float, int]],
    decimals: int = 1,
) -> Optional[float]:
    """Calculate percentage change of current_value relative to baseline_value (FR-2).

    Args:
        current_value: Current metric numerical value; None if unavailable.
        baseline_value: Baseline historical numerical value; None if unavailable.
        decimals: Number of decimal places to round result (default 1).

    Returns:
        Optional[float]: Calculated percentage change, or None if invalid or unavailable.
    """
    if current_value is None or baseline_value is None:
        return None

    try:
        curr = float(current_value)
        base = float(baseline_value)
    except (ValueError, TypeError):
        return None

    if base == 0.0:
        if curr == 0.0:
            return 0.0
        return None

    pct_change = ((curr - base) / abs(base)) * 100.0
    return round(pct_change, decimals)


def lookup_baseline_metric(
    db: Session,
    domain_name: str,
    metric_key: str,
) -> Optional[float]:
    """Look up baseline metric numerical value from the MetricSnapshot database table (FR-2).

    Args:
        db: Synchronous SQLAlchemy database session.
        domain_name: Operational domain identifier string (e.g. 'financials').
        metric_key: Unique metric identifier key (e.g. 'recurring_revenue').

    Returns:
        Optional[float]: Baseline value if found in snapshot database, else None.
    """
    snapshot = get_latest_metric_snapshot(
        db=db,
        domain_name=domain_name,
        metric_key=metric_key,
    )
    if snapshot is None:
        return None

    if snapshot.baseline_value is not None:
        return snapshot.baseline_value
    if snapshot.current_value is not None:
        return snapshot.current_value

    return None


def enrich_metric_with_baseline(
    metric: MetricResult,
    db: Optional[Session] = None,
    baseline_override: Optional[float] = None,
) -> MetricResult:
    """Enrich a MetricResult DTO with baseline metric lookup and percentage change calculation (FR-2).

    Args:
        metric: Input MetricResult DTO.
        db: Optional synchronous SQLAlchemy database session for DB baseline lookup.
        baseline_override: Optional explicit baseline value to override or fallback to.

    Returns:
        MetricResult: Updated MetricResult DTO with resolved baseline_value and percentage_change.
    """
    baseline_val = metric.baseline_value if metric.baseline_value is not None else baseline_override
    if baseline_val is None and db is not None:
        baseline_val = lookup_baseline_metric(db=db, domain_name=metric.domain, metric_key=metric.metric_key)

    pct_change = metric.percentage_change
    if pct_change is None and metric.raw_value is not None and baseline_val is not None:
        pct_change = calculate_percentage_change(metric.raw_value, baseline_val)

    status = classify_metric_status(raw_value=metric.raw_value, error_detail=metric.error_detail)
    formatted = format_metric_value(raw_value=metric.raw_value, unit=metric.unit, status=status)

    result = MetricResult(
        domain=metric.domain,
        metric_key=metric.metric_key,
        display_name=metric.display_name,
        raw_value=metric.raw_value,
        baseline_value=baseline_val,
        formatted_value=formatted,
        percentage_change=pct_change,
        status=status,
        unit=metric.unit,
        error_detail=metric.error_detail,
    )

    log_metric_classification(result)
    return result


def process_metric_classification(
    domain: str,
    metric_key: str,
    display_name: str,
    raw_value: Optional[Union[float, int]] = None,
    unit: MetricUnit = MetricUnit.COUNT,
    baseline_value: Optional[float] = None,
    percentage_change: Optional[float] = None,
    error_detail: Optional[str] = None,
    db: Optional[Session] = None,
) -> MetricResult:
    """Classify and format a metric into a standardized MetricResult DTO with structured logging.

    Args:
        domain: Domain identifier string.
        metric_key: Unique metric identifier key.
        display_name: Human-readable display label for UI card.
        raw_value: Raw numeric value; None if unavailable.
        unit: Metric measurement unit type.
        baseline_value: Comparative baseline value.
        percentage_change: Calculated percentage change.
        error_detail: Error message if acquisition failed.
        db: Optional database session for baseline lookup.

    Returns:
        MetricResult: Standardized DTO with classified status and formatted value.
    """
    if baseline_value is None and db is not None:
        baseline_value = lookup_baseline_metric(db=db, domain_name=domain, metric_key=metric_key)

    if percentage_change is None and raw_value is not None and baseline_value is not None:
        percentage_change = calculate_percentage_change(current_value=raw_value, baseline_value=baseline_value)

    status = classify_metric_status(raw_value=raw_value, error_detail=error_detail)
    formatted = format_metric_value(raw_value=raw_value, unit=unit, status=status)

    result = MetricResult(
        domain=domain,
        metric_key=metric_key,
        display_name=display_name,
        raw_value=float(raw_value) if raw_value is not None else None,
        baseline_value=baseline_value,
        formatted_value=formatted,
        percentage_change=percentage_change,
        status=status,
        unit=unit,
        error_detail=error_detail,
    )

    log_metric_classification(result)
    return result


def log_metric_classification(metric: MetricResult) -> None:
    """Emit structured JSON log record with correlation metadata for a classified metric.

    Args:
        metric: MetricResult DTO containing status and details.
    """
    cid = get_correlation_id()
    log_data: Dict[str, Any] = {
        "correlation_id": cid,
        "domain": metric.domain,
        "metric_key": metric.metric_key,
        "display_name": metric.display_name,
        "status": metric.status.value,
        "raw_value": metric.raw_value,
        "formatted_value": metric.formatted_value,
        "unit": metric.unit.value,
        "error_detail": metric.error_detail,
    }

    masked_data = mask_sensitive_data(log_data)

    if metric.status == MetricStatus.UNAVAILABLE:
        logger.warning(
            "Metric status classified as UNAVAILABLE for %s:%s",
            metric.domain,
            metric.metric_key,
            extra=masked_data,
        )
    elif metric.status == MetricStatus.ZERO:
        logger.info(
            "Metric status classified as ZERO for %s:%s",
            metric.domain,
            metric.metric_key,
            extra=masked_data,
        )
    else:
        logger.info(
            "Metric status classified as VALID for %s:%s",
            metric.domain,
            metric.metric_key,
            extra=masked_data,
        )


def evaluate_overall_status(
    domain_results: List[DomainMetricsResult],
) -> AggregationStatus:
    """Evaluate overall dashboard aggregation status (COMPLETE, PARTIAL, FAILED) across domain results (FR-1, FR-3).

    Args:
        domain_results: List of DomainMetricsResult containers from domain connector ports.

    Returns:
        AggregationStatus:
            - COMPLETE: All domain results returned metrics without any UNAVAILABLE states.
            - PARTIAL: At least one valid/zero metric exists while others failed or were UNAVAILABLE.
            - FAILED: All domain results or metrics failed/UNAVAILABLE (or list is empty).
    """
    if not domain_results:
        return AggregationStatus.FAILED

    total_metrics = 0
    valid_or_zero_count = 0
    unavailable_count = 0

    for dr in domain_results:
        if dr.status == DomainStatus.UNAVAILABLE or not dr.metrics:
            if dr.metrics:
                for m in dr.metrics:
                    total_metrics += 1
                    unavailable_count += 1
            else:
                total_metrics += 1
                unavailable_count += 1
        else:
            for m in dr.metrics:
                total_metrics += 1
                status = classify_metric_status(raw_value=m.raw_value, error_detail=m.error_detail)
                if status == MetricStatus.UNAVAILABLE or m.status == MetricStatus.UNAVAILABLE:
                    unavailable_count += 1
                else:
                    valid_or_zero_count += 1

    if total_metrics == 0 or valid_or_zero_count == 0:
        return AggregationStatus.FAILED

    if unavailable_count == 0:
        return AggregationStatus.COMPLETE

    return AggregationStatus.PARTIAL


def collate_domain_metrics(
    domain_results: List[DomainMetricsResult],
    db: Optional[Session] = None,
) -> AggregatedSummaryResult:
    """Collate metric results from multiple domains into a unified summary DTO (FR-1, FR-3).

    Flattens domain metric lists, evaluates metric status and baseline enrichment,
    computes overall aggregation status (COMPLETE, PARTIAL, FAILED), and emits structured log.

    Args:
        domain_results: List of DomainMetricsResult DTOs collected from connectors.
        db: Optional synchronous SQLAlchemy Session for baseline metric DB lookups.

    Returns:
        AggregatedSummaryResult: Collated dashboard summary containing overall status and metrics.
    """
    collated_metrics: List[MetricResult] = []

    for dr in domain_results:
        for m in dr.metrics:
            enriched = enrich_metric_with_baseline(metric=m, db=db)
            collated_metrics.append(enriched)

    overall_status = evaluate_overall_status(domain_results)
    generated_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    summary = AggregatedSummaryResult(
        status=overall_status,
        generated_at=generated_at,
        metrics=collated_metrics,
    )

    log_aggregation_summary(summary)
    return summary


def aggregate_domain_connectors(
    connectors: List[DomainConnectorPort],
    timeframe: str = "30d",
    db: Optional[Session] = None,
) -> AggregatedSummaryResult:
    """Execute metric collection across provided domain connector ports and collate results (FR-1, FR-3).

    Args:
        connectors: List of DomainConnectorPort adapter instances.
        timeframe: Comparative period string (e.g. '30d').
        db: Optional synchronous SQLAlchemy Session for baseline DB lookups.

    Returns:
        AggregatedSummaryResult: Unified summary payload.
    """
    domain_results: List[DomainMetricsResult] = []

    for connector in connectors:
        try:
            res = connector.fetch_metrics(timeframe=timeframe)
            domain_results.append(res)
        except Exception as exc:
            domain_results.append(
                DomainMetricsResult(
                    domain=getattr(connector, "domain_id", "unknown"),
                    status=DomainStatus.UNAVAILABLE,
                    metrics=[],
                    error_detail=f"Unhandled exception during fetch_metrics: {exc}",
                )
            )

    return collate_domain_metrics(domain_results=domain_results, db=db)


def log_aggregation_summary(summary: AggregatedSummaryResult) -> None:
    """Emit structured JSON log record with correlation metadata for dashboard summary aggregation.

    Args:
        summary: AggregatedSummaryResult DTO.
    """
    cid = get_correlation_id()
    log_data: Dict[str, Any] = {
        "correlation_id": cid,
        "overall_status": summary.status.value,
        "total_metrics": len(summary.metrics),
        "valid_count": sum(1 for m in summary.metrics if m.status == MetricStatus.VALID),
        "zero_count": sum(1 for m in summary.metrics if m.status == MetricStatus.ZERO),
        "unavailable_count": sum(1 for m in summary.metrics if m.status == MetricStatus.UNAVAILABLE),
        "generated_at": summary.generated_at,
    }

    masked_data = mask_sensitive_data(log_data)

    if summary.status == AggregationStatus.FAILED:
        logger.warning(
            "Dashboard aggregation completed with status FAILED (%d unavailable metrics)",
            log_data["unavailable_count"],
            extra=masked_data,
        )
    elif summary.status == AggregationStatus.PARTIAL:
        logger.warning(
            "Dashboard aggregation completed with status PARTIAL (%d valid/zero, %d unavailable metrics)",
            log_data["valid_count"] + log_data["zero_count"],
            log_data["unavailable_count"],
            extra=masked_data,
        )
    else:
        logger.info(
            "Dashboard aggregation completed successfully with status COMPLETE (%d metrics)",
            log_data["total_metrics"],
            extra=masked_data,
        )


class DashboardAggregationService:
    """Service providing metric status classification, formatting, baseline lookup, and percentage change calculation."""

    def classify_metric_status(
        self,
        raw_value: Optional[Union[float, int]] = None,
        error_detail: Optional[str] = None,
    ) -> MetricStatus:
        """Classify the operational status of a metric."""
        return classify_metric_status(raw_value=raw_value, error_detail=error_detail)

    def format_metric_value(
        self,
        raw_value: Optional[Union[float, int]],
        unit: MetricUnit = MetricUnit.COUNT,
        status: Optional[MetricStatus] = None,
    ) -> str:
        """Format raw numerical metric value into human-readable string."""
        return format_metric_value(raw_value=raw_value, unit=unit, status=status)

    def calculate_percentage_change(
        self,
        current_value: Optional[Union[float, int]],
        baseline_value: Optional[Union[float, int]],
        decimals: int = 1,
    ) -> Optional[float]:
        """Calculate percentage change of current value relative to baseline."""
        return calculate_percentage_change(
            current_value=current_value,
            baseline_value=baseline_value,
            decimals=decimals,
        )

    def lookup_baseline_metric(
        self,
        db: Session,
        domain_name: str,
        metric_key: str,
    ) -> Optional[float]:
        """Look up baseline metric value from snapshot database."""
        return lookup_baseline_metric(db=db, domain_name=domain_name, metric_key=metric_key)

    def enrich_metric_with_baseline(
        self,
        metric: MetricResult,
        db: Optional[Session] = None,
        baseline_override: Optional[float] = None,
    ) -> MetricResult:
        """Enrich a MetricResult DTO with baseline metric lookup and percentage change calculation."""
        return enrich_metric_with_baseline(
            metric=metric,
            db=db,
            baseline_override=baseline_override,
        )

    def process_metric(
        self,
        domain: str,
        metric_key: str,
        display_name: str,
        raw_value: Optional[Union[float, int]] = None,
        unit: MetricUnit = MetricUnit.COUNT,
        baseline_value: Optional[float] = None,
        percentage_change: Optional[float] = None,
        error_detail: Optional[str] = None,
        db: Optional[Session] = None,
    ) -> MetricResult:
        """Process, classify, and log a single metric."""
        return process_metric_classification(
            domain=domain,
            metric_key=metric_key,
            display_name=display_name,
            raw_value=raw_value,
            unit=unit,
            baseline_value=baseline_value,
            percentage_change=percentage_change,
            error_detail=error_detail,
            db=db,
        )

    def evaluate_metric_result(self, metric: MetricResult) -> MetricResult:
        """Re-evaluate and classify an existing MetricResult object, ensuring status and formatted_value align."""
        status = classify_metric_status(raw_value=metric.raw_value, error_detail=metric.error_detail)
        formatted = format_metric_value(raw_value=metric.raw_value, unit=metric.unit, status=status)

        pct_change = metric.percentage_change
        if pct_change is None and metric.raw_value is not None and metric.baseline_value is not None:
            pct_change = calculate_percentage_change(metric.raw_value, metric.baseline_value)

        evaluated = MetricResult(
            domain=metric.domain,
            metric_key=metric.metric_key,
            display_name=metric.display_name,
            raw_value=metric.raw_value,
            baseline_value=metric.baseline_value,
            formatted_value=formatted,
            percentage_change=pct_change,
            status=status,
            unit=metric.unit,
            error_detail=metric.error_detail,
        )

        log_metric_classification(evaluated)
        return evaluated

    def evaluate_overall_status(
        self,
        domain_results: List[DomainMetricsResult],
    ) -> AggregationStatus:
        """Evaluate overall dashboard aggregation status across domain results."""
        return evaluate_overall_status(domain_results=domain_results)

    def collate_domain_metrics(
        self,
        domain_results: List[DomainMetricsResult],
        db: Optional[Session] = None,
    ) -> AggregatedSummaryResult:
        """Collate multi-domain metric results into a unified summary DTO."""
        return collate_domain_metrics(domain_results=domain_results, db=db)

    def aggregate_domain_connectors(
        self,
        connectors: List[DomainConnectorPort],
        timeframe: str = "30d",
        db: Optional[Session] = None,
    ) -> AggregatedSummaryResult:
        """Execute metric collection across domain connector ports and collate summary."""
        return aggregate_domain_connectors(
            connectors=connectors,
            timeframe=timeframe,
            db=db,
        )

