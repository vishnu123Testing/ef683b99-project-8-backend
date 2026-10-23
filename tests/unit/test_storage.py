"""Unit tests for storage connector port and blob storage adapter [FR-2, FR-3, NFR-2, NFR-4, NFR-5]."""

from datetime import datetime, timezone
import hashlib
import os
import tempfile
import uuid

import pytest

from src.connectors.storage import (
    AttachmentNotFoundError,
    BlobStorageAdapter,
    StorageConnector,
    StorageError,
    StorageNotFoundError,
    StoragePort,
    StorageResult,
    StorageUploadError,
    StorageValidationError,
)


@pytest.fixture
def temp_storage_dir():
    """Fixture providing a temporary directory for local file storage testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


@pytest.fixture
def storage_adapter(temp_storage_dir):
    """Fixture providing an initialized BlobStorageAdapter instance."""
    return BlobStorageAdapter(
        storage_dir=temp_storage_dir,
        max_file_size_bytes=1024 * 1024,  # 1 MB
        allowed_mime_types=["image/jpeg", "image/png", "application/pdf", "text/plain"],
        allowed_extensions=[".jpg", ".jpeg", ".png", ".pdf", ".txt"],
        enable_encryption=False,
    )


def test_save_and_get_attachment_local(storage_adapter, temp_storage_dir):
    """Test saving and retrieving an attachment payload in local storage mode [FR-2, FR-3]."""
    ticket_id = uuid.uuid4()
    message_id = uuid.uuid4()
    filename = "support_log.pdf"
    content = b"%PDF-1.4 Support Document Sample Data for Ticket Attachment"
    mime_type = "application/pdf"

    result = storage_adapter.save_attachment(
        ticket_id=ticket_id,
        message_id=message_id,
        filename=filename,
        content=content,
        mime_type=mime_type,
    )

    assert result.filename == filename
    assert result.file_size_bytes == len(content)
    assert result.mime_type == mime_type
    assert result.checksum == hashlib.sha256(content).hexdigest()
    assert isinstance(result.stored_at, datetime)
    assert str(ticket_id) in result.storage_key
    assert str(message_id) in result.storage_key

    # Verify existence and retrieval
    assert storage_adapter.exists(result.storage_key) is True
    retrieved_bytes = storage_adapter.get_attachment(result.storage_key)
    assert retrieved_bytes == content


def test_validate_attachment_valid(storage_adapter):
    """Test that validate_attachment passes for valid files [FR-3, NFR-2]."""
    content = b"Simple text attachment"
    filename = "notes.txt"
    mime_type = "text/plain"

    # Should not raise any exception
    storage_adapter.validate_attachment(
        filename=filename,
        content=content,
        mime_type=mime_type,
    )


def test_validate_attachment_empty_content(storage_adapter):
    """Test that validate_attachment raises StorageValidationError for empty content."""
    with pytest.raises(StorageValidationError, match="cannot be empty"):
        storage_adapter.validate_attachment(
            filename="notes.txt",
            content=b"",
            mime_type="text/plain",
        )


def test_validate_attachment_exceeds_max_size(temp_storage_dir):
    """Test that validate_attachment raises StorageValidationError when file exceeds size limit [FR-3]."""
    small_adapter = BlobStorageAdapter(
        storage_dir=temp_storage_dir,
        max_file_size_bytes=100,  # 100 bytes limit
    )

    large_content = b"A" * 150
    with pytest.raises(StorageValidationError, match="exceeds maximum limit"):
        small_adapter.validate_attachment(
            filename="large.txt",
            content=large_content,
            mime_type="text/plain",
        )


def test_validate_attachment_disallowed_extension(storage_adapter):
    """Test that validate_attachment raises StorageValidationError for disallowed extensions."""
    with pytest.raises(StorageValidationError, match="not permitted"):
        storage_adapter.validate_attachment(
            filename="malicious.exe",
            content=b"executable content",
            mime_type="text/plain",
        )


def test_validate_attachment_disallowed_mime_type(storage_adapter):
    """Test that validate_attachment raises StorageValidationError for disallowed MIME types."""
    with pytest.raises(StorageValidationError, match="not permitted"):
        storage_adapter.validate_attachment(
            filename="script.txt",
            content=b"script content",
            mime_type="application/x-sh",
        )


def test_exists_and_delete_attachment(storage_adapter):
    """Test checking existence and deleting attachment files."""
    ticket_id = uuid.uuid4()
    message_id = uuid.uuid4()
    filename = "screenshot.png"
    content = b"\x89PNG\r\n\x1a\nFake PNG Data"

    result = storage_adapter.save_attachment(
        ticket_id=ticket_id,
        message_id=message_id,
        filename=filename,
        content=content,
        mime_type="image/png",
    )

    key = result.storage_key
    assert storage_adapter.exists(key) is True

    deleted = storage_adapter.delete_attachment(key)
    assert deleted is True
    assert storage_adapter.exists(key) is False

    # Deleting non-existent file returns False
    assert storage_adapter.delete_attachment(key) is False


def test_get_non_existent_attachment_raises_not_found(storage_adapter):
    """Test retrieving a non-existent storage key raises StorageNotFoundError."""
    missing_key = f"{uuid.uuid4()}/{uuid.uuid4()}/missing.pdf"

    with pytest.raises(StorageNotFoundError, match="not found"):
        storage_adapter.get_attachment(missing_key)


def test_path_traversal_prevention(storage_adapter):
    """Test that path traversal attempts raise StorageValidationError."""
    traversal_key = "../../etc/passwd"

    with pytest.raises(StorageValidationError, match="Security violation"):
        storage_adapter.get_attachment(traversal_key)


def test_encrypted_blob_storage(temp_storage_dir):
    """Test storing and retrieving attachments with AES-256 encryption at rest [NFR-5]."""
    encrypted_adapter = BlobStorageAdapter(
        storage_dir=temp_storage_dir,
        enable_encryption=True,
        encryption_key="test-secret-encryption-key-for-unit-tests",
    )

    ticket_id = uuid.uuid4()
    message_id = uuid.uuid4()
    filename = "confidential.pdf"
    content = b"TOP SECRET CLIENT SUPPORT INCIDENT DATA"

    result = encrypted_adapter.save_attachment(
        ticket_id=ticket_id,
        message_id=message_id,
        filename=filename,
        content=content,
        mime_type="application/pdf",
    )

    # Verify raw disk bytes are encrypted and do not contain plain text
    abs_stored_path = encrypted_adapter._get_resolved_path(result.storage_key)
    with open(abs_stored_path, "rb") as f:
        disk_bytes = f.read()

    assert disk_bytes.startswith(b"AES256GCM:")
    assert content not in disk_bytes

    # Verify retrieval decrypts back to original content
    decrypted = encrypted_adapter.get_attachment(result.storage_key)
    assert decrypted == content


def test_generic_save_file_and_get_file_aliases(storage_adapter):
    """Test generic save_file, get_file, and delete_file aliases."""
    key = "custom/path/document.txt"
    content = b"Generic file content"

    result = storage_adapter.save_file(storage_key=key, content=content)
    assert result.storage_key == key
    assert result.file_size_bytes == len(content)

    retrieved = storage_adapter.get_file(key)
    assert retrieved == content

    assert storage_adapter.delete_file(key) is True


def test_storage_result_to_dict():
    """Test StorageResult serialization to dictionary."""
    now = datetime.now(timezone.utc)
    res = StorageResult(
        storage_key="t1/m1/file.pdf",
        filename="file.pdf",
        file_size_bytes=100,
        mime_type="application/pdf",
        checksum="abc123sha256",
        stored_at=now,
    )

    d = res.to_dict()
    assert d["storage_key"] == "t1/m1/file.pdf"
    assert d["filename"] == "file.pdf"
    assert d["file_size_bytes"] == 100
    assert d["mime_type"] == "application/pdf"
    assert d["checksum"] == "abc123sha256"
    assert d["stored_at"] == now.isoformat()
    assert d["file_path"] == "t1/m1/file.pdf"


def test_storage_connector_alias():
    """Verify StorageConnector is an alias of BlobStorageAdapter."""
    assert StorageConnector is BlobStorageAdapter
    adapter = StorageConnector()
    assert isinstance(adapter, StoragePort)
