# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the audio transcription plugin."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# We need to ensure parsers.registry is importable
# (conftest.py handles sys.path insertion)


class _FakeSegment:
    """Mock Whisper segment."""

    def __init__(self, start: float, end: float, text: str):
        self.start = start
        self.end = end
        self.text = text


class _FakeInfo:
    """Mock Whisper transcription info."""

    def __init__(self, language: str = "en", duration: float = 30.0):
        self.language = language
        self.duration = duration


def _make_mock_whisper_model():
    """Create a mock WhisperModel."""
    model = MagicMock()
    segments = [
        _FakeSegment(0.0, 2.5, " Hello world"),
        _FakeSegment(2.5, 5.0, " This is a test"),
    ]
    info = _FakeInfo(language="en", duration=5.0)
    model.transcribe.return_value = (iter(segments), info)
    return model


@pytest.fixture
def mock_faster_whisper(monkeypatch):
    """Patch faster_whisper before the audio plugin tries to import it."""
    fake_module = MagicMock()
    mock_model = _make_mock_whisper_model()
    fake_module.WhisperModel.return_value = mock_model
    monkeypatch.setitem(sys.modules, "faster_whisper", fake_module)
    return mock_model


@pytest.fixture
def audio_plugin(mock_faster_whisper):
    """Import audio plugin with mocked faster_whisper."""
    # Clear any cached model
    plugin_path = (
        Path(__file__).resolve().parent.parent.parent.parent
        / "plugins"
        / "audio"
        / "plugin.py"
    )
    if not plugin_path.exists():
        pytest.skip("Audio plugin not found at expected path")

    import importlib.util

    spec = importlib.util.spec_from_file_location("audio_plugin_test", str(plugin_path))
    module = importlib.util.module_from_spec(spec)
    # Reset singleton
    module._whisper_model = None
    spec.loader.exec_module(module)
    return module


class TestAudioTranscribe:
    """Test audio transcription functionality."""

    def test_transcribe_returns_text(self, audio_plugin, tmp_path):
        """transcribe() returns combined text from all segments."""
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"\x00" * 100)

        result = audio_plugin.transcribe(str(wav))

        assert "text" in result
        assert result["text"] == "Hello world This is a test"
        assert result["language"] == "en"
        assert result["duration"] == 5.0

    def test_transcribe_with_timestamps(self, audio_plugin, tmp_path):
        """transcribe() includes segments when include_timestamps=True."""
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"\x00" * 100)

        result = audio_plugin.transcribe(str(wav), include_timestamps=True)

        assert "segments" in result
        assert len(result["segments"]) == 2
        assert result["segments"][0]["start"] == 0.0
        assert result["segments"][0]["text"] == "Hello world"

    def test_transcribe_without_timestamps(self, audio_plugin, tmp_path):
        """transcribe() omits segments when include_timestamps=False."""
        wav = tmp_path / "test.wav"
        wav.write_bytes(b"\x00" * 100)

        result = audio_plugin.transcribe(str(wav), include_timestamps=False)

        assert "segments" not in result

    def test_transcribe_file_not_found(self, audio_plugin):
        """transcribe() raises FileNotFoundError for missing files."""
        with pytest.raises(FileNotFoundError):
            audio_plugin.transcribe("/nonexistent/audio.wav")

    def test_transcribe_unsupported_format(self, audio_plugin, tmp_path):
        """transcribe() raises ValueError for unsupported extensions."""
        bad = tmp_path / "test.xyz"
        bad.write_bytes(b"\x00" * 100)

        with pytest.raises(ValueError, match="Unsupported audio format"):
            audio_plugin.transcribe(str(bad))


class TestAudioParseInterface:
    """Test parser registry interface."""

    def test_parse_audio_returns_dict(self, audio_plugin, tmp_path):
        """parse_audio() returns the standard parser dict."""
        wav = tmp_path / "test.mp3"
        wav.write_bytes(b"\x00" * 100)

        result = audio_plugin.parse_audio(str(wav))

        assert "text" in result
        assert result["file_type"] == ".mp3"
        assert result["page_count"] is None


class TestAudioRegister:
    """Test plugin registration."""

    def test_register_adds_to_registry(self, audio_plugin, monkeypatch):
        """register() adds parsers for all supported audio extensions."""
        fake_registry = {}
        monkeypatch.setattr(
            "parsers.registry.PARSER_REGISTRY", fake_registry
        )

        audio_plugin.register()

        for ext in audio_plugin.SUPPORTED_EXTENSIONS:
            assert ext in fake_registry
            assert fake_registry[ext] == audio_plugin.parse_audio

    def test_whisper_model_env_var(self, monkeypatch):
        """WHISPER_MODEL env var configures the model size."""
        monkeypatch.setenv("WHISPER_MODEL", "large-v3")

        mock_whisper = MagicMock()
        mock_whisper.WhisperModel.return_value = _make_mock_whisper_model()
        monkeypatch.setitem(sys.modules, "faster_whisper", mock_whisper)

        # Re-import to pick up env var
        plugin_path = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "plugins"
            / "audio"
            / "plugin.py"
        )
        if not plugin_path.exists():
            pytest.skip("Audio plugin not found")

        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "audio_plugin_env_test", str(plugin_path)
        )
        module = importlib.util.module_from_spec(spec)
        module._whisper_model = None
        spec.loader.exec_module(module)

        # Trigger model load
        module._get_model()

        mock_whisper.WhisperModel.assert_called_once_with(
            "large-v3", device="cpu", compute_type="int8"
        )
