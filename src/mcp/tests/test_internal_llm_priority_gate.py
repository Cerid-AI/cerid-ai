# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Interactive priority on the local internal-LLM pacing gate.

The plain FIFO semaphore let the ingest tail (``wiki_summary``,
``entity_extraction``, …) hold both permits while a chat turn's
``claim_extraction`` / ``memory_extract`` waited out its budget
(tasks/2026-09-06-chat-verify-performance-root-cause.md §2a). The gate now
reserves headroom for interactive stages, keeps the timeout cooldown per
class, and records interactive demand for background schedulers.
"""

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import core.utils.internal_llm as mod


@pytest.fixture(autouse=True)
def _fresh_pacing_state():
    mod._reset_pacing_state()
    yield
    mod._reset_pacing_state()


class _PassThroughBreaker:
    async def call(self, fn):  # type: ignore[no-untyped-def]
        return await fn()


class _FakeResponse:
    status_code = 200

    def raise_for_status(self):
        return None

    def json(self):
        return {"message": {"content": "ok"}}


def _wire_backend(monkeypatch, post):
    fake_client = MagicMock()
    fake_client.post = post
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))
    monkeypatch.setattr(mod, "get_breaker", lambda _name: _PassThroughBreaker())
    monkeypatch.setenv("OLLAMA_URL", "http://test-host:11434")
    monkeypatch.setattr(mod.config, "OLLAMA_DEFAULT_MODEL", "test-model", raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_MODEL", "", raising=False)
    monkeypatch.setenv("INTERNAL_LLM_RETRY_BACKOFF", "0.001")
    monkeypatch.setenv("INTERNAL_LLM_MAX_RETRIES", "3")
    monkeypatch.setenv("INTERNAL_LLM_MAX_CONCURRENCY", "2")


def _call(tag: str, *, stage: str | None = None, interactive: bool = False):
    """One local call whose request body carries *tag* as its only content."""
    return mod._call_ollama(
        [{"role": "user", "content": tag}],
        temperature=0,
        max_tokens=5,
        stage=stage,
        interactive=interactive,
    )


def _blocking_post(started: list[str], holds: dict[str, asyncio.Event]):
    async def _post(url, json=None):
        tag = json["messages"][0]["content"]
        started.append(tag)
        await holds[tag].wait()
        return _FakeResponse()

    return _post


async def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached within timeout")
        await asyncio.sleep(0.005)


async def _settle() -> None:
    """Give freshly-created tasks time to park inside the gate."""
    await asyncio.sleep(0.05)


@pytest.mark.asyncio
async def test_second_background_call_waits_for_the_first(monkeypatch):
    """Background callers get capacity - 1 permits, so with capacity 2 the
    second background call queues behind the first."""
    started: list[str] = []
    holds = {t: asyncio.Event() for t in ("B1", "B2")}
    _wire_backend(monkeypatch, _blocking_post(started, holds))

    tasks = [
        asyncio.create_task(_call("B1", stage="wiki_summary")),
        asyncio.create_task(_call("B2", stage="entity_extraction")),
    ]
    await _wait_until(lambda: started == ["B1"])
    await _settle()
    assert started == ["B1"], "second background call must not hold the spare permit"

    for event in holds.values():
        event.set()
    assert await asyncio.gather(*tasks) == ["ok", "ok"]


@pytest.mark.asyncio
async def test_interactive_call_takes_the_free_permit(monkeypatch):
    """An interactive call arriving while a background call holds a permit
    proceeds immediately instead of queueing."""
    started: list[str] = []
    holds = {t: asyncio.Event() for t in ("B", "I")}
    _wire_backend(monkeypatch, _blocking_post(started, holds))

    background = asyncio.create_task(_call("B", stage="wiki_summary"))
    await _wait_until(lambda: started == ["B"])
    interactive = asyncio.create_task(_call("I", stage="claim_extraction"))
    await _wait_until(lambda: "I" in started)
    assert not background.done()

    for event in holds.values():
        event.set()
    await asyncio.gather(background, interactive)


@pytest.mark.asyncio
async def test_interactive_waiter_wins_over_earlier_background_waiter(monkeypatch):
    """Both permits busy, a background caller queued FIRST and an interactive
    caller second: the freed permit goes to the interactive caller."""
    started: list[str] = []
    holds = {t: asyncio.Event() for t in ("A1", "A2", "C", "D")}
    _wire_backend(monkeypatch, _blocking_post(started, holds))

    a1 = asyncio.create_task(_call("A1", stage="claim_extraction"))
    a2 = asyncio.create_task(_call("A2", stage="memory_extract"))
    await _wait_until(lambda: sorted(started) == ["A1", "A2"])

    c = asyncio.create_task(_call("C", stage="wiki_summary"))
    await _settle()
    d = asyncio.create_task(_call("D", stage="query_decompose"))
    await _settle()
    assert len(started) == 2, "gate admitted more than capacity"

    holds["A1"].set()
    await _wait_until(lambda: len(started) == 3)
    assert started[2] == "D", "background waiter took the permit ahead of interactive"

    for event in holds.values():
        event.set()
    await asyncio.gather(a1, a2, c, d)


@pytest.mark.asyncio
async def test_cancelled_holder_releases_its_permit(monkeypatch):
    """A call cancelled while holding a permit hands it back."""
    started: list[str] = []
    holds = {t: asyncio.Event() for t in ("B1", "B2")}
    _wire_backend(monkeypatch, _blocking_post(started, holds))

    b1 = asyncio.create_task(_call("B1", stage="wiki_summary"))
    await _wait_until(lambda: started == ["B1"])
    b2 = asyncio.create_task(_call("B2", stage="wiki_summary"))
    await _settle()
    assert started == ["B1"]

    b1.cancel()
    await _wait_until(lambda: started == ["B1", "B2"])

    holds["B2"].set()
    assert await b2 == "ok"
    with pytest.raises(asyncio.CancelledError):
        await b1


@pytest.mark.asyncio
async def test_cancelled_interactive_waiter_unblocks_background(monkeypatch):
    """A cancelled interactive waiter stops counting as demand, so the
    background caller behind it is admitted."""
    started: list[str] = []
    holds = {t: asyncio.Event() for t in ("A1", "A2", "C", "D")}
    _wire_backend(monkeypatch, _blocking_post(started, holds))

    a1 = asyncio.create_task(_call("A1", stage="claim_extraction"))
    a2 = asyncio.create_task(_call("A2", stage="memory_extract"))
    await _wait_until(lambda: sorted(started) == ["A1", "A2"])

    c = asyncio.create_task(_call("C", stage="wiki_summary"))
    await _settle()
    d = asyncio.create_task(_call("D", stage="claim_extraction"))
    await _settle()

    d.cancel()
    with pytest.raises(asyncio.CancelledError):
        await d
    holds["A1"].set()
    await _wait_until(lambda: len(started) == 3)
    assert started[2] == "C"

    for event in holds.values():
        event.set()
    await asyncio.gather(a1, a2, c)


@pytest.mark.asyncio
async def test_background_timeout_does_not_delay_interactive(monkeypatch):
    """The timeout cooldown is per class: a background timeout paces later
    background calls and leaves interactive callers untouched."""
    monkeypatch.setattr(
        "core.utils.llm_client.call_llm", AsyncMock(return_value="cloud"),
    )

    async def _post(url, json=None):
        raise httpx.TimeoutException("slow")

    _wire_backend(monkeypatch, _post)
    monkeypatch.setenv("INTERNAL_LLM_MAX_RETRIES", "1")
    monkeypatch.setenv("INTERNAL_LLM_TIMEOUT_COOLDOWN", "0.2")

    assert await _call("bg", stage="wiki_summary") == "cloud"

    start = time.monotonic()
    await mod._wait_pacing_cooldown(interactive=True)
    assert time.monotonic() - start < 0.05

    start = time.monotonic()
    await mod._wait_pacing_cooldown(interactive=False)
    assert time.monotonic() - start >= 0.1


@pytest.mark.asyncio
async def test_interactive_timeout_does_not_delay_background(monkeypatch):
    monkeypatch.setattr(
        "core.utils.llm_client.call_llm", AsyncMock(return_value="cloud"),
    )

    async def _post(url, json=None):
        raise httpx.TimeoutException("slow")

    _wire_backend(monkeypatch, _post)
    monkeypatch.setenv("INTERNAL_LLM_MAX_RETRIES", "1")
    monkeypatch.setenv("INTERNAL_LLM_TIMEOUT_COOLDOWN", "0.2")

    assert await _call("i", stage="claim_extraction") == "cloud"

    start = time.monotonic()
    await mod._wait_pacing_cooldown(interactive=False)
    assert time.monotonic() - start < 0.05


@pytest.mark.asyncio
async def test_interactive_demand_recent_tracks_interactive_calls(monkeypatch):
    async def _post(url, json=None):
        return _FakeResponse()

    _wire_backend(monkeypatch, _post)

    assert mod.interactive_demand_recent() is False
    await _call("bg", stage="wiki_summary")
    assert mod.interactive_demand_recent() is False

    await _call("i", stage="memory_extract")
    assert mod.interactive_demand_recent() is True

    await asyncio.sleep(0.05)
    assert mod.interactive_demand_recent(window_s=0.01) is False


@pytest.mark.asyncio
async def test_explicit_interactive_flag_overrides_stage_class(monkeypatch):
    """A background-named stage passed with interactive=True is interactive."""
    async def _post(url, json=None):
        return _FakeResponse()

    _wire_backend(monkeypatch, _post)

    await _call("x", stage="entity_extraction", interactive=True)
    assert mod.interactive_demand_recent() is True


@pytest.mark.asyncio
async def test_background_waiters_keep_arrival_order(monkeypatch):
    """Background fairness stays FIFO across an interactive bounce: with
    arrival order B1, I, B2 the interactive caller goes first and B1 still
    precedes B2, even though B1 was pushed back once to let I through."""
    started: list[str] = []
    holds = {t: asyncio.Event() for t in ("A1", "A2", "B1", "I", "B2")}
    _wire_backend(monkeypatch, _blocking_post(started, holds))

    a1 = asyncio.create_task(_call("A1", stage="claim_extraction"))
    a2 = asyncio.create_task(_call("A2", stage="memory_extract"))
    await _wait_until(lambda: sorted(started) == ["A1", "A2"])

    b1 = asyncio.create_task(_call("B1", stage="wiki_summary"))
    await _settle()
    i = asyncio.create_task(_call("I", stage="claim_extraction"))
    await _settle()
    b2 = asyncio.create_task(_call("B2", stage="entity_extraction"))
    await _settle()
    assert len(started) == 2

    holds["A1"].set()
    await _wait_until(lambda: len(started) == 3)
    assert started[2] == "I", "interactive caller must pre-empt both background waiters"

    holds["A2"].set()
    await _wait_until(lambda: len(started) == 4)
    assert started[3] == "B1", "background waiters must keep arrival order"

    holds["B1"].set()
    await _wait_until(lambda: len(started) == 5)
    assert started[4] == "B2"

    for event in holds.values():
        event.set()
    await asyncio.gather(a1, a2, b1, i, b2)


@pytest.mark.asyncio
async def test_release_survives_a_second_cancellation(monkeypatch):
    """Cancelling a holder a second time while it hands its permit back must
    not lose the permit. The gate lock is held here so the release actually
    parks on it — an uncontended give-back never suspends and so can never be
    interrupted."""
    started: list[str] = []
    holds = {t: asyncio.Event() for t in ("B", "AFTER")}
    _wire_backend(monkeypatch, _blocking_post(started, holds))

    gate = mod._get_pacing_gate()
    b = asyncio.create_task(_call("B", stage="wiki_summary"))
    await _wait_until(lambda: started == ["B"])
    assert gate._held == 1

    async with gate._cond:
        b.cancel()  # lands on the in-flight request
        await asyncio.sleep(0.01)  # b unwinds and parks on the release
        b.cancel()  # lands on the release
        await asyncio.sleep(0)
    with pytest.raises(asyncio.CancelledError):
        await b

    await _wait_until(lambda: gate._held == 0)
    assert gate._background_held == 0

    after = asyncio.create_task(_call("AFTER", stage="wiki_summary"))
    await _wait_until(lambda: "AFTER" in started)
    holds["AFTER"].set()
    assert await after == "ok"


def test_mcp_tool_stage_classifies_interactive():
    assert mod._is_interactive("mcp_answer_with_citations", False) is True


def test_unknown_mcp_stage_classifies_interactive():
    """The prefix rule covers a stage nobody enumerated, by design."""
    assert mod._is_interactive("mcp_future_stage", False) is True


def test_wiki_summary_still_classifies_background():
    assert mod._is_interactive("wiki_summary", False) is False


@pytest.mark.asyncio
async def test_mcp_tool_call_takes_the_free_permit_ahead_of_background(monkeypatch):
    """An MCP tool-stage call arriving while a background call holds a permit
    proceeds immediately instead of queueing, per the mcp_* prefix rule."""
    started: list[str] = []
    holds = {t: asyncio.Event() for t in ("B", "M")}
    _wire_backend(monkeypatch, _blocking_post(started, holds))

    background = asyncio.create_task(_call("B", stage="wiki_summary"))
    await _wait_until(lambda: started == ["B"])
    mcp_call = asyncio.create_task(_call("M", stage="mcp_answer_with_citations"))
    await _wait_until(lambda: "M" in started)
    assert not background.done()

    for event in holds.values():
        event.set()
    await asyncio.gather(background, mcp_call)
