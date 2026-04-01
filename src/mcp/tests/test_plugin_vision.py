# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the vision/image understanding plugin."""

from __future__ import annotations

import base64
import importlib.util
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


def _load_vision_plugin():
    """Load the vision plugin module from the plugins directory."""
    plugin_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "plugins"
        / "vision"
        / "plugin.py"
    )
    if not plugin_path.exists():
        pytest.skip("Vision plugin not found at expected path")

    spec = importlib.util.spec_from_file_location("vision_plugin_test", str(plugin_path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def vision_plugin():
    """Import the vision plugin module."""
    return _load_vision_plugin()


class TestImageEncoding:
    """Test base64 image encoding."""

    def test_encode_png(self, vision_plugin, tmp_path):
        """_encode_image correctly encodes a PNG file."""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 50)

        b64_data, mime = vision_plugin._encode_image(str(img))

        assert isinstance(b64_data, str)
        assert mime == "image/png"
        # Verify round-trip
        decoded = base64.b64decode(b64_data)
        assert decoded[:4] == b"\x89PNG"

    def test_encode_jpeg(self, vision_plugin, tmp_path):
        """_encode_image returns correct MIME for JPEG."""
        img = tmp_path / "photo.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        _, mime = vision_plugin._encode_image(str(img))
        assert mime == "image/jpeg"

    def test_encode_rejects_large_files(self, vision_plugin, tmp_path):
        """_encode_image raises for files over 20MB."""
        img = tmp_path / "huge.png"
        img.write_bytes(b"\x00" * (21 * 1024 * 1024))

        with pytest.raises(ValueError, match="Image too large"):
            vision_plugin._encode_image(str(img))


class TestAnalyzeImage:
    """Test async image analysis via vision LLM."""

    @pytest.mark.asyncio
    async def test_analyze_image_calls_bifrost(self, vision_plugin, tmp_path):
        """analyze_image sends base64 image to Bifrost."""
        img = tmp_path / "test.png"
        img.write_bytes(b"\x89PNG" + b"\x00" * 50)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [
                {
                    "message": {
                        "content": "A test image showing placeholder content."
                    }
                }
            ]
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(vision_plugin._get_httpx(), "AsyncClient", return_value=mock_client):
            result = await vision_plugin.analyze_image(str(img))

        assert result == "A test image showing placeholder content."
        mock_client.post.assert_called_once()

        # Verify the payload structure
        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["model"] == "openai/gpt-4o"
        assert len(payload["messages"]) == 1
        content = payload["messages"][0]["content"]
        assert any(c["type"] == "image_url" for c in content)

    @pytest.mark.asyncio
    async def test_analyze_image_file_not_found(self, vision_plugin):
        """analyze_image raises FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError):
            await vision_plugin.analyze_image("/nonexistent/image.png")

    @pytest.mark.asyncio
    async def test_analyze_image_unsupported_format(self, vision_plugin, tmp_path):
        """analyze_image raises ValueError for unsupported extensions."""
        bad = tmp_path / "test.psd"
        bad.write_bytes(b"\x00" * 50)

        with pytest.raises(ValueError, match="Unsupported image format"):
            await vision_plugin.analyze_image(str(bad))

    @pytest.mark.asyncio
    async def test_analyze_image_custom_model(self, vision_plugin, tmp_path, monkeypatch):
        """VISION_MODEL env var overrides the default model."""
        monkeypatch.setenv("VISION_MODEL", "anthropic/claude-3.5-sonnet")

        img = tmp_path / "test.jpg"
        img.write_bytes(b"\xff\xd8\xff" + b"\x00" * 50)

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": "An image."}}]
        }

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch.object(vision_plugin._get_httpx(), "AsyncClient", return_value=mock_client):
            await vision_plugin.analyze_image(str(img))

        call_args = mock_client.post.call_args
        payload = call_args.kwargs.get("json") or call_args[1].get("json")
        assert payload["model"] == "anthropic/claude-3.5-sonnet"


class TestVisionRegister:
    """Test plugin registration behavior."""

    def test_register_skips_existing_parsers(self, vision_plugin, monkeypatch):
        """Vision register() doesn't override existing parsers (e.g., OCR)."""
        fake_registry = {".png": lambda f: {"text": "ocr result"}}
        monkeypatch.setattr("parsers.registry.PARSER_REGISTRY", fake_registry)

        vision_plugin.register()

        # .png should still be the original OCR parser
        assert fake_registry[".png"]("test") == {"text": "ocr result"}

    def test_register_adds_unregistered_types(self, vision_plugin, monkeypatch):
        """Vision register() adds parsers for extensions not already registered."""
        fake_registry = {}
        monkeypatch.setattr("parsers.registry.PARSER_REGISTRY", fake_registry)

        vision_plugin.register()

        for ext in vision_plugin.SUPPORTED_EXTENSIONS:
            assert ext in fake_registry


class TestDetectPlugin:
    """Test the multimodal service plugin detection."""

    def test_detect_audio(self):
        """Audio extensions route to audio plugin."""
        from services.multimodal import _detect_plugin

        assert _detect_plugin("/path/to/file.mp3", "") == "audio"
        assert _detect_plugin("/path/to/file.wav", "") == "audio"
        assert _detect_plugin("/path/to/file.flac", "") == "audio"

    def test_detect_ocr(self):
        """Image extensions route to OCR plugin by default."""
        from services.multimodal import _detect_plugin

        assert _detect_plugin("/path/to/file.png", "") == "ocr"
        assert _detect_plugin("/path/to/file.jpg", "") == "ocr"
        assert _detect_plugin("/path/to/file.tiff", "") == "ocr"

    def test_detect_override(self):
        """Plugin override forces specific plugin."""
        from services.multimodal import _detect_plugin

        assert _detect_plugin("/path/to/file.png", "vision") == "vision"
        assert _detect_plugin("/path/to/file.png", "ocr") == "ocr"

    def test_detect_unknown_extension(self):
        """Unknown extensions raise ValueError."""
        from services.multimodal import _detect_plugin

        with pytest.raises(ValueError, match="No multi-modal plugin"):
            _detect_plugin("/path/to/file.xyz", "")

    def test_detect_unknown_override(self):
        """Unknown plugin override raises ValueError."""
        from services.multimodal import _detect_plugin

        with pytest.raises(ValueError, match="Unknown plugin"):
            _detect_plugin("/path/to/file.png", "nonexistent")


class TestIngestMultimodal:
    """Test the multimodal ingestion service."""

    @pytest.mark.asyncio
    async def test_ingest_blocked_in_community(self):
        """ingest_multimodal raises FeatureGateError in community tier."""
        from errors import FeatureGateError
        from services.multimodal import ingest_multimodal

        with patch("config.features.FEATURE_TIER", "community"), \
             pytest.raises(FeatureGateError, match="pro"):
            await ingest_multimodal("/some/file.mp3")

    @pytest.mark.asyncio
    async def test_ingest_file_not_found(self):
        """ingest_multimodal returns error for missing files."""
        from services.multimodal import ingest_multimodal

        with patch("services.multimodal.config") as mock_config, \
             patch("config.features.FEATURE_TIER", "pro"):
            mock_config.FEATURE_TIER = "pro"
            result = await ingest_multimodal("/nonexistent/file.mp3")

        assert result["status"] == "error"
        assert "File not found" in result["error"]
