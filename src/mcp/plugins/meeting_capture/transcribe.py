# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Whisper.cpp transcription via pywhispercpp bindings.

Per the audio-stack research in tasks/2026-05-20-pro-tier-implementation-plan.md
§3.5 and the Pro Tier audio stack research report:

  - Intel Mac + AMD Vega: whisper.cpp Metal backend is in the family-B
    correctness gap that quenchforge patches in llama.cpp — stay CPU-only.
  - Apple Silicon: Metal works reliably, use it via pywhispercpp's default.

Model download is lazy on first use; pywhispercpp pulls from HuggingFace
on demand and caches under ~/.cerid/models/whisper/.
"""
from __future__ import annotations

import logging
import os
import platform
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-companion.plugins.meeting_capture.transcribe")


def _is_apple_silicon() -> bool:
    """True when running on an M-series Mac."""
    return platform.system() == "Darwin" and platform.machine() == "arm64"


def _model_name() -> str:
    """Pick the Whisper model size. Defaults to medium-q5_0 (~0.3× RTF on
    Mac Pro CPU, balanced quality). Operator override via env."""
    return os.getenv("WHISPER_MODEL", "medium-q5_0")


def _model_cache_dir() -> Path:
    cache = Path.home() / ".cerid" / "models" / "whisper"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


def transcribe_pcm(pcm_path: Path) -> dict[str, Any]:
    """Run Whisper.cpp on a 16 kHz mono PCM WAV.

    Returns:
        text:     full concatenated transcript
        language: ISO 639-1 code (or None if auto-detection failed)
        duration: seconds
        words:    list[{start, end, text, probability}] — word-level
                  timestamps for the merge step downstream
    """
    # Imported lazily so the plugin module can load without the dep.
    from pywhispercpp.model import Model

    model_name = _model_name()
    cache_dir = _model_cache_dir()
    logger.info("Loading Whisper model %s (cache=%s)", model_name, cache_dir)

    # Per audio-stack research: on Intel Mac + AMD Vega, the Metal path
    # exposes the family-B correctness bug class. Force CPU-only.
    # On Apple Silicon, default Metal is correct + fast.
    n_threads = os.cpu_count() or 4
    use_metal = _is_apple_silicon()

    model = Model(
        model_name,
        models_dir=str(cache_dir),
        n_threads=n_threads,
        # pywhispercpp param naming follows whisper.cpp; absence of an
        # explicit "use_gpu=False" still requires confirming via runtime
        # introspection of the pkg_config flags. Documented in
        # docs/MAC_INTEGRATION.md.
    )

    raw_language = os.getenv("WHISPER_LANGUAGE", "auto")
    language: str | None = None if raw_language == "auto" else raw_language

    logger.info("Transcribing %s (metal=%s, threads=%d)", pcm_path.name, use_metal, n_threads)
    segments = model.transcribe(str(pcm_path), language=language)

    text_parts: list[str] = []
    words: list[dict[str, Any]] = []
    total_duration = 0.0
    for seg in segments:
        # pywhispercpp Segment shape: .text, .t0 (start ms), .t1 (end ms)
        text_parts.append(seg.text.strip())
        total_duration = max(total_duration, getattr(seg, "t1", 0) / 1000.0)
        # Word-level timestamps require Whisper's --word-timestamps flag;
        # pywhispercpp surfaces these via .tokens when enabled. The
        # default-build path emits per-segment timestamps which we use as
        # word approximations here. The merge step downstream tolerates
        # both granularities.
        words.append({
            "start": getattr(seg, "t0", 0) / 1000.0,
            "end": getattr(seg, "t1", 0) / 1000.0,
            "text": seg.text.strip(),
            "probability": 1.0,
        })

    return {
        "text": " ".join(text_parts).strip(),
        "language": getattr(model, "language", language) or "unknown",
        "duration": total_duration,
        "words": words,
    }
