# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A failed local call on an inbox stage is an error, not a cloud retry.

The fallback re-sends the identical payload — the mail body — to OpenRouter.
Inbox stages never do that unless CERID_INBOX_CLOUD_FALLBACK=true, and the
global ALLOW_CLOUD_EGRESS_WHEN_LOCAL=false still wins over that flag.
"""
from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import core.utils.internal_llm as mod
from core.utils import llm_client

pytestmark = pytest.mark.asyncio


class _PassThroughBreaker:
    async def call(self, fn):  # type: ignore[no-untyped-def]
        return await fn()


@pytest.fixture
def cloud(monkeypatch) -> AsyncMock:
    """A local backend that refuses every connection, and a cloud spy."""

    async def _refused(url, json=None):
        raise httpx.ConnectError("refused", request=httpx.Request("POST", url))

    fake_client = MagicMock()
    fake_client.post = _refused
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(mod, "get_breaker", lambda _name: _PassThroughBreaker())
    monkeypatch.setenv("OLLAMA_URL", "http://test-host:11434")
    monkeypatch.setenv("INTERNAL_LLM_MAX_RETRIES", "1")
    monkeypatch.setenv("INTERNAL_LLM_RETRY_BACKOFF", "0.001")
    monkeypatch.setattr(mod.config, "OLLAMA_DEFAULT_MODEL", "test-model", raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "", raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "ollama", raising=False)
    monkeypatch.setattr(mod.config, "ALLOW_CLOUD_EGRESS_WHEN_LOCAL", True, raising=False)
    monkeypatch.setattr(mod.config, "INBOX_CLOUD_FALLBACK", False, raising=False)
    monkeypatch.delenv("PROVIDER_STAGE_INBOX_TRIAGE", raising=False)
    monkeypatch.delenv("HOST_RECOMMENDED_LOCAL_BACKEND", raising=False)
    spy = AsyncMock(return_value="cloud-reply")
    monkeypatch.setattr(llm_client, "call_llm", spy)
    return spy


@pytest.mark.parametrize("stage", ["inbox_triage", "inbox_triage_review", "inbox_triage_escalate", "inbox_triage_draft"])
async def test_an_inbox_stage_does_not_fall_back_to_the_cloud(cloud, stage, caplog):
    caplog.set_level(logging.ERROR, logger="core.utils.internal_llm")
    with pytest.raises(RuntimeError, match=stage):
        await mod._call_ollama([{"role": "user", "content": "mail body"}], temperature=0, max_tokens=5, stage=stage)
    cloud.assert_not_awaited()
    assert any(stage in r.getMessage() for r in caplog.records), "the stage's failure is its recorded outcome"


async def test_the_flag_allows_the_inbox_fallback(cloud, monkeypatch):
    monkeypatch.setattr(mod.config, "INBOX_CLOUD_FALLBACK", True, raising=False)
    reply = await mod._call_ollama([{"role": "user", "content": "mail body"}], temperature=0, max_tokens=5, stage="inbox_triage")
    assert reply == "cloud-reply"
    cloud.assert_awaited_once()


async def test_the_global_switch_still_wins_over_the_flag(cloud, monkeypatch):
    monkeypatch.setattr(mod.config, "INBOX_CLOUD_FALLBACK", True, raising=False)
    monkeypatch.setattr(mod.config, "ALLOW_CLOUD_EGRESS_WHEN_LOCAL", False, raising=False)
    with pytest.raises(RuntimeError):
        await mod._call_ollama([{"role": "user", "content": "mail body"}], temperature=0, max_tokens=5, stage="inbox_triage")
    cloud.assert_not_awaited()


async def test_other_stages_keep_the_fallback(cloud):
    reply = await mod._call_ollama([{"role": "user", "content": "x"}], temperature=0, max_tokens=5, stage="wiki_summary")
    assert reply == "cloud-reply"
    cloud.assert_awaited_once()


async def test_the_triage_pass_records_the_failure_as_the_stage_outcome(cloud, caplog, monkeypatch):
    """Through call_internal_llm under local-only: every rung fails, nothing
    reaches the cloud, and the thread is held for review with each failure
    logged under its stage. (Under hybrid the ladder's frontier rung is a
    cloud call by design; that is escalation, not this fallback.)"""
    from core.agents.inbox_triage import _categorize_thread

    monkeypatch.setattr(mod.config, "CERID_ENVIRONMENT_PROFILE", "local-only", raising=False)

    class _Msg:
        title = "Note"
        content = "please look at this"
        source_name = "gmail"
        confidence = 0.5
        metadata = {"provider_thread_id": "t1", "provider_message_id": "t1-m", "from": "a@example.com"}

    caplog.set_level(logging.WARNING, logger="ai-companion.swallowed")
    result = await _categorize_thread("t1", [_Msg()])
    cloud.assert_not_awaited()
    assert result["band"] == "needs_review"
    stages = [r.getMessage() for r in caplog.records if "inbox_triage.llm." in r.getMessage()]
    assert stages and all("RuntimeError" in m for m in stages)
