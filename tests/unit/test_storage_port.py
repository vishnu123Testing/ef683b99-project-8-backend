"""Unit tests for document persistence port interface and StorageResult DTO [FR-3, FR-4]."""

from datetime import datetime, timezone
import hashlib
from typing import Dict, Union
import uuid

import pytest

from src.services.contract.models import ContractCategory
from src.services.contract.storage_port import (
    DocumentNotFoundError,
    StoragePort,
    StoragePortError,
    StorageResult,
    StorageUploadError,
)


class DummyInMemoryStorageAdapter(StoragePort):
    """Minimal in-memory adapter implementing StoragePort for testing abstract contract."""

    def __init__(self) -> None:
        self._store: Dict[str, bytes] = {}

    def build_file_path(
        self,
        contract_id: uuid.UUID,
        category: Union[ContractCategory, str],
        version_number: int,
        file_name: str,
    ) -> str:
        cat_str = category.value if isinstance(category, ContractCategory) else category
        return f"{contract_id}/{cat_str}/v{version_number}/{file_name}"

    def calculate_checksum(self, content: bytes) -> str:
        return hashlib.sha256(content).hexdigest()

    def save_document(
        self,
        contract_id: uuid.UUID,
        category: Union[ContractCategory, str],
        version_number: int,
        file_name: str,
        content: bytes,
    ) -> StorageResult:
        if not content:
            raise StorageUploadError("Cannot store empty content payload.")
        file_path = self.build_file_path(contract_id, category, version_number, file_name)
        checksum = self.calculate_checksum(content)
        stored_at = datetime.now(timezone.utc)
        self._store[file_path] = content
        return StorageResult(
            file_path=file_path,
            checksum=checksum,
            file_size_bytes=len(content),
            stored_at=stored_at,
        )

    def get_document(self, file_path: str) -> bytes:
        if file_path not in self._store:
            raise DocumentNotFoundError(f"Document at '{file_path}' not found.")
        return self._store[file_path]

    def delete_document(self, file_path: str) -> bool:
        if file_path in self._store:
            del self._store[file_path]
            return True
        return False

    def exists(self, file_path: str) -> bool:
        return file_path in self._store


def test_storage_exceptions():
    """Verify storage exception class hierarchy."""
    assert issubclass(DocumentNotFoundError, StoragePortError)
    assert issubclass(StorageUploadError, StoragePortError)


def test_storage_result_serialization():
    """Verify StorageResult instantiation and serialization."""
    now = datetime.now(timezone.utc)
    result = StorageResult(
        file_path="contracts/123/v1/doc.pdf",
        checksum="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        file_size_bytes=1024,
        stored_at=now,
    )

    assert result.file_path == "contracts/123/v1/doc.pdf"
    assert result.checksum == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    assert result.file_size_bytes == 1024
    assert result.stored_at == now

    d = result.to_dict()
    assert d["file_path"] == "contracts/123/v1/doc.pdf"
    assert d["file_size_bytes"] == 1024
    assert d["stored_at"] == now.isoformat()


def test_storage_port_adapter_implementation():
    """Test full document persistence workflow using in-memory port adapter [FR-3, FR-4]."""
    adapter = DummyInMemoryStorageAdapter()
    contract_id = uuid.uuid4()
    content = b"PDF Document Binary Content for Master Service Agreement"

    # Test path building for categories [FR-3] and version 1 [FR-4]
    path_v1 = adapter.build_file_path(contract_id, ContractCategory.CONTRACT, 1, "msa.pdf")
    assert f"{contract_id}/Contract/v1/msa.pdf" == path_v1

    # Save v1 document
    res_v1 = adapter.save_document(contract_id, ContractCategory.CONTRACT, 1, "msa.pdf", content)
    assert res_v1.file_path == path_v1
    assert res_v1.file_size_bytes == len(content)
    assert res_v1.checksum == adapter.calculate_checksum(content)

    # Verify exists & get
    assert adapter.exists(path_v1)
    retrieved = adapter.get_document(path_v1)
    assert retrieved == content

    # Upload v2 document with revised content [FR-4]
    content_v2 = b"PDF Document Binary Content for Revised Master Service Agreement v2"
    path_v2 = adapter.build_file_path(contract_id, ContractCategory.CONTRACT, 2, "msa_v2.pdf")
    res_v2 = adapter.save_document(contract_id, ContractCategory.CONTRACT, 2, "msa_v2.pdf", content_v2)
    assert res_v2.file_path == path_v2

    # Both v1 and v2 should exist simultaneously [FR-4]
    assert adapter.exists(path_v1)
    assert adapter.exists(path_v2)
    assert adapter.get_document(path_v1) != adapter.get_document(path_v2)

    # Test category variations [FR-3]
    for cat in [ContractCategory.SOW, ContractCategory.NDA, ContractCategory.EXHIBIT]:
        p = adapter.build_file_path(contract_id, cat, 1, f"{cat.value.lower()}.pdf")
        assert f"{contract_id}/{cat.value}/v1/{cat.value.lower()}.pdf" == p

    # Test delete
    assert adapter.delete_document(path_v1) is True
    assert adapter.exists(path_v1) is False
    assert adapter.delete_document(path_v1) is False

    with pytest.raises(DocumentNotFoundError):
        adapter.get_document(path_v1)


def test_storage_port_upload_error():
    """Verify StorageUploadError handling."""
    adapter = DummyInMemoryStorageAdapter()
    contract_id = uuid.uuid4()

    with pytest.raises(StorageUploadError):
        adapter.save_document(contract_id, ContractCategory.NDA, 1, "empty.pdf", b"")
