# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Speaker diarization via pyannote.audio 3.x.

Requires:
  - HF_TOKEN env var (Hugging Face access token).
  - User has accepted terms on these gated models:
      pyannote/speaker-diarization-3.1
      pyannote/segmentation-3.0

  Cerid's onboarding wizard walks the user through both steps the
  first time meeting capture is invoked.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-companion.plugins.meeting_capture.diarize")

_PIPELINE_NAME = "pyannote/speaker-diarization-3.1"
_pipeline_cache: Any = None  # lazy-loaded singleton


def _load_pipeline():
    """Cache the pyannote Pipeline across calls — model load is ~3s and
    each meeting parse would otherwise pay the cost twice (transcribe +
    diarize)."""
    global _pipeline_cache
    if _pipeline_cache is not None:
        return _pipeline_cache

    hf_token = os.getenv("HF_TOKEN", "")
    if not hf_token:
        raise RuntimeError(
            "HF_TOKEN environment variable is not set. Diarization requires "
            "a Hugging Face access token with read access to "
            "pyannote/speaker-diarization-3.1 and pyannote/segmentation-3.0. "
            "Run the Cerid onboarding wizard to set this up."
        )

    from pyannote.audio import Pipeline

    logger.info("Loading pyannote pipeline %s (first use; ~3s)", _PIPELINE_NAME)
    _pipeline_cache = Pipeline.from_pretrained(_PIPELINE_NAME, use_auth_token=hf_token)
    return _pipeline_cache


def diarize_pcm(
    pcm_path: Path,
    *,
    min_speakers: int | None = None,
    max_speakers: int | None = None,
) -> dict[str, Any]:
    """Run pyannote diarization on a 16 kHz mono PCM file.

    Args:
        pcm_path: WAV file at 16 kHz mono.
        min_speakers / max_speakers: tighten pyannote's auto-detection.
            When calendar_stitch matched a known event, pass
            ``max_speakers=len(attendees)`` for the documented accuracy
            lift per audio-stack research §3.

    Returns:
        speaker_turns: list[{start, end, speaker}] — pyannote labels are
                       SPEAKER_00, SPEAKER_01, … (not resolved to real names)
        speaker_count: int — distinct speakers in the result
        quality:       "full" | "partial" | "none"
                       partial = some segments dropped due to confidence
                       below threshold; none = pipeline returned no turns.
    """
    pipeline = _load_pipeline()

    # Default bounds from manifest. Operators can override per-call.
    if min_speakers is None:
        min_speakers = int(os.getenv("DIARIZATION_MIN_SPEAKERS", "2"))
    if max_speakers is None:
        max_speakers = int(os.getenv("DIARIZATION_MAX_SPEAKERS", "10"))

    kwargs: dict[str, Any] = {}
    if min_speakers:
        kwargs["min_speakers"] = min_speakers
    if max_speakers:
        kwargs["max_speakers"] = max_speakers

    logger.info(
        "Diarizing %s (min=%d, max=%d)",
        pcm_path.name, min_speakers, max_speakers,
    )
    annotation = pipeline(str(pcm_path), **kwargs)

    turns: list[dict[str, Any]] = []
    speakers: set[str] = set()
    for turn, _, speaker in annotation.itertracks(yield_label=True):
        turns.append({
            "start": float(turn.start),
            "end": float(turn.end),
            "speaker": speaker,
        })
        speakers.add(speaker)

    quality = "full" if turns else "none"

    return {
        "speaker_turns": turns,
        "speaker_count": len(speakers),
        "quality": quality,
    }
