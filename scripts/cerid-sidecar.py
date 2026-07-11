#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cerid AI — FastEmbed Sidecar Server.

Lightweight FastAPI server that wraps FastEmbed + cross-encoder ONNX models
with native GPU acceleration (Metal on macOS, CUDA on Linux).

Runs as a native process OUTSIDE Docker to leverage host GPU.

Usage:
    python scripts/cerid-sidecar.py              # default port 8889
    CERID_SIDECAR_PORT=8890 python scripts/cerid-sidecar.py

Endpoints:
    POST /embed          — Embed texts, returns {"embeddings": [[float, ...]]}
    POST /rerank         — Rerank query+documents, returns {"scores": [float, ...]}
    POST /encode/sparse  — SPLADE-v3 sparse encode, returns {"vectors": [{tid: w, ...}, ...]}
    GET  /health         — Health check with model info
"""
from __future__ import annotations

import logging
import os
import platform
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import numpy as np

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
logger = logging.getLogger("cerid-sidecar")

# ---------------------------------------------------------------------------
# Model configuration
# ---------------------------------------------------------------------------
EMBEDDING_MODEL = os.getenv("SIDECAR_EMBEDDING_MODEL", "Snowflake/snowflake-arctic-embed-m-v1.5")
EMBEDDING_ONNX = os.getenv("SIDECAR_EMBEDDING_ONNX", "onnx/model.onnx")
RERANK_MODEL = os.getenv("SIDECAR_RERANK_MODEL", "cross-encoder/ms-marco-MiniLM-L-6-v2")
RERANK_ONNX = os.getenv("SIDECAR_RERANK_ONNX", "onnx/model.onnx")
PORT = int(os.getenv("CERID_SIDECAR_PORT", "8889"))

# SPLADE-v3 (Cycle 3.2 / v0.93.3). Lazy-loaded on the first
# /encode/sparse request — operators who don't enable sparse pay no
# cold-start cost.  Matches the model + ONNX file conventions used by
# the in-process encoder at core/retrieval/sparse.py.
SPARSE_MODEL = os.getenv("SIDECAR_SPARSE_MODEL", "Qdrant/Splade_PP_en_v1")
SPARSE_ONNX = os.getenv("SIDECAR_SPARSE_ONNX", "model.onnx")
SPARSE_TOP_K_TERMS = int(os.getenv("SIDECAR_SPARSE_TOP_K_TERMS", "256"))

# ---------------------------------------------------------------------------
# ONNX provider detection
# ---------------------------------------------------------------------------

def _detect_providers() -> list[str]:
    """Detect best available ONNX execution providers."""
    try:
        import onnxruntime as ort
        available = ort.get_available_providers()
    except ImportError:
        return ["CPUExecutionProvider"]

    providers: list[str] = []
    system = platform.system().lower()
    machine = platform.machine().lower()

    if system == "darwin" and machine in ("arm64", "aarch64"):
        if "CoreMLExecutionProvider" in available:
            providers.append("CoreMLExecutionProvider")
    elif system == "linux":
        if "CUDAExecutionProvider" in available:
            providers.append("CUDAExecutionProvider")
        if "ROCMExecutionProvider" in available:
            providers.append("ROCMExecutionProvider")

    providers.append("CPUExecutionProvider")
    return providers


# ---------------------------------------------------------------------------
# Model loading (singleton)
# ---------------------------------------------------------------------------
_embed_session = None
_embed_tokenizer = None
_rerank_session = None
_rerank_tokenizer = None
_splade_session = None
_splade_tokenizer = None
# SPLADE export branch — populated by _load_splade_model().  When the
# ONNX session emits "logits" the head is baked into the graph; otherwise
# the session emits only the backbone hidden state and the MLM head is
# bolted in numpy from _splade_decoder_w + _splade_decoder_b cached
# alongside the model.
_splade_has_logits_head = False
_splade_decoder_w = None
_splade_decoder_b = None
_providers: list[str] = []
_query_prefix = "Represent this sentence for searching relevant passages: "


def _load_embedding_model():
    global _embed_session, _embed_tokenizer
    if _embed_session is not None:
        return

    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    logger.info("Loading embedding model: %s", EMBEDDING_MODEL)
    model_path = hf_hub_download(repo_id=EMBEDDING_MODEL, filename=EMBEDDING_ONNX)
    tok_path = hf_hub_download(repo_id=EMBEDDING_MODEL, filename="tokenizer.json")

    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = min(4, os.cpu_count() or 1)

    _embed_session = ort.InferenceSession(model_path, sess_options=opts, providers=_providers)
    _embed_tokenizer = Tokenizer.from_file(tok_path)
    _embed_tokenizer.enable_truncation(max_length=512)
    _embed_tokenizer.enable_padding()

    logger.info("Embedding model loaded (providers: %s)", _providers)


def _load_rerank_model():
    global _rerank_session, _rerank_tokenizer
    if _rerank_session is not None:
        return

    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    logger.info("Loading rerank model: %s", RERANK_MODEL)
    model_path = hf_hub_download(repo_id=RERANK_MODEL, filename=RERANK_ONNX)
    tok_path = hf_hub_download(repo_id=RERANK_MODEL, filename="tokenizer.json")

    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = min(4, os.cpu_count() or 1)

    _rerank_session = ort.InferenceSession(model_path, sess_options=opts, providers=_providers)
    _rerank_tokenizer = Tokenizer.from_file(tok_path)
    _rerank_tokenizer.enable_truncation(max_length=512)
    _rerank_tokenizer.enable_padding()

    logger.info("Rerank model loaded (providers: %s)", _providers)


def _load_splade_model() -> None:
    """Lazy-load SPLADE-v3 on first /encode/sparse call.

    Mirrors the two-branch loader in ``core/retrieval/sparse.py``: if the
    ONNX export emits ``logits`` directly, the head is baked into the
    graph; otherwise the decoder weight + bias are bolted in numpy from
    a cached ``mlm_head.npz`` materialized via ``AutoModelForMaskedLM``
    once on first call (then pure-numpy on subsequent runs).
    """
    global _splade_session, _splade_tokenizer
    global _splade_has_logits_head, _splade_decoder_w, _splade_decoder_b
    if _splade_session is not None:
        return

    import onnxruntime as ort
    from huggingface_hub import hf_hub_download
    from tokenizers import Tokenizer

    logger.info("Loading SPLADE-v3 sparse model: %s", SPARSE_MODEL)
    model_path = hf_hub_download(repo_id=SPARSE_MODEL, filename=SPARSE_ONNX)
    tok_path = hf_hub_download(repo_id=SPARSE_MODEL, filename="tokenizer.json")

    opts = ort.SessionOptions()
    opts.inter_op_num_threads = 1
    opts.intra_op_num_threads = min(4, os.cpu_count() or 1)

    session = ort.InferenceSession(model_path, sess_options=opts, providers=_providers)
    tokenizer = Tokenizer.from_file(tok_path)
    tokenizer.enable_truncation(max_length=512)
    tokenizer.enable_padding()

    # Branch picker matches the in-process encoder.
    output_names = [o.name for o in session.get_outputs()]
    has_logits = any("logits" in name.lower() for name in output_names)

    if not has_logits:
        logger.info(
            "SPLADE export is backbone-only (outputs=%s); bolting MLM head",
            output_names,
        )
        # Bolt the head: cache from AutoModelForMaskedLM the first time,
        # then re-use the .npz on subsequent process starts.  The hf_hub
        # cache dir is the right home for it because it lives next to
        # the ONNX file.
        cache_dir = os.path.dirname(model_path)
        cache_path = os.path.join(cache_dir, "splade_mlm_head.npz")
        if os.path.exists(cache_path):
            data = np.load(cache_path)
            _splade_decoder_w = data["w"]
            _splade_decoder_b = data["b"]
        else:
            try:
                from transformers import AutoModelForMaskedLM
            except ImportError:
                raise RuntimeError(
                    "Sidecar SPLADE bolted-head loading requires the optional "
                    "`transformers` package with torch.  Install via "
                    "`pip install transformers[torch]` or provide a full-model "
                    "ONNX export that includes the MLM head.",
                )
            model = AutoModelForMaskedLM.from_pretrained(SPARSE_MODEL)
            decoder = model.cls.predictions.decoder
            w = decoder.weight.detach().cpu().numpy()
            b = (
                decoder.bias.detach().cpu().numpy()
                if decoder.bias is not None
                else np.zeros(w.shape[0], dtype=np.float32)
            )
            np.savez(cache_path, w=w, b=b)
            _splade_decoder_w = w
            _splade_decoder_b = b
            logger.info("SPLADE MLM head materialized + cached at %s", cache_path)

    _splade_session = session
    _splade_tokenizer = tokenizer
    _splade_has_logits_head = has_logits
    logger.info(
        "SPLADE model loaded (branch=%s, providers=%s)",
        "full_model" if has_logits else "bolted_head",
        _providers,
    )


def _splade_from_logits(
    logits: np.ndarray,
    attention_mask: np.ndarray,
    top_k: int,
) -> list[dict[str, float]]:
    """Apply ``log(1 + ReLU(max_pool(logits)))`` mask-aware + top-k prune.

    Returns one ``{token_id_str: weight}`` mapping per input.  Keys are
    cast to strings so the JSON response carries them unchanged for the
    client to ``int()`` on the way in.

    Mirrors ``core/retrieval/sparse.py:_splade_from_logits`` exactly so
    the sidecar fast-path and the local-ONNX fallback are wire-identical.
    """
    # logits: (B, T, V), attention_mask: (B, T)
    mask = attention_mask[..., None].astype("float32")  # (B, T, 1)
    masked = logits * mask + (1.0 - mask) * -1e4
    relu = np.maximum(masked, 0.0)
    pooled = relu.max(axis=1)  # (B, V)
    weights = np.log1p(pooled)  # (B, V)

    out: list[dict[str, float]] = []
    for row in weights:
        nz_idx = np.where(row > 0)[0]
        if nz_idx.size == 0:
            out.append({})
            continue
        nz_vals = row[nz_idx]
        if nz_idx.size > top_k:
            cut = np.argpartition(-nz_vals, top_k)[:top_k]
            nz_idx = nz_idx[cut]
            nz_vals = nz_vals[cut]
        out.append({str(int(tid)): float(w) for tid, w in zip(nz_idx, nz_vals)})
    return out


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------
from fastapi import FastAPI
from pydantic import BaseModel


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    global _providers
    _providers = _detect_providers()
    logger.info("ONNX providers: %s", _providers)
    _load_embedding_model()
    _load_rerank_model()
    logger.info("Sidecar ready on port %d", PORT)
    yield


app = FastAPI(title="Cerid Sidecar", lifespan=lifespan)


class EmbedRequest(BaseModel):
    texts: list[str]
    is_query: bool = False


class EmbedResponse(BaseModel):
    embeddings: list[list[float]]
    latency_ms: float


class RerankRequest(BaseModel):
    query: str
    documents: list[str]


class RerankResponse(BaseModel):
    scores: list[float]
    latency_ms: float


class SparseRequest(BaseModel):
    texts: list[str]
    is_query: bool = False
    """``is_query`` is accepted for API symmetry with /embed but SPLADE-v3
    treats queries and documents identically, so it has no effect."""


class SparseResponse(BaseModel):
    # JSON has no integer keys; client casts back to int.
    vectors: list[dict[str, float]]
    latency_ms: float
    branch: str  # "full_model" or "bolted_head" — observability aid


@app.post("/embed", response_model=EmbedResponse)
def embed(req: EmbedRequest):
    t0 = time.perf_counter()

    texts = req.texts
    if req.is_query and _query_prefix:
        texts = [_query_prefix + t for t in texts]

    encoded = _embed_tokenizer.encode_batch(texts)
    input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
    token_type_ids = np.zeros_like(input_ids)

    outputs = _embed_session.run(None, {
        "input_ids": input_ids,
        "attention_mask": attention_mask,
        "token_type_ids": token_type_ids,
    })

    # Mean pooling
    token_embeddings = outputs[0]
    mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
    summed = (token_embeddings * mask_expanded).sum(axis=1)
    counts = mask_expanded.sum(axis=1).clip(min=1e-9)
    pooled = summed / counts

    # L2 normalize
    norms = np.linalg.norm(pooled, axis=1, keepdims=True).clip(min=1e-12)
    normalized = (pooled / norms).tolist()

    latency_ms = (time.perf_counter() - t0) * 1000
    return EmbedResponse(embeddings=normalized, latency_ms=round(latency_ms, 2))


def _sigmoid(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


@app.post("/rerank", response_model=RerankResponse)
def rerank(req: RerankRequest):
    t0 = time.perf_counter()

    pairs = [(req.query, doc) for doc in req.documents]
    encoded = _rerank_tokenizer.encode_batch(pairs)
    input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)
    token_type_ids = np.array([e.type_ids for e in encoded], dtype=np.int64)

    outputs = _rerank_session.run(None, {
        "input_ids": input_ids,
        "attention_mask": attention_mask,
        "token_type_ids": token_type_ids,
    })

    logits = outputs[0].flatten()
    scores = _sigmoid(logits).tolist()

    latency_ms = (time.perf_counter() - t0) * 1000
    return RerankResponse(scores=scores, latency_ms=round(latency_ms, 2))


@app.post("/encode/sparse", response_model=SparseResponse)
def encode_sparse(req: SparseRequest):
    """SPLADE-v3 learned-sparse encode (Cycle 3.2 / v0.93.3).

    The endpoint is lazy — the model is loaded on the first call.
    Operators who never enable sparse retrieval pay zero cold-start
    cost.  The response wire format intentionally mirrors what the
    in-process encoder produces so callers can swap freely between
    sidecar fast-path and local-ONNX fallback.
    """
    _ = req.is_query  # accepted for symmetry; SPLADE doesn't branch on it
    _load_splade_model()
    t0 = time.perf_counter()

    encoded = _splade_tokenizer.encode_batch(req.texts)
    input_ids = np.array([e.ids for e in encoded], dtype=np.int64)
    attention_mask = np.array([e.attention_mask for e in encoded], dtype=np.int64)

    feeds: dict[str, np.ndarray] = {
        "input_ids": input_ids,
        "attention_mask": attention_mask,
    }
    # token_type_ids is optional — some exports require it, some don't.
    # Tokenizers from BERT-family models emit type_ids, but the SPLADE
    # session may not accept them.  Filter to what the session needs.
    expected = {i.name for i in _splade_session.get_inputs()}
    if "token_type_ids" in expected:
        feeds["token_type_ids"] = np.array(
            [e.type_ids for e in encoded], dtype=np.int64,
        )
    feeds = {k: v for k, v in feeds.items() if k in expected}

    outputs = _splade_session.run(None, feeds)

    if _splade_has_logits_head:
        logits = outputs[0]
    else:
        # outputs[0]: (B, T, H) hidden state — bolt the MLM head.
        hidden = outputs[0]
        logits = hidden @ _splade_decoder_w.T + _splade_decoder_b

    vectors = _splade_from_logits(logits, attention_mask, SPARSE_TOP_K_TERMS)
    latency_ms = (time.perf_counter() - t0) * 1000
    return SparseResponse(
        vectors=vectors,
        latency_ms=round(latency_ms, 2),
        branch="full_model" if _splade_has_logits_head else "bolted_head",
    )


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "embedding_model": EMBEDDING_MODEL,
        "rerank_model": RERANK_MODEL,
        "sparse_model": SPARSE_MODEL,
        "sparse_loaded": _splade_session is not None,
        "sparse_branch": (
            ("full_model" if _splade_has_logits_head else "bolted_head")
            if _splade_session is not None
            else None
        ),
        "providers": _providers,
        "platform": f"{platform.system()} {platform.machine()}",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
