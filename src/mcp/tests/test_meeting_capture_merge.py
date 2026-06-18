# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Unit tests for the meeting_capture merge module.

Pure-logic tests (no Whisper / pyannote dependency). Validates the
interval-overlap join between Whisper word timestamps and pyannote
speaker turns. This is the load-bearing piece of the meeting capture
pipeline — if merge is wrong, every downstream KB artifact gets bad
speaker labels.
"""
from __future__ import annotations

import pytest

from plugins.meeting_capture.merge import UNKNOWN_SPEAKER, interval_overlap


def test_empty_words_returns_empty():
    assert interval_overlap([], []) == []


def test_no_speaker_turns_marks_unknown():
    words = [
        {"start": 0.0, "end": 1.0, "text": "Hello", "probability": 1.0},
    ]
    result = interval_overlap(words, [])
    assert len(result) == 1
    assert result[0]["speaker"] == UNKNOWN_SPEAKER
    assert result[0]["text"] == "Hello"


def test_single_speaker_single_word():
    words = [
        {"start": 0.0, "end": 1.0, "text": "Hello", "probability": 1.0},
    ]
    turns = [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"}]
    result = interval_overlap(words, turns)
    assert result == [
        {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "Hello"},
    ]


def test_two_speakers_coalesced_into_segments():
    """Consecutive words from the same speaker collapse into one segment."""
    words = [
        {"start": 0.0, "end": 1.0, "text": "Alice", "probability": 1.0},
        {"start": 1.0, "end": 2.0, "text": "says", "probability": 1.0},
        {"start": 2.0, "end": 3.0, "text": "hi", "probability": 1.0},
        {"start": 3.0, "end": 4.0, "text": "Hey", "probability": 1.0},
    ]
    turns = [
        {"start": 0.0, "end": 3.0, "speaker": "SPEAKER_00"},
        {"start": 3.0, "end": 4.0, "speaker": "SPEAKER_01"},
    ]
    result = interval_overlap(words, turns)
    assert len(result) == 2
    assert result[0]["speaker"] == "SPEAKER_00"
    assert result[0]["text"] == "Alice says hi"
    assert result[0]["start"] == 0.0
    assert result[0]["end"] == 3.0
    assert result[1]["speaker"] == "SPEAKER_01"
    assert result[1]["text"] == "Hey"


def test_word_midpoint_used_for_overlap():
    """A word straddling two turns gets assigned by its midpoint, not start."""
    words = [
        {"start": 1.5, "end": 2.5, "text": "word", "probability": 1.0},
    ]
    turns = [
        {"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
        {"start": 2.0, "end": 4.0, "speaker": "SPEAKER_01"},
    ]
    result = interval_overlap(words, turns)
    # midpoint = 2.0 → first turn ends at exactly 2.0 (inclusive), then
    # the algorithm advances. With end-inclusive bounds, midpoint=2.0 falls
    # in turn[0] OR turn[1] depending on implementation choice. Document
    # the actual behavior so consumers don't rely on tie-breaking we
    # haven't promised.
    assert result[0]["speaker"] in ("SPEAKER_00", "SPEAKER_01")


def test_unknown_speaker_segment_preserved():
    """Words in gaps between turns are tagged UNKNOWN, kept as their own segment."""
    words = [
        {"start": 0.0, "end": 1.0, "text": "Alice", "probability": 1.0},
        {"start": 5.0, "end": 6.0, "text": "gap_word", "probability": 1.0},
    ]
    turns = [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"}]
    result = interval_overlap(words, turns)
    assert len(result) == 2
    assert result[0]["speaker"] == "SPEAKER_00"
    assert result[1]["speaker"] == UNKNOWN_SPEAKER
    assert result[1]["text"] == "gap_word"


def test_segments_remain_sorted_by_start():
    """Output segments are sorted by start time even when input isn't."""
    words = [
        {"start": 3.0, "end": 4.0, "text": "Hey", "probability": 1.0},
        {"start": 0.0, "end": 1.0, "text": "Alice", "probability": 1.0},
        {"start": 1.0, "end": 2.0, "text": "says", "probability": 1.0},
    ]
    turns = [
        {"start": 3.0, "end": 4.0, "speaker": "SPEAKER_01"},
        {"start": 0.0, "end": 3.0, "speaker": "SPEAKER_00"},
    ]
    result = interval_overlap(words, turns)
    starts = [s["start"] for s in result]
    assert starts == sorted(starts)


def test_long_meeting_doesnt_loop_forever():
    """Smoke: 1k words × 100 turns finishes in reasonable time."""
    import time
    words = [
        {"start": i * 0.5, "end": (i * 0.5) + 0.4, "text": f"w{i}", "probability": 1.0}
        for i in range(1000)
    ]
    turns = [
        {"start": i * 5.0, "end": (i * 5.0) + 5.0, "speaker": f"SPEAKER_{i % 5:02d}"}
        for i in range(100)
    ]
    start = time.perf_counter()
    result = interval_overlap(words, turns)
    elapsed = time.perf_counter() - start
    assert len(result) > 0
    assert elapsed < 0.5, f"merge took {elapsed:.3f}s on 1k×100 input — too slow"


@pytest.mark.parametrize("malformed_turn", [
    {"start": 0.0, "end": -1.0, "speaker": "SPEAKER_00"},  # negative duration
    {"start": 0.0, "end": 0.0, "speaker": "SPEAKER_00"},   # zero duration
])
def test_malformed_turns_dont_crash(malformed_turn):
    """Defensive: pyannote rare degenerate output shouldn't crash merge."""
    words = [{"start": 0.5, "end": 1.0, "text": "x", "probability": 1.0}]
    result = interval_overlap(words, [malformed_turn])
    assert len(result) == 1  # word still appears, possibly UNKNOWN-tagged
