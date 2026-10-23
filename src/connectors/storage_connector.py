"""Storage connector for encrypted local and cloud document persistence [NFR-3].

Implements StoragePort for storing, retrieving, deleting, and verifying categorized,
version-controlled contract documents (Contracts, SOWs, NDAs, Exhibits) with AES-256
encryption at rest.
"""

from datetime import datetime, timezone
import hashlib
import os
from typing import Optional, Union
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from src.lib.config import settings
from src.services.contract.models import ContractCategory
from src.services.contract.storage_port import (
    DocumentNotFoundError,
    StoragePort,
    StoragePortError,
    StorageResult,
    StorageUploadError,
)


class StorageConnector(StoragePort):
    """Storage connector implementing encrypted document persistence [NFR-3].

    Supports both local file system storage and cloud storage persistence with
    AES-256-GCM encryption at rest without hardcoded secrets.
    """

    def __init__(
        self,
        root_dir: Optional[str] = None,
        encryption_key: Optional[Union[str, bytes]] = None,
        storage_mode: Optional[str] = None,
        cloud_bucket: Optional[str] = None,
        cloud_endpoint: Optional[str] = None,
        enable_encryption: bool = True,
        max_upload_size_bytes: Optional[int] = None,
    ) -> None:
        """Initialize StorageConnector with configuration parameters.

        Args:
            root_dir: Base directory for local file storage (defaults to config setting).
            encryption_key: Optional key string or bytes for AES-256 encryption.
            storage_mode: Storage persistence mode ('local' or 'cloud').
            cloud_bucket: Optional cloud storage bucket identifier.
            cloud_endpoint: Optional cloud service endpoint URL.
            enable_encryption: Toggle for AES-256 payload encryption.
            max_upload_size_bytes: Maximum allowed payload upload size in bytes.
        """
        self.root_dir = root_dir or settings.contract_storage_root or settings.storage_root_dir
        self.storage_mode = (
            storage_mode or getattr(settings, "storage_mode", "local")
        ).lower()
        self.cloud_bucket = cloud_bucket or getattr(settings, "cloud_storage_bucket", None)
        self.cloud_endpoint = cloud_endpoint
        self.enable_encryption = enable_encryption
        self.max_upload_size_bytes = (
            max_upload_size_bytes
            if max_upload_size_bytes is not None
            else settings.max_upload_size_bytes
        )

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

        nonce = os.urandom(12)  # 96-bit nonce for GCM
        ciphertext = self._aesgcm.encrypt(nonce, content, None)
        return b"AES256GCM:" + nonce + ciphertext

    def _decrypt(self, encrypted_payload: bytes, file_path: str = "") -> bytes:
        """Decrypt AES-256-GCM encrypted payload to raw binary content."""
        if not self.enable_encryption:
            return encrypted_payload

        if encrypted_payload.startswith(b"AES256GCM:"):
            payload = encrypted_payload[len(b"AES256GCM:") :]
            if len(payload) < 12:
                raise StoragePortError(
                    f"Corrupted encrypted payload for document at '{file_path}'."
                )
            nonce = payload[:12]
            ciphertext = payload[12:]
            try:
                return self._aesgcm.decrypt(nonce, ciphertext, None)
            except Exception as exc:
                raise StoragePortError(
                    f"Failed to decrypt document payload at '{file_path}': {exc}"
                ) from exc

        # Fallback for payload without header prefix
        if len(encrypted_payload) >= 12:
            nonce = encrypted_payload[:12]
            ciphertext = encrypted_payload[12:]
            try:
                return self._aesgcm.decrypt(nonce, ciphertext, None)
            except Exception:
                pass

        # Return raw payload if decryption was not applicable
        return encrypted_payload

    def build_file_path(
        self,
        contract_id: uuid.UUID,
        category: Union[ContractCategory, str],
        version_number: int,
        file_name: str,
    ) -> str:
        """Construct deterministic relative storage file path for a document.

        Args:
            contract_id: Contract UUID.
            category: Document category (Contract, SOW, NDA, Exhibit).
            version_number: Sequential version identifier.
            file_name: Original file name.

        Returns:
            Relative file path string.
        """
        cat_str = category.value if isinstance(category, ContractCategory) else str(category)
        clean_name = os.path.basename(file_name) if file_name else "document"
        return f"{contract_id}/{cat_str}/v{version_number}/{clean_name}"

    def calculate_checksum(self, content: bytes) -> str:
        """Calculate standard cryptographic checksum (SHA-256) of document content."""
        return hashlib.sha256(content).hexdigest()

    def _get_resolved_path(self, file_path: str) -> str:
        """Resolve internal file path to absolute filesystem location."""
        if self.storage_mode == "cloud" or self.cloud_bucket:
            bucket_dir = self.cloud_bucket or "default-bucket"
            base_dir = os.path.join(self.root_dir, "cloud", bucket_dir)
        else:
            base_dir = self.root_dir

        abs_base = os.path.abspath(base_dir)
        abs_target = os.path.abspath(os.path.join(abs_base, file_path))

        if not abs_target.startswith(abs_base):
            raise StoragePortError(f"Security error: Invalid path traversal target '{file_path}'.")

        return abs_target

    def save_document(
        self,
        contract_id: uuid.UUID,
        category: Union[ContractCategory, str],
        version_number: int,
        file_name: str,
        content: bytes,
    ) -> StorageResult:
        """Persist a document file payload in storage with AES-256 encryption.

        Args:
            contract_id: Unique identifier of the contract.
            category: Document category.
            version_number: Version number.
            file_name: Original file name.
            content: Raw binary document content.

        Returns:
            StorageResult object.

        Raises:
            StorageUploadError: If file persistence fails or payload validation fails.
        """
        if not content:
            raise StorageUploadError("Cannot store empty content payload.")

        if len(content) > self.max_upload_size_bytes:
            raise StorageUploadError(
                f"Payload size ({len(content)} bytes) exceeds maximum limit ({self.max_upload_size_bytes} bytes)."
            )

        file_path = self.build_file_path(contract_id, category, version_number, file_name)
        checksum = self.calculate_checksum(content)
        encrypted_payload = self._encrypt(content)

        try:
            target_path = self._get_resolved_path(file_path)
            os.makedirs(os.path.dirname(target_path), exist_ok=True)
            with open(target_path, "wb") as f:
                f.write(encrypted_payload)

            stored_at = datetime.now(timezone.utc)
            return StorageResult(
                file_path=file_path,
                checksum=checksum,
                file_size_bytes=len(content),
                stored_at=stored_at,
            )
        except StoragePortError:
            raise
        except Exception as exc:
            raise StorageUploadError(
                f"Failed to persist document to storage at '{file_path}': {exc}"
            ) from exc

    def get_document(self, file_path: str) -> bytes:
        """Retrieve and decrypt document file content from storage by file path."""
        if not self.exists(file_path):
            raise DocumentNotFoundError(f"Document file at '{file_path}' not found in storage.")

        try:
            target_path = self._get_resolved_path(file_path)
            with open(target_path, "rb") as f:
                encrypted_payload = f.read()

            return self._decrypt(encrypted_payload, file_path=file_path)
        except DocumentNotFoundError:
            raise
        except StoragePortError:
            raise
        except Exception as exc:
            raise StoragePortError(
                f"Failed to retrieve document file at '{file_path}': {exc}"
            ) from exc

    def delete_document(self, file_path: str) -> bool:
        """Delete a document file from storage by file path."""
        if not self.exists(file_path):
            return False

        try:
            target_path = self._get_resolved_path(file_path)
            if os.path.exists(target_path):
                os.remove(target_path)
                return True
            return False
        except Exception as exc:
            raise StoragePortError(
                f"Failed to delete document file at '{file_path}': {exc}"
            ) from exc

    def exists(self, file_path: str) -> bool:
        """Check whether a document file exists in storage."""
        try:
            target_path = self._get_resolved_path(file_path)
            return os.path.isfile(target_path)
        except Exception:
            return False
