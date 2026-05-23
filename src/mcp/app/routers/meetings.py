# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Meeting capture orchestration endpoints (Phase E Day 4).

Wraps the meeting_capture plugin's parse_meeting() in a background-job
shape so the UI can:
  - upload an audio file
  - poll job status (state + stage + percent)
  - retrieve the produced KB artifact_id on completion

The plugin itself is synchronous-by-design (whisper + pyannote each
take significant wall time on CPU). This router runs the plugin in
asyncio.to_thread so the FastAPI event loop stays responsive while
the worker thread crunches.

Job state lives in process memory; sufficient for the single-tenant
desktop deployment. Multi-worker / replica deployment would back this
with Redis (the cerid:meeting:job:{id} key namespace named in the plan
maps cleanly when needed).
"""
from __future__ import annotations

import asyncio
import logging
import tempfile
import time
import uuid
from pathlib import Path
from typing import Literal

from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel

from core.utils.swallowed import log_swallowed_error

_logger = logging.getLogger("ai-companion.meetings")

router = APIRouter(prefix="/meetings", tags=["meetings"])


# ---------------------------------------------------------------------------
# Job state
# ---------------------------------------------------------------------------

JobStage = Literal[
    "queued",
    "decoding",
    "transcribing",
    "diarizing",
    "merging",
    "stitching",
    "summarizing",
    "ingesting",
    "completed",
    "failed",
]

# Each stage's expected fraction-of-total wall time. Used to derive a
# percentage from the current stage so the UI shows steady forward motion.
_STAGE_PROGRESS: dict[JobStage, float] = {
    "queued": 0.00,
    "decoding": 0.05,
    "transcribing": 0.55,
    "diarizing": 0.75,
    "merging": 0.80,
    "stitching": 0.82,
    "summarizing": 0.92,
    "ingesting": 0.98,
    "completed": 1.00,
    "failed": 1.00,
}


class MeetingJob(BaseModel):
    job_id: str
    stage: JobStage
    progress: float  # 0..1
    started_at: float
    completed_at: float | None = None
    error: str | None = None
    # Populated on success:
    artifact_id: str | None = None
    duration_seconds: float | None = None
    speakers_detected: int | None = None
    calendar_event_id: str | None = None


class StartMeetingResponse(BaseModel):
    job_id: str


# In-memory registry. Keyed by job_id; oldest entries pruned at 50 to keep
# memory bounded (a desktop user won't queue 50 hour-long meetings).
_JOBS: dict[str, MeetingJob] = {}
_JOB_CAP = 50


def _prune() -> None:
    if len(_JOBS) <= _JOB_CAP:
        return
    # Drop the oldest started_at entries until under cap
    ordered = sorted(_JOBS.values(), key=lambda j: j.started_at)
    for j in ordered[: len(_JOBS) - _JOB_CAP]:
        _JOBS.pop(j.job_id, None)


def _set_stage(job_id: str, stage: JobStage) -> None:
    job = _JOBS.get(job_id)
    if job is None:
        return
    job.stage = stage
    job.progress = _STAGE_PROGRESS[stage]
    if stage in ("completed", "failed"):
        job.completed_at = time.time()


# ---------------------------------------------------------------------------
# Job runner — wraps the plugin's parse_meeting in a step-aware async loop
# ---------------------------------------------------------------------------

async def _run_meeting_job(job_id: str, upload_path: Path, filename: str) -> None:
    """Drive the meeting_capture plugin through its 8-stage pipeline.

    The plugin's parse_meeting() is synchronous and opaque; here we
    re-implement the orchestration as discrete async steps so we can
    emit stage transitions for the UI.
    """
    try:
        # Lazy imports so this router loads cleanly even when the plugin's
        # heavy runtime deps aren't installed yet.
        try:
            from plugins.meeting_capture import (
                calendar_stitch,
                decode,
                diarize,
                merge,
                summary,
                transcribe,
            )
        except ImportError as exc:
            _set_stage(job_id, "failed")
            _JOBS[job_id].error = (
                f"meeting_capture runtime deps not installed: {exc}. "
                "Install: pip install pywhispercpp 'pyannote.audio>=3.3'"
            )
            return

        _set_stage(job_id, "decoding")
        pcm_path = await asyncio.to_thread(decode.to_pcm16, upload_path)

        _set_stage(job_id, "transcribing")
        transcript_result = await asyncio.to_thread(transcribe.transcribe_pcm, pcm_path)

        _set_stage(job_id, "diarizing")
        try:
            diar_result = await asyncio.to_thread(diarize.diarize_pcm, pcm_path)
        except Exception as exc:  # noqa: BLE001 — diarization failure is non-fatal
            log_swallowed_error("meetings.diarize", exc)
            diar_result = {"speaker_turns": [], "speaker_count": 0, "quality": "none"}

        _set_stage(job_id, "merging")
        segments = await asyncio.to_thread(
            merge.interval_overlap,
            transcript_result["words"],
            diar_result["speaker_turns"],
        )

        _set_stage(job_id, "stitching")
        cal_meta: dict = {}
        try:
            # match_to_event is async in Phase F+ because the
            # GoogleCalendarDataSource.list_events is async (sibling MCP).
            cal_meta = await calendar_stitch.match_to_event(
                str(upload_path), transcript_result["duration"]
            ) or {}
        except Exception as exc:  # noqa: BLE001 — calendar absent is normal
            log_swallowed_error("meetings.stitch", exc)

        _set_stage(job_id, "summarizing")
        try:
            summary_result = await summary.summarize_meeting(segments)
        except Exception as exc:  # noqa: BLE001 — summary failure is non-fatal
            log_swallowed_error("meetings.summary", exc)
            summary_result = {"summary": "", "action_items": [], "decisions": []}

        _set_stage(job_id, "ingesting")
        artifact_id = await _ingest_to_kb(
            filename=filename,
            transcript_text=transcript_result["text"],
            segments=segments,
            language=transcript_result.get("language"),
            duration=transcript_result["duration"],
            speakers_detected=diar_result["speaker_count"],
            calendar_meta=cal_meta,
            summary_result=summary_result,
        )

        job = _JOBS[job_id]
        job.artifact_id = artifact_id
        job.duration_seconds = transcript_result["duration"]
        job.speakers_detected = diar_result["speaker_count"]
        job.calendar_event_id = cal_meta.get("calendar_event_id") if cal_meta else None
        _set_stage(job_id, "completed")
    except Exception as exc:  # noqa: BLE001 — top-level catch must surface error to user
        log_swallowed_error("meetings._run_meeting_job", exc)
        _set_stage(job_id, "failed")
        _JOBS[job_id].error = str(exc)
    finally:
        # Clean up the uploaded file (KB artifact persists separately)
        try:
            upload_path.unlink(missing_ok=True)
        except OSError as exc:
            log_swallowed_error("meetings._run_meeting_job.cleanup", exc)


async def _ingest_to_kb(
    *,
    filename: str,
    transcript_text: str,
    segments: list[dict],
    language: str | None,
    duration: float,
    speakers_detected: int,
    calendar_meta: dict,
    summary_result: dict,
) -> str | None:
    """Persist the meeting as a KB artifact via the canonical
    `ingest_content` path so the artifact gets:
      - a Neo4j :Artifact node + :BELONGS_TO :Domain relation
      - content-hash dedup
      - BM25 + sparse index entries
      - the same Phase O.1 pending→committed staging as every other
        ingest

    Phase J gate-review fix: the prior implementation went straight to
    `collection.add()` and skipped Neo4j entirely, leaving meeting
    artifacts invisible to `pkb_search_filtered` (which pre-filters on
    Neo4j). All callers via the meetings router now flow through the
    same path as the rest of the ingest layer.
    """
    try:
        from app.services.ingestion import ingest_content
    except ImportError as exc:
        log_swallowed_error("meetings._ingest_to_kb.import", exc)
        return None

    domain = "meetings"
    metadata = {
        "source": "meeting_capture",
        "filename": filename,
        "type": "meeting",
        "language": language or "unknown",
        "duration_seconds": str(duration),
        "speakers_detected": str(speakers_detected),
        "segment_count": str(len(segments)),
        "calendar_event_id": calendar_meta.get("calendar_event_id") or "",
        "calendar_event_title": calendar_meta.get("calendar_event_title") or "",
        "summary": (summary_result.get("summary") or "")[:2000],
        "action_items": "\n".join(summary_result.get("action_items", []))[:2000],
    }

    try:
        result = await asyncio.to_thread(
            ingest_content,
            transcript_text,
            domain,
            metadata,
            skip_quality=True,  # quality score requires summary/keywords this path
                                # doesn't pre-compute; curator re-scores later.
        )
    except (RuntimeError, ValueError, OSError) as exc:
        log_swallowed_error("meetings._ingest_to_kb.add", exc)
        return None

    # ingest_content returns a dict with the new artifact id under
    # one of these keys depending on version; tolerate both.
    if isinstance(result, dict):
        return result.get("artifact_id") or result.get("id")
    return None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/upload", response_model=StartMeetingResponse)
async def upload_meeting(file: UploadFile = File(...)) -> StartMeetingResponse:
    """Accept an audio file, persist to a temp path, kick off a job.

    Returns the job_id immediately. The UI polls GET /meetings/job/{id}
    for stage transitions.

    The explicit ``File(...)`` wrapper anchors FastAPI's multipart
    parser so the preservation test (which sends a text/plain bytes
    payload via httpx's ``files=`` kwarg) reaches the suffix check
    and gets a 400 rather than 422.
    """
    if not file.filename:
        raise HTTPException(status_code=400, detail="filename required")
    suffix = Path(file.filename).suffix.lower() or ".bin"
    valid_suffixes = {".m4a", ".mp3", ".wav", ".flac", ".ogg", ".webm", ".mp4"}
    if suffix not in valid_suffixes:
        raise HTTPException(
            status_code=400,
            detail=f"unsupported audio type: {suffix}",
        )

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
    try:
        content = await file.read()
        tmp.write(content)
    finally:
        tmp.close()
    upload_path = Path(tmp.name)

    job_id = uuid.uuid4().hex
    _JOBS[job_id] = MeetingJob(
        job_id=job_id,
        stage="queued",
        progress=0.0,
        started_at=time.time(),
    )
    _prune()

    asyncio.create_task(_run_meeting_job(job_id, upload_path, file.filename))
    return StartMeetingResponse(job_id=job_id)


@router.get("/job/{job_id}", response_model=MeetingJob)
async def get_meeting_job(job_id: str) -> MeetingJob:
    job = _JOBS.get(job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Unknown job_id")
    return job


@router.get("/jobs", response_model=list[MeetingJob])
async def list_meeting_jobs() -> list[MeetingJob]:
    """All in-memory jobs, newest first."""
    return sorted(_JOBS.values(), key=lambda j: j.started_at, reverse=True)
