# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for meetings router (Phase E Day 4).

Strategy: mock all plugin sub-modules + the chroma factory so the test
exercises the orchestration layer (stage transitions, error paths,
metadata wiring) without needing whisper/pyannote/ffmpeg installed.
"""
from __future__ import annotations

import asyncio
from io import BytesIO
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _make_app() -> FastAPI:
    from app.routers.meetings import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture()
def client():
    from app.routers import meetings
    meetings._JOBS.clear()
    return TestClient(_make_app())


class TestUploadValidation:
    def test_rejects_unknown_suffix(self, client):
        resp = client.post(
            "/meetings/upload",
            files={"file": ("note.txt", BytesIO(b"hi"), "text/plain")},
        )
        assert resp.status_code == 400
        assert "unsupported audio type" in resp.json()["detail"]

    def test_accepts_m4a(self, client, monkeypatch):
        # Stub the job runner so the test doesn't drag in plugin imports.
        async def _stub_run(job_id, _path, _filename):
            from app.routers.meetings import _set_stage
            _set_stage(job_id, "completed")

        with patch("app.routers.meetings._run_meeting_job", _stub_run):
            resp = client.post(
                "/meetings/upload",
                files={"file": ("meeting.m4a", BytesIO(b"\x00" * 32), "audio/m4a")},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert "job_id" in body
        assert len(body["job_id"]) == 32


class TestGetJob:
    def test_unknown_id_returns_404(self, client):
        resp = client.get("/meetings/job/nonexistent")
        assert resp.status_code == 404

    def test_known_id_returns_state(self, client, monkeypatch):
        async def _stub_run(job_id, _path, _filename):
            from app.routers.meetings import _set_stage
            _set_stage(job_id, "completed")

        with patch("app.routers.meetings._run_meeting_job", _stub_run):
            up = client.post(
                "/meetings/upload",
                files={"file": ("m.m4a", BytesIO(b"\x00"), "audio/m4a")},
            )
            job_id = up.json()["job_id"]
            # Give the task a tick to flip the state
            import time
            time.sleep(0.05)
            resp = client.get(f"/meetings/job/{job_id}")
        body = resp.json()
        assert body["job_id"] == job_id
        # State will be either "queued" (just started) or "completed" (stub finished)
        assert body["stage"] in ("queued", "completed")


class TestListJobs:
    def test_empty_initially(self, client):
        resp = client.get("/meetings/jobs")
        assert resp.json() == []


class TestStageProgression:
    """End-to-end orchestration with all plugin modules mocked."""

    @pytest.mark.asyncio
    async def test_run_meeting_job_full_pipeline(self, tmp_path):
        from app.routers import meetings

        # Mock the entire plugins.meeting_capture package
        decode_m = MagicMock()
        decode_m.to_pcm16.return_value = tmp_path / "audio.pcm"

        transcribe_m = MagicMock()
        transcribe_m.transcribe_pcm.return_value = {
            "text": "hello world this is a meeting",
            "language": "en",
            "duration": 60.0,
            "words": [
                {"start": 0.0, "end": 0.5, "text": "hello", "probability": 0.98},
                {"start": 0.5, "end": 1.0, "text": "world", "probability": 0.97},
            ],
        }

        diarize_m = MagicMock()
        diarize_m.diarize_pcm.return_value = {
            "speaker_turns": [
                {"speaker": "SPEAKER_00", "start": 0.0, "end": 1.0},
            ],
            "speaker_count": 1,
            "quality": "full",
        }

        merge_m = MagicMock()
        merge_m.interval_overlap.return_value = [
            {"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00", "text": "hello world"},
        ]

        # match_to_event is async in Phase F+ (Google Calendar via sibling
        # MCP). Stub returns a coroutine that resolves to the expected dict.
        cal_m = MagicMock()
        async def _match(_path, _duration):
            return {
                "calendar_event_id": "evt:abc",
                "calendar_event_title": "Standup",
                "attendees": ["alice@example.com"],
            }
        cal_m.match_to_event = _match

        summary_m = MagicMock()
        async def _summarize(_segments):
            return {
                "summary": "team standup",
                "action_items": ["alice ships X"],
                "decisions": [],
            }
        summary_m.summarize_meeting = _summarize

        # Stub ingest_content — the meetings router now routes its
        # write-back through the canonical app.services.ingestion
        # path (Phase J gate-review fix) instead of going direct to
        # Chroma. The test asserts the artifact metadata reaches
        # ingest_content with the right shape.
        ingest_calls: list[dict] = []

        def _fake_ingest_content(content, domain, metadata, *, skip_quality=False, **kwargs):
            ingest_calls.append({
                "content": content,
                "domain": domain,
                "metadata": metadata,
                "skip_quality": skip_quality,
            })
            return {"artifact_id": "meeting:test-artifact-id"}

        # Pre-create the job (upload() would have)
        job_id = "test_job"
        from app.routers.meetings import MeetingJob
        meetings._JOBS[job_id] = MeetingJob(
            job_id=job_id,
            stage="queued",
            progress=0.0,
            started_at=0.0,
        )

        upload_path = tmp_path / "fake.m4a"
        upload_path.write_bytes(b"\x00" * 16)

        # Patch the entire plugins.meeting_capture submodule namespace.
        # Inject into BOTH sys.modules (so `from plugins.meeting_capture.X
        # import Y` resolves to the mock) AND the parent package's
        # attributes (so `from plugins.meeting_capture import X` resolves
        # to the mock — this matters when the real module has already
        # been imported by a previous test in the same session).
        import sys

        import plugins.meeting_capture as _meeting_pkg  # noqa: PLC0415
        _orig_attrs = {}
        for name, mod in (
            ("decode", decode_m),
            ("transcribe", transcribe_m),
            ("diarize", diarize_m),
            ("merge", merge_m),
            ("calendar_stitch", cal_m),
            ("summary", summary_m),
        ):
            sys.modules[f"plugins.meeting_capture.{name}"] = mod
            if hasattr(_meeting_pkg, name):
                _orig_attrs[name] = getattr(_meeting_pkg, name)
            setattr(_meeting_pkg, name, mod)

        with patch(
            "app.services.ingestion.ingest_content",
            side_effect=_fake_ingest_content,
        ):
            await meetings._run_meeting_job(job_id, upload_path, "fake.m4a")

        # Clean up sys.modules patches AND parent package attrs
        for name in (
            "decode", "transcribe", "diarize", "merge", "calendar_stitch", "summary",
        ):
            sys.modules.pop(f"plugins.meeting_capture.{name}", None)
            if name in _orig_attrs:
                setattr(_meeting_pkg, name, _orig_attrs[name])
            elif hasattr(_meeting_pkg, name):
                delattr(_meeting_pkg, name)

        job = meetings._JOBS[job_id]
        assert job.stage == "completed"
        assert job.progress == 1.0
        assert job.artifact_id == "meeting:test-artifact-id"
        assert job.duration_seconds == 60.0
        assert job.speakers_detected == 1
        assert job.calendar_event_id == "evt:abc"

        # Verify ingest_content received the right shape (Phase J
        # gate-review: meeting artifacts now flow through the
        # canonical ingestion path so they show up in Neo4j +
        # pkb_search_filtered).
        assert len(ingest_calls) == 1
        call = ingest_calls[0]
        assert call["content"] == "hello world this is a meeting"
        assert call["domain"] == "meetings"
        assert call["skip_quality"] is True
        meta = call["metadata"]
        assert meta["source"] == "meeting_capture"
        assert meta["type"] == "meeting"
        assert meta["speakers_detected"] == "1"
        assert meta["calendar_event_id"] == "evt:abc"

        # The temp upload file should be cleaned up
        assert not upload_path.exists()

    @pytest.mark.asyncio
    async def test_run_meeting_job_missing_deps_marks_failed(self, tmp_path):
        # Ensure plugins.meeting_capture is NOT importable so the lazy
        # try/except inside _run_meeting_job hits its failure branch.
        import sys

        from app.routers import meetings
        for mod in list(sys.modules):
            if mod.startswith("plugins.meeting_capture"):
                sys.modules.pop(mod, None)

        job_id = "test_fail"
        from app.routers.meetings import MeetingJob
        meetings._JOBS[job_id] = MeetingJob(
            job_id=job_id,
            stage="queued",
            progress=0.0,
            started_at=0.0,
        )
        upload_path = tmp_path / "fake.m4a"
        upload_path.write_bytes(b"\x00")

        # Patch import to raise so the function takes its ImportError branch.
        import builtins
        orig_import = builtins.__import__

        def _mock_import(name, globals_=None, locals_=None, fromlist=(), level=0):
            if (
                name == "plugins.meeting_capture"
                and fromlist
                and any(
                    sub in fromlist
                    for sub in (
                        "decode",
                        "transcribe",
                        "diarize",
                        "merge",
                        "calendar_stitch",
                        "summary",
                    )
                )
            ):
                raise ImportError("simulated missing deps")
            return orig_import(name, globals_, locals_, fromlist, level)

        with patch.object(builtins, "__import__", _mock_import):
            await meetings._run_meeting_job(job_id, upload_path, "fake.m4a")

        job = meetings._JOBS[job_id]
        assert job.stage == "failed"
        assert "deps not installed" in (job.error or "")


# Required for pytest-asyncio to pick up the async tests.
@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()
