"""Unit tests for VideoCDNConnector adapter [FR-5, NFR-2, NFR-5]."""

from datetime import datetime, timezone

import pytest

from src.connectors.video_cdn import VideoCDNConnector
from src.services.knowledge.ports import (
    StreamTokenResult,
    StreamingTokenError,
    VideoMetadata,
    VideoNotFoundError,
    VideoProviderError,
    VideoProviderPort,
    VideoUnavailableError,
)


def test_video_cdn_connector_implements_port():
    """Verify VideoCDNConnector inherits from VideoProviderPort."""
    connector = VideoCDNConnector()
    assert isinstance(connector, VideoProviderPort)


def test_get_video_metadata_success():
    """Test retrieving metadata for seeded default video asset."""
    connector = VideoCDNConnector()
    meta = connector.get_video_metadata("vid-101")

    assert isinstance(meta, VideoMetadata)
    assert meta.video_id == "vid-101"
    assert meta.title == "Platform Overview & Getting Started"
    assert meta.duration_seconds == 300
    assert meta.is_available is True
    assert "token" not in meta.embed_url  # Raw embed URL before signing


def test_get_video_metadata_by_url():
    """Test retrieving metadata when passing a full URL string."""
    connector = VideoCDNConnector()
    meta = connector.get_video_metadata("https://cdn.example.com/embed/vid-102.mp4")

    assert meta.video_id == "vid-102"
    assert meta.format == "m3u8"


def test_get_video_metadata_not_found():
    """Test VideoNotFoundError is raised for non-existent video asset."""
    connector = VideoCDNConnector()
    with pytest.raises(VideoNotFoundError) as exc_info:
        connector.get_video_metadata("nonexistent-video-999")

    assert "nonexistent-video-999" in str(exc_info.value)


def test_get_video_metadata_empty_id():
    """Test VideoNotFoundError is raised when video_id_or_url is empty."""
    connector = VideoCDNConnector()
    with pytest.raises(VideoNotFoundError):
        connector.get_video_metadata("")


def test_get_video_metadata_unavailable():
    """Test VideoUnavailableError is raised when video asset is marked unavailable."""
    connector = VideoCDNConnector()
    with pytest.raises(VideoUnavailableError) as exc_info:
        connector.get_video_metadata("vid-unavailable")

    assert "vid-unavailable" in str(exc_info.value)


def test_generate_embed_url_success():
    """Test generating signed embed URL with user and tenant tokens."""
    connector = VideoCDNConnector(secret_key="my-test-secret-key")
    embed_url = connector.generate_embed_url(
        video_id_or_url="vid-101",
        user_id="usr-123",
        tenant_id="ten-456",
        expires_in_seconds=1800,
    )

    assert embed_url.startswith("https://cdn.example.com/embed/vid-101")
    assert "token=" in embed_url
    assert "expires=" in embed_url
    assert "user_id=usr-123" in embed_url
    assert "tenant_id=ten-456" in embed_url


def test_verify_stream_asset():
    """Test asset stream availability verification."""
    connector = VideoCDNConnector()

    assert connector.verify_stream_asset("vid-101") is True
    assert connector.verify_stream_asset("vid-unavailable") is False
    assert connector.verify_stream_asset("nonexistent-vid") is False


def test_get_streaming_token_success():
    """Test generating temporary streaming access token result."""
    connector = VideoCDNConnector(secret_key="secret-key-xyz")
    result = connector.get_streaming_token(
        video_id_or_url="vid-101",
        user_id="usr-789",
        expires_in_seconds=3600,
    )

    assert isinstance(result, StreamTokenResult)
    assert result.video_id == "vid-101"
    assert len(result.token) > 0
    assert result.expires_at is not None
    assert result.expires_at > datetime.now(timezone.utc)
    assert "token=" in result.stream_url


def test_get_streaming_token_not_found():
    """Test get_streaming_token raises VideoNotFoundError when asset missing."""
    connector = VideoCDNConnector()
    with pytest.raises(VideoNotFoundError):
        connector.get_streaming_token("nonexistent-vid")


def test_get_streaming_token_unavailable():
    """Test get_streaming_token raises StreamingTokenError when asset unavailable."""
    connector = VideoCDNConnector()
    with pytest.raises(StreamingTokenError):
        connector.get_streaming_token("vid-unavailable")


def test_simulated_failures_and_timeouts():
    """Test simulation flags for failure and timeout testing."""
    fail_connector = VideoCDNConnector(simulate_failure=True)
    with pytest.raises(VideoProviderError) as exc_fail:
        fail_connector.get_video_metadata("vid-101")
    assert "network error" in str(exc_fail.value)

    timeout_connector = VideoCDNConnector(simulate_timeout=True)
    with pytest.raises(VideoProviderError) as exc_timeout:
        timeout_connector.get_video_metadata("vid-101")
    assert "timeout" in str(exc_timeout.value)

    assert fail_connector.verify_stream_asset("vid-101") is False


def test_custom_video_asset_registration():
    """Test registering, removing, and clearing custom video assets."""
    connector = VideoCDNConnector()
    custom_video = VideoMetadata(
        video_id="custom-vid-001",
        title="Custom Training Lesson",
        duration_seconds=600,
        embed_url="https://cdn.example.com/embed/custom-001",
        is_available=True,
    )
    connector.add_video_asset(custom_video)

    fetched = connector.get_video_metadata("custom-vid-001")
    assert fetched.title == "Custom Training Lesson"

    connector.remove_video_asset("custom-vid-001")
    with pytest.raises(VideoNotFoundError):
        connector.get_video_metadata("custom-vid-001")
