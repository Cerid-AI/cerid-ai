# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BSL-1.1

"""Audio transcription plugin — convert audio files to searchable text via Whisper.

Uses faster-whisper (CTranslate2-based) for efficient CPU-only transcription.

System dependency for non-WAV formats:
  - ffmpeg (required for .mp3, .m4a, .ogg, .flac, .webm decoding)
  - macOS:  brew install ffmpeg
  - Debian: apt-get install ffmpeg
  - Docker: add to Dockerfile (apt-get install -y ffmpeg)

Python dependency: faster-whisper>=1.0.0

Environment variables:
  WHISPER_MODEL  — model size (default: "base"). Options: tiny, base, small, medium, large-v3
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-companion.plugins.audio")

# Supported audio extensions
SUPPORTED_EXTENSIONS = [".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm"]

# Model singleton (loaded once, reused across calls)
_whisper_model = None


def _get_model():
    """Lazy-load the Whisper model (singleton)."""
    global _whisper_model
    if _whisper_model is not None:
        return _whisper_model

    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise ImportError(
            "faster-whisper is required for audio transcription plugin. "
            "Install with: pip install faster-whisper>=1.0.0\n"
            "Non-WAV formats also require ffmpeg system package."
        )

    model_size = os.getenv("WHISPER_MODEL", "base")
    logger.info("Loading Whisper model: %s (CPU mode)", model_size)

    _whisper_model = WhisperModel(
        model_size,
        device="cpu",
        compute_type="int8",
    )
    logger.info("Whisper model loaded successfully")
    return _whisper_model


def transcribe(file_path: str, include_timestamps: bool = False) -> dict[str, Any]:
    """
    Transcribe an audio file to text.

    Args:
        file_path: Path to audio file.
        include_timestamps: If True, include per-segment timestamps.

    Returns:
        {"text": str, "segments": list[dict] | None, "language": str, "duration": float}
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported audio format: {ext}. "
            f"Supported: {SUPPORTED_EXTENSIONS}"
        )

    logger.info("Transcribing audio: %s (format: %s)", path.name, ext)

    model = _get_model()
    segments_iter, info = model.transcribe(
        str(path),
        beam_size=5,
        language=None,  # auto-detect
        vad_filter=True,  # filter silence
    )

    segments = list(segments_iter)
    full_text = " ".join(seg.text.strip() for seg in segments).strip()

    result: dict[str, Any] = {
        "text": full_text,
        "language": info.language,
        "duration": info.duration,
    }

    if include_timestamps:
        result["segments"] = [
            {
                "start": round(seg.start, 2),
                "end": round(seg.end, 2),
                "text": seg.text.strip(),
            }
            for seg in segments
        ]

    logger.info(
        "Transcribed %s: %d chars, lang=%s, duration=%.1fs",
        path.name,
        len(full_text),
        info.language,
        info.duration,
    )
    return result


def parse_audio(file_path: str) -> dict[str, Any]:
    """
    Parse audio file for KB ingestion (parser registry interface).

    Args:
        file_path: Path to audio file.

    Returns:
        {"text": str, "file_type": str, "page_count": None}
    """
    result = transcribe(file_path)
    ext = Path(file_path).suffix.lower()

    return {
        "text": result["text"],
        "file_type": ext,
        "page_count": None,
    }


def register() -> None:
    """Register audio parsers for supported audio file types."""
    from parsers.registry import PARSER_REGISTRY

    for ext in SUPPORTED_EXTENSIONS:
        if ext in PARSER_REGISTRY:
            logger.info("Audio plugin overriding parser for %s", ext)
        PARSER_REGISTRY[ext] = parse_audio

    logger.info(
        "Audio plugin registered parsers for: %s",
        ", ".join(SUPPORTED_EXTENSIONS),
    )
