# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cross-encoder reranker using ONNX Runtime.

Downloads and caches ms-marco-MiniLM-L-6-v2 (or configured model) from
HuggingFace on first use.  All runtime dependencies (onnxruntime, tokenizers,
numpy, huggingface-hub) are already present via chromadb — no extra pip
packages required.
"""

import logging
import os
import threading
from typing import Any

import numpy as np
import onnxruntime as ort
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

import config

logger = logging.getLogger("ai-companion.reranker")

# ---------------------------------------------------------------------------
# Singleton model loader
# ---------------------------------------------------------------------------

_session: ort.InferenceSession | None = None
_tokenizer: Tokenizer | None = None
_lock = threading.Lock()


def _load_model() -> tuple[ort.InferenceSession, Tokenizer]:
    """Download (once) and return the cross-encoder ONNX session + tokenizer."""
    global _session, _tokenizer
    if _session is not None and _tokenizer is not None:
        return _session, _tokenizer

    with _lock:
        if _session is not None and _tokenizer is not None:
            return _session, _tokenizer

        repo = config.RERANK_CROSS_ENCODER_MODEL
        onnx_file = config.RERANK_ONNX_FILENAME
        cache = config.RERANK_MODEL_CACHE_DIR or None  # empty → huggingface default

        logger.info("Downloading cross-encoder model: %s/%s", repo, onnx_file)
        model_path = hf_hub_download(repo_id=repo, filename=onnx_file, cache_dir=cache)
        tok_path = hf_hub_download(repo_id=repo, filename="tokenizer.json", cache_dir=cache)

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

        logger.info("Cross-encoder model ready (%s)", repo)
        return _session, _tokenizer


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -20, 20)))


def _score_pairs(query: str, documents: list[str]) -> list[float]:
    """Score (query, document) pairs via the cross-encoder.

    Returns a list of float scores in [0, 1] (sigmoid-normalised).
    """
    session, tokenizer = _load_model()

    encodings = tokenizer.encode_batch([(query, doc) for doc in documents])

    input_ids = np.array([e.ids for e in encodings], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encodings], dtype=np.int64)
    token_type_ids = np.array([e.type_ids for e in encodings], dtype=np.int64)

    # Only pass inputs the model actually expects
    expected = {inp.name for inp in session.get_inputs()}
    feeds: dict[str, np.ndarray] = {}
    if "input_ids" in expected:
        feeds["input_ids"] = input_ids
    if "attention_mask" in expected:
        feeds["attention_mask"] = attention_mask
    if "token_type_ids" in expected:
        feeds["token_type_ids"] = token_type_ids

    logits = session.run(None, feeds)[0]  # (N, num_labels) or (N,)

    if logits.ndim == 2 and logits.shape[1] >= 2:
        scores = _sigmoid(logits[:, 1])  # positive-class logit
    elif logits.ndim == 2:
        scores = _sigmoid(logits[:, 0])
    else:
        scores = _sigmoid(logits)

    return scores.tolist()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def warmup() -> None:
    """Pre-load ONNX model at startup to avoid cold-start penalty."""
    global _session, _tokenizer
    if _session is not None:
        return
    try:
        _load_model()
        logger.info("Reranker ONNX model pre-warmed")
    except (OSError, RuntimeError, ValueError, ImportError) as e:
        logger.warning("Reranker warmup failed (will retry on first use): %s", e)


def rerank(
    query: str,
    results: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rerank retrieval results using cross-encoder scores.

    Takes the top ``QUERY_RERANK_CANDIDATES`` results, scores each
    (query, chunk) pair through the cross-encoder, blends with the
    original hybrid-search relevance, and returns the full list
    re-sorted by the blended score.
    """
    if len(results) <= 1:
        return results

    candidates = results[: config.QUERY_RERANK_CANDIDATES]
    remainder = results[config.QUERY_RERANK_CANDIDATES :]

    if len(candidates) <= 1:
        return results

    documents = [r["content"] for r in candidates]
    ce_scores = _score_pairs(query, documents)

    from utils.retrieval_profile import get_rerank_weights

    for result, ce_score in zip(candidates, ce_scores):
        original = result["relevance"]
        # Per-chunk profile adjusts CE vs original weights
        ce_w, orig_w = get_rerank_weights(
            result.get("retrieval_profile"),
            config.RERANK_CE_WEIGHT,
            config.RERANK_ORIGINAL_WEIGHT,
        )
        result["relevance"] = round(
            ce_w * ce_score + orig_w * original,
            4,
        )

    candidates.sort(key=lambda x: x["relevance"], reverse=True)
    return candidates + remainder
