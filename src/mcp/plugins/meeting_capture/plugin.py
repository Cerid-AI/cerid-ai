# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Meeting Capture Plugin — Pro Tier anchor feature.
#
# Phase 2 of the 2026-05-20 Pro Tier Implementation Plan. This is the
# scaffolding layer; the heavyweight inference (whisper.cpp + pyannote.audio)
# is wired in by the per-module helpers in this directory and gated on
# the meeting_diarization / calendar_stitching / meeting_summary feature
# flags.
#
# Activation requirements (operator side, post-install):
#   1. Install python deps: `pip install pywhispercpp 'pyannote.audio>=3.3'`
#   2. Provide HF_TOKEN env var (Hugging Face access token with read access
#      to the gated pyannote models). Cerid's onboarding wizard guides
#      the user through accepting terms on:
#        - pyannote/speaker-diarization-3.1
#        - pyannote/segmentation-3.0
#   3. Download Whisper model (pywhispercpp does this lazily on first use;
#      cached at ~/.cerid/models/whisper/).
#
# The plugin is gated runtime-side: when meeting_diarization is False,
# audio uploads continue to flow through the community plugin
# (plugins/voice_memos) for plain transcription without speaker labels.
"""Pro-tier meeting capture with diarization + calendar stitching."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from plugins.base import ParserPlugin

logger = logging.getLogger("ai-companion.plugins.meeting_capture")


class MeetingCapturePlugin(ParserPlugin):
    """Audio meeting capture with speaker diarization + summary.

    Routes incoming meeting-shaped audio through the full pipeline:
      decode → VAD chunking → whisper transcription → pyannote diarization
      → merge → calendar stitching → KB ingestion (artifact + fragments).

    Falls back to plain transcription if pyannote is unavailable (preserves
    Pro feature operability when the HF model gate is unset, only losing
    speaker labels). Falls back further to a "feature gated" no-op if
    the meeting_diarization flag is off (community tier).
    """

    @property
    def name(self) -> str:
        return "meeting_capture"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return (
            "Pro meeting capture: speaker diarization + calendar-aware "
            "stitching + LLM-generated summary"
        )

    def get_parsers(self) -> dict[str, Any]:
        """Register .m4a/.mp3/.wav/.flac/.ogg/.webm/.mp4 → parse_meeting.

        At plugin-load time the global feature flag isn't necessarily set
        yet (license activation can flip it later). The actual gating
        happens inside parse_meeting() so each call is correctly tier-checked.
        """
        return {
            ".m4a":  self.parse_meeting,
            ".mp3":  self.parse_meeting,
            ".wav":  self.parse_meeting,
            ".flac": self.parse_meeting,
            ".ogg":  self.parse_meeting,
            ".webm": self.parse_meeting,
            ".mp4":  self.parse_meeting,
        }

    def parse_meeting(self, file_path: str) -> dict[str, Any]:
        """Run the meeting capture pipeline on a single file.

        Returns the ParserPlugin contract dict:
            text         — full concatenated transcript
            language     — detected/forced language code
            duration     — seconds
            file_type    — 'audio/meeting'
            page_count   — 1 (ParserPlugin contract requirement)
            segments     — list[{start, end, speaker, text}]

        Plus meeting-specific keys:
            speakers_detected      — int
            calendar_event_id      — str | None (when stitching matched)
            attendees              — list[str] | None
            summary                — str (LLM-generated meeting summary)
            action_items           — list[str]
            speaker_label_quality  — "full" | "partial" | "none"
        """
        from config.features import is_feature_enabled

        if not is_feature_enabled("meeting_diarization"):
            logger.info(
                "meeting_capture.parse_meeting skipped: meeting_diarization flag "
                "is off — community user uploaded audio. Caller should re-route "
                "via plugins/voice_memos for plain transcription."
            )
            return {
                "text": "",
                "file_type": "audio/meeting",
                "page_count": 1,
                "skipped": True,
                "reason": "feature_gated",
                "feature": "meeting_diarization",
            }

        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            return {
                "text": "",
                "file_type": "audio/meeting",
                "page_count": 1,
                "skipped": True,
                "reason": "file_not_found",
                "path": str(path),
            }

        # Lazy imports so the plugin can be loaded even when heavy deps
        # aren't installed. The error surface is a clean "missing deps"
        # message instead of an ImportError at plugin load.
        try:
            from plugins.meeting_capture import (
                decode,
                diarize,
                merge,
                transcribe,
            )
        except ImportError as exc:
            logger.warning("meeting_capture deps not installed: %s", exc)
            return {
                "text": "",
                "file_type": "audio/meeting",
                "page_count": 1,
                "skipped": True,
                "reason": "dependencies_missing",
                "missing": str(exc),
                "install_hint": "pip install pywhispercpp 'pyannote.audio>=3.3'",
            }

        # Step 1: normalize to 16 kHz mono PCM (ffmpeg)
        pcm_path = decode.to_pcm16(path)

        # Step 2: transcribe via whisper.cpp (CPU on Intel Mac, Metal on
        # Apple Silicon). Per-platform routing handled in transcribe module.
        transcript_result = transcribe.transcribe_pcm(pcm_path)

        # Step 3: diarize via pyannote (Pro feature; ungated for now since
        # the meeting_diarization gate already fired above)
        diar_result = diarize.diarize_pcm(pcm_path)

        # Step 4: merge word-timestamps × speaker turns
        segments = merge.interval_overlap(
            transcript_result["words"],
            diar_result["speaker_turns"],
        )

        # Step 5: calendar stitching (Pro feature, soft-fail if no calendar
        # connector is registered)
        calendar_metadata = self._calendar_stitch(file_path, transcript_result["duration"])

        # Step 6: LLM-generated summary + action items
        summary_result = self._generate_summary(segments)

        return {
            "text": transcript_result["text"],
            "language": transcript_result["language"],
            "duration": transcript_result["duration"],
            "file_type": "audio/meeting",
            "page_count": 1,
            "segments": segments,
            "speakers_detected": diar_result["speaker_count"],
            "speaker_label_quality": diar_result.get("quality", "full"),
            **calendar_metadata,
            **summary_result,
        }

    def _calendar_stitch(self, file_path: str, duration: float) -> dict[str, Any]:
        """Match recording window to a calendar event from a registered
        connector. Returns empty dict on no match or no calendar.

        match_to_event is async (Phase F+ — talks to a sibling MCP server
        for Google Calendar). Bridging back to sync via asyncio.run here
        is safe because parse_meeting is invoked from a worker thread
        (asyncio.to_thread in the meetings router), never the main loop.
        Direct callers of parse_meeting from inside an event loop should
        prefer the async meetings-router path.
        """
        import asyncio

        from config.features import is_feature_enabled

        if not is_feature_enabled("calendar_stitching"):
            return {"calendar_event_id": None, "attendees": None}

        try:
            from plugins.meeting_capture import calendar_stitch
            result = asyncio.run(calendar_stitch.match_to_event(file_path, duration))
            return result or {"calendar_event_id": None, "attendees": None}
        except ImportError:
            return {"calendar_event_id": None, "attendees": None}
        except RuntimeError as exc:
            # asyncio.run inside an already-running loop — happens if caller
            # invokes parse_meeting directly from async code. Surface as
            # soft-skip rather than crashing the meeting ingest.
            logger.warning("calendar stitch could not run inside live loop: %s", exc)
            return {"calendar_event_id": None, "attendees": None}

    def _generate_summary(self, segments: list[dict]) -> dict[str, Any]:
        """LLM summary + action-item extraction.

        Gated separately from diarization — a user could want
        diarized transcripts without paying the per-meeting summary cost.

        Bridges the async ``summarize_meeting`` into the sync parse_meeting
        path. ``parse_meeting`` is invoked from the ingestion pipeline which
        is itself sync; we run a fresh event loop for the summary call.
        """
        import asyncio

        from config.features import is_feature_enabled

        if not is_feature_enabled("meeting_summary"):
            return {"summary": "", "action_items": [], "decisions": []}

        try:
            from plugins.meeting_capture import summary
            return asyncio.run(summary.summarize_meeting(segments))
        except ImportError:
            return {"summary": "", "action_items": [], "decisions": []}
