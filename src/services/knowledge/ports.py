"""Abstract port interface and data models for video streaming provider integration.

Defines port contracts, data structures, and exception boundaries for video metadata
retrieval, streaming asset verification, secure embed URL generation, and streaming
token issuance [FR-5, NFR-2, NFR-5].
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, Optional


class VideoProviderError(Exception):
    """Base exception for all video streaming provider port operations."""

    pass


class VideoNotFoundError(VideoProviderError):
    """Raised when a requested video asset is not found on the provider/CDN."""

    pass


class VideoUnavailableError(VideoProviderError):
    """Raised when a video asset is unavailable, forbidden, or unplayable."""

    pass


class StreamingTokenError(VideoProviderError):
    """Raised when video streaming token generation or validation fails."""

    pass


@dataclass
class VideoMetadata:
    """Dataclass holding metadata and stream attributes for a training video asset [FR-5]."""

    video_id: str
    title: str
    duration_seconds: Optional[int] = None
    embed_url: str = ""
    thumbnail_url: Optional[str] = None
    format: str = "mp4"
    is_available: bool = True
    captions_url: Optional[str] = None
    provider_name: str = "video_cdn"
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Convert VideoMetadata to dictionary representation."""
        return {
            "video_id": self.video_id,
            "title": self.title,
            "duration_seconds": self.duration_seconds,
            "embed_url": self.embed_url,
            "thumbnail_url": self.thumbnail_url,
            "format": self.format,
            "is_available": self.is_available,
            "captions_url": self.captions_url,
            "provider_name": self.provider_name,
            "metadata": self.metadata,
        }


@dataclass
class StreamTokenResult:
    """Dataclass holding access token and temporary stream URL details [NFR-2, NFR-5]."""

    video_id: str
    token: str
    expires_at: Optional[datetime] = None
    stream_url: str = ""

    def to_dict(self) -> Dict[str, Any]:
        """Convert StreamTokenResult to dictionary representation."""
        return {
            "video_id": self.video_id,
            "token": self.token,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "stream_url": self.stream_url,
        }


class VideoProviderPort(ABC):
    """Abstract port defining contracts for video streaming provider integration.

    Abstracts underlying video hosting, transcoding, CDN delivery, and token generation
    mechanisms from knowledge domain business logic [FR-5, NFR-2, NFR-5].
    """

    @abstractmethod
    def get_video_metadata(self, video_id_or_url: str) -> VideoMetadata:
        """Retrieve metadata, duration, and stream details for a training video asset.

        Args:
            video_id_or_url: Video identifier or asset source URL.

        Returns:
            VideoMetadata object containing video details.

        Raises:
            VideoNotFoundError: If the video asset does not exist.
            VideoUnavailableError: If the video asset is not playable.
            VideoProviderError: If communication with provider fails.
        """
        pass

    @abstractmethod
    def generate_embed_url(
        self,
        video_id_or_url: str,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        expires_in_seconds: int = 3600,
    ) -> str:
        """Generate a secure, signed embed URL for displaying a video in a media player.

        Args:
            video_id_or_url: Video identifier or asset source URL.
            user_id: Optional user identifier requesting access.
            tenant_id: Optional client organization tenant identifier.
            expires_in_seconds: Validity window for the signed embed link in seconds.

        Returns:
            Signed embed URL string.

        Raises:
            VideoNotFoundError: If the video asset does not exist.
            VideoProviderError: If link generation fails.
        """
        pass

    @abstractmethod
    def verify_stream_asset(self, video_id_or_url: str) -> bool:
        """Verify whether a video asset exists and is ready for streaming on the provider.

        Args:
            video_id_or_url: Video identifier or asset source URL.

        Returns:
            True if video asset exists and is accessible; False otherwise.
        """
        pass

    @abstractmethod
    def get_streaming_token(
        self,
        video_id_or_url: str,
        user_id: Optional[str] = None,
        expires_in_seconds: int = 3600,
    ) -> StreamTokenResult:
        """Generate a secure temporary access token for streaming video content.

        Args:
            video_id_or_url: Video identifier or asset source URL.
            user_id: Optional user identifier requesting access.
            expires_in_seconds: Token validity window in seconds.

        Returns:
            StreamTokenResult containing token string and expiration timestamp.

        Raises:
            StreamingTokenError: If token generation fails.
            VideoNotFoundError: If the video asset does not exist.
        """
        pass
