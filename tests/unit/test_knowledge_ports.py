"""Unit tests for video streaming provider port interface, dataclasses, and exceptions [FR-5, NFR-2, NFR-5]."""

from datetime import datetime, timezone
from typing import Optional

import pytest

from src.services.knowledge.ports import (
    StreamTokenResult,
    StreamingTokenError,
    VideoMetadata,
    VideoNotFoundError,
    VideoProviderError,
    VideoProviderPort,
    VideoUnavailableError,
)


class DummyVideoProviderAdapter(VideoProviderPort):
    """Minimal dummy adapter implementing VideoProviderPort for contract testing."""

    def __init__(self) -> None:
        self._videos = {
            "vid-123": VideoMetadata(
                video_id="vid-123",
                title="System Overview Training",
                duration_seconds=300,
                embed_url="https://cdn.example.com/embed/vid-123",
                thumbnail_url="https://cdn.example.com/thumbs/vid-123.jpg",
                format="mp4",
                is_available=True,
                captions_url="https://cdn.example.com/captions/vid-123.vtt",
                provider_name="test_cdn",
                metadata={"resolution": "1080p"},
            ),
            "vid-unavailable": VideoMetadata(
                video_id="vid-unavailable",
                title="Unavailable Video",
                duration_seconds=120,
                embed_url="",
                is_available=False,
            ),
        }

    def get_video_metadata(self, video_id_or_url: str) -> VideoMetadata:
        if video_id_or_url not in self._videos:
            raise VideoNotFoundError(f"Video asset '{video_id_or_url}' not found.")
        meta = self._videos[video_id_or_url]
        if not meta.is_available:
            raise VideoUnavailableError(f"Video asset '{video_id_or_url}' is unavailable.")
        return meta

    def generate_embed_url(
        self,
        video_id_or_url: str,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        expires_in_seconds: int = 3600,
    ) -> str:
        meta = self.get_video_metadata(video_id_or_url)
        return f"{meta.embed_url}?token=signed_token&expires={expires_in_seconds}"

    def verify_stream_asset(self, video_id_or_url: str) -> bool:
        if video_id_or_url in self._videos:
            return self._videos[video_id_or_url].is_available
        return False

    def get_streaming_token(
        self,
        video_id_or_url: str,
        user_id: Optional[str] = None,
        expires_in_seconds: int = 3600,
    ) -> StreamTokenResult:
        if not self.verify_stream_asset(video_id_or_url):
            raise StreamingTokenError(f"Failed to issue token for asset '{video_id_or_url}'.")
        now = datetime.now(timezone.utc)
        return StreamTokenResult(
            video_id=video_id_or_url,
            token="token-abc-123",
            expires_at=now,
            stream_url=f"https://cdn.example.com/stream/{video_id_or_url}?t=token-abc-123",
        )


def test_video_provider_exception_hierarchy():
    """Test exception inheritance hierarchy for video provider port."""
    assert issubclass(VideoNotFoundError, VideoProviderError)
    assert issubclass(VideoUnavailableError, VideoProviderError)
    assert issubclass(StreamingTokenError, VideoProviderError)


def test_video_metadata_defaults_and_dict():
    """Test VideoMetadata instantiation, default values, and serialization."""
    meta = VideoMetadata(
        video_id="vid-001",
        title="Introductory Guide",
        duration_seconds=180,
        embed_url="https://cdn.example.com/embed/vid-001",
    )

    assert meta.video_id == "vid-001"
    assert meta.title == "Introductory Guide"
    assert meta.duration_seconds == 180
    assert meta.format == "mp4"
    assert meta.is_available is True
    assert meta.provider_name == "video_cdn"

    d = meta.to_dict()
    assert d["video_id"] == "vid-001"
    assert d["title"] == "Introductory Guide"
    assert d["duration_seconds"] == 180
    assert d["embed_url"] == "https://cdn.example.com/embed/vid-001"
    assert d["thumbnail_url"] is None
    assert d["captions_url"] is None
    assert d["format"] == "mp4"
    assert d["is_available"] is True


def test_stream_token_result_serialization():
    """Test StreamTokenResult instantiation and dictionary conversion."""
    now = datetime.now(timezone.utc)
    token_res = StreamTokenResult(
        video_id="vid-001",
        token="signed_token_xyz",
        expires_at=now,
        stream_url="https://cdn.example.com/stream/vid-001",
    )

    assert token_res.video_id == "vid-001"
    assert token_res.token == "signed_token_xyz"
    assert token_res.expires_at == now

    d = token_res.to_dict()
    assert d["video_id"] == "vid-001"
    assert d["token"] == "signed_token_xyz"
    assert d["expires_at"] == now.isoformat()
    assert d["stream_url"] == "https://cdn.example.com/stream/vid-001"

    token_res_no_exp = StreamTokenResult(video_id="vid-002", token="tok")
    assert token_res_no_exp.to_dict()["expires_at"] is None


def test_dummy_video_provider_adapter_success():
    """Test DummyVideoProviderAdapter methods under valid conditions."""
    adapter = DummyVideoProviderAdapter()

    assert adapter.verify_stream_asset("vid-123") is True
    assert adapter.verify_stream_asset("vid-missing") is False

    meta = adapter.get_video_metadata("vid-123")
    assert meta.title == "System Overview Training"
    assert meta.duration_seconds == 300

    embed_url = adapter.generate_embed_url("vid-123", expires_in_seconds=1800)
    assert "https://cdn.example.com/embed/vid-123" in embed_url
    assert "expires=1800" in embed_url

    st = adapter.get_streaming_token("vid-123")
    assert st.video_id == "vid-123"
    assert st.token == "token-abc-123"


def test_dummy_video_provider_adapter_error_cases():
    """Test DummyVideoProviderAdapter raising expected exceptions for missing/unavailable videos."""
    adapter = DummyVideoProviderAdapter()

    with pytest.raises(VideoNotFoundError) as exc_info:
        adapter.get_video_metadata("non-existent-vid")
    assert "not found" in str(exc_info.value)

    with pytest.raises(VideoUnavailableError) as exc_info:
        adapter.get_video_metadata("vid-unavailable")
    assert "unavailable" in str(exc_info.value)

    with pytest.raises(StreamingTokenError) as exc_info:
        adapter.get_streaming_token("non-existent-vid")
    assert "Failed to issue token" in str(exc_info.value)
