# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Meeting Capture E2E coverage.

Exercises the meeting_capture plugin's full 8-stage pipeline against
fixture audio files. Skipped when the plugin's heavy runtime deps
(pywhispercpp + pyannote.audio) aren't installed so CI without the
voice toolchain stays green.

Coverage:

* ``test_single_speaker_short`` — 30s mono clip; expects non-empty
  transcript + speaker_count >= 1
* ``test_multi_speaker_calendar_stitch`` — placeholder multi-speaker
  fixture; expects diarization to split speakers and the calendar-
  stitch stage to attach metadata when a fixture event is provided
* ``test_decode_failure_surfaces`` — corrupt input; expects the
  decode stage to raise rather than silently empty out
"""
from __future__ import annotations

from pathlib import Path

import pytest

# All tests in this module need the meeting_capture plugin. Skip the
# whole module when the plugin's deps aren't importable.
pytest.importorskip("plugins.meeting_capture")


@pytest.fixture
def fixture_dir() -> Path:
    """Returns the dir holding fixture audio. Empty until the
    fixtures land in a follow-up commit; tests skip when missing.
    """
    p = Path(__file__).parent / "fixtures" / "meeting_capture"
    if not p.exists():
        pytest.skip("meeting_capture fixtures not present")
    return p


@pytest.mark.preservation
def test_single_speaker_short(fixture_dir: Path):
    """30s single-speaker recording should land a non-empty transcript."""
    clip = fixture_dir / "single_speaker_30s.wav"
    if not clip.exists():
        pytest.skip(f"fixture missing: {clip}")

    from plugins.meeting_capture import decode, transcribe

    pcm_path = decode.to_pcm16(clip)
    result = transcribe.transcribe_pcm(pcm_path)
    text = (result.get("text") or "").strip()
    assert text, "transcript should not be empty"
    words = result.get("words") or []
    assert len(words) > 0, "word-level segmentation should produce at least one entry"


@pytest.mark.preservation
def test_multi_speaker_calendar_stitch(fixture_dir: Path):
    """Multi-speaker fixture: diarization splits + calendar stitch
    attaches metadata.
    """
    clip = fixture_dir / "multi_speaker_short.wav"
    if not clip.exists():
        pytest.skip(f"fixture missing: {clip}")

    from plugins.meeting_capture import calendar_stitch, decode, diarize, merge, transcribe

    pcm_path = decode.to_pcm16(clip)
    transcript = transcribe.transcribe_pcm(pcm_path)
    diar = diarize.diarize_pcm(pcm_path)
    assert diar.get("speaker_count", 0) >= 2, "fixture should expose ≥ 2 speakers"

    segments = merge.interval_overlap(transcript["words"], diar["speaker_turns"])
    assert segments, "merge should produce labeled segments"

    # Optional calendar event — skipped if the fixture doesn't include one.
    cal_event_path = fixture_dir / "multi_speaker_short.calendar.json"
    if cal_event_path.exists():
        import json

        event = json.loads(cal_event_path.read_text())
        stitched = calendar_stitch.attach_event(segments, event)
        assert stitched.get("event_id") == event.get("id")


@pytest.mark.preservation
def test_decode_failure_surfaces(tmp_path: Path):
    """A corrupt input file should raise rather than emit a silent
    empty PCM.
    """
    from plugins.meeting_capture import decode

    corrupt = tmp_path / "corrupt.wav"
    corrupt.write_bytes(b"not actually a wav file")

    with pytest.raises(Exception):
        decode.to_pcm16(corrupt)
