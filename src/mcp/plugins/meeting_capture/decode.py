# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Audio decoding helpers — ffmpeg subprocess to 16 kHz mono PCM WAV."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from errors import IngestionError

logger = logging.getLogger("ai-companion.plugins.meeting_capture.decode")


def _ffmpeg_binary() -> str:
    """Locate ffmpeg binary. Bundled with Cerid's Docker image; on dev
    machines, falls back to system ffmpeg."""
    bundled = os.getenv("CERID_FFMPEG_PATH")
    if bundled and Path(bundled).is_file():
        return bundled
    found = shutil.which("ffmpeg")
    if not found:
        raise IngestionError(
            "ffmpeg not found. Install via `brew install ffmpeg` (macOS) or "
            "`apt install ffmpeg` (Linux), or set CERID_FFMPEG_PATH."
        )
    return found


def to_pcm16(source: Path) -> Path:
    """Normalize an audio file to 16 kHz mono PCM-WAV (Whisper input format).

    Output goes to a temp file in the system tmpdir. Caller is responsible
    for cleanup (or letting the OS reap it).

    16 kHz mono PCM is Whisper's working format internally; we materialize
    it once so both transcription + diarization pipelines read the same
    canonical input. Avoids the double-decode that happens when each
    library invokes its own ffmpeg.
    """
    source = source.expanduser().resolve()
    if not source.exists():
        raise IngestionError(f"Audio source not found: {source}")

    out_dir = Path(tempfile.gettempdir()) / "cerid_meeting_capture"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{source.stem}.16k_mono.wav"

    ffmpeg = _ffmpeg_binary()
    cmd = [
        ffmpeg,
        "-y",                    # overwrite
        "-loglevel", "error",
        "-i", str(source),
        "-ac", "1",              # mono
        "-ar", "16000",          # 16 kHz
        "-c:a", "pcm_s16le",     # signed 16-bit little-endian
        str(out_path),
    ]
    try:
        subprocess.run(cmd, check=True, capture_output=True, text=True)
    except subprocess.CalledProcessError as exc:
        raise IngestionError(
            f"ffmpeg decode failed for {source.name}: {exc.stderr.strip()[:400]}"
        ) from exc

    return out_path
