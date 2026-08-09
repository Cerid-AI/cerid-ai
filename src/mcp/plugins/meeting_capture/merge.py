# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Merge Whisper word-timestamps with pyannote speaker turns.

The two pipelines (Whisper + pyannote) run independently on the same
16 kHz mono PCM input. This module joins them by interval overlap so each
transcribed word gets a speaker label.

Algorithm (interval overlap, O(n + m)):
  - Sort both streams by start time (Whisper already does; pyannote
    usually does).
  - Walk a two-pointer scan; for each Whisper word, find the speaker turn
    whose interval covers the word's midpoint. Mid-point is more stable
    than start-time when a word straddles two turns (long pause + speaker
    swap mid-utterance).

Note: this is the WhisperX algorithm re-implemented in pure Python so we
don't pull WhisperX as a dep (CUDA-first, pinned, gated). ~80 LOC total.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ai-companion.plugins.meeting_capture.merge")

UNKNOWN_SPEAKER = "SPEAKER_UNKNOWN"


def interval_overlap(
    words: list[dict[str, Any]],
    speaker_turns: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Produce segment list with speaker labels.

    Args:
        words: list[{start, end, text, probability}] from transcribe.py.
            (Each "word" may actually be a Whisper segment, since default
            whisper.cpp builds emit per-segment timestamps; the algorithm
            handles either granularity.)
        speaker_turns: list[{start, end, speaker}] from diarize.py.

    Returns:
        list[{start, end, speaker, text}] — words/segments grouped by
        contiguous same-speaker runs, with consecutive same-speaker spans
        coalesced into one segment for readability.
    """
    if not words:
        return []

    # Sort defensively
    words = sorted(words, key=lambda w: w["start"])
    speaker_turns = sorted(speaker_turns, key=lambda t: t["start"])

    # Build a quick midpoint→speaker lookup
    annotated: list[dict[str, Any]] = []
    turn_idx = 0
    for w in words:
        midpoint = (w["start"] + w["end"]) / 2.0
        # Advance turn_idx until we either find the covering turn or
        # exhaust the list. Two-pointer scan: O(n + m) overall.
        while (
            turn_idx < len(speaker_turns)
            and speaker_turns[turn_idx]["end"] < midpoint
        ):
            turn_idx += 1

        speaker = UNKNOWN_SPEAKER
        if turn_idx < len(speaker_turns):
            t = speaker_turns[turn_idx]
            if t["start"] <= midpoint <= t["end"]:
                speaker = t["speaker"]

        annotated.append({
            "start": w["start"],
            "end": w["end"],
            "speaker": speaker,
            "text": w["text"],
        })

    # Coalesce consecutive same-speaker words into segments
    segments: list[dict[str, Any]] = []
    for w in annotated:
        if segments and segments[-1]["speaker"] == w["speaker"]:
            segments[-1]["end"] = w["end"]
            segments[-1]["text"] = (segments[-1]["text"] + " " + w["text"]).strip()
        else:
            segments.append({
                "start": w["start"],
                "end": w["end"],
                "speaker": w["speaker"],
                "text": w["text"],
            })

    return segments
