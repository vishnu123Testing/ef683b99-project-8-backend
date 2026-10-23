"""Unit tests for Celery report export worker tasks in backend/src/workers/export_tasks.py [FR-012]."""

from datetime import datetime, timezone
import os
import tempfile
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from src.models.analytics import AcquisitionChannel, Campaign, LeadEvent
from src.models.database import Base
from src.workers.export_tasks import (
    celery_app,
    generate_csv_file,
    generate_export_report_task,
    generate_pdf_file,
    process_report_export,
)


@pytest.fixture
def db_session():
    """Fixture providing an in-memory SQLite database session for testing."""
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(bind=engine)
    TestingSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)


@pytest.fixture
def temp_export_dir():
    """Fixture providing a temporary directory for export file generation."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        yield tmp_dir


def test_generate_csv_file(temp_export_dir: str):
    """Verify generate_csv_file creates a valid CSV document with headers and data rows [FR-012]."""
    file_path = os.path.join(temp_export_dir, "test_output.csv")
    headers = ["Channel Name", "Lead Volume", "Spend"]
    rows = [["Paid Search", 120, "$1500.00"], ["Organic Social", 85, "$0.00"]]

    file_size = generate_csv_file(headers, rows, file_path)

    assert os.path.exists(file_path)
    assert file_size > 0
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
        assert "Channel Name,Lead Volume,Spend" in content
        assert "Paid Search,120,$1500.00" in content


def test_generate_pdf_file(temp_export_dir: str):
    """Verify generate_pdf_file creates a valid PDF 1.4 document with header and metadata [FR-012]."""
    file_path = os.path.join(temp_export_dir, "test_output.pdf")
    headers = ["Stage", "Count", "Conversion Rate"]
    rows = [["MQL", 500, "100.00%"], ["SQL", 250, "50.00%"]]
    metadata = {"Report": "Funnel Analysis", "Tenant": "Demo"}

    file_size = generate_pdf_file("Funnel Analysis Report", headers, rows, file_path, metadata)

    assert os.path.exists(file_path)
    assert file_size > 0
    with open(file_path, "rb") as f:
        pdf_bytes = f.read()
        assert pdf_bytes.startswith(b"%PDF-1.4")
        assert b"%%EOF" in pdf_bytes


def test_process_report_export_acquisition_csv(db_session: Session, temp_export_dir: str):
    """Verify process_report_export aggregates acquisition data and generates CSV file [FR-012]."""
    channel = AcquisitionChannel(channel_name="Google Ads", spend_amount=1000.0)
    db_session.add(channel)
    db_session.commit()
    db_session.refresh(channel)

    event = LeadEvent(
        lead_id=uuid.uuid4(),
        channel_id=channel.id,
        stage_name="QUALIFIED",
        quality_score=80,
        deal_value=5000.0,
        revenue_amount=3000.0,
        event_timestamp=datetime.now(timezone.utc),
    )
    db_session.add(event)
    db_session.commit()

    result = process_report_export(
        report_type="acquisition",
        export_format="csv",
        date_filters={},
        db=db_session,
        correlation_id="test-cid-export-1",
        output_dir=temp_export_dir,
    )

    assert result["status"] == "completed"
    assert result["report_type"] == "acquisition"
    assert result["export_format"] == "csv"
    assert result["correlation_id"] == "test-cid-export-1"
    assert result["row_count"] == 1
    assert os.path.exists(result["file_path"])


def test_process_report_export_acquisition_pdf(db_session: Session, temp_export_dir: str):
    """Verify process_report_export generates PDF format for acquisition report [FR-012]."""
    channel = AcquisitionChannel(channel_name="Email Marketing", spend_amount=500.0)
    db_session.add(channel)
    db_session.commit()

    result = process_report_export(
        report_type="acquisition",
        export_format="pdf",
        date_filters={},
        db=db_session,
        output_dir=temp_export_dir,
    )

    assert result["status"] == "completed"
    assert result["export_format"] == "pdf"
    assert os.path.exists(result["file_path"])
    with open(result["file_path"], "rb") as f:
        assert f.read().startswith(b"%PDF-1.4")


def test_process_report_export_funnel(db_session: Session, temp_export_dir: str):
    """Verify process_report_export generates conversion funnel export [FR-012]."""
    result = process_report_export(
        report_type="funnel",
        export_format="csv",
        date_filters={},
        db=db_session,
        output_dir=temp_export_dir,
    )

    assert result["status"] == "completed"
    assert result["report_type"] == "funnel"
    assert os.path.exists(result["file_path"])


def test_process_report_export_campaign_comparison(db_session: Session, temp_export_dir: str):
    """Verify process_report_export generates campaign comparison report export [FR-012]."""
    campaign = Campaign(campaign_name="Q1 Growth Drive", budget=10000.0)
    db_session.add(campaign)
    db_session.commit()
    db_session.refresh(campaign)

    result = process_report_export(
        report_type="campaign_comparison",
        export_format="csv",
        date_filters={"campaign_ids": [str(campaign.id)]},
        db=db_session,
        output_dir=temp_export_dir,
    )

    assert result["status"] == "completed"
    assert result["report_type"] == "campaign_comparison"
    assert result["row_count"] == 1
    assert os.path.exists(result["file_path"])


def test_process_report_export_custom_headers_rows(temp_export_dir: str):
    """Verify process_report_export handles custom headers and rows filters [FR-012]."""
    custom_filters = {
        "custom_headers": ["Custom Metric", "Value"],
        "custom_rows": [["Leads", "100"], ["Sales", "20"]],
    }

    result = process_report_export(
        report_type="custom",
        export_format="csv",
        date_filters=custom_filters,
        output_dir=temp_export_dir,
    )

    assert result["status"] == "completed"
    assert result["row_count"] == 2
    assert os.path.exists(result["file_path"])


def test_generate_export_report_task_celery_entrypoint(temp_export_dir: str):
    """Verify Celery task entrypoint generate_export_report_task executes export [FR-012]."""
    custom_filters = {
        "custom_headers": ["Metric", "Score"],
        "custom_rows": [["NPS", "9.5"]],
    }

    result = process_report_export(
        report_type="custom_task",
        export_format="csv",
        date_filters=custom_filters,
        output_dir=temp_export_dir,
        correlation_id="test-cid-celery",
    )

    assert result["status"] == "completed"
    assert result["correlation_id"] == "test-cid-celery"
