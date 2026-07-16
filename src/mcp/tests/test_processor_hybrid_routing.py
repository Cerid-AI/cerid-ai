# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""End-to-end tests for hybrid-mode job routing through ProcessorWorker (Task 2.5b).

Exercises the full path a real LLM-backed job takes: worker resolves a
``ModelDecision`` via ``app.processor.model_policy.resolve_job_model``, scopes
``core.utils.internal_llm.llm_call_override`` around ``job.run()`` when the API
tier is chosen, and prices the completion against whichever model actually
ran. A stub job calls the real ``call_internal_llm`` (with ``_call_ollama`` /
``call_llm`` mocked at the transport boundary) so the override is proven live,
not just asserted against a recorded value.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.processor.worker import ProcessorWorker
from config import settings
from core.processor.cost import CostEstimate
from core.processor.job import BaseJob, JobRecord, JobResult, JobState
from core.processor.priority import Priority
from core.utils import internal_llm as internal_llm_mod
from core.utils import llm_client
from core.utils.internal_llm import call_internal_llm
from tests.test_processor_worker import _make_worker, _mock_queue

# ---------------------------------------------------------------------------
# Stub LLM job — calls the REAL call_internal_llm so the override is proven
# live rather than merely asserted against a stored decision.
# ---------------------------------------------------------------------------


class _LLMStubJob(BaseJob):
    job_type = "llm_stub_job"

    #: Every constructed instance, so tests can inspect whether run() fired.
    instances: list["_LLMStubJob"] = []

    def __init__(
        self, tokens_in: int = 5000, tokens_out: int = 500, **kwargs
    ) -> None:
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        self.ran = False
        _LLMStubJob.instances.append(self)

    @property
    def priority(self) -> Priority:
        return Priority.LOW

    def estimate_cost(self) -> CostEstimate:
        return CostEstimate(
            estimated_tokens_in=self._tokens_in,
            estimated_tokens_out=self._tokens_out,
            model="ollama/local",
            estimated_usd=Decimal("0.00"),
            confidence="high",
        )

    async def run(self, progress_cb) -> JobResult:
        self.ran = True
        await progress_cb(0.0)
        content = await call_internal_llm(
            [{"role": "user", "content": "hi"}],
            stage="hybrid_routing_test",
        )
        await progress_cb(1.0)
        return JobResult(
            job_id="",
            actual_tokens_in=self._tokens_in,
            actual_tokens_out=self._tokens_out,
            metadata={"content": content},
        )


def _make_llm_record(
    *, tokens_in: int = 5000, tokens_out: int = 500, model: str = "ollama/local",
) -> JobRecord:
    return JobRecord(
        id=str(uuid.uuid4()),
        job_type="llm_stub_job",
        state=JobState.PENDING,
        priority=Priority.LOW,
        payload={"tokens_in": tokens_in, "tokens_out": tokens_out},
        enqueued_at=datetime.now(tz=timezone.utc),
        estimated_tokens_in=tokens_in,
        estimated_tokens_out=tokens_out,
        requires_llm=True,
        model=model,
    )


@pytest.fixture(autouse=True)
def _reset_stub_instances():
    _LLMStubJob.instances.clear()
    yield
    _LLMStubJob.instances.clear()


@pytest.fixture(autouse=True)
def _local_baseline_provider(monkeypatch):
    """Pin call_internal_llm's own baseline resolution to local ollama.

    Mirrors how the four real LLM jobs behave when INTERNAL_LLM_PROVIDER (or
    a per-stage PIPELINE_PROVIDERS entry) points at the local daemon — the
    scenario hybrid mode exists to escape. Isolates the worker's routing
    decision (the thing under test) from the module-global default.
    """
    monkeypatch.setattr(internal_llm_mod.config, "INTERNAL_LLM_PROVIDER", "ollama", raising=False)
    monkeypatch.setattr(internal_llm_mod.config, "PIPELINE_PROVIDERS", {}, raising=False)
    monkeypatch.delenv("PROVIDER_STAGE_HYBRID_ROUTING_TEST", raising=False)


@pytest.fixture
def _llm_transport(monkeypatch):
    """Mock the two transports call_internal_llm can reach: local + cloud."""
    fake_ollama = AsyncMock(return_value="local-response")
    monkeypatch.setattr(internal_llm_mod, "_call_ollama", fake_ollama)
    fake_call_llm = AsyncMock(return_value="cloud-response")
    monkeypatch.setattr(llm_client, "call_llm", fake_call_llm)
    return fake_ollama, fake_call_llm


def _make_llm_worker(record: JobRecord) -> tuple[MagicMock, ProcessorWorker]:
    queue = _mock_queue(record)
    worker = _make_worker(
        queue,
        registry={"llm_stub_job": _LLMStubJob},
        redis_client=MagicMock(),
    )
    return queue, worker


# ---------------------------------------------------------------------------
# 1. hybrid + big job + spend < cap -> API override active, cost non-zero
# ---------------------------------------------------------------------------


async def test_hybrid_big_job_under_cap_routes_to_api_and_records_real_cost(
    monkeypatch, _llm_transport,
):
    fake_ollama, fake_call_llm = _llm_transport
    monkeypatch.setattr(settings, "PROCESSOR_MODE", "hybrid", raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_THRESHOLD_TOKENS", 4000, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_MONTHLY_CAP_USD", 5.0, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_CAP_FALLBACK", "local", raising=False)
    monkeypatch.setattr(
        settings, "CATEGORIZE_MODELS", {"smart": "anthropic/claude-haiku-4-5"}, raising=False,
    )

    record = _make_llm_record(tokens_in=5000, tokens_out=500)
    queue, worker = _make_llm_worker(record)

    with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)), patch(
        "app.processor.metrics.processor_cost_usd_month",
        new_callable=AsyncMock,
        return_value=Decimal("1.00"),
    ), patch(
        "app.processor.metrics.record_completion", new_callable=AsyncMock,
    ) as mock_record:
        await worker.start()
        for _ in range(20):
            if queue.mark_completed.await_count:
                break
            await asyncio.sleep(0.02)
        await worker.stop()

    # The override was live during job.run(): the cloud transport fired with
    # the api-tier model, and the local transport never fired.
    fake_call_llm.assert_awaited_once()
    _, call_kwargs = fake_call_llm.call_args
    assert call_kwargs["model"] == "anthropic/claude-haiku-4-5"
    fake_ollama.assert_not_awaited()

    mock_record.assert_awaited_once()
    actual_cost = mock_record.call_args.kwargs["actual_cost_usd"]
    assert actual_cost > Decimal("0")


# ---------------------------------------------------------------------------
# 2. hybrid + big job + spend >= cap + fallback=local -> no override, local cost
# ---------------------------------------------------------------------------


async def test_hybrid_big_job_over_cap_fallback_local_no_override(
    monkeypatch, _llm_transport,
):
    fake_ollama, fake_call_llm = _llm_transport
    monkeypatch.setattr(settings, "PROCESSOR_MODE", "hybrid", raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_THRESHOLD_TOKENS", 4000, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_MONTHLY_CAP_USD", 5.0, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_CAP_FALLBACK", "local", raising=False)
    monkeypatch.setattr(
        settings, "CATEGORIZE_MODELS", {"smart": "anthropic/claude-haiku-4-5"}, raising=False,
    )

    record = _make_llm_record(tokens_in=5000, tokens_out=500)
    queue, worker = _make_llm_worker(record)

    with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)), patch(
        "app.processor.metrics.processor_cost_usd_month",
        new_callable=AsyncMock,
        return_value=Decimal("9.99"),
    ), patch(
        "app.processor.metrics.record_completion", new_callable=AsyncMock,
    ) as mock_record:
        await worker.start()
        for _ in range(20):
            if queue.mark_completed.await_count:
                break
            await asyncio.sleep(0.02)
        await worker.stop()

    fake_ollama.assert_awaited_once()
    fake_call_llm.assert_not_awaited()

    mock_record.assert_awaited_once()
    actual_cost = mock_record.call_args.kwargs["actual_cost_usd"]
    assert actual_cost == Decimal("0")


# ---------------------------------------------------------------------------
# 3. hybrid + big job + spend >= cap + fallback=hold -> job never runs
# ---------------------------------------------------------------------------


async def test_hybrid_big_job_over_cap_hold_never_runs(monkeypatch, _llm_transport, caplog):
    fake_ollama, fake_call_llm = _llm_transport
    monkeypatch.setattr(settings, "PROCESSOR_MODE", "hybrid", raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_THRESHOLD_TOKENS", 4000, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_MONTHLY_CAP_USD", 5.0, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_CAP_FALLBACK", "hold", raising=False)
    monkeypatch.setattr(
        settings, "CATEGORIZE_MODELS", {"smart": "anthropic/claude-haiku-4-5"}, raising=False,
    )

    record = _make_llm_record(tokens_in=5000, tokens_out=500)
    queue, worker = _make_llm_worker(record)

    with caplog.at_level(logging.WARNING, logger="ai-companion.processor.worker"):
        with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)), patch(
            "app.processor.metrics.processor_cost_usd_month",
            new_callable=AsyncMock,
            return_value=Decimal("5.00"),
        ), patch(
            "app.processor.metrics.record_completion", new_callable=AsyncMock,
        ) as mock_record:
            await worker.start()
            for _ in range(20):
                if queue.mark_held.await_count:
                    break
                await asyncio.sleep(0.02)
            await worker.stop()

    fake_ollama.assert_not_awaited()
    fake_call_llm.assert_not_awaited()
    assert len(_LLMStubJob.instances) == 1
    assert _LLMStubJob.instances[0].ran is False

    # CL-5/AF-017: a cost-cap hold is recorded via mark_held (HELD state), NOT
    # mark_completed — so it can never be mistaken for a successful run.
    queue.mark_held.assert_called_once()
    queue.mark_completed.assert_not_called()
    held_job_id, result = queue.mark_held.call_args[0]
    assert held_job_id == record.id
    assert result.metadata == {"held": True, "reason": "hybrid_cap_hold"}
    assert result.actual_tokens_in == 0
    assert result.actual_tokens_out == 0

    mock_record.assert_not_awaited()

    worker_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING and r.name == "ai-companion.processor.worker"
    ]
    assert len(worker_warnings) == 1
    assert record.id in worker_warnings[0].getMessage()


# ---------------------------------------------------------------------------
# 4. hybrid + small job -> no override, no monthly-spend Redis round-trip
# ---------------------------------------------------------------------------


async def test_hybrid_small_job_skips_monthly_spend_lookup(monkeypatch, _llm_transport):
    fake_ollama, fake_call_llm = _llm_transport
    monkeypatch.setattr(settings, "PROCESSOR_MODE", "hybrid", raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_THRESHOLD_TOKENS", 4000, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_MONTHLY_CAP_USD", 5.0, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_CAP_FALLBACK", "local", raising=False)
    monkeypatch.setattr(
        settings, "CATEGORIZE_MODELS", {"smart": "anthropic/claude-haiku-4-5"}, raising=False,
    )

    record = _make_llm_record(tokens_in=100, tokens_out=50)
    queue, worker = _make_llm_worker(record)

    with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)), patch(
        "app.processor.metrics.processor_cost_usd_month", new_callable=AsyncMock,
    ) as mock_spend, patch(
        "app.processor.metrics.record_completion", new_callable=AsyncMock,
    ) as mock_record:
        await worker.start()
        for _ in range(20):
            if queue.mark_completed.await_count:
                break
            await asyncio.sleep(0.02)
        await worker.stop()

    fake_ollama.assert_awaited_once()
    fake_call_llm.assert_not_awaited()
    mock_spend.assert_not_awaited()

    mock_record.assert_awaited_once()
    assert mock_record.call_args.kwargs["actual_cost_usd"] == Decimal("0")


# ---------------------------------------------------------------------------
# 5. local/default mode -> no override, identical behavior, local cost
# ---------------------------------------------------------------------------


async def test_hybrid_pro_model_routes_to_api_and_cap_accrues(
    monkeypatch, _llm_transport,
):
    """Task 2.5b fix: the shipped "pro" model id must price, or the cap never accrues.

    ``config.settings.CATEGORIZE_MODELS["pro"]`` is
    ``openrouter/anthropic/claude-sonnet-4.6`` — an ``openrouter/``-prefixed,
    dot-versioned id that ``PricingTable``'s default rows didn't cover before
    this fix. Prove the worker records a non-zero, correctly-computed cost
    for it so ``PROCESSOR_MONTHLY_CAP_USD`` actually tracks this spend.
    """
    fake_ollama, fake_call_llm = _llm_transport
    monkeypatch.setattr(settings, "PROCESSOR_MODE", "hybrid", raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_THRESHOLD_TOKENS", 4000, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_MONTHLY_CAP_USD", 5.0, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_CAP_FALLBACK", "local", raising=False)
    pro_model = "openrouter/anthropic/claude-sonnet-4.6"
    monkeypatch.setattr(
        settings, "CATEGORIZE_MODELS", {"smart": pro_model}, raising=False,
    )

    record = _make_llm_record(tokens_in=5000, tokens_out=500)
    queue, worker = _make_llm_worker(record)

    with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)), patch(
        "app.processor.metrics.processor_cost_usd_month",
        new_callable=AsyncMock,
        return_value=Decimal("1.00"),
    ), patch(
        "app.processor.metrics.record_completion", new_callable=AsyncMock,
    ) as mock_record:
        await worker.start()
        for _ in range(20):
            if queue.mark_completed.await_count:
                break
            await asyncio.sleep(0.02)
        await worker.stop()

    fake_call_llm.assert_awaited_once()
    _, call_kwargs = fake_call_llm.call_args
    assert call_kwargs["model"] == pro_model
    fake_ollama.assert_not_awaited()

    mock_record.assert_awaited_once()
    actual_cost = mock_record.call_args.kwargs["actual_cost_usd"]

    from core.processor.cost import estimate as cost_estimate

    expected = cost_estimate(pro_model, 5000, 500).estimated_usd
    assert expected > Decimal("0")
    assert actual_cost == expected


async def test_hybrid_unpriced_model_warns_and_falls_back_to_estimate(
    monkeypatch, _llm_transport, caplog,
):
    """A genuinely-unpriced routed model must WARN, not silently record $0.

    Distinguishable from the ``local``/``hold`` no-op paths: this job
    actually runs against the API-tier model (proving the override fired
    and cost tracking was attempted), but the pricing table has no row for
    it, so the worker must surface a WARNING naming the model rather than
    silently letting the cap under-count real spend.
    """
    fake_ollama, fake_call_llm = _llm_transport
    monkeypatch.setattr(settings, "PROCESSOR_MODE", "hybrid", raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_THRESHOLD_TOKENS", 4000, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_MONTHLY_CAP_USD", 5.0, raising=False)
    monkeypatch.setattr(settings, "PROCESSOR_API_CAP_FALLBACK", "local", raising=False)
    unpriced_model = "openrouter/some-vendor/unpriced-model"
    monkeypatch.setattr(
        settings, "CATEGORIZE_MODELS", {"smart": unpriced_model}, raising=False,
    )

    record = _make_llm_record(tokens_in=5000, tokens_out=500)
    queue, worker = _make_llm_worker(record)

    with caplog.at_level(logging.WARNING, logger="ai-companion.processor.worker"):
        with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)), patch(
            "app.processor.metrics.processor_cost_usd_month",
            new_callable=AsyncMock,
            return_value=Decimal("1.00"),
        ), patch(
            "app.processor.metrics.record_completion", new_callable=AsyncMock,
        ) as mock_record:
            await worker.start()
            for _ in range(20):
                if queue.mark_completed.await_count:
                    break
                await asyncio.sleep(0.02)
            await worker.stop()

    fake_call_llm.assert_awaited_once()
    fake_ollama.assert_not_awaited()

    mock_record.assert_awaited_once()
    # Falls back to the job's own pre-execution estimate (ollama/local, $0)
    # since the routed model couldn't be priced.
    assert mock_record.call_args.kwargs["actual_cost_usd"] == Decimal("0.00")

    cost_warnings = [
        r for r in caplog.records
        if r.levelno == logging.WARNING
        and r.name == "ai-companion.processor.worker"
        and unpriced_model in r.getMessage()
    ]
    assert len(cost_warnings) == 1


async def test_local_mode_no_override_records_local_cost(monkeypatch, _llm_transport):
    fake_ollama, fake_call_llm = _llm_transport
    monkeypatch.setattr(settings, "PROCESSOR_MODE", "local", raising=False)

    record = _make_llm_record(tokens_in=5000, tokens_out=500)
    queue, worker = _make_llm_worker(record)

    with patch("os.getloadavg", return_value=(0.0, 0.0, 0.0)), patch(
        "app.processor.metrics.processor_cost_usd_month", new_callable=AsyncMock,
    ) as mock_spend, patch(
        "app.processor.metrics.record_completion", new_callable=AsyncMock,
    ) as mock_record:
        await worker.start()
        for _ in range(20):
            if queue.mark_completed.await_count:
                break
            await asyncio.sleep(0.02)
        await worker.stop()

    fake_ollama.assert_awaited_once()
    fake_call_llm.assert_not_awaited()
    mock_spend.assert_not_awaited()

    mock_record.assert_awaited_once()
    assert mock_record.call_args.kwargs["actual_cost_usd"] == Decimal("0")
