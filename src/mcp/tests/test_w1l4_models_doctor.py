# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""GET /models/doctor must audit the pins in force, against the live daemon.

F030 — PATCH /settings writes the model knobs to ``os.environ`` only, so an
audit that reads the ``config`` attrs keeps grading the boot-time pin.
F170 — the audit compared local pins to a static table and never asked the
daemon which models it actually serves.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest


class _FakeTagsResponse:
    def __init__(self, names: list[str]):
        self._names = names

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return {"models": [{"name": n} for n in self._names]}


@pytest.fixture
def doctor_env(monkeypatch):
    """A quenchforge box whose boot-time rerank pin is the known-good one."""
    import config as settings

    monkeypatch.setenv("INTERNAL_LLM_PROVIDER", "quenchforge")
    monkeypatch.setenv("QUENCHFORGE_URL", "http://127.0.0.1:11434")
    monkeypatch.setattr(settings, "CERID_HARDWARE_PROFILE", "amd-mac", raising=False)
    monkeypatch.setattr(settings, "INTERNAL_LLM_PROVIDER", "quenchforge", raising=False)
    monkeypatch.setattr(settings, "INTERNAL_LLM_MODEL", "", raising=False)
    monkeypatch.setattr(settings, "OLLAMA_DEFAULT_MODEL", "", raising=False)
    monkeypatch.setattr(settings, "QUENCHFORGE_EMBED_MODEL", "", raising=False)
    monkeypatch.setattr(settings, "QUENCHFORGE_RERANK_MODEL", "bge-reranker-v2-m3", raising=False)
    monkeypatch.delenv("QUENCHFORGE_RERANK_MODEL", raising=False)
    monkeypatch.delenv("QUENCHFORGE_EMBED_MODEL", raising=False)
    monkeypatch.delenv("OLLAMA_DEFAULT_MODEL", raising=False)
    monkeypatch.delenv("INTERNAL_LLM_MODEL", raising=False)


async def _run_doctor(daemon_models: list[str] | None):
    """Call the endpoint with the daemon's /api/tags stubbed at the wire."""
    from app.routers import models as models_router

    get_mock: AsyncMock
    if daemon_models is None:
        get_mock = AsyncMock(side_effect=OSError("connection refused"))
    else:
        get_mock = AsyncMock(return_value=_FakeTagsResponse(daemon_models))

    with (
        patch.object(models_router, "_current_assignments", return_value={}),
        patch.object(models_router, "fetch_openrouter_catalog", AsyncMock(return_value={})),
        patch("httpx.AsyncClient.get", get_mock),
    ):
        return await models_router.model_doctor()


class TestDoctorReadsTheLivePin:
    @pytest.mark.asyncio
    async def test_audits_the_env_pin_not_the_boot_time_pin(self, doctor_env, monkeypatch):
        # The operator repinned rerank through PATCH /settings, which writes env.
        monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "stale-rerank-gguf")

        report = await _run_doctor(["bge-reranker-v2-m3", "llama3.1-8b"])

        audited = {f["model"] for f in report["findings"]}
        assert "stale-rerank-gguf" in audited, (
            "the doctor graded the boot-time pin, not the one PATCH /settings wrote"
        )
        assert "bge-reranker-v2-m3" not in audited


class TestDoctorAsksTheDaemon:
    @pytest.mark.asyncio
    async def test_flags_a_pin_the_daemon_does_not_serve(self, doctor_env, monkeypatch):
        monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "bge-reranker-v2-m3")

        report = await _run_doctor(["llama3.1-8b", "nomic-embed-text-v1.5"])

        not_served = [f for f in report["findings"] if f["kind"] == "not_served"]
        assert not_served, (
            "the pinned reranker is absent from the daemon's /api/tags and the "
            "doctor still reported the box clean"
        )
        assert not_served[0]["model"] == "bge-reranker-v2-m3"
        assert not_served[0]["severity"] == "error"
        assert report["ok"] is False

    @pytest.mark.asyncio
    async def test_a_served_pin_produces_no_finding(self, doctor_env, monkeypatch):
        monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "bge-reranker-v2-m3")

        report = await _run_doctor(["bge-reranker-v2-m3:latest"])

        assert [f for f in report["findings"] if f["kind"] == "not_served"] == []
        assert report["local_daemon"]["reachable"] is True

    @pytest.mark.asyncio
    async def test_unreachable_daemon_is_reported_not_assumed_clean(
        self, doctor_env, monkeypatch
    ):
        monkeypatch.setenv("QUENCHFORGE_RERANK_MODEL", "bge-reranker-v2-m3")

        report = await _run_doctor(None)

        assert report["local_daemon"]["reachable"] is False, (
            "an unreachable daemon must not be indistinguishable from a clean audit"
        )
        assert [f for f in report["findings"] if f["kind"] == "not_served"] == []
