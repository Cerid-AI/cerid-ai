# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""NLI entailment scoring using ONNX Runtime.

Downloads and caches cross-encoder/nli-deberta-v3-xsmall (or configured model)
from HuggingFace on first use.  All runtime dependencies (onnxruntime, tokenizers,
numpy, huggingface-hub) are already present via chromadb — no extra pip packages
required.

Label order for cross-encoder/nli-deberta-v3-xsmall:
  index 0 = contradiction
  index 1 = entailment
  index 2 = neutral

Convention: premise = evidence (KB content), hypothesis = claim.
"""

import asyncio
import logging
import os
import threading
from typing import Any

import numpy as np
import onnxruntime as ort
import sentry_sdk
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

import config
from core.observability.span_helpers import span
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.nli")

_LABEL_NAMES = ["contradiction", "entailment", "neutral"]

_session: ort.InferenceSession | None = None
_tokenizer: Tokenizer | None = None
_lock = threading.Lock()

# Task 14: observable load status for startup invariants.  Flipped to True
# only after a successful load; surfaced by /health via
# ``app.startup.invariants``.
_MODEL_LOADED: bool = False


def _softmax(logits: np.ndarray) -> np.ndarray:
    """Numerically stable softmax over the last axis."""
    shifted = logits - np.max(logits, axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.sum(exp, axis=-1, keepdims=True)


def _load_model() -> tuple[ort.InferenceSession, Tokenizer]:
    """Download (once) and return the NLI ONNX session + tokenizer."""
    global _session, _tokenizer, _MODEL_LOADED
    if _session is not None and _tokenizer is not None:
        return _session, _tokenizer

    with _lock:
        if _session is not None and _tokenizer is not None:
            return _session, _tokenizer

        repo = config.NLI_MODEL
        onnx_file = config.NLI_ONNX_FILENAME
        cache = config.NLI_MODEL_CACHE_DIR or None

        logger.info("Downloading NLI model: %s/%s", repo, onnx_file)
        try:
            model_path = hf_hub_download(
                repo_id=repo, filename=onnx_file, cache_dir=cache,
            )
            tok_path = hf_hub_download(
                repo_id=repo, filename="tokenizer.json", cache_dir=cache,
            )
        except Exception:
            logger.exception("Failed to download NLI model from HuggingFace")
            raise

        sess_opts = ort.SessionOptions()
        sess_opts.inter_op_num_threads = 1
        sess_opts.intra_op_num_threads = min(4, os.cpu_count() or 1)

        _session = ort.InferenceSession(
            model_path,
            sess_options=sess_opts,
            providers=["CPUExecutionProvider"],
        )
        _tokenizer = Tokenizer.from_file(tok_path)
        _tokenizer.enable_truncation(max_length=512)
        _tokenizer.enable_padding()

        _MODEL_LOADED = True
        logger.info("NLI model ready (%s)", repo)
        return _session, _tokenizer


def warmup() -> None:
    """Pre-load the NLI model so first call isn't slow.

    Task 14: no longer silent.  On failure, ``_MODEL_LOADED`` stays False and
    the exception message is logged at WARNING.  ``/health`` surfaces the
    failure via the invariants snapshot and flips to 503 — verification,
    Self-RAG, and RAGAS all depend on NLI, so running without it is a
    deployment incident, not a soft failure.

    The server itself still starts — the model will be loaded lazily on
    first use if this warmup fails, which gives an operator the chance to
    restore network connectivity without a restart.
    """
    global _session, _MODEL_LOADED
    if _session is not None:
        _MODEL_LOADED = True
        return
    try:
        _load_model()
    except Exception:
        _MODEL_LOADED = False
        logger.exception(
            "nli.warmup_failed — /health will report unhealthy until a "
            "lazy load on first use succeeds"
        )
        sentry_sdk.capture_exception()


def nli_score(premise: str, hypothesis: str) -> dict[str, Any]:
    """Score a single (premise, hypothesis) pair via NLI.

    Args:
        premise: The evidence text (e.g. KB content).
        hypothesis: The claim to check against the evidence.

    Returns:
        Dict with keys:
        - "entailment": float probability [0, 1]
        - "contradiction": float probability [0, 1]
        - "neutral": float probability [0, 1]
        - "label": str — highest-probability class name
    """
    session, tokenizer = _load_model()

    encoding = tokenizer.encode(premise, hypothesis)

    input_ids = np.array([encoding.ids], dtype=np.int64)
    attention_mask = np.array([encoding.attention_mask], dtype=np.int64)
    token_type_ids = np.array([encoding.type_ids], dtype=np.int64)

    expected = {inp.name for inp in session.get_inputs()}
    feeds: dict[str, np.ndarray] = {}
    if "input_ids" in expected:
        feeds["input_ids"] = input_ids
    if "attention_mask" in expected:
        feeds["attention_mask"] = attention_mask
    if "token_type_ids" in expected:
        feeds["token_type_ids"] = token_type_ids

    logits = session.run(None, feeds)[0]  # shape: (1, 3)
    probs = _softmax(logits)[0]  # shape: (3,)

    best_idx = int(np.argmax(probs))
    return {
        "contradiction": round(float(probs[0]), 4),
        "entailment": round(float(probs[1]), 4),
        "neutral": round(float(probs[2]), 4),
        "label": _LABEL_NAMES[best_idx],
    }


def batch_nli_score(pairs: list[tuple[str, str]]) -> list[dict[str, Any]]:
    """Score multiple (premise, hypothesis) pairs in a single batch.

    Args:
        pairs: List of (premise, hypothesis) tuples.

    Returns:
        List of dicts, one per pair, same format as nli_score().
    """
    if not pairs:
        return []

    session, tokenizer = _load_model()

    encodings = tokenizer.encode_batch([(p, h) for p, h in pairs])

    input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    token_type_ids = np.array([e.type_ids for e in encodings], dtype=np.int64)

    expected = {inp.name for inp in session.get_inputs()}
    feeds: dict[str, np.ndarray] = {}
    if "input_ids" in expected:
        feeds["input_ids"] = input_ids
    if "attention_mask" in expected:
        feeds["attention_mask"] = attention_mask
    if "token_type_ids" in expected:
        feeds["token_type_ids"] = token_type_ids

    with span("retrieval.nli", "onnx_entail", batch_size=len(pairs)):
        logits = session.run(None, feeds)[0]  # shape: (N, 3)
    probs = _softmax(logits)  # shape: (N, 3)

    results: list[dict[str, Any]] = []
    for row in probs:
        best_idx = int(np.argmax(row))
        results.append({
            "contradiction": round(float(row[0]), 4),
            "entailment": round(float(row[1]), 4),
            "neutral": round(float(row[2]), 4),
            "label": _LABEL_NAMES[best_idx],
        })
    return results


# ---------------------------------------------------------------------------
# Async batch-coalescing API (v0.93.10)
#
# Verification path's `verify_claim` is async and dispatched via
# `asyncio.gather` for N claims in parallel.  Pre-v0.93.10 each task
# called sync `nli_score()` which serialised on the ONNX-session lock —
# N concurrent tasks took N × per-call time instead of one batch's worth.
#
# `nli_score_async()` joins a sliding-window batch.  Concurrent callers
# inside the same window get coalesced into a single `batch_nli_score()`
# invocation, then each receives their own per-pair result.  Single
# callers (no concurrency) still pay the worst-case ``coalesce_ms`` wait
# but the win on the hot path is substantial: the typical
# /agent/hallucination call has 5-15 claims dispatched concurrently and
# they all rendezvous in one batch.
#
# Time-window default: 10 ms.  Tuned to `~half the per-call latency` so
# the wait cost is bounded.  Operators tuning for latency-critical
# workloads can drop it to 0 (no batching) via NLI_COALESCE_MS=0.
# ---------------------------------------------------------------------------

_COALESCE_MS = int(os.getenv("NLI_COALESCE_MS", "10"))
_COALESCE_MAX_BATCH = int(os.getenv("NLI_COALESCE_MAX_BATCH", "32"))


class _NliBatcher:
    """Async batch-coalescer for NLI calls.

    Public surface: ``async submit(premise, hypothesis) -> dict``.

    Per-event-loop instance — created lazily on first call via
    ``_get_batcher()``.  The "one instance per loop" rule matters because
    `asyncio.Lock()` is bound to the loop it was constructed under, and
    cerid runs both the uvicorn loop and per-test loops (pytest-asyncio).
    """

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._pending: list[tuple[tuple[str, str], asyncio.Future]] = []
        self._flush_task: asyncio.Task | None = None

    async def submit(self, premise: str, hypothesis: str) -> dict[str, Any]:
        """Submit one pair; return the per-pair result.

        Joins the current batch if a flush task is already pending;
        otherwise starts one with ``coalesce_ms`` delay.
        """
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        async with self._lock:
            self._pending.append(((premise, hypothesis), future))
            # Force-flush if we already hit the max batch.  Otherwise let
            # the pending flush task fire on its own timer.
            should_force = len(self._pending) >= _COALESCE_MAX_BATCH
            if self._flush_task is None or self._flush_task.done():
                self._flush_task = loop.create_task(self._flush_after_delay())
        if should_force:
            # Cancel the timer-driven flush and run immediately.
            self._flush_task.cancel()
            await self._flush()
        return await future

    async def _flush_after_delay(self) -> None:
        """Wait `coalesce_ms`, then flush whatever's pending."""
        try:
            if _COALESCE_MS > 0:
                await asyncio.sleep(_COALESCE_MS / 1000.0)
            await self._flush()
        except asyncio.CancelledError:
            # Force-flush path cancelled us; the caller is doing the
            # flush itself, so we just exit cleanly.
            pass

    async def _flush(self) -> None:
        """Drain the pending list and resolve all futures in one batch."""
        async with self._lock:
            if not self._pending:
                return
            batch = self._pending
            self._pending = []

        pairs = [pair for pair, _ in batch]
        # The CPU-bound ONNX call would otherwise block the event loop.
        # Run in a worker thread; ONNX session is internally thread-safe
        # for inference once initialized, but `_lock` in `_load_model`
        # guards the one-time init.
        try:
            results = await asyncio.to_thread(batch_nli_score, pairs)
        except Exception as exc:  # noqa: BLE001 — futures must always resolve
            log_swallowed_error("core.utils.nli.async_batch", exc)
            # Resolve every future with a neutral verdict so callers
            # don't hang forever.  Same shape as the per-claim exception
            # handler in verification.py.
            neutral = {
                "contradiction": 0.0,
                "entailment": 0.0,
                "neutral": 1.0,
                "label": "neutral",
            }
            for _, fut in batch:
                if not fut.done():
                    fut.set_result(neutral)
            return

        for (_, fut), result in zip(batch, results):
            if not fut.done():
                fut.set_result(result)


# Per-event-loop cache.  ``id(loop)`` keys avoid the cross-loop
# `asyncio.Lock` problem flagged by pytest-asyncio's loop-per-test mode.
_batchers: dict[int, "_NliBatcher"] = {}
_batchers_lock = threading.Lock()


def _get_batcher() -> "_NliBatcher":
    """Return the batcher bound to the current event loop."""
    loop = asyncio.get_running_loop()
    key = id(loop)
    # Fast path — no lock if already present.
    cached = _batchers.get(key)
    if cached is not None:
        return cached
    with _batchers_lock:
        cached = _batchers.get(key)
        if cached is None:
            cached = _NliBatcher()
            _batchers[key] = cached
        return cached


async def nli_score_async(premise: str, hypothesis: str) -> dict[str, Any]:
    """Async NLI scoring with automatic batch coalescing.

    Preferred over sync ``nli_score()`` whenever the caller is inside an
    asyncio task — concurrent calls in the same event-loop tick get
    coalesced into a single batch inference.

    Args:
        premise: The evidence text (e.g. KB content).
        hypothesis: The claim to check against the evidence.

    Returns:
        Same shape as ``nli_score()``: ``{"contradiction", "entailment",
        "neutral", "label"}``.

    Tuning env vars:
        ``NLI_COALESCE_MS=10`` — batch-window in milliseconds; 0 disables
        coalescing (each call runs solo).
        ``NLI_COALESCE_MAX_BATCH=32`` — max pairs per inference call;
        any submitted past this cap force-flush the current batch.
    """
    batcher = _get_batcher()
    return await batcher.submit(premise, hypothesis)


def reset_async_batcher_for_test() -> None:
    """Clear all per-loop batchers — test hook only.

    pytest-asyncio uses a fresh event loop per test; without this the
    `_batchers` dict accumulates stale (loop, batcher) pairs that point
    at closed loops.  Tests that exercise the async path call this in
    a fixture teardown.
    """
    with _batchers_lock:
        _batchers.clear()
