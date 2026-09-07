# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for the local-throughput probe and the per-function expectations it feeds.

``InferenceConfig`` detects availability but never speed — ``embed_latency_ms``
and ``rerank_latency_ms`` sit at 0 on non-quenchforge installs, and nothing
tells an operator how long a 1,000-token background call actually takes on
their hardware. ``probe_local_throughput()`` runs one small completion against
the configured local backend off the request path; ``expectations_for()``
turns the measured rate into per-stage seconds.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

import utils.inference_config as mod
from core.utils import internal_llm as illm


@pytest.fixture(autouse=True)
def _isolate_config(monkeypatch):
    """Every test gets a fresh, un-detected InferenceConfig singleton and no
    local provider unless it opts in. Pre-seeding ``_config`` (rather than
    resetting it to ``None``) keeps ``get_inference_config()`` from falling
    through to real hardware/network detection — this suite fakes the HTTP
    boundary only, not GPU probing or an actual Ollama connectivity check."""
    monkeypatch.setattr(mod, "_config", mod.InferenceConfig(), raising=False)
    monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "openrouter", raising=False)


class _FakeResponse:
    def __init__(self, body: dict[str, Any]) -> None:
        self._body = body

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict[str, Any]:
        return self._body


def _wire_fake_client(monkeypatch, post_impl):
    fake_client = MagicMock()
    fake_client.post = post_impl
    monkeypatch.setattr(mod, "_get_ollama_client", AsyncMock(return_value=fake_client))


# ---------------------------------------------------------------------------
# probe_local_throughput — llama-server (quenchforge) timings
# ---------------------------------------------------------------------------


class TestProbeLocalThroughputQuenchforge:
    async def test_parses_llama_server_timings_into_rates(self, monkeypatch):
        monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "quenchforge", raising=False)
        monkeypatch.setattr(
            mod, "effective_local_model_async", AsyncMock(return_value="qwen2.5-7b"),
        )

        async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
            # quenchforge's gateway does not expose llama-server's native
            # /completion route (404 live) — it proxies the OpenAI-shaped
            # /v1/chat/completions instead, carrying `timings` alongside
            # `usage`.
            assert url.endswith("/v1/chat/completions")
            assert json["model"] == "qwen2.5-7b"
            assert json["max_tokens"] == 64
            assert json["temperature"] == 0
            assert json["messages"][0]["content"]
            return _FakeResponse(
                {
                    "timings": {
                        "prompt_n": 256,
                        "prompt_per_second": 100.0,
                        "predicted_n": 64,
                        "predicted_per_second": 9.0,
                    },
                    "usage": {"prompt_tokens": 256, "completion_tokens": 64},
                },
            )

        _wire_fake_client(monkeypatch, _post)

        await mod.probe_local_throughput()

        cfg = mod.get_inference_config()
        assert cfg.local_prompt_tok_s == pytest.approx(100.0)
        assert cfg.local_gen_tok_s == pytest.approx(9.0)
        assert cfg.local_probe_at is not None

    async def test_prompt_carries_a_nonce_and_differs_between_probes(self, monkeypatch):
        """quenchforge's gateway caches prompt prefixes — live, a repeated
        identical prompt collapsed to `prompt_n: 1, cached_tokens: 34`
        (a cache hit, not a measurement). Each probe must send a distinct
        prompt so the prompt phase is genuinely reprocessed."""
        monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "quenchforge", raising=False)
        monkeypatch.setattr(
            mod, "effective_local_model_async", AsyncMock(return_value="qwen2.5-7b"),
        )

        seen_prompts: list[str] = []

        async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
            seen_prompts.append(json["messages"][0]["content"])
            return _FakeResponse({"timings": {"prompt_per_second": 100.0, "predicted_per_second": 9.0}})

        _wire_fake_client(monkeypatch, _post)

        await mod.probe_local_throughput()
        await mod.probe_local_throughput()

        assert len(seen_prompts) == 2
        assert seen_prompts[0] != seen_prompts[1]

    async def test_cache_hit_prompt_n_discards_the_probe(self, monkeypatch, caplog):
        """Defense in depth behind the nonce: a `timings.prompt_n` far below
        the prompt we sent means the server served the prompt phase from
        cache, so `prompt_per_second` measured almost nothing. Such a probe
        must not be stored — it would leak an inflated rate into every
        `expectations_for()` projection as `basis: "measured"`."""
        monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "quenchforge", raising=False)
        monkeypatch.setattr(
            mod, "effective_local_model_async", AsyncMock(return_value="qwen2.5-7b"),
        )

        async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
            return _FakeResponse(
                {
                    "timings": {
                        "prompt_n": 1,
                        "cache_n": 34,
                        "prompt_per_second": 2500.0,
                        "predicted_n": 64,
                        "predicted_per_second": 9.0,
                    },
                },
            )

        _wire_fake_client(monkeypatch, _post)

        with caplog.at_level(logging.INFO, logger=mod.logger.name):
            await mod.probe_local_throughput()

        cfg = mod.get_inference_config()
        assert cfg.local_prompt_tok_s is None
        assert cfg.local_gen_tok_s is None
        assert cfg.local_probe_at is None
        cache_lines = [r for r in caplog.records if "prompt_n" in r.getMessage()]
        assert len(cache_lines) == 1

    async def test_skipped_when_no_local_provider_configured(self, monkeypatch):
        # Fixture default: INTERNAL_LLM_PROVIDER=openrouter (no local backend).
        fake_post = AsyncMock()
        _wire_fake_client(monkeypatch, fake_post)

        await mod.probe_local_throughput()

        fake_post.assert_not_called()
        cfg = mod.get_inference_config()
        assert cfg.local_prompt_tok_s is None
        assert cfg.local_gen_tok_s is None
        assert cfg.local_probe_at is None


# ---------------------------------------------------------------------------
# probe_local_throughput — Ollama /api/generate shape
# ---------------------------------------------------------------------------


class TestProbeLocalThroughputOllama:
    async def test_parses_ollama_eval_counts_into_rates(self, monkeypatch):
        monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "ollama", raising=False)
        monkeypatch.setattr(
            mod, "effective_local_model_async", AsyncMock(return_value="llama3.2:3b"),
        )

        async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
            assert url.endswith("/api/generate")
            assert json["model"] == "llama3.2:3b"
            return _FakeResponse(
                {
                    "prompt_eval_count": 215,
                    "prompt_eval_duration": 1_000_000_000,  # 1s -> 215 tok/s
                    "eval_count": 19,
                    "eval_duration": 1_000_000_000,  # 1s -> 19 tok/s
                },
            )

        _wire_fake_client(monkeypatch, _post)

        await mod.probe_local_throughput()

        cfg = mod.get_inference_config()
        assert cfg.local_prompt_tok_s == pytest.approx(215.0)
        assert cfg.local_gen_tok_s == pytest.approx(19.0)


# ---------------------------------------------------------------------------
# probe_local_throughput — timeout / failure
# ---------------------------------------------------------------------------


class TestProbeLocalThroughputTimeout:
    async def test_timeout_leaves_fields_none_and_logs_once(self, monkeypatch, caplog):
        monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "quenchforge", raising=False)
        monkeypatch.setattr(mod, "LOCAL_THROUGHPUT_PROBE_TIMEOUT_S", 0.05, raising=False)
        monkeypatch.setattr(
            mod, "effective_local_model_async", AsyncMock(return_value="qwen2.5-7b"),
        )

        async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
            await asyncio.sleep(10)
            raise AssertionError("unreachable")

        _wire_fake_client(monkeypatch, _post)

        with caplog.at_level(logging.INFO, logger="ai-companion"):
            await mod.probe_local_throughput()

        cfg = mod.get_inference_config()
        assert cfg.local_prompt_tok_s is None
        assert cfg.local_gen_tok_s is None
        assert cfg.local_probe_at is None

        probe_records = [r for r in caplog.records if "throughput probe" in r.message.lower()]
        assert len(probe_records) == 1

    async def test_connection_failure_leaves_fields_none_and_logs_once(self, monkeypatch, caplog):
        monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "quenchforge", raising=False)
        monkeypatch.setattr(
            mod, "effective_local_model_async", AsyncMock(return_value="qwen2.5-7b"),
        )

        async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
            raise httpx.ConnectError("connection refused")

        _wire_fake_client(monkeypatch, _post)

        with caplog.at_level(logging.INFO, logger="ai-companion"):
            await mod.probe_local_throughput()

        cfg = mod.get_inference_config()
        assert cfg.local_gen_tok_s is None

        probe_records = [r for r in caplog.records if "throughput probe" in r.message.lower()]
        assert len(probe_records) == 1


# ---------------------------------------------------------------------------
# probe_local_throughput — wall-clock fallback must exclude gate queue time
# ---------------------------------------------------------------------------


class TestProbeWallClockFallback:
    """When neither shape reports per-phase timing, the probe falls back to
    a whole-round-trip rate. That round trip must start at the HTTP call,
    not at the top of the function — a caller queued behind the pacing gate
    would otherwise have its queue wait counted as inference time, deflating
    the reported rate."""

    async def test_fallback_rate_excludes_gate_queue_wait(self, monkeypatch):
        monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "quenchforge", raising=False)
        monkeypatch.setattr(
            mod, "effective_local_model_async", AsyncMock(return_value="qwen2.5-7b"),
        )

        @asynccontextmanager
        async def _delayed_slot(interactive: bool):  # noqa: ARG001
            # Simulate a caller queued behind the pacing gate for 1s before
            # a permit frees up.
            await asyncio.sleep(1.0)
            yield

        fake_gate = MagicMock()
        fake_gate.slot = _delayed_slot
        monkeypatch.setattr(mod, "_get_pacing_gate", lambda: fake_gate)

        async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
            return _FakeResponse({})  # no `timings` -> forces the wall-clock fallback

        _wire_fake_client(monkeypatch, _post)

        await mod.probe_local_throughput()

        cfg = mod.get_inference_config()
        # If the 1s gate wait leaked into the fallback's elapsed time,
        # prompt_tok_s would work out to ~256/1.0 = 256. A near-instant
        # post (elapsed measured only from acquisition) yields a rate far
        # above that.
        assert cfg.local_prompt_tok_s > 1000
        assert cfg.local_gen_tok_s > 1000


# ---------------------------------------------------------------------------
# probe_local_throughput — local-LLM priority-gate discipline
# ---------------------------------------------------------------------------


class TestProbePacingGate:
    """The probe must not bypass the local-LLM priority gate
    (``core.utils.internal_llm._get_pacing_gate``): landing as an extra
    concurrent request on top of ``INTERNAL_LLM_MAX_CONCURRENCY``, or
    ahead of interactive work, was exactly the bug the gate exists to
    prevent for every other call site."""

    @pytest.fixture(autouse=True)
    def _fresh_pacing_state(self):
        illm._reset_pacing_state()
        yield
        illm._reset_pacing_state()

    async def test_probe_acquires_gate_as_background(self, monkeypatch):
        """During the in-flight request, the gate's background-held counter
        must be 1 — proof the probe entered ``slot(interactive=False)``, not
        the interactive path (which would leave ``_background_held`` at 0)."""
        monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "quenchforge", raising=False)
        monkeypatch.setattr(
            mod, "effective_local_model_async", AsyncMock(return_value="qwen2.5-7b"),
        )
        monkeypatch.setenv("INTERNAL_LLM_MAX_CONCURRENCY", "2")

        seen_background_held: dict[str, int] = {}

        async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
            seen_background_held["held"] = illm._get_pacing_gate()._background_held
            return _FakeResponse({"timings": {"prompt_per_second": 100.0, "predicted_per_second": 9.0}})

        _wire_fake_client(monkeypatch, _post)

        await mod.probe_local_throughput()

        assert seen_background_held["held"] == 1

    async def test_probe_waits_when_interactive_holds_full_capacity(self, monkeypatch):
        """Capacity 2, two interactive callers holding both permits: the
        probe (background) must queue rather than run concurrently — it
        never lands as an extra permit on top of the cap nor displaces
        interactive work."""
        monkeypatch.setattr(mod.config, "INTERNAL_LLM_PROVIDER", "quenchforge", raising=False)
        monkeypatch.setattr(
            mod, "effective_local_model_async", AsyncMock(return_value="qwen2.5-7b"),
        )
        monkeypatch.setenv("INTERNAL_LLM_MAX_CONCURRENCY", "2")
        gate = illm._get_pacing_gate()

        release = asyncio.Event()

        async def _hold_interactive():
            async with gate.slot(interactive=True):
                await release.wait()

        holders = [asyncio.create_task(_hold_interactive()) for _ in range(2)]
        await asyncio.sleep(0.02)  # let both holders acquire their permits

        async def _post(url: str, *, json: dict) -> _FakeResponse:  # noqa: ARG001
            return _FakeResponse({"timings": {"prompt_per_second": 100.0, "predicted_per_second": 9.0}})

        _wire_fake_client(monkeypatch, _post)

        probe_task = asyncio.create_task(mod.probe_local_throughput())
        await asyncio.sleep(0.05)
        assert not probe_task.done(), "probe must queue while interactive holds every permit"

        release.set()
        await asyncio.gather(*holders)
        await probe_task

        cfg = mod.get_inference_config()
        assert cfg.local_gen_tok_s == pytest.approx(9.0)


# ---------------------------------------------------------------------------
# expectations_for — pure derivation from measured rates
# ---------------------------------------------------------------------------


class TestExpectationsFor:
    def test_measured_rates_yield_expected_seconds(self):
        cfg = mod.InferenceConfig(local_prompt_tok_s=100.0, local_gen_tok_s=9.0)

        result = mod.expectations_for(cfg)

        assert result["memory_extract"]["seconds"] == pytest.approx(43.3, abs=0.05)
        assert result["memory_extract"]["basis"] == "measured"
        assert result["entity_extraction"]["seconds"] == pytest.approx(60.4, abs=0.05)
        assert result["entity_extraction"]["basis"] == "measured"

    def test_chat_turn_tail_sums_memory_and_entity_extraction(self):
        cfg = mod.InferenceConfig(local_prompt_tok_s=100.0, local_gen_tok_s=9.0)

        result = mod.expectations_for(cfg)

        expected = result["memory_extract"]["seconds"] + result["entity_extraction"]["seconds"]
        assert result["chat_turn_tail_s"] == pytest.approx(expected)

    def test_unmeasured_config_yields_unmeasured_basis_and_no_seconds(self):
        cfg = mod.InferenceConfig()  # local_prompt_tok_s / local_gen_tok_s default None

        result = mod.expectations_for(cfg)

        for stage in mod.STAGE_TOKEN_SHAPES:
            assert result[stage]["basis"] == "unmeasured"
            assert "seconds" not in result[stage]
        assert result["chat_turn_tail_s"] is None

    def test_all_stage_shapes_present(self):
        cfg = mod.InferenceConfig(local_prompt_tok_s=100.0, local_gen_tok_s=9.0)

        result = mod.expectations_for(cfg)

        for stage in ("memory_extract", "entity_extraction", "wiki_summary", "claim_extraction", "topic_extraction"):
            assert stage in result


# ---------------------------------------------------------------------------
# inference_health_payload — surfaces expectations
# ---------------------------------------------------------------------------


def test_inference_health_payload_carries_expectations(monkeypatch):
    cfg = mod.InferenceConfig(local_prompt_tok_s=100.0, local_gen_tok_s=9.0)
    monkeypatch.setattr(mod, "_config", cfg, raising=False)

    payload = mod.inference_health_payload()

    assert "expectations" in payload
    assert payload["expectations"]["memory_extract"]["basis"] == "measured"


# ---------------------------------------------------------------------------
# _inference_recheck_loop — re-probes on every pass
# ---------------------------------------------------------------------------


async def test_recheck_loop_disabled_interval_skips_probe(monkeypatch):
    monkeypatch.setenv("INFERENCE_RECHECK_INTERVAL", "0")
    probe_calls = AsyncMock()
    monkeypatch.setattr(mod, "probe_local_throughput", probe_calls)

    # INFERENCE_RECHECK_INTERVAL=0 returns immediately without looping/probing —
    # pins the disabled-interval short-circuit still wins over the new probe call.
    await mod._inference_recheck_loop()

    probe_calls.assert_not_called()


def test_recheck_loop_source_calls_probe_each_pass():
    """Static contract for the infinite ``while True`` loop body: exercising
    a real pass requires driving ``asyncio.sleep`` inside it, which risks
    patching the real event loop's sleep. Source-pinned instead, mirroring
    the existing ``test_main_prewarm_block_gates_on_ollama_enabled`` pattern."""
    import inspect

    src = inspect.getsource(mod._inference_recheck_loop)
    assert "probe_local_throughput()" in src


def test_recheck_loop_source_calls_detection_via_to_thread():
    """``detect_embedding_provider()`` runs subprocess GPU probes (5s
    timeouts) and several ``httpx.get`` calls (2s timeouts) synchronously —
    calling it directly from the recheck loop blocks the event loop for the
    duration of every recheck pass."""
    import inspect

    src = inspect.getsource(mod._inference_recheck_loop)
    assert "asyncio.to_thread(detect_embedding_provider)" in src


async def test_recheck_loop_probe_failure_keeps_previous_rates(monkeypatch):
    """``detect_embedding_provider()`` installs a brand new ``InferenceConfig``
    with no measured rates. If the loop doesn't carry the old rates onto it
    before re-probing, a probe failure on this pass leaves the UI reporting
    "unmeasured" even though a measurement from an earlier pass is still
    good."""
    monkeypatch.setenv("INFERENCE_RECHECK_INTERVAL", "300")

    old_cfg = mod.InferenceConfig(local_prompt_tok_s=100.0, local_gen_tok_s=9.0, local_probe_at=123.0)
    monkeypatch.setattr(mod, "_config", old_cfg, raising=False)

    def _detect_replacement():
        # Mirrors detect_embedding_provider(): builds+installs a fresh
        # config with no rate fields set.
        fresh = mod.InferenceConfig()
        monkeypatch.setattr(mod, "_config", fresh, raising=False)
        return fresh

    monkeypatch.setattr(mod, "detect_embedding_provider", _detect_replacement)

    async def _failing_probe():
        return None  # simulates a timed-out/failed probe: cfg is untouched

    monkeypatch.setattr(mod, "probe_local_throughput", _failing_probe)

    sleep_calls = {"n": 0}

    async def _fake_sleep(_seconds):
        sleep_calls["n"] += 1
        if sleep_calls["n"] > 1:
            raise RuntimeError("stop-after-one-pass")

    monkeypatch.setattr(mod.asyncio, "sleep", _fake_sleep)

    with pytest.raises(RuntimeError, match="stop-after-one-pass"):
        await mod._inference_recheck_loop()

    cfg = mod.get_inference_config()
    assert cfg.local_prompt_tok_s == pytest.approx(100.0)
    assert cfg.local_gen_tok_s == pytest.approx(9.0)
    assert cfg.local_probe_at == pytest.approx(123.0)


# ---------------------------------------------------------------------------
# Lifespan wiring — static contract (no live stack in this test tier)
# ---------------------------------------------------------------------------


def test_main_lifespan_starts_probe_and_recheck_loop():
    from pathlib import Path

    main_src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert "probe_local_throughput" in main_src
    assert "_inference_recheck_loop" in main_src
    assert "app.state.inference_probe_task" in main_src


def test_main_lifespan_cancels_recheck_loop_on_shutdown():
    """Static contract mirroring test_main_lifespan_wires_invariants_refresh_loop
    (tests/test_invariants.py): _inference_recheck_loop is a perpetual
    ``while True`` task like app.state.invariants_refresh_task, so it needs
    the identical store-on-app.state + cancel()+await shutdown pattern or it
    leaks past lifespan exit. The boot-time probe task needs the same
    treatment — it's a real asyncio.Task, just not a looping one."""
    from pathlib import Path

    main_src = (Path(__file__).resolve().parents[1] / "app" / "main.py").read_text(encoding="utf-8")
    assert "app.state.inference_recheck_task" in main_src
    assert "app.state.inference_recheck_task.cancel()" in main_src
    assert "await app.state.inference_recheck_task" in main_src
    assert "app.state.inference_probe_task" in main_src
    assert "app.state.inference_probe_task.cancel()" in main_src
    assert "await app.state.inference_probe_task" in main_src
