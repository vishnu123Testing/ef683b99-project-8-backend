"""Storage connector port and blob storage adapter for ticket file attachments [FR-2, FR-3, NFR-2, NFR-5].

Provides abstract port interface and concrete BlobStorageAdapter implementation for
storing, retrieving, deleting, and validating support ticket file attachments.
Supports local filesystem and cloud blob storage mechanisms with optional AES-256 encryption.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import mimetypes
import os
from typing import List, Optional, Union
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.lib.config import settings


class StorageError(Exception):
    """Base exception for blob storage operations."""

    pass


class StorageNotFoundError(StorageError):
    """Raised when a requested file or attachment is not found in storage."""

    pass


class StorageUploadError(StorageError):
    """Raised when file upload or persistence fails."""

    pass


class StorageValidationError(StorageError):
    """Raised when file payload, MIME type, extension, or size validation fails."""

    pass


# Exception aliases for compatibility across services
AttachmentNotFoundError = StorageNotFoundError


@dataclass
class StorageResult:
    """Dataclass holding metadata resulting from a storage persistence operation."""

    storage_key: str
    filename: str
    file_size_bytes: int
    mime_type: str
    checksum: str
    stored_at: datetime
    file_path: Optional[str] = None

    def __post_init__(self) -> None:
        if self.file_path is None:
            self.file_path = self.storage_key

    def to_dict(self) -> dict:
        """Convert storage result to a dictionary representation."""
        return {
            "storage_key": self.storage_key,
            "filename": self.filename,
            "file_size_bytes": self.file_size_bytes,
            "mime_type": self.mime_type,
            "checksum": self.checksum,
            "stored_at": self.stored_at.isoformat(),
            "file_path": self.file_path,
        }


class StoragePort(ABC):
    """Abstract port interface for file attachment storage [FR-2, FR-3, NFR-4].

    Abstracts underlying physical blob storage mechanisms (local filesystem,
    cloud storage buckets, object stores) from domain business logic.
    """

    @abstractmethod
    def validate_attachment(
        self,
        filename: str,
        content: bytes,
        mime_type: Optional[str] = None,
    ) -> None:
        """Validate attachment payload size, MIME type, and file extension [FR-3, NFR-2].

        Args:
            filename: Original file name.
            content: Raw binary payload.
            mime_type: Optional MIME type string.

        Raises:
            StorageValidationError: If file size, MIME type, or extension is invalid.
        """
        pass

    @abstractmethod
    def save_attachment(
        self,
        ticket_id: Union[uuid.UUID, str],
        message_id: Union[uuid.UUID, str],
        filename: str,
        content: bytes,
        mime_type: Optional[str] = None,
    ) -> StorageResult:
        """Persist a ticket attachment file payload in storage [FR-2, FR-3].

        Args:
            ticket_id: Support ticket UUID or string identifier.
            message_id: Message thread entry UUID or string identifier.
            filename: Original attachment filename.
            content: Raw binary attachment content.
            mime_type: Optional MIME type string.

        Returns:
            StorageResult object containing storage metadata.

        Raises:
            StorageValidationError: If payload validation fails.
            StorageUploadError: If file persistence fails.
        """
        pass

    @abstractmethod
    def get_attachment(self, storage_key: str) -> bytes:
        """Retrieve binary file content from storage given its storage key [FR-2].

        Args:
            storage_key: Relative storage key or file path.

        Returns:
            Raw binary file content.

        Raises:
            StorageNotFoundError: If the file does not exist.
            StorageError: If retrieval fails.
        """
        pass

    @abstractmethod
    def delete_attachment(self, storage_key: str) -> bool:
        """Delete an attachment file from storage given its storage key.

        Args:
            storage_key: Relative storage key or file path.

        Returns:
            True if file was deleted, False if not found.

        Raises:
            StorageError: If deletion fails.
        """
        pass

    @abstractmethod
    def exists(self, storage_key: str) -> bool:
        """Check whether a file exists in storage for a given storage key.

        Args:
            storage_key: Relative storage key or file path.

        Returns:
            True if file exists, False otherwise.
        """
        pass


class BlobStorageAdapter(StoragePort):
    """Concrete storage adapter for local filesystem and cloud blob storage [FR-2, FR-3, NFR-2, NFR-5].

    Handles attachment persistence, retrieval, deletion, and validation against
    configured file size limits, MIME types, and extension rules.
    Supports optional AES-256 encryption at rest.
    """

    def __init__(
        self,
        storage_dir: Optional[str] = None,
        max_file_size_bytes: Optional[int] = None,
        allowed_mime_types: Optional[List[str]] = None,
        allowed_extensions: Optional[List[str]] = None,
        storage_mode: Optional[str] = None,
        cloud_bucket: Optional[str] = None,
        enable_encryption: bool = False,
        encryption_key: Optional[Union[str, bytes]] = None,
    ) -> None:
        """Initialize BlobStorageAdapter with configuration parameters [NFR-4, NFR-5].

        Args:
            storage_dir: Base directory for local file storage (defaults to config settings).
            max_file_size_bytes: Maximum allowed payload size in bytes.
            allowed_mime_types: List of permitted MIME types for attachments.
            allowed_extensions: List of permitted file extensions.
            storage_mode: Persistence mode ('local' or 'cloud').
            cloud_bucket: Optional cloud storage bucket identifier.
            enable_encryption: Toggle for AES-256 payload encryption at rest.
            encryption_key: Optional key string or bytes for encryption.
        """
        self.storage_dir = (
            storage_dir
            or getattr(settings, "support_attachment_storage_dir", None)
            or settings.storage_root_dir
        )
        self.max_file_size_bytes = (
            max_file_size_bytes
            if max_file_size_bytes is not None
            else getattr(settings, "attachment_max_bytes", settings.max_upload_size_bytes)
        )
        self.allowed_mime_types = (
            allowed_mime_types
            if allowed_mime_types is not None
            else getattr(settings, "allowed_attachment_mime_types", [])
        )
        self.allowed_extensions = (
            allowed_extensions
            if allowed_extensions is not None
            else getattr(settings, "allowed_attachment_extensions", [])
        )
        self.storage_mode = (
            storage_mode or getattr(settings, "storage_mode", "local")
        ).lower()
        self.cloud_bucket = cloud_bucket or getattr(settings, "cloud_storage_bucket", None)
        self.enable_encryption = enable_encryption

        if self.enable_encryption:
            self._encryption_key = self._derive_key_256(encryption_key)
            self._aesgcm = AESGCM(self._encryption_key)

    def _derive_key_256(self, key_input: Optional[Union[str, bytes]]) -> bytes:
        """Derive a 256-bit (32-byte) key for AES-256 encryption without hardcoded secrets."""
        if key_input is not None:
            if isinstance(key_input, bytes):
                return key_input if len(key_input) == 32 else hashlib.sha256(key_input).digest()
            return hashlib.sha256(key_input.encode("utf-8")).digest()

        if getattr(settings, "storage_encryption_key", None):
            return hashlib.sha256(
                settings.storage_encryption_key.encode("utf-8")
            ).digest()

        return hashlib.sha256(settings.secret_key.encode("utf-8")).digest()

    def _encrypt(self, content: bytes) -> bytes:
        """Encrypt raw binary content using AES-256-GCM."""
        if not self.enable_encryption:
            return content

        nonce = os.urandom(12)
        ciphertext = self._aesgcm.encrypt(nonce, content, None)
        return b"AES256GCM:" + nonce + ciphertext

    def _decrypt(self, encrypted_payload: bytes, storage_key: str = "") -> bytes:
        """Decrypt AES-256-GCM encrypted payload to raw binary content."""
        if not self.enable_encryption:
            return encrypted_payload

        if encrypted_payload.startswith(b"AES256GCM:"):
            payload = encrypted_payload[len(b"AES256GCM:") :]
            if len(payload) < 12:
                raise StorageError(f"Corrupted encrypted payload for file at '{storage_key}'.")
            nonce = payload[:12]
            ciphertext = payload[12:]
            try:
                return self._aesgcm.decrypt(nonce, ciphertext, None)
            except Exception as exc:
                raise StorageError(
                    f"Failed to decrypt attachment payload at '{storage_key}': {exc}"
                ) from exc

        return encrypted_payload

    def calculate_checksum(self, content: bytes) -> str:
        """Calculate standard SHA-256 checksum of payload content."""
        return hashlib.sha256(content).hexdigest()

    def _get_resolved_path(self, storage_key: str) -> str:
        """Resolve storage key to absolute filesystem location with path traversal checks."""
        if self.storage_mode == "cloud" or self.cloud_bucket:
            bucket_dir = self.cloud_bucket or "default-bucket"
            base_dir = os.path.join(self.storage_dir, "cloud", bucket_dir)
        else:
            base_dir = self.storage_dir

        abs_base = os.path.abspath(base_dir)
        abs_target = os.path.abspath(os.path.join(abs_base, storage_key))

        if not abs_target.startswith(abs_base):
            raise StorageValidationError(
                f"Security violation: Invalid path traversal target '{storage_key}'."
            )

        return abs_target

    def validate_attachment(
        self,
        filename: str,
        content: bytes,
        mime_type: Optional[str] = None,
    ) -> None:
        """Validate payload size, MIME type, and extension rules [FR-3, NFR-2].

        Raises:
            StorageValidationError: If content is empty, exceeds max size, or has forbidden MIME/extension.
        """
        if not content:
            raise StorageValidationError("Attachment content payload cannot be empty.")

        if len(content) > self.max_file_size_bytes:
            raise StorageValidationError(
                f"Attachment payload size ({len(content)} bytes) exceeds maximum limit "
                f"({self.max_file_size_bytes} bytes)."
            )

        clean_filename = os.path.basename(filename) if filename else ""
        if not clean_filename:
            raise StorageValidationError("Attachment filename cannot be empty.")

        _, ext = os.path.splitext(clean_filename)
        ext_lower = ext.lower()

        if self.allowed_extensions:
            normalized_allowed_exts = [
                e.lower() if e.startswith(".") else f".{e.lower()}"
                for e in self.allowed_extensions
            ]
            if ext_lower not in normalized_allowed_exts:
                raise StorageValidationError(
                    f"File extension '{ext_lower}' is not permitted. "
                    f"Allowed extensions: {self.allowed_extensions}."
                )

        detected_mime = mime_type
        if not detected_mime:
            guessed, _ = mimetypes.guess_type(clean_filename)
            detected_mime = guessed or "application/octet-stream"

        if self.allowed_mime_types:
            mime_lower = detected_mime.lower()
            allowed_mimes_lower = [m.lower() for m in self.allowed_mime_types]
            if mime_lower not in allowed_mimes_lower:
                raise StorageValidationError(
                    f"MIME type '{detected_mime}' is not permitted. "
                    f"Allowed MIME types: {self.allowed_mime_types}."
                )

    def save_attachment(
        self,
        ticket_id: Union[uuid.UUID, str],
        message_id: Union[uuid.UUID, str],
        filename: str,
        content: bytes,
        mime_type: Optional[str] = None,
    ) -> StorageResult:
        """Persist a ticket attachment file in storage [FR-2, FR-3]."""
        self.validate_attachment(filename=filename, content=content, mime_type=mime_type)

        clean_filename = os.path.basename(filename)
        detected_mime = mime_type
        if not detected_mime:
            guessed, _ = mimetypes.guess_type(clean_filename)
            detected_mime = guessed or "application/octet-stream"

        unique_prefix = uuid.uuid4().hex[:8]
        storage_key = f"{ticket_id}/{message_id}/{unique_prefix}_{clean_filename}"
        checksum = self.calculate_checksum(content)
        encrypted_payload = self._encrypt(content)

        try:
            target_path = self._get_resolved_path(storage_key)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "wb") as f:
                f.write(encrypted_payload)

            stored_at = datetime.now(timezone.utc)
            return StorageResult(
                storage_key=storage_key,
                filename=clean_filename,
                file_size_bytes=len(content),
                mime_type=detected_mime,
                checksum=checksum,
                stored_at=stored_at,
                file_path=storage_key,
            )
        except StorageError:
            raise
        except Exception as exc:
            raise StorageUploadError(
                f"Failed to persist attachment file at '{storage_key}': {exc}"
            ) from exc

    def get_attachment(self, storage_key: str) -> bytes:
        """Retrieve attachment file content from storage [FR-2]."""
        if not self.exists(storage_key):
            raise StorageNotFoundError(
                f"Attachment file for storage key '{storage_key}' not found."
            )

        try:
            target_path = self._get_resolved_path(storage_key)
            with open(target_path, "rb") as f:
                encrypted_payload = f.read()

            return self._decrypt(encrypted_payload, storage_key=storage_key)
        except StorageNotFoundError:
            raise
        except StorageError:
            raise
        except Exception as exc:
            raise StorageError(
                f"Failed to retrieve attachment file at '{storage_key}': {exc}"
            ) from exc

    def delete_attachment(self, storage_key: str) -> bool:
        """Delete an attachment file from storage."""
        if not self.exists(storage_key):
            return False

        try:
            target_path = self._get_resolved_path(storage_key)
            if os.path.isfile(target_path):
                os.remove(target_path)
                return True
            return False
        except Exception as exc:
            raise StorageError(
                f"Failed to delete attachment file at '{storage_key}': {exc}"
            ) from exc

    def exists(self, storage_key: str) -> bool:
        """Check whether an attachment file exists in storage."""
        try:
            target_path = self._get_resolved_path(storage_key)
            return os.path.isfile(target_path)
        except Exception:
            return False

    # Generic file persistence aliases
    def save_file(self, storage_key: str, content: bytes) -> StorageResult:
        """Persist raw bytes directly to a specified storage key."""
        if not content:
            raise StorageValidationError("File content payload cannot be empty.")

        filename = os.path.basename(storage_key)
        checksum = self.calculate_checksum(content)
        encrypted_payload = self._encrypt(content)

        try:
            target_path = self._get_resolved_path(storage_key)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "wb") as f:
                f.write(encrypted_payload)

            stored_at = datetime.now(timezone.utc)
            guessed_mime, _ = mimetypes.guess_type(filename)
            mime_type = guessed_mime or "application/octet-stream"

            return StorageResult(
                storage_key=storage_key,
                filename=filename,
                file_size_bytes=len(content),
                mime_type=mime_type,
                checksum=checksum,
                stored_at=stored_at,
                file_path=storage_key,
            )
        except StorageError:
            raise
        except Exception as exc:
            raise StorageUploadError(
                f"Failed to persist file at '{storage_key}': {exc}"
            ) from exc

    def get_file(self, storage_key: str) -> bytes:
        """Alias for get_attachment."""
        return self.get_attachment(storage_key)

    def delete_file(self, storage_key: str) -> bool:
        """Alias for delete_attachment."""
        return self.delete_attachment(storage_key)


# Alias for backward compatibility / flexibility
StorageConnector = BlobStorageAdapter
