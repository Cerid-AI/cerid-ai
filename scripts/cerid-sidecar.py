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
    POST /embed   — Embed texts, returns {"embeddings": [[float, ...]]}
    POST /rerank  — Rerank query+documents, returns {"scores": [float, ...]}
    GET  /health  — Health check with model info
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


@app.get("/health")
def health():
    return {
        "status": "healthy",
        "embedding_model": EMBEDDING_MODEL,
        "rerank_model": RERANK_MODEL,
        "providers": _providers,
        "platform": f"{platform.system()} {platform.machine()}",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=PORT)
