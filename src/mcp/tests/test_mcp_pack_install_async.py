# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""AF-011 — the pkb_knowledge_pack_install MCP tool queues a background job.

Installing inline loaded a whole pack into the caller's memory and OOM'd on big
packs mid-beta; the REST path moved to a background KnowledgePackInstallJob. The
MCP tool now mirrors that: already-installed short-circuit, in-flight-job dedup,
then enqueue — returning a job_id instead of the installed record.
"""
from __future__ import annotations

from contextlib import ExitStack
from unittest.mock import MagicMock, patch

import pytest

from app.tools import _dispatch_raw


def _pack(version: str = "1.0.0") -> MagicMock:
    return MagicMock(version=version)


async def _call(pack_id: str = "p1"):
    return await _dispatch_raw("pkb_knowledge_pack_install", {"pack_id": pack_id})


def _common_patchers(registry, *, find=None, active=None):
    return [
        patch("core.knowledge.packs.load_registry", return_value=registry),
        patch("core.knowledge.packs.find_installed", return_value=find),
        patch("core.knowledge.packs.load_install_state", return_value=[]),
        patch("app.services.knowledge_packs.default_registry_path", return_value="reg"),
        patch("app.services.knowledge_packs.default_state_path", return_value="state"),
        patch("app.services.knowledge_packs.active_install_jobs",
              return_value=active if active is not None else {}),
    ]


@pytest.mark.asyncio
async def test_install_enqueues_and_returns_job_id():
    with ExitStack() as stack:
        for p in _common_patchers({"p1": _pack()}):
            stack.enter_context(p)
        enq = stack.enter_context(
            patch("app.services.knowledge_packs.enqueue_install_job", return_value="job-1")
        )
        result = await _call()
    assert result["pack_id"] == "p1"
    assert result["status"] == "queued"
    assert result["job_id"] == "job-1"
    assert "poll" in result  # tells the caller how to check completion
    enq.assert_called_once()


@pytest.mark.asyncio
async def test_install_already_installed_short_circuits():
    with ExitStack() as stack:
        for p in _common_patchers({"p1": _pack("1.0.0")}, find=MagicMock(version="1.0.0")):
            stack.enter_context(p)
        enq = stack.enter_context(
            patch("app.services.knowledge_packs.enqueue_install_job")
        )
        result = await _call()
    assert result["status"] == "already_installed"
    assert result["version"] == "1.0.0"
    assert "job_id" not in result
    enq.assert_not_called()


@pytest.mark.asyncio
async def test_install_reuses_inflight_job_without_reenqueue():
    with ExitStack() as stack:
        for p in _common_patchers({"p1": _pack()}, active={"p1": "existing-job"}):
            stack.enter_context(p)
        enq = stack.enter_context(
            patch("app.services.knowledge_packs.enqueue_install_job")
        )
        result = await _call()
    assert result["job_id"] == "existing-job"
    assert result["status"] == "queued"
    enq.assert_not_called()  # in-flight install deduped


@pytest.mark.asyncio
async def test_install_unknown_pack_raises():
    with (
        patch("core.knowledge.packs.load_registry", return_value={}),
        patch("app.services.knowledge_packs.default_registry_path", return_value="reg"),
    ):
        with pytest.raises(ValueError):
            await _call("nope")
