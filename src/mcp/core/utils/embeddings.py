# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Client-side ONNX embedding function for ChromaDB.

When ``EMBEDDING_MODEL`` differs from the ChromaDB server default
(``all-MiniLM-L6-v2``), this module provides a drop-in replacement that
runs inference locally via ONNX Runtime.  All dependencies (onnxruntime,
tokenizers, numpy, huggingface-hub) are already present via chromadb.

The ``deps.py`` ``_EmbeddingAwareClient`` wrapper automatically injects
the embedding function into every ``get_or_create_collection`` and
``get_collection`` call — no changes needed at call sites.
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
from core.utils.onnx_providers import resolve_providers

logger = logging.getLogger("ai-companion.embeddings")


# ---------------------------------------------------------------------------
# Distance → relevance conversion
# ---------------------------------------------------------------------------


def l2_distance_to_relevance(distance: float) -> float:
    """Convert ChromaDB L2 distance to a [0, 1] relevance score.

    ChromaDB defaults to L2 (Euclidean) distance.  For unit-norm embeddings
    (which Snowflake arctic-embed and most modern models produce), the
    relationship is:

        L2² = 2 · (1 − cosine_similarity)

    So: ``cosine_sim = 1 − distance² / 2``.

    The naive ``1 − distance`` formula clips to 0 whenever distance > 1
    (cosine_sim < 0.5), silently dropping moderately relevant results.
    """
    return max(0.0, min(1.0, 1.0 - distance * distance / 2.0))

# ChromaDB's built-in default — when this is the configured model we skip
# client-side embedding and let the server handle it (zero-migration path).
_SERVER_DEFAULT_MODEL = "all-MiniLM-L6-v2"

# Sentinel used by sentence-transformers models (Snowflake Arctic, etc.)
_QUERY_PREFIX_MAP: dict[str, str] = {
    "Snowflake/snowflake-arctic-embed-m-v1.5": "Represent this sentence for searching relevant passages: ",
}


# ---------------------------------------------------------------------------
# ONNX Embedding Function (implements chromadb.EmbeddingFunction protocol)
# ---------------------------------------------------------------------------

class OnnxEmbeddingFunction:
    """Compute embeddings locally via ONNX Runtime.

    Thread-safe lazy loading — the model is downloaded and loaded on the
    first ``__call__``.
    """

    def __init__(
        self,
        model_id: str,
        onnx_filename: str = "onnx/model.onnx",
        cache_dir: str | None = None,
        dimensions: int | None = None,
    ) -> None:
        self._model_id = model_id
        self._onnx_filename = onnx_filename
        self._cache_dir = cache_dir
        self._dimensions = dimensions  # Matryoshka: truncate to this dim
        self._session: ort.InferenceSession | None = None
        self._tokenizer: Tokenizer | None = None
        self._lock = threading.Lock()
        self._query_prefix = _QUERY_PREFIX_MAP.get(model_id, "")

    # -- lazy loading -------------------------------------------------------

    def _load(self) -> tuple[ort.InferenceSession, Tokenizer]:
        if self._session is not None and self._tokenizer is not None:
            return self._session, self._tokenizer

        with self._lock:
            if self._session is not None and self._tokenizer is not None:
                return self._session, self._tokenizer

            logger.info("Downloading embedding model: %s/%s", self._model_id, self._onnx_filename)
            model_path = hf_hub_download(
                repo_id=self._model_id,
                filename=self._onnx_filename,
                cache_dir=self._cache_dir,
            )
            tok_path = hf_hub_download(
                repo_id=self._model_id,
                filename="tokenizer.json",
                cache_dir=self._cache_dir,
            )

            opts = ort.SessionOptions()
            opts.inter_op_num_threads = 1
            opts.intra_op_num_threads = min(4, os.cpu_count() or 1)

            self._session = ort.InferenceSession(
                model_path,
                sess_options=opts,
                providers=resolve_providers(config.ONNX_EXECUTION_PROVIDERS),
            )
            self._tokenizer = Tokenizer.from_file(tok_path)
            self._tokenizer.enable_truncation(max_length=512)
            self._tokenizer.enable_padding()

            logger.info("Embedding model ready: %s (%d dims)", self._model_id, self._output_dim())
            return self._session, self._tokenizer

    def _output_dim(self) -> int:
        """Infer output dimension from the model."""
        assert self._session is not None
        out_shape = self._session.get_outputs()[0].shape
        # Shape is typically [batch, seq_len, hidden_dim]
        return out_shape[-1] if isinstance(out_shape[-1], int) else 0

    # -- embedding ----------------------------------------------------------

    def __call__(self, input: list[str]) -> list[list[float]]:  # noqa: A002
        """Embed a batch of texts.  ChromaDB ``EmbeddingFunction`` protocol.

        Workstream E Phase E.6.4: when the inference auto-detector says
        ``provider == "fastembed-sidecar"`` and the sidecar is reachable,
        route through it for GPU acceleration. On any sidecar failure
        (timeout, bad response, dim mismatch) silently fall through to
        the local ONNX path so the call still produces an embedding —
        operators don't lose ingest because the GPU sidecar restarted.
        """
        if not input:
            return []

        # Sidecar fast-path — only when explicitly preferred by inference
        # detection AND reachable. Sync-bridge to async via the proven
        # ThreadPoolExecutor pattern (mirrors core.utils.contextual and
        # app.queue.tasks; chromadb's EmbeddingFunction.__call__ is sync).
        sidecar_result = self._maybe_embed_via_sidecar(input)
        if sidecar_result is not None:
            return sidecar_result

        session, tokenizer = self._load()
        encodings = tokenizer.encode_batch(input)

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

        outputs = session.run(None, feeds)
        hidden = outputs[0]  # (batch, seq_len, hidden_dim)

        # Mean pooling over sequence length, weighted by attention mask
        if hidden.ndim == 3:
            mask_expanded = attention_mask[:, :, np.newaxis].astype(np.float32)
            summed = (hidden * mask_expanded).sum(axis=1)
            counts = mask_expanded.sum(axis=1).clip(min=1e-9)
            embeddings = summed / counts
        else:
            # Some models output (batch, hidden_dim) directly
            embeddings = hidden

        # L2 normalize
        norms = np.linalg.norm(embeddings, axis=1, keepdims=True).clip(min=1e-12)
        embeddings = embeddings / norms

        # Matryoshka: truncate to requested dimensions
        if self._dimensions and embeddings.shape[1] > self._dimensions:
            embeddings = embeddings[:, : self._dimensions]
            # Re-normalize after truncation
            norms = np.linalg.norm(embeddings, axis=1, keepdims=True).clip(min=1e-12)
            embeddings = embeddings / norms

        return [embeddings[i] for i in range(embeddings.shape[0])]

    def embed_query(self, query: str) -> list[float]:
        """Embed a single query, applying query prefix if configured."""
        text = self._query_prefix + query if self._query_prefix else query
        return self.__call__([text])[0]

    # -- sidecar fast-path (Workstream E Phase E.6.4) ----------------------

    def _maybe_embed_via_sidecar(self, texts: list[str]) -> list[list[float]] | None:
        """If the auto-detected provider is the sidecar AND it's reachable,
        embed via HTTP and return the result. Returns ``None`` to signal
        "fall through to local ONNX" — never raises.
        """
        try:
            from utils.inference_config import get_inference_config
        except Exception:  # noqa: BLE001 — module load failure → local ONNX
            return None
        try:
            cfg = get_inference_config()
        except Exception:  # noqa: BLE001
            return None
        if cfg.provider != "fastembed-sidecar" or not cfg.sidecar_available:
            return None

        # Sync-bridge to the async sidecar client. ThreadPoolExecutor +
        # fresh event loop in a worker thread is the same pattern
        # core.utils.contextual + app.queue.tasks use to call async APIs
        # from sync ChromaDB code paths without polluting the caller's
        # asyncio state.
        import asyncio
        from concurrent.futures import ThreadPoolExecutor

        def _runner() -> list[list[float]]:
            from utils.inference_sidecar_client import sidecar_embed
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                return loop.run_until_complete(sidecar_embed(texts))
            finally:
                loop.close()

        try:
            with ThreadPoolExecutor(max_workers=1) as executor:
                return executor.submit(_runner).result()
        except Exception as exc:  # noqa: BLE001 — observability boundary
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error(
                "core.utils.embeddings.sidecar_fallthrough", exc,
            )
            return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_embedding_fn: OnnxEmbeddingFunction | None = None
_ef_lock = threading.Lock()


def is_loading() -> bool:
    """True when the embedder model is currently downloading on another thread.

    Workstream E Phase E.6.6 — pairs with :func:`reranker.is_loading` so
    the GUI's "first-query model download in progress" notification can
    distinguish in-flight downloads from "not yet cached, idle" states.
    Returns False when the singleton hasn't been instantiated yet (no
    download has started); True when the singleton's per-instance lock
    is held with the session not yet ready (worker is inside the
    hf_hub_download + ONNX init block).
    """
    if _embedding_fn is None:
        return False
    return _embedding_fn._lock.locked() and _embedding_fn._session is None

# Server default output dim (all-MiniLM-L6-v2 → 384). Used by get_embedding_dim()
# when EMBEDDING_MODEL == _SERVER_DEFAULT_MODEL (server-side embedding path).
_SERVER_DEFAULT_DIM = 384


def get_embedding_function() -> Any | None:
    """Return the configured embedding function, or ``None`` for server default.

    When ``EMBEDDING_MODEL`` matches the ChromaDB server default
    (``all-MiniLM-L6-v2``), returns ``None`` — the server handles embedding
    transparently.  Otherwise, returns an ``OnnxEmbeddingFunction`` instance.

    This is the ONLY legitimate way to obtain an embedder instance anywhere
    in the codebase.  It is keyed on the ``EMBEDDING_MODEL`` env var and
    guarantees a single shared instance per process.  Do NOT instantiate
    ``OnnxEmbeddingFunction`` directly — collections get dim-locked on
    first use, and diverging entry points cause the dim-mismatch bug that
    crashes first-ingest on fresh installs.
    """
    model = config.EMBEDDING_MODEL
    if model == _SERVER_DEFAULT_MODEL:
        return None

    global _embedding_fn
    if _embedding_fn is not None:
        return _embedding_fn

    with _ef_lock:
        if _embedding_fn is not None:
            return _embedding_fn

        onnx_file = config.EMBEDDING_ONNX_FILENAME
        dims = config.EMBEDDING_DIMENSIONS if config.EMBEDDING_DIMENSIONS > 0 else None
        cache = config.EMBEDDING_MODEL_CACHE_DIR or None

        _embedding_fn = OnnxEmbeddingFunction(
            model_id=model,
            onnx_filename=onnx_file,
            cache_dir=cache,
            dimensions=dims,
        )
        return _embedding_fn


# Canonical alias — new code should call get_embedder() for clarity.
def get_embedder() -> Any | None:
    """Alias for :func:`get_embedding_function` — the singleton embedder accessor.

    Returns ``None`` when the ChromaDB server default model is configured
    (server-side embedding), otherwise an ``OnnxEmbeddingFunction`` singleton.
    """
    return get_embedding_function()


def get_embedding_dim() -> int:
    """Return the output dimensionality of the configured embedder.

    - Matryoshka models with ``EMBEDDING_DIMENSIONS > 0`` return that value.
    - Server-default model returns 384 (all-MiniLM-L6-v2).
    - Client-side ONNX models return the loaded model's output dim (forces
      a model load on first call if it has not happened yet).

    Raises ``RuntimeError`` if the dim cannot be inferred (e.g. the ONNX
    output shape has a symbolic last dimension).
    """
    # Matryoshka truncation wins — it's the final dim ChromaDB will see.
    if config.EMBEDDING_DIMENSIONS and config.EMBEDDING_DIMENSIONS > 0:
        return int(config.EMBEDDING_DIMENSIONS)

    if config.EMBEDDING_MODEL == _SERVER_DEFAULT_MODEL:
        return _SERVER_DEFAULT_DIM

    ef = get_embedding_function()
    if ef is None:
        return _SERVER_DEFAULT_DIM

    # Force model load so _output_dim() has a session to inspect.
    ef._load()
    dim = ef._output_dim()
    if not isinstance(dim, int) or dim <= 0:
        raise RuntimeError(
            f"Could not infer embedding dim for model {config.EMBEDDING_MODEL!r} "
            f"(got: {dim!r}). Set EMBEDDING_DIMENSIONS explicitly."
        )
    return dim


def _reset_singleton_for_testing() -> None:
    """Reset the module-level singleton. Tests only — never call in production."""
    global _embedding_fn
    with _ef_lock:
        _embedding_fn = None
