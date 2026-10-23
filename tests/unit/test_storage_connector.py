"""Unit tests for encrypted StorageConnector document persistence adapter [NFR-3]."""

from datetime import datetime, timezone
import hashlib
import os
import tempfile
import uuid

import pytest

from src.connectors.storage_connector import StorageConnector
from src.services.contract.models import ContractCategory
from src.services.contract.storage_port import (
    DocumentNotFoundError,
    StoragePortError,
    StorageUploadError,
)


@pytest.fixture
def temp_storage_dir():
    """Fixture providing a temporary storage directory."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield tmpdir


def test_save_and_get_document_local(temp_storage_dir):
    """Test saving and retrieving an encrypted document in local storage mode."""
    connector = StorageConnector(root_dir=temp_storage_dir, storage_mode="local")

    contract_id = uuid.uuid4()
    content = b"Confidential Agreement Document Content - Secret Data"
    file_name = "agreement.pdf"

    result = connector.save_document(
        contract_id=contract_id,
        category=ContractCategory.CONTRACT,
        version_number=1,
        file_name=file_name,
        content=content,
    )

    assert result.file_path == f"{contract_id}/Contract/v1/{file_name}"
    assert result.checksum == hashlib.sha256(content).hexdigest()
    assert result.file_size_bytes == len(content)
    assert isinstance(result.stored_at, datetime)

    # Verify file is stored encrypted on disk
    abs_stored_file = os.path.join(temp_storage_dir, result.file_path)
    assert os.path.exists(abs_stored_file)
    with open(abs_stored_file, "rb") as f:
        stored_bytes = f.read()

    assert stored_bytes.startswith(b"AES256GCM:")
    assert content not in stored_bytes  # Verify plain text is NOT exposed on disk

    # Verify retrieval decrypts to original payload
    retrieved = connector.get_document(result.file_path)
    assert retrieved == content


def test_exists_and_delete_document(temp_storage_dir):
    """Test checking existence and deleting stored documents."""
    connector = StorageConnector(root_dir=temp_storage_dir)

    contract_id = uuid.uuid4()
    content = b"Statement of Work v1"
    file_name = "sow.pdf"

    result = connector.save_document(
        contract_id=contract_id,
        category="SOW",
        version_number=1,
        file_name=file_name,
        content=content,
    )

    assert connector.exists(result.file_path) is True

    deleted = connector.delete_document(result.file_path)
    assert deleted is True
    assert connector.exists(result.file_path) is False

    # Deleting again returns False
    assert connector.delete_document(result.file_path) is False


def test_save_empty_content_raises_error(temp_storage_dir):
    """Verify storing empty document content raises StorageUploadError."""
    connector = StorageConnector(root_dir=temp_storage_dir)
    contract_id = uuid.uuid4()

    with pytest.raises(StorageUploadError, match="empty content"):
        connector.save_document(
            contract_id=contract_id,
            category="NDA",
            version_number=1,
            file_name="nda.pdf",
            content=b"",
        )


def test_save_exceeding_max_size_raises_error(temp_storage_dir):
    """Verify storing payload exceeding max size limit raises StorageUploadError."""
    connector = StorageConnector(
        root_dir=temp_storage_dir, max_upload_size_bytes=100
    )
    contract_id = uuid.uuid4()
    large_content = b"A" * 150

    with pytest.raises(StorageUploadError, match="exceeds maximum limit"):
        connector.save_document(
            contract_id=contract_id,
            category="Exhibit",
            version_number=1,
            file_name="exhibit.pdf",
            content=large_content,
        )


def test_get_nonexistent_document_raises_not_found(temp_storage_dir):
    """Verify retrieving nonexistent file path raises DocumentNotFoundError."""
    connector = StorageConnector(root_dir=temp_storage_dir)

    with pytest.raises(DocumentNotFoundError, match="not found"):
        connector.get_document("invalid/path/v1/doc.pdf")


def test_path_traversal_prevention(temp_storage_dir):
    """Verify path traversal attempt raises StoragePortError."""
    connector = StorageConnector(root_dir=temp_storage_dir)

    with pytest.raises(StoragePortError, match="path traversal"):
        connector._get_resolved_path("../../etc/passwd")


def test_cloud_storage_mode(temp_storage_dir):
    """Test saving and retrieving document in cloud storage persistence mode."""
    connector = StorageConnector(
        root_dir=temp_storage_dir,
        storage_mode="cloud",
        cloud_bucket="prod-contracts-bucket",
    )

    contract_id = uuid.uuid4()
    content = b"Cloud Persisted NDA Content"
    file_name = "nda.pdf"

    result = connector.save_document(
        contract_id=contract_id,
        category=ContractCategory.NDA,
        version_number=2,
        file_name=file_name,
        content=content,
    )

    assert connector.exists(result.file_path) is True
    retrieved = connector.get_document(result.file_path)
    assert retrieved == content

    assert connector.delete_document(result.file_path) is True
    assert connector.exists(result.file_path) is False


def test_custom_encryption_key_mismatch(temp_storage_dir):
    """Verify attempting to decrypt with a different encryption key raises StoragePortError."""
    key1 = "secret-key-1-for-encryption-123456"
    key2 = "different-key-for-encryption-987654"

    connector1 = StorageConnector(root_dir=temp_storage_dir, encryption_key=key1)
    connector2 = StorageConnector(root_dir=temp_storage_dir, encryption_key=key2)

    contract_id = uuid.uuid4()
    content = b"Top Secret Terms"

    result = connector1.save_document(
        contract_id=contract_id,
        category="Contract",
        version_number=1,
        file_name="terms.pdf",
        content=content,
    )

    # connector1 can decrypt
    assert connector1.get_document(result.file_path) == content

    # connector2 with different key fails to decrypt
    with pytest.raises(StoragePortError, match="Failed to decrypt"):
        connector2.get_document(result.file_path)
