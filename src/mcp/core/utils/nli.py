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
