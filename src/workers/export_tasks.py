"""Celery worker tasks for asynchronous CSV and PDF report generation [FR-012].

Provides background execution for generating downloadable CSV and PDF reports
from marketing analytics, conversion funnel, campaign comparison, and territory
data. Emits structured JSON logs with correlation IDs.
"""

import csv
from datetime import datetime, timezone
import logging
import os
from typing import Any, Dict, List, Optional
import uuid

from celery import Celery
from sqlalchemy.orm import Session

from src.lib.config import settings
from src.lib.logging import get_correlation_id, set_correlation_id
from src.models.database import SessionLocal
from src.services.analytics.funnel import analyze_conversion_funnel
from src.services.analytics.reporting import ReportingService
from src.services.marketing.territory import get_territory_heatmap_data

logger = logging.getLogger(__name__)

# Initialize Celery worker application
celery_app = Celery(
    "export_worker",
    broker=settings.celery_broker_url,
    backend=settings.celery_result_backend,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    task_default_queue=getattr(settings, "export_worker_queue", "exports"),
)


def _parse_datetime(val: Any) -> Optional[datetime]:
    """Parse string or datetime into UTC timezone-aware datetime."""
    if val is None:
        return None
    if isinstance(val, datetime):
        if val.tzinfo is None:
            return val.replace(tzinfo=timezone.utc)
        return val.astimezone(timezone.utc)
    if isinstance(val, str):
        v_clean = val.strip()
        if not v_clean:
            return None
        try:
            if v_clean.endswith("Z"):
                v_clean = v_clean[:-1] + "+00:00"
            dt = datetime.fromisoformat(v_clean)
            if dt.tzinfo is None:
                return dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            return None
    return None


def generate_csv_file(
    headers: List[str],
    rows: List[List[Any]],
    file_path: str,
) -> int:
    """Generate a standard CSV file from headers and rows [FR-012].

    Args:
        headers: List of column header strings.
        rows: List of data rows.
        file_path: Output file path.

    Returns:
        Integer size of the generated CSV file in bytes.
    """
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)
    with open(file_path, mode="w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if headers:
            writer.writerow(headers)
        for row in rows:
            writer.writerow(row)
    return os.path.getsize(file_path)


def generate_pdf_file(
    title: str,
    headers: List[str],
    rows: List[List[Any]],
    file_path: str,
    metadata: Optional[Dict[str, Any]] = None,
) -> int:
    """Generate a standard PDF 1.4 document containing title, metadata, and data table [FR-012].

    Args:
        title: Report document title string.
        headers: List of column header strings.
        rows: List of data rows.
        file_path: Output file path.
        metadata: Optional dictionary of key-value metadata pairs.

    Returns:
        Integer size of the generated PDF file in bytes.
    """
    os.makedirs(os.path.dirname(os.path.abspath(file_path)), exist_ok=True)

    def escape_pdf_text(text: Any) -> str:
        s = str(text) if text is not None else ""
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    stream_lines: List[str] = []
    stream_lines.append("BT")
    stream_lines.append("/F1 18 Tf")
    stream_lines.append("50 750 Td")
    stream_lines.append(f"({escape_pdf_text(title)}) Tj")

    y_curr = 720
    stream_lines.append("/F1 10 Tf")

    if metadata:
        meta_parts = [
            f"{k}: {v}" for k, v in metadata.items() if v is not None and str(v).strip()
        ]
        if meta_parts:
            meta_str = " | ".join(meta_parts)
            stream_lines.append("0 -20 Td")
            stream_lines.append(f"({escape_pdf_text(meta_str)}) Tj")
            y_curr -= 20

    gen_time_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    stream_lines.append("0 -15 Td")
    stream_lines.append(f"(Generated on: {escape_pdf_text(gen_time_str)}) Tj")
    y_curr -= 15

    stream_lines.append("0 -15 Td")
    stream_lines.append(
        "(--------------------------------------------------------------------------------) Tj"
    )
    y_curr -= 15

    if headers:
        stream_lines.append("/F2 11 Tf")
        header_str = "  |  ".join(str(h) for h in headers)
        if len(header_str) > 120:
            header_str = header_str[:117] + "..."
        stream_lines.append("0 -20 Td")
        stream_lines.append(f"({escape_pdf_text(header_str)}) Tj")
        stream_lines.append("/F1 10 Tf")
        y_curr -= 20

    if not rows:
        stream_lines.append("0 -20 Td")
        stream_lines.append("(No records found for the specified filters.) Tj")
    else:
        for row in rows:
            row_str = "  |  ".join(str(val) if val is not None else "" for val in row)
            if len(row_str) > 120:
                row_str = row_str[:117] + "..."
            stream_lines.append("0 -15 Td")
            stream_lines.append(f"({escape_pdf_text(row_str)}) Tj")
            y_curr -= 15

    stream_lines.append("ET")
    content_bytes = "\n".join(stream_lines).encode("utf-8")

    pdf_header = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"
    obj1 = b"1 0 obj\n<< /Type /Catalog /Pages 2 0 R >>\nendobj\n"
    obj2 = b"2 0 obj\n<< /Type /Pages /Count 1 /Kids [5 0 R] >>\nendobj\n"
    obj3 = b"3 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>\nendobj\n"
    obj4 = b"4 0 obj\n<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>\nendobj\n"
    obj5 = b"5 0 obj\n<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 3 0 R /F2 4 0 R >> >> /Contents 6 0 R >>\nendobj\n"

    stream_len = len(content_bytes)
    obj6 = (
        f"6 0 obj\n<< /Length {stream_len} >>\nstream\n".encode("utf-8")
        + content_bytes
        + b"\nendstream\nendobj\n"
    )

    objects = [obj1, obj2, obj3, obj4, obj5, obj6]

    with open(file_path, "wb") as f:
        offsets = [len(pdf_header)]
        f.write(pdf_header)

        for obj in objects:
            offsets.append(f.tell())
            f.write(obj)

        xref_offset = f.tell()
        f.write(b"xref\n0 7\n0000000000 65535 f \n")
        for offset in offsets[1:]:
            f.write(f"{offset:010d} 00000 n \n".encode("utf-8"))

        f.write(
            f"trailer\n<< /Size 7 /Root 1 0 R >>\nstartxref\n{xref_offset}\n%%EOF\n".encode(
                "utf-8"
            )
        )

    return os.path.getsize(file_path)


def process_report_export(
    report_type: str,
    export_format: str = "csv",
    date_filters: Optional[Dict[str, Any]] = None,
    db: Optional[Session] = None,
    correlation_id: Optional[str] = None,
    output_dir: Optional[str] = None,
    task_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Execute background data aggregation and generate CSV or PDF export file [FR-012].

    Args:
        report_type: Identifier for report type (e.g. 'acquisition', 'funnel', 'campaign_comparison', 'territory').
        export_format: Desired export document format ('csv' or 'pdf').
        date_filters: Optional parameter dictionary containing start/end date boundaries and scope filters.
        db: Optional SQLAlchemy Session instance.
        correlation_id: Optional correlation ID string.
        output_dir: Optional output directory override.
        task_id: Optional task tracking UUID string.

    Returns:
        Summary dictionary with export tracking ID, status, file metadata, and correlation ID.
    """
    cid = correlation_id or get_correlation_id() or str(uuid.uuid4())
    token = set_correlation_id(cid)

    export_task_id = task_id or str(uuid.uuid4())
    filters = date_filters or {}

    fmt = str(export_format or "csv").strip().lower()
    if fmt not in ("csv", "pdf"):
        fmt = "csv"

    clean_report_type = str(report_type or "acquisition").strip().lower()

    target_dir = output_dir or getattr(settings, "export_output_dir", "./storage/exports")
    os.makedirs(os.path.abspath(target_dir), exist_ok=True)

    close_session = False
    if db is None:
        db = SessionLocal()
        close_session = True

    try:
        start_dt = _parse_datetime(filters.get("start_date_val"))
        end_dt = _parse_datetime(filters.get("end_date_val"))

        headers: List[str] = []
        rows: List[List[Any]] = []
        doc_title = f"{clean_report_type.replace('_', ' ').title()} Analytics Report"

        if clean_report_type in ("acquisition", "channel_acquisition", "acquisition_report"):
            reporting_service = ReportingService(db=db)
            channel_id = filters.get("channel_id")
            sort_by = filters.get("sort_by", "quality_score")
            sort_order = filters.get("sort_order", "desc")

            report_data = reporting_service.get_channel_acquisition_report(
                start_date_val=start_dt,
                end_date_val=end_dt,
                channel_id=channel_id,
                sort_by=sort_by,
                sort_order=sort_order,
            )

            headers = [
                "Channel ID",
                "Channel Name",
                "Lead Volume",
                "Quality Score",
                "Spend ($)",
                "Revenue ($)",
                "ROI",
            ]
            for item in report_data:
                rows.append([
                    item.get("channel_id", ""),
                    item.get("channel_name", ""),
                    item.get("lead_volume", 0),
                    item.get("quality_score", 0.0),
                    f"${item.get('spend', 0.0):.2f}",
                    f"${item.get('revenue', 0.0):.2f}",
                    item.get("roi_str", "N/A"),
                ])

        elif clean_report_type in ("funnel", "conversion_funnel", "funnel_report"):
            funnel_id = filters.get("funnel_id")
            funnel_data = analyze_conversion_funnel(
                db=db,
                funnel_id=funnel_id,
                start_date_val=start_dt,
                end_date_val=end_dt,
            )

            headers = [
                "Stage Name",
                "Stage Count",
                "Conversion Rate (%)",
                "Dropoff Rate (%)",
                "Is Bottleneck",
            ]
            for item in funnel_data:
                rows.append([
                    item.get("stage_name", ""),
                    item.get("stage_count", 0),
                    f"{item.get('conversion_rate', 0.0):.2f}%",
                    f"{item.get('dropoff_rate', 0.0):.2f}%",
                    "Yes" if item.get("is_bottleneck") else "No",
                ])

        elif clean_report_type in ("campaign_comparison", "campaigns", "campaign_report"):
            reporting_service = ReportingService(db=db)
            campaign_ids = filters.get("campaign_ids", [])
            if isinstance(campaign_ids, str):
                campaign_ids = [c.strip() for c in campaign_ids.split(",") if c.strip()]

            campaign_data = reporting_service.get_campaign_comparison(
                campaign_ids=campaign_ids,
                start_date_val=start_dt,
                end_date_val=end_dt,
            )

            headers = [
                "Campaign ID",
                "Campaign Name",
                "Budget ($)",
                "Lead Volume",
                "Cost Per Lead",
                "Total Revenue ($)",
                "Avg Quality Score",
                "Engagement Rate",
            ]
            for item in campaign_data:
                rows.append([
                    item.get("campaign_id", ""),
                    item.get("campaign_name", ""),
                    f"${item.get('budget', 0.0):.2f}",
                    item.get("lead_volume", 0),
                    item.get("cost_per_lead_str", "N/A"),
                    f"${item.get('total_revenue', 0.0):.2f}",
                    item.get("avg_quality_score", 0.0),
                    item.get("engagement_rate_str", "0.00%"),
                ])

        elif clean_report_type in ("territory", "territory_heatmap", "territory_report"):
            lead_status = filters.get("lead_status")
            min_deal_value = filters.get("min_deal_value")
            max_deal_value = filters.get("max_deal_value")
            territory_id = filters.get("territory_id")

            map_data = get_territory_heatmap_data(
                db=db,
                lead_status=lead_status,
                min_deal_value=min_deal_value,
                max_deal_value=max_deal_value,
                territory_id=territory_id,
            )

            headers = [
                "Territory Code",
                "Territory Name",
                "Lead Count",
                "Total Deal Value ($)",
                "Avg Quality Score",
            ]
            for t in map_data.get("territories", []):
                rows.append([
                    t.get("territory_code", ""),
                    t.get("territory_name", ""),
                    t.get("lead_count", 0),
                    f"${t.get('total_deal_value', 0.0):.2f}",
                    t.get("avg_quality_score", 0.0),
                ])

        elif "custom_headers" in filters and "custom_rows" in filters:
            headers = filters["custom_headers"]
            rows = filters["custom_rows"]

        else:
            headers = ["Report Type", "Start Filter", "End Filter", "Status"]
            rows = [[
                clean_report_type,
                str(start_dt) if start_dt else "All",
                str(end_dt) if end_dt else "All",
                "No custom generator found; default summary generated",
            ]]

        filename = f"{clean_report_type}_{export_task_id}.{fmt}"
        file_path = os.path.join(target_dir, filename)

        if fmt == "csv":
            file_size = generate_csv_file(headers=headers, rows=rows, file_path=file_path)
        else:
            meta = {
                "Report Type": clean_report_type,
                "Task ID": export_task_id,
                "Start Date": start_dt.strftime("%Y-%m-%d") if start_dt else "All",
                "End Date": end_dt.strftime("%Y-%m-%d") if end_dt else "All",
            }
            file_size = generate_pdf_file(
                title=doc_title,
                headers=headers,
                rows=rows,
                file_path=file_path,
                metadata=meta,
            )

        summary = {
            "task_id": export_task_id,
            "status": "completed",
            "report_type": clean_report_type,
            "export_format": fmt,
            "file_path": os.path.abspath(file_path),
            "file_name": filename,
            "file_size_bytes": file_size,
            "row_count": len(rows),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "correlation_id": cid,
        }

        logger.info(
            f"Report export completed successfully: task_id={export_task_id}, type={clean_report_type}, "
            f"format={fmt}, rows={len(rows)}, file_size={file_size} bytes.",
            extra={
                "correlation_id": cid,
                "event_type": "report_export_completed",
                "task_id": export_task_id,
                "report_type": clean_report_type,
                "export_format": fmt,
                "row_count": len(rows),
                "file_size_bytes": file_size,
            },
        )

        return summary

    except Exception as exc:
        logger.error(
            f"Failed to generate report export: task_id={export_task_id}, type={clean_report_type}, format={fmt}: {exc}",
            extra={
                "correlation_id": cid,
                "event_type": "report_export_failed",
                "task_id": export_task_id,
                "report_type": clean_report_type,
                "export_format": fmt,
                "error_detail": str(exc),
            },
            exc_info=True,
        )
        raise
    finally:
        if close_session and db is not None:
            db.close()


@celery_app.task(name="src.workers.export_tasks.generate_export_report", bind=True)
def generate_export_report_task(
    self: Any,
    report_type: str,
    export_format: str = "csv",
    date_filters: Optional[Dict[str, Any]] = None,
    correlation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Celery task entry point for asynchronous CSV and PDF report export generation [FR-012].

    Args:
        report_type: Report dataset identifier.
        export_format: Requested output format ('csv' or 'pdf').
        date_filters: Optional parameter filter dictionary.
        correlation_id: Optional correlation tracking ID string.

    Returns:
        Summary dictionary containing export file tracking metadata.
    """
    task_id = getattr(self.request, "id", None) or str(uuid.uuid4())
    return process_report_export(
        report_type=report_type,
        export_format=export_format,
        date_filters=date_filters,
        correlation_id=correlation_id,
        task_id=task_id,
    )
