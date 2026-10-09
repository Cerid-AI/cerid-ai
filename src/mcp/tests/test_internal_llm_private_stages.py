# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The forget assistant's stage never leaves the machine.

``forget_assist`` reads what the user asked to forget and snippets of it.
It is forced to the local backend whatever the profile, pin or override says,
and a local failure is its outcome: nothing is re-sent to OpenRouter. The
consented stage ``forget_assist_cloud`` goes to the cloud, except under the
local-only profile.
"""
from __future__ import annotations

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
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "openrouter", raising=False)
    monkeypatch.setattr(mod.config, "CERID_ENVIRONMENT_PROFILE", "cloud-first", raising=False)
    monkeypatch.setattr(mod.config, "ALLOW_CLOUD_EGRESS_WHEN_LOCAL", True, raising=False)
    monkeypatch.delenv("HOST_RECOMMENDED_LOCAL_BACKEND", raising=False)
    spy = AsyncMock(return_value="cloud-reply")
    monkeypatch.setattr(llm_client, "call_llm", spy)
    return spy


async def test_resolves_local_under_a_cloud_first_profile(cloud):
    assert mod._resolve_stage_provider("forget_assist", "openrouter") in ("ollama", "quenchforge")


async def test_an_env_pin_to_the_cloud_is_ignored(cloud, monkeypatch):
    monkeypatch.setenv("PROVIDER_STAGE_FORGET_ASSIST", "openrouter")
    assert mod._resolve_stage_provider("forget_assist", "openrouter") in ("ollama", "quenchforge")


async def test_a_local_failure_is_not_resent_to_the_cloud(cloud):
    with pytest.raises(RuntimeError, match="forget_assist"):
        await mod.call_internal_llm([{"role": "user", "content": "my old 401(k)"}], stage="forget_assist")
    cloud.assert_not_awaited()


async def test_a_cloud_override_is_refused(cloud):
    token = mod._llm_override.set(("openrouter", "some/model"))
    try:
        with pytest.raises(RuntimeError, match="forget_assist"):
            await mod.call_internal_llm([{"role": "user", "content": "x"}], stage="forget_assist")
    finally:
        mod._llm_override.reset(token)
    cloud.assert_not_awaited()


async def test_the_consented_stage_goes_to_the_cloud(cloud):
    assert mod._resolve_stage_provider("forget_assist_cloud", "ollama") == "openrouter"
    assert await mod.call_internal_llm([{"role": "user", "content": "x"}], stage="forget_assist_cloud") == "cloud-reply"


async def test_the_consented_stage_stays_local_under_local_only(cloud, monkeypatch):
    monkeypatch.setattr(mod.config, "CERID_ENVIRONMENT_PROFILE", "local-only", raising=False)
    assert mod._resolve_stage_provider("forget_assist_cloud", "openrouter") in ("ollama", "quenchforge")
