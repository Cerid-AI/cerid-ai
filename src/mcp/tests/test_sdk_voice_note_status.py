# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""/sdk/v1/ingest/voice-note must not report 201 Created for a non-creation.

The route carried a flat ``status_code=201`` and returned the ingest
outcome in the body, so a duplicate (``artifact_id: None``) and an outright
ingest failure both came back as 201. Both bundled SDK clients — and the F11
voice-capture overlay — treat 2xx as "artifact stored", so the overlay showed
a success state and a transcript for a note that was never stored (F015).
"""

from __future__ import annotations

import sys
import types
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.routers import sdk

    app = FastAPI()
    app.include_router(sdk.router)
    return TestClient(app)


@pytest.fixture
def whisper_stub():
    """Stand in for the internal-only meeting_capture transcription plugin."""
    mod = types.ModuleType("plugins.meeting_capture")
    mod.decode = MagicMock()
    mod.decode.to_pcm16 = MagicMock(side_effect=lambda p: p)
    mod.transcribe = MagicMock()
    mod.transcribe.transcribe_pcm = MagicMock(return_value={"text": "remember the milk"})
    with patch.dict(sys.modules, {"plugins.meeting_capture": mod}):
        yield mod


def _post(client) -> "object":
    return client.post(
        "/sdk/v1/ingest/voice-note",
        files={"audio": ("voice.wav", b"RIFFfake", "audio/wav")},
    )


@pytest.mark.parametrize(
    ("ingest_result", "expected_status"),
    [
        ({"status": "success", "artifact_id": "a-1"}, 201),
        ({"status": "updated", "artifact_id": "a-1"}, 201),
        ({"status": "duplicate", "artifact_id": None, "duplicate_of": "a-0"}, 200),
        ({"status": "dropped", "artifact_id": None, "reason": "low_quality"}, 200),
        ({"status": "error", "artifact_id": None, "error": "chroma down"}, 502),
    ],
)
def test_status_code_matches_the_ingest_outcome(
    client, whisper_stub, ingest_result, expected_status
):
    with patch("app.services.ingestion.ingest_content", return_value=ingest_result):
        resp = _post(client)

    assert resp.status_code == expected_status, (
        f"{ingest_result['status']} ingest answered {resp.status_code}"
    )
    assert resp.json()["status"] == ingest_result["status"]
    assert resp.json()["transcript"] == "remember the milk"


def test_every_returned_status_code_is_documented(client):
    """A code the caller can receive but the spec omits is undocumented drift."""
    from app.routers import sdk

    route = next(
        r for r in sdk.router.routes
        if getattr(r, "path", None) == "/sdk/v1/ingest/voice-note"
    )
    assert {200, 201, 502} <= set(route.responses), sorted(route.responses)
