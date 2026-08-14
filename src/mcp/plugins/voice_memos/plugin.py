# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
#
# Voice Memos Plugin — Community Tier, macOS-only.
#
# Phase 1.5 D4 of the 2026-05-20 Pro Tier Implementation Plan. Plain
# Whisper transcription without diarization, calendar stitching, or LLM
# summary — those are the Pro-only meeting_capture plugin.
#
# Two activation surfaces:
#   1. Drop-zone upload of an .m4a file → parse_voice_memo() returns text.
#   2. Optional folder watcher on ~/Library/.../com.apple.voicememos/
#      Recordings/ — only enabled when VOICE_MEMOS_OPT_IN=true (explicit
#      user consent during setup wizard).
"""Voice Memos auto-watch + plain transcription for community tier."""
from __future__ import annotations

import logging
import os
import platform
import sys
import threading
from pathlib import Path
from typing import Any

from plugins.base import ParserPlugin

logger = logging.getLogger("ai-companion.plugins.voice_memos")


class VoiceMemosPlugin(ParserPlugin):
    """Plain audio transcription via Whisper — community baseline."""

    @property
    def name(self) -> str:
        return "voice_memos"

    @property
    def version(self) -> str:
        return "0.1.0"

    @property
    def description(self) -> str:
        return "Mac Voice Memos plain transcription (community tier)"

    def get_parsers(self) -> dict[str, Any]:
        return {".m4a": self.parse_voice_memo}

    def on_startup(self) -> None:
        """Start the opt-in folder watcher once every plugin has registered.

        VOICE_MEMOS_OPT_IN is a plain env var, read here the same way every
        other boolean toggle in config/settings.py is (set it in .env or the
        process environment). No-op unless the user opted in AND the process
        is actually running on macOS (the watcher is meaningless inside a
        Linux container; the Darwin-only recordings directory would never
        exist there).
        """
        if platform.system() != "Darwin":
            return
        opted_in = os.getenv("VOICE_MEMOS_OPT_IN", "false").strip().lower() in (
            "true", "1", "yes", "on",
        )
        if not opted_in:
            return

        threading.Thread(
            target=watch_voice_memos_dir,
            args=(_ingest_voice_memo,),
            name="voice-memos-watcher",
            daemon=True,
        ).start()
        logger.info("Voice Memos watcher thread started (VOICE_MEMOS_OPT_IN=true)")

    def parse_voice_memo(self, file_path: str) -> dict[str, Any]:
        """Run Whisper on a .m4a file → plain text transcript.

        Returns the ParserPlugin contract dict:
            text:        full transcript
            language:    detected ISO 639-1
            duration:    seconds
            file_type:   'audio/voice_memo'
            page_count:  1
            segments:    list[{start, end, text}] — NO speaker labels
                         (speaker_label="unknown" sentinel)
        """
        from config.features import is_feature_enabled

        if not is_feature_enabled("audio_transcription_plain"):
            return {
                "text": "",
                "file_type": "audio/voice_memo",
                "page_count": 1,
                "skipped": True,
                "reason": "feature_gated",
                "feature": "audio_transcription_plain",
            }

        path = Path(file_path).expanduser().resolve()
        if not path.exists():
            return {
                "text": "",
                "file_type": "audio/voice_memo",
                "page_count": 1,
                "skipped": True,
                "reason": "file_not_found",
                "path": str(path),
            }

        # Reuse the meeting_capture decode + transcribe modules — single
        # implementation, two consumers. The Pro plugin layers diarization
        # + stitching + summary on top of the same primitives.
        try:
            from plugins.meeting_capture import decode, transcribe
        except ImportError as exc:
            return {
                "text": "",
                "file_type": "audio/voice_memo",
                "page_count": 1,
                "skipped": True,
                "reason": "dependencies_missing",
                "missing": str(exc),
                "install_hint": "pip install pywhispercpp",
            }

        pcm_path = decode.to_pcm16(path)
        transcript = transcribe.transcribe_pcm(pcm_path)

        segments = [
            {
                "start": w["start"],
                "end": w["end"],
                "text": w["text"],
                "speaker": "unknown",  # community tier: no diarization
            }
            for w in transcript["words"]
        ]

        return {
            "text": transcript["text"],
            "language": transcript["language"],
            "duration": transcript["duration"],
            "file_type": "audio/voice_memo",
            "page_count": 1,
            "segments": segments,
        }


# ---------------------------------------------------------------------------
# Folder watch (opt-in)
# ---------------------------------------------------------------------------

_DEFAULT_VOICE_MEMOS_DIR = (
    "~/Library/Application Support/com.apple.voicememos/Recordings"
)


def default_voice_memos_dir() -> Path:
    """Expand the canonical Voice Memos.app recording directory.

    Note: macOS Voice Memos sometimes stores recordings under
    `~/Library/Group Containers/group.com.apple.VoiceMemos.MacOS/Recordings/`
    on newer macOS releases. We expand the configured path and fall back
    to the group container only if the primary path is empty.
    """
    primary = Path(os.path.expanduser(_DEFAULT_VOICE_MEMOS_DIR))
    if primary.exists():
        return primary
    group = Path.home() / "Library" / "Group Containers" / "group.com.apple.VoiceMemos.MacOS" / "Recordings"
    return group if group.exists() else primary


def _ingest_voice_memo(file_path: str) -> None:
    """Sync bridge from the polling watcher thread into the async ingestion pipeline.

    ``.m4a`` is already routed to ``parse_voice_memo`` via ``PARSER_REGISTRY``
    (set up by ``ParserPlugin.register()``), so ``ingest_file`` transcribes,
    chunks, and stores the recording the same way a manual drop-zone upload
    would. Runs its own event loop since this executes on the watcher's
    dedicated thread, not the app's asyncio loop.
    """
    import asyncio

    from app.services.ingestion import ingest_file

    asyncio.run(ingest_file(file_path, client_source="voice_memos_watcher"))


def watch_voice_memos_dir(
    ingestion_callback,
    *,
    directory: Path | None = None,
    poll_interval_s: int = 5,
) -> None:
    """Block-monitor a directory for new .m4a files; invoke callback per new file.

    Designed to run in its own thread or asyncio task. Quietly exits on
    non-Darwin platforms (Voice Memos is Mac-only).

    The watcher is *opt-in* — callers should only invoke when the user has
    explicitly enabled VOICE_MEMOS_OPT_IN. Surprise auto-ingestion of
    voice recordings is a privacy red flag.
    """
    if platform.system() != "Darwin":
        logger.info("Voice Memos watcher: not on macOS, exiting")
        return

    directory = directory or default_voice_memos_dir()
    if not directory.exists():
        logger.warning("Voice Memos directory does not exist: %s", directory)
        return

    if "pytest" in sys.modules:
        # In test contexts, don't block — caller drives via the parser directly.
        return

    seen: set[str] = set()
    # Seed with current contents so we don't re-ingest historic recordings.
    for f in directory.glob("*.m4a"):
        seen.add(f.name)
    logger.info(
        "Voice Memos watcher started — dir=%s, baseline=%d files",
        directory, len(seen),
    )

    # Poll-based watcher. fswatch + native FSEvents would be ideal but
    # adds a binary dep; polling at 5s is fine for personal voice memos
    # (low file-arrival rate) and avoids the binary.
    import time
    while True:
        try:
            current = {f.name for f in directory.glob("*.m4a")}
            new_files = current - seen
            for name in sorted(new_files):
                full_path = directory / name
                # Wait for file to stabilize (Voice Memos.app writes
                # incrementally; we want a fully-closed file).
                last_size = -1
                for _ in range(10):
                    try:
                        size = full_path.stat().st_size
                    except FileNotFoundError:
                        break
                    if size == last_size and size > 0:
                        break
                    last_size = size
                    time.sleep(0.5)
                else:
                    continue  # never stabilized — skip this iteration
                try:
                    ingestion_callback(str(full_path))
                except (ValueError, OSError, RuntimeError) as exc:
                    logger.warning("Voice Memos ingestion failed for %s: %s",
                                   name, exc)
                seen.add(name)
            time.sleep(poll_interval_s)
        except KeyboardInterrupt:
            logger.info("Voice Memos watcher interrupted, exiting")
            return
