"""Abstract port interface for document file persistence and retrieval.

Defines the contract for storing, retrieving, deleting, and verifying categorized,
version-controlled contract documents (Contracts, SOWs, NDAs, Exhibits) [FR-3, FR-4].
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Union
import uuid

from src.services.contract.models import ContractCategory


class StoragePortError(Exception):
    """Base exception for document storage persistence errors."""

    pass


class DocumentNotFoundError(StoragePortError):
    """Raised when a requested document file is not found in storage."""

    pass


class StorageUploadError(StoragePortError):
    """Raised when document file upload or persistence fails."""

    pass


@dataclass
class StorageResult:
    """Dataclass holding metadata resulting from a document persistence operation."""

    file_path: str
    checksum: str
    file_size_bytes: int
    stored_at: datetime

    def to_dict(self) -> dict:
        """Convert storage result to dictionary representation."""
        return {
            "file_path": self.file_path,
            "checksum": self.checksum,
            "file_size_bytes": self.file_size_bytes,
            "stored_at": self.stored_at.isoformat(),
        }


class StoragePort(ABC):
    """Abstract port interface for document file persistence.

    Abstracts underlying physical storage mechanisms (local file system, cloud storage,
    encrypted object stores) from domain business logic [FR-3, FR-4].
    """

    @abstractmethod
    def save_document(
        self,
        contract_id: uuid.UUID,
        category: Union[ContractCategory, str],
        version_number: int,
        file_name: str,
        content: bytes,
    ) -> StorageResult:
        """Persist a document file payload in storage for a given contract version and category.

        Args:
            contract_id: Unique identifier of the contract.
            category: Document category (Contract, SOW, NDA, Exhibit) [FR-3].
            version_number: Sequential version identifier (1, 2, 3...) [FR-4].
            file_name: Original file name.
            content: Raw binary document file payload.

        Returns:
            StorageResult containing file_path, checksum (SHA-256), file_size_bytes, and stored_at.

        Raises:
            StorageUploadError: If file persistence fails.
        """
        pass

    @abstractmethod
    def get_document(self, file_path: str) -> bytes:
        """Retrieve binary document file content from storage by file path.

        Args:
            file_path: Internal relative or resolved path to the stored document.

        Returns:
            Raw binary file content.

        Raises:
            DocumentNotFoundError: If the specified file path does not exist.
            StoragePortError: If document retrieval fails.
        """
        pass

    @abstractmethod
    def delete_document(self, file_path: str) -> bool:
        """Delete a document file from storage by file path.

        Args:
            file_path: Internal relative or resolved path to the stored document.

        Returns:
            True if file was successfully deleted or removed, False if file was not found.

        Raises:
            StoragePortError: If file deletion fails due to permissions or I/O error.
        """
        pass

    @abstractmethod
    def exists(self, file_path: str) -> bool:
        """Check whether a document file exists in storage at the given file path.

        Args:
            file_path: Path to the document.

        Returns:
            True if document exists, False otherwise.
        """
        pass

    @abstractmethod
    def calculate_checksum(self, content: bytes) -> str:
        """Calculate standard cryptographic checksum (SHA-256) of document content.

        Args:
            content: Raw binary document content.

        Returns:
            Hexadecimal SHA-256 checksum string.
        """
        pass

    @abstractmethod
    def build_file_path(
        self,
        contract_id: uuid.UUID,
        category: Union[ContractCategory, str],
        version_number: int,
        file_name: str,
    ) -> str:
        """Construct deterministic storage file path for a categorized, versioned document.

        Args:
            contract_id: Contract UUID.
            category: Document category (Contract, SOW, NDA, Exhibit) [FR-3].
            version_number: Sequential version number [FR-4].
            file_name: Original file name.

        Returns:
            Deterministic relative file path string.
        """
        pass
