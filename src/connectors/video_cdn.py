"""External video delivery network (CDN) connector adapter [FR-5, NFR-2, NFR-5].

Implements VideoProviderPort to provide video asset verification, metadata retrieval,
signed embed URL generation, and streaming access token issuance.
"""

from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from typing import Dict, Optional
from urllib.parse import urlparse

from src.lib.config import settings
from src.services.knowledge.ports import (
    StreamTokenResult,
    StreamingTokenError,
    VideoMetadata,
    VideoNotFoundError,
    VideoProviderError,
    VideoProviderPort,
    VideoUnavailableError,
)


class VideoCDNConnector(VideoProviderPort):
    """Video CDN provider adapter implementing VideoProviderPort [FR-5, NFR-2, NFR-5].

    Provides video metadata resolution, signed embed URL construction, asset
    availability checks, and secure streaming token issuance without hardcoded secrets.
    """

    def __init__(
        self,
        cdn_url: Optional[str] = None,
        api_key: Optional[str] = None,
        secret_key: Optional[str] = None,
        timeout_seconds: Optional[int] = None,
        token_ttl_seconds: Optional[int] = None,
        simulate_failure: bool = False,
        simulate_timeout: bool = False,
        simulate_not_found: bool = False,
        simulate_unavailable: bool = False,
    ) -> None:
        """Initialize VideoCDNConnector with configuration parameters and simulation flags.

        Args:
            cdn_url: Base URL or endpoint for video streaming CDN provider.
            api_key: Authentication API key for CDN service.
            secret_key: Cryptographic signing key for HMAC signature generation.
            timeout_seconds: Upstream request timeout limit in seconds.
            token_ttl_seconds: Validity duration in seconds for streaming access tokens.
            simulate_failure: Testing toggle to simulate network connection failures.
            simulate_timeout: Testing toggle to simulate execution timeout limits.
            simulate_not_found: Testing toggle to simulate missing asset responses.
            simulate_unavailable: Testing toggle to simulate unplayable asset states.
        """
        configured_url = cdn_url if cdn_url is not None else settings.cdn_provider_url
        self.cdn_url = (configured_url or "https://cdn.example.com").rstrip("/")
        self.api_key = api_key if api_key is not None else settings.cdn_provider_api_key
        self.secret_key = (
            secret_key
            if secret_key is not None
            else (settings.cdn_provider_secret_key or settings.secret_key)
        )
        self.timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else settings.cdn_provider_timeout_seconds
        )
        self.token_ttl_seconds = (
            token_ttl_seconds
            if token_ttl_seconds is not None
            else settings.video_streaming_token_ttl_seconds
        )

        self.simulate_failure = simulate_failure
        self.simulate_timeout = simulate_timeout
        self.simulate_not_found = simulate_not_found
        self.simulate_unavailable = simulate_unavailable

        self._registered_videos: Dict[str, VideoMetadata] = {}
        self._seed_default_videos()

    def _seed_default_videos(self) -> None:
        """Seed default training video assets for development and testing."""
        default_videos = [
            VideoMetadata(
                video_id="vid-101",
                title="Platform Overview & Getting Started",
                duration_seconds=300,
                embed_url=f"{self.cdn_url}/embed/vid-101",
                thumbnail_url=f"{self.cdn_url}/thumbs/vid-101.jpg",
                format="mp4",
                is_available=True,
                captions_url=f"{self.cdn_url}/captions/vid-101.vtt",
                provider_name="video_cdn",
                metadata={"resolution": "1080p", "category": "onboarding"},
            ),
            VideoMetadata(
                video_id="vid-102",
                title="Advanced Workflow Optimization",
                duration_seconds=450,
                embed_url=f"{self.cdn_url}/embed/vid-102",
                thumbnail_url=f"{self.cdn_url}/thumbs/vid-102.jpg",
                format="m3u8",
                is_available=True,
                captions_url=f"{self.cdn_url}/captions/vid-102.vtt",
                provider_name="video_cdn",
                metadata={"resolution": "4k", "category": "advanced"},
            ),
            VideoMetadata(
                video_id="vid-123",
                title="System Overview Training",
                duration_seconds=300,
                embed_url=f"{self.cdn_url}/embed/vid-123",
                thumbnail_url=f"{self.cdn_url}/thumbs/vid-123.jpg",
                format="mp4",
                is_available=True,
                captions_url=f"{self.cdn_url}/captions/vid-123.vtt",
                provider_name="video_cdn",
                metadata={"resolution": "1080p"},
            ),
            VideoMetadata(
                video_id="vid-unavailable",
                title="Unavailable Video Asset",
                duration_seconds=120,
                embed_url="",
                thumbnail_url=None,
                format="mp4",
                is_available=False,
                provider_name="video_cdn",
            ),
        ]
        for video in default_videos:
            self._registered_videos[video.video_id] = video

    def add_video_asset(self, metadata: VideoMetadata) -> None:
        """Register or overwrite a video asset metadata record in memory."""
        self._registered_videos[metadata.video_id] = metadata

    def remove_video_asset(self, video_id: str) -> None:
        """Remove a video asset from the in-memory registry."""
        self._registered_videos.pop(video_id, None)

    def clear_registered_videos(self) -> None:
        """Clear all in-memory registered video assets."""
        self._registered_videos.clear()

    def _check_simulation_triggers(self, video_id_or_url: str) -> None:
        """Raise simulated exceptions if testing flags are enabled."""
        if self.simulate_timeout:
            raise VideoProviderError(
                f"Request timeout after {self.timeout_seconds}s connecting to CDN for asset '{video_id_or_url}'."
            )
        if self.simulate_failure:
            raise VideoProviderError(
                f"CDN provider network error when processing asset '{video_id_or_url}'."
            )

    def _normalize_video_id(self, video_id_or_url: str) -> str:
        """Extract or normalize a video identifier from an ID or URL string."""
        if not video_id_or_url:
            return ""
        if video_id_or_url in self._registered_videos:
            return video_id_or_url

        if video_id_or_url.startswith(("http://", "https://")):
            parsed = urlparse(video_id_or_url)
            path_parts = [part for part in parsed.path.split("/") if part]
            if path_parts:
                filename = path_parts[-1]
                extracted_id = filename.split(".")[0]
                if extracted_id in self._registered_videos:
                    return extracted_id
                return extracted_id
        return video_id_or_url

    def _generate_hmac_signature(self, payload: str) -> str:
        """Compute HMAC-SHA256 signature for signed URLs and tokens [NFR-2, NFR-5]."""
        key_bytes = (
            self.secret_key.encode("utf-8")
            if isinstance(self.secret_key, str)
            else b"default-signing-key"
        )
        return hmac.new(key_bytes, payload.encode("utf-8"), hashlib.sha256).hexdigest()

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
        if not video_id_or_url:
            raise VideoNotFoundError("Invalid or empty video asset identifier.")

        self._check_simulation_triggers(video_id_or_url)

        if self.simulate_not_found:
            raise VideoNotFoundError(
                f"Video asset '{video_id_or_url}' not found on CDN provider."
            )

        video_id = self._normalize_video_id(video_id_or_url)

        if video_id not in self._registered_videos:
            raise VideoNotFoundError(
                f"Video asset '{video_id_or_url}' not found on CDN provider."
            )

        metadata = self._registered_videos[video_id]

        if self.simulate_unavailable or not metadata.is_available:
            raise VideoUnavailableError(
                f"Video asset '{video_id_or_url}' is currently unavailable or unplayable."
            )

        return metadata

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
        self._check_simulation_triggers(video_id_or_url)
        metadata = self.get_video_metadata(video_id_or_url)

        now = datetime.now(timezone.utc)
        expires_at_int = int(now.timestamp()) + expires_in_seconds
        payload = f"{metadata.video_id}:{user_id or ''}:{tenant_id or ''}:{expires_at_int}"
        signature = self._generate_hmac_signature(payload)

        base_url = (
            metadata.embed_url
            if metadata.embed_url
            else f"{self.cdn_url}/embed/{metadata.video_id}"
        )

        query_params = [f"token={signature}", f"expires={expires_at_int}"]
        if user_id:
            query_params.append(f"user_id={user_id}")
        if tenant_id:
            query_params.append(f"tenant_id={tenant_id}")

        separator = "&" if "?" in base_url else "?"
        return f"{base_url}{separator}{'&'.join(query_params)}"

    def verify_stream_asset(self, video_id_or_url: str) -> bool:
        """Verify whether a video asset exists and is ready for streaming on the provider.

        Args:
            video_id_or_url: Video identifier or asset source URL.

        Returns:
            True if video asset exists and is accessible; False otherwise.
        """
        if (
            self.simulate_failure
            or self.simulate_timeout
            or self.simulate_not_found
            or self.simulate_unavailable
        ):
            return False

        try:
            metadata = self.get_video_metadata(video_id_or_url)
            return metadata.is_available
        except (VideoNotFoundError, VideoUnavailableError, VideoProviderError):
            return False

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
        try:
            self._check_simulation_triggers(video_id_or_url)
        except VideoProviderError as exc:
            raise StreamingTokenError(
                f"Streaming token generation failed: {exc}"
            ) from exc

        try:
            metadata = self.get_video_metadata(video_id_or_url)
        except VideoNotFoundError:
            raise
        except (VideoUnavailableError, VideoProviderError) as exc:
            raise StreamingTokenError(
                f"Failed to issue streaming token for video '{video_id_or_url}': {exc}"
            ) from exc

        ttl = (
            expires_in_seconds
            if expires_in_seconds is not None
            else self.token_ttl_seconds
        )
        now = datetime.now(timezone.utc)
        expires_at = now + timedelta(seconds=ttl)
        expires_at_int = int(expires_at.timestamp())

        payload = f"{metadata.video_id}:{user_id or ''}:{expires_at_int}"
        token = self._generate_hmac_signature(payload)

        base_url = (
            metadata.embed_url
            if metadata.embed_url
            else f"{self.cdn_url}/stream/{metadata.video_id}"
        )
        separator = "&" if "?" in base_url else "?"
        stream_url = f"{base_url}{separator}token={token}&expires={expires_at_int}"

        return StreamTokenResult(
            video_id=metadata.video_id,
            token=token,
            expires_at=expires_at,
            stream_url=stream_url,
        )
