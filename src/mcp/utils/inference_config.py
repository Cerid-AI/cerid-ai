# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tiered inference configuration with platform/GPU detection.

Detects the best available execution provider for ONNX models and selects
the optimal embedding/reranking backend automatically.

Provider hierarchy (best → worst):
  1. fastembed-sidecar  — native GPU via sidecar process (Metal/CUDA/ROCm)
  2. ollama             — Ollama embedding endpoint (if available)
  3. onnx-gpu           — in-process ONNX with GPU provider
  4. onnx-cpu           — in-process ONNX with CPU (Docker default)

Usage:
  from utils.inference_config import get_inference_config
  cfg = get_inference_config()
  cfg.onnx_providers   # ["CoreMLExecutionProvider", "CPUExecutionProvider"]
  cfg.provider          # "onnx-cpu"
"""
from __future__ import annotations

import asyncio
import logging
import os
import platform
import shutil
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from http import HTTPStatus
from typing import Any

import httpx

import config
from config.constants import LOCAL_THROUGHPUT_PROBE_TIMEOUT_S
from core.utils.internal_llm import (
    _get_ollama_client,
    _get_pacing_gate,
    _served_models_url,
    effective_local_model_async,
)
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion")


class Platform(Enum):
    MACOS_ARM = "macos-arm"
    MACOS_INTEL = "macos-intel"
    LINUX_X86 = "linux-x86"
    LINUX_ARM = "linux-arm"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


class InferenceTier(Enum):
    OPTIMAL = "optimal"     # GPU-accelerated (sidecar or native)
    GOOD = "good"           # CPU sidecar (native, not in Docker)
    DEGRADED = "degraded"   # Docker CPU only
    UNKNOWN = "unknown"


@dataclass
class InferenceConfig:
    """Singleton holding the detected inference configuration."""

    provider: str = "onnx-cpu"
    tier: InferenceTier = InferenceTier.DEGRADED
    platform: Platform = Platform.UNKNOWN
    gpu_available: bool = False
    gpu_name: str = ""
    onnx_providers: list[str] = field(default_factory=lambda: ["CPUExecutionProvider"])
    ollama_available: bool = False
    sidecar_available: bool = False
    sidecar_url: str = ""
    embed_latency_ms: float = 0.0
    rerank_latency_ms: float = 0.0
    message: str = ""
    detected_at: float = 0.0
    # Measured local chat-model throughput (probe_local_throughput). None
    # until the first successful probe — never inferred from tier/hardware.
    local_prompt_tok_s: float | None = None
    local_gen_tok_s: float | None = None
    local_probe_at: float | None = None
    # True when the most recently *stored* measurement was taken while
    # another caller held a pacing-gate permit — a contended run measures
    # queueing, not the backend's quiet throughput.
    local_probe_contended: bool = False


# Module-level singleton
_config: InferenceConfig | None = None


def get_inference_config() -> InferenceConfig:
    """Return the current inference config (detect on first call)."""
    global _config
    if _config is None:
        _config = detect_embedding_provider()
    return _config


def detect_platform() -> Platform:
    """Detect the current platform."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return Platform.MACOS_ARM
        return Platform.MACOS_INTEL
    if system == "linux":
        if machine in ("x86_64", "amd64"):
            return Platform.LINUX_X86
        if machine in ("aarch64", "arm64"):
            return Platform.LINUX_ARM
        return Platform.LINUX_X86
    if system == "windows":
        return Platform.WINDOWS
    return Platform.UNKNOWN


def _probe_gpu(plat: Platform) -> tuple[bool, str]:
    """Detect GPU availability per platform."""
    if plat == Platform.MACOS_ARM:
        # Apple Silicon always has Metal (unified memory)
        return True, "Apple Silicon (Metal)"

    if plat in (Platform.LINUX_X86, Platform.LINUX_ARM):
        # Check NVIDIA
        if shutil.which("nvidia-smi"):
            try:
                result = subprocess.run(
                    ["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0 and result.stdout.strip():
                    return True, result.stdout.strip().split("\n")[0]
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                log_swallowed_error("utils.inference_config.probe_gpu_nvidia", exc)
        # Check ROCm
        if shutil.which("rocm-smi"):
            try:
                result = subprocess.run(
                    ["rocm-smi", "--showproductname"],
                    capture_output=True, text=True, timeout=5,
                )
                if result.returncode == 0:
                    return True, "AMD ROCm GPU"
            except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
                log_swallowed_error("utils.inference_config.probe_gpu_rocm", exc)

    return False, ""


def _probe_ollama() -> bool:
    """Check if Ollama is reachable."""
    ollama_url = os.getenv("OLLAMA_URL", "http://host.docker.internal:11434")
    try:
        import httpx
        resp = httpx.get(f"{ollama_url}/api/tags", timeout=2)
        return resp.status_code == HTTPStatus.OK
    except (httpx.HTTPError, OSError, ValueError) as exc:  # noqa: BLE001
        log_swallowed_error(
            "utils.inference_config.probe_ollama",
            exc,
            context={"ollama_url": ollama_url},
        )
    # Fallback: try localhost
    if "host.docker.internal" in ollama_url:
        try:
            import httpx
            resp = httpx.get("http://localhost:11434/api/tags", timeout=2)
            return resp.status_code == HTTPStatus.OK
        except (httpx.HTTPError, OSError, ValueError) as exc:  # noqa: BLE001
            log_swallowed_error(
                "utils.inference_config.probe_ollama_localhost",
                exc,
            )
    return False


def _probe_sidecar() -> tuple[bool, str]:
    """Check if the FastEmbed sidecar is running."""
    port = os.getenv("CERID_SIDECAR_PORT", "8889")
    url = os.getenv("CERID_SIDECAR_URL", f"http://localhost:{port}")
    try:
        import httpx
        resp = httpx.get(f"{url}/health", timeout=2)
        if resp.status_code == HTTPStatus.OK:
            return True, url
    except (httpx.HTTPError, OSError, ValueError) as exc:  # noqa: BLE001
        log_swallowed_error(
            "utils.inference_config.probe_sidecar",
            exc,
            context={"sidecar_url": url},
        )
    return False, url


def _select_onnx_providers(plat: Platform, gpu_available: bool) -> list[str]:
    """Select ONNX execution providers based on platform and GPU."""
    providers: list[str] = []

    if plat == Platform.MACOS_ARM and gpu_available:
        # CoreML is available on Apple Silicon
        try:
            import onnxruntime as ort
            available = ort.get_available_providers()
            if "CoreMLExecutionProvider" in available:
                providers.append("CoreMLExecutionProvider")
        except ImportError as exc:
            log_swallowed_error(
                "utils.inference_config.select_onnx_providers_coreml",
                exc,
            )

    if plat in (Platform.LINUX_X86, Platform.LINUX_ARM) and gpu_available:
        try:
            import onnxruntime as ort
            available = ort.get_available_providers()
            if "CUDAExecutionProvider" in available:
                providers.append("CUDAExecutionProvider")
            if "ROCMExecutionProvider" in available:
                providers.append("ROCMExecutionProvider")
        except ImportError as exc:
            log_swallowed_error(
                "utils.inference_config.select_onnx_providers_cuda_rocm",
                exc,
            )

    if plat == Platform.WINDOWS and gpu_available:
        try:
            import onnxruntime as ort
            available = ort.get_available_providers()
            if "DmlExecutionProvider" in available:
                providers.append("DmlExecutionProvider")
            if "CUDAExecutionProvider" in available:
                providers.append("CUDAExecutionProvider")
        except ImportError as exc:
            log_swallowed_error(
                "utils.inference_config.select_onnx_providers_dml_cuda",
                exc,
            )

    # CPU is always the fallback
    providers.append("CPUExecutionProvider")
    return providers


def detect_embedding_provider() -> InferenceConfig:
    """Detect the best available inference provider.

    Called once at startup and cached. Can be re-called to refresh.
    """
    global _config

    plat = detect_platform()
    gpu_available, gpu_name = _probe_gpu(plat)
    onnx_providers = _select_onnx_providers(plat, gpu_available)
    ollama_available = _probe_ollama()
    sidecar_available, sidecar_url = _probe_sidecar()

    # Determine best provider + tier
    if sidecar_available:
        provider = "fastembed-sidecar"
        tier = InferenceTier.OPTIMAL if gpu_available else InferenceTier.GOOD
        message = f"Sidecar at {sidecar_url}" + (f" with {gpu_name}" if gpu_name else "")
    elif len(onnx_providers) > 1:  # Has a GPU provider + CPU fallback
        provider = "onnx-gpu"
        tier = InferenceTier.OPTIMAL
        message = f"ONNX with {onnx_providers[0]}"
    elif ollama_available:
        provider = "ollama"
        tier = InferenceTier.GOOD
        message = "Ollama available for LLM tasks"
    else:
        provider = "onnx-cpu"
        tier = InferenceTier.DEGRADED
        message = "CPU-only inference (Docker default)"

    # Allow manual override
    manual = os.getenv("INFERENCE_MODE", "").lower()
    if manual and manual != "auto":
        if manual in ("onnx-cpu", "onnx-gpu", "ollama", "fastembed-sidecar"):
            provider = manual
            message = f"Manual override: {manual}"
            logger.info("Inference provider manually set to: %s", manual)

    config = InferenceConfig(
        provider=provider,
        tier=tier,
        platform=plat,
        gpu_available=gpu_available,
        gpu_name=gpu_name,
        onnx_providers=onnx_providers,
        ollama_available=ollama_available,
        sidecar_available=sidecar_available,
        sidecar_url=sidecar_url,
        message=message,
        detected_at=time.time(),
    )

    logger.info(
        "Inference detection: provider=%s tier=%s platform=%s gpu=%s onnx=%s",
        config.provider, config.tier.value, config.platform.value,
        config.gpu_name or "none", config.onnx_providers,
    )

    _config = config
    return config


async def _inference_recheck_loop() -> None:
    """Background coroutine that re-checks inference providers periodically.

    Detects upgrades (e.g. Ollama started mid-session) and downgrades
    (e.g. sidecar stopped). Logs tier changes. Also re-runs
    ``probe_local_throughput`` on every pass so measured tok/s tracks a
    local backend that gets faster/slower (model swap, contention) between
    boot and now, not just at startup.
    """
    interval = int(os.getenv("INFERENCE_RECHECK_INTERVAL", "300"))
    if interval <= 0:
        logger.info("Inference recheck disabled (INFERENCE_RECHECK_INTERVAL=0)")
        return

    logger.info("Inference recheck loop started (every %ds)", interval)
    while True:
        await asyncio.sleep(interval)
        try:
            old = get_inference_config()
            old_provider = old.provider
            old_tier = old.tier

            # detect_embedding_provider() is synchronous (subprocess GPU
            # probes with 5s timeouts, three httpx.get calls with 2s
            # timeouts) — run it off the event loop.
            new = await asyncio.to_thread(detect_embedding_provider)
            # It also builds and installs a brand new InferenceConfig, which
            # would otherwise null out the measured rates below until the
            # re-probe (which can itself fail) fills them back in.
            new.local_prompt_tok_s = old.local_prompt_tok_s
            new.local_gen_tok_s = old.local_gen_tok_s
            new.local_probe_at = old.local_probe_at
            new.local_probe_contended = old.local_probe_contended

            if new.provider != old_provider or new.tier != old_tier:
                direction = "upgrade" if _tier_rank(new.tier) > _tier_rank(old_tier) else "downgrade"
                logger.info(
                    "Inference provider %s: %s (%s) -> %s (%s)",
                    direction, old_provider, old_tier.value, new.provider, new.tier.value,
                )
            else:
                logger.debug("Inference recheck: no change (%s/%s)", new.provider, new.tier.value)

            await probe_local_throughput()
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(
                "utils.inference_config.recheck_loop",
                exc,
            )


def _tier_rank(tier: InferenceTier) -> int:
    """Numeric rank for tier comparison (higher = better)."""
    return {
        InferenceTier.UNKNOWN: 0,
        InferenceTier.DEGRADED: 1,
        InferenceTier.GOOD: 2,
        InferenceTier.OPTIMAL: 3,
    }.get(tier, 0)


# ── Local chat-model throughput probe ──────────────────────────────────────
# Detection above answers "is a local backend available"; it says nothing
# about how fast a real completion runs there. probe_local_throughput()
# answers that with one small measured completion, off the request path, so
# expectations_for() can report real per-function seconds instead of a
# hardware-tier guess.

_PROBE_PROMPT_TOKENS = 256
_PROBE_GEN_TOKENS = 64

# A probe is "contended" when more than this many pacing-gate permits are
# held while it runs — i.e. some other caller is in flight alongside it.
_CONTENDED_HELD_THRESHOLD = 1


def _probe_prompt() -> str:
    """Build the probe prompt fresh on every call.

    A fixed prompt string measured a cache hit, not a completion: live
    against quenchforge's gateway, an identical repeated prompt collapsed
    to ``prompt_n: 1, cached_tokens: 34`` (llama-server's prompt-prefix
    cache), corrupting ``prompt_tok_s``. The leading nonce defeats the
    cache so the prompt phase is genuinely reprocessed each probe.
    """
    nonce = uuid.uuid4().hex
    words = [nonce, *(["probe"] * (_PROBE_PROMPT_TOKENS - 1))]
    return " ".join(words)


# (prompt_tokens, output_tokens) per pipeline stage — fixed shapes, not
# measured; only the tok/s rate is measured. Values from
# tasks/2026-09-07-hardware-limited-client-configs.md §1/§4a.
STAGE_TOKEN_SHAPES: dict[str, tuple[int, int]] = {
    "memory_extract": (1000, 300),
    "entity_extraction": (1600, 400),
    "wiki_summary": (900, 500),
    "claim_extraction": (800, 200),
    "topic_extraction": (700, 100),
}


def _ollama_generate_rate(count: int | None, duration_ns: int | None) -> float | None:
    """tokens/second from an Ollama ``/api/generate`` count+duration pair.

    Durations are nanoseconds; a missing or zero duration can't derive a
    rate, so it reports "no data" rather than dividing by zero.
    """
    if not count or not duration_ns:
        return None
    return count / (duration_ns / 1_000_000_000)


async def probe_local_throughput() -> None:
    """Measure real local chat-model throughput with one small completion.

    Runs off the request path — the lifespan startup hook and every
    ``_inference_recheck_loop`` pass — never on a request. Skipped entirely
    when the configured provider isn't a local one (``ollama``/
    ``quenchforge``). On timeout or failure the rate fields are left as
    they were (``None`` until a probe actually succeeds) and exactly one
    INFO line explains why.
    """
    provider = getattr(config, "INTERNAL_LLM_PROVIDER", "openrouter")
    if provider not in ("ollama", "quenchforge"):
        return

    base_url = _served_models_url()
    try:
        client = await _get_ollama_client()
        async with asyncio.timeout(LOCAL_THROUGHPUT_PROBE_TIMEOUT_S):
            model = await effective_local_model_async()
            prompt = _probe_prompt()
            if provider == "quenchforge":
                # The gateway does NOT expose llama-server's native
                # /completion route (404 live: quenchforge's routes are
                # /api/chat, /api/generate, /v1/chat/completions,
                # /v1/embeddings, /v1/rerank) — it proxies the
                # OpenAI-compatible /v1/chat/completions instead, which
                # carries llama-server's `timings` alongside `usage`.
                # Acquired as a BACKGROUND-class caller on the same
                # priority gate _call_ollama uses: the probe must queue
                # behind INTERNAL_LLM_MAX_CONCURRENCY like any other
                # non-interactive stage, never land as an extra permit on
                # top of it or displace an interactive caller.
                async with _get_pacing_gate().slot(interactive=False):
                    # Held while still inside the slot: a permit count above
                    # one means another caller is running alongside us, so
                    # this run measures contention, not quiet throughput.
                    held = _get_pacing_gate().held()
                    # Measured from here, not gate acquisition, so the
                    # wall-clock fallback below times only the HTTP call.
                    start = time.monotonic()
                    resp = await client.post(
                        f"{base_url}/v1/chat/completions",
                        json={
                            "model": model,
                            "messages": [{"role": "user", "content": prompt}],
                            "max_tokens": _PROBE_GEN_TOKENS,
                            "temperature": 0,
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                timings = data.get("timings") or {}
                prompt_n = timings.get("prompt_n")
                if prompt_n is not None and prompt_n < _PROBE_PROMPT_TOKENS // 2:
                    # The server processed far fewer prompt tokens than we
                    # sent: a prefix-cache hit despite the nonce. The rate
                    # it reports measured almost nothing, and storing it
                    # would leak into every expectation as "measured".
                    logger.info(
                        "Local throughput probe discarded: prompt_n=%s of %s sent (cache hit)",
                        prompt_n, _PROBE_PROMPT_TOKENS,
                    )
                    return
                prompt_tok_s = timings.get("prompt_per_second")
                gen_tok_s = timings.get("predicted_per_second")
            else:
                async with _get_pacing_gate().slot(interactive=False):
                    held = _get_pacing_gate().held()
                    # Measured from here, not gate acquisition, so the
                    # wall-clock fallback below times only the HTTP call.
                    start = time.monotonic()
                    resp = await client.post(
                        f"{base_url}/api/generate",
                        json={
                            "model": model,
                            "prompt": prompt,
                            "stream": False,
                            "options": {"num_predict": _PROBE_GEN_TOKENS, "temperature": 0},
                        },
                    )
                    resp.raise_for_status()
                    data = resp.json()
                prompt_tok_s = _ollama_generate_rate(
                    data.get("prompt_eval_count"), data.get("prompt_eval_duration"),
                )
                gen_tok_s = _ollama_generate_rate(data.get("eval_count"), data.get("eval_duration"))

            if prompt_tok_s is None or gen_tok_s is None:
                # Neither shape reported per-phase timing (an older
                # llama-server build, or a stripped Ollama response) —
                # approximate from the whole round trip instead of
                # discarding a perfectly good completion. Coarse: it
                # ignores the prompt/generation split, but a rough number
                # beats none.
                elapsed = time.monotonic() - start
                if elapsed > 0:
                    if prompt_tok_s is None:
                        prompt_tok_s = _PROBE_PROMPT_TOKENS / elapsed
                    if gen_tok_s is None:
                        gen_tok_s = _PROBE_GEN_TOKENS / elapsed
    except (TimeoutError, httpx.HTTPError, ValueError, KeyError) as exc:
        logger.info("Local throughput probe failed or timed out: %s: %s", type(exc).__name__, exc)
        return

    if prompt_tok_s is None or gen_tok_s is None:
        logger.info("Local throughput probe returned no usable timing data")
        return

    contended = held > _CONTENDED_HELD_THRESHOLD
    cfg = get_inference_config()
    if contended and cfg.local_gen_tok_s is not None and not cfg.local_probe_contended:
        # A quiet measurement is already on file — a contended run would
        # only make the reported rate worse without saying why.
        logger.info(
            "Local throughput probe was contended (permits held=%d); keeping the "
            "previous quiet measurement", held,
        )
        return
    cfg.local_probe_contended = contended
    cfg.local_prompt_tok_s = prompt_tok_s
    cfg.local_gen_tok_s = gen_tok_s
    cfg.local_probe_at = time.time()


def expectations_for(cfg: InferenceConfig) -> dict:
    """Derive per-function latency expectations from cfg's measured rates.

    Pure — reads only ``cfg.local_prompt_tok_s`` / ``cfg.local_gen_tok_s``,
    never triggers a probe. Each ``STAGE_TOKEN_SHAPES`` entry reports its
    projected ``seconds`` (rounded to 1 decimal) and a ``basis`` of
    "measured" or "unmeasured"; ``chat_turn_tail_s`` sums memory_extract +
    entity_extraction — the two stages every chat turn pays for.
    """
    prompt_tok_s = cfg.local_prompt_tok_s
    gen_tok_s = cfg.local_gen_tok_s
    measured = bool(prompt_tok_s) and bool(gen_tok_s)

    result: dict[str, Any] = {}
    memory_extract_seconds: float | None = None
    entity_extraction_seconds: float | None = None
    for stage, (prompt_tokens, output_tokens) in STAGE_TOKEN_SHAPES.items():
        if measured and prompt_tok_s is not None and gen_tok_s is not None:
            seconds = round(prompt_tokens / prompt_tok_s + output_tokens / gen_tok_s, 1)
            result[stage] = {"seconds": seconds, "basis": "measured"}
            if stage == "memory_extract":
                memory_extract_seconds = seconds
            elif stage == "entity_extraction":
                entity_extraction_seconds = seconds
        else:
            result[stage] = {"basis": "unmeasured"}

    result["chat_turn_tail_s"] = (
        round(memory_extract_seconds + entity_extraction_seconds, 1)
        if memory_extract_seconds is not None and entity_extraction_seconds is not None
        else None
    )
    return result


def inference_health_payload() -> dict:
    """Return inference status for the /health endpoint.

    ``detect_embedding_provider`` runs at boot and caches, so the platform and
    provider facts below are a snapshot. The tier is NOT: a workload that has
    fallen back is a live fact recorded by ``core.utils.inference_health``, and
    reporting tier "good" while the ``inference_routing`` block in the same
    payload says the rerank lane is degraded gave an operator two opposite
    answers about one lane. The tier is reconciled against that signal here.

    The latency fields are ``None`` when nothing has measured them. They are
    only ever written by the Quenchforge and sidecar clients, never by the
    in-process ONNX leg, so an outage that pushes every rerank onto ONNX leaves
    them unwritten — and ``0.0`` reads as "instant" rather than "unmeasured".
    """
    cfg = get_inference_config()
    tier = cfg.tier
    degraded_workloads = sorted(
        name for name, st in _degradation_snapshot().items() if st.get("degraded")
    )
    if degraded_workloads and _tier_rank(tier) > _tier_rank(InferenceTier.DEGRADED):
        tier = InferenceTier.DEGRADED
    return {
        "provider": cfg.provider,
        "tier": tier.value,
        "gpu": cfg.gpu_available,
        "gpu_name": cfg.gpu_name,
        "platform": cfg.platform.value,
        "onnx_providers": cfg.onnx_providers,
        "ollama_available": cfg.ollama_available,
        "sidecar_available": cfg.sidecar_available,
        "embed_latency_ms": round(cfg.embed_latency_ms, 2) if cfg.embed_latency_ms else None,
        "rerank_latency_ms": round(cfg.rerank_latency_ms, 2) if cfg.rerank_latency_ms else None,
        "degraded_workloads": degraded_workloads,
        "message": cfg.message,
        "expectations": expectations_for(cfg),
        "contended": cfg.local_probe_contended,
    }


def _degradation_snapshot() -> dict:
    """Live per-workload degradation state. Never raises — /health must answer."""
    try:
        from core.utils import inference_health
        return inference_health.snapshot()
    except Exception as exc:  # noqa: BLE001 — observability fallback
        log_swallowed_error("utils.inference_config.degradation_snapshot", exc)
        return {}
