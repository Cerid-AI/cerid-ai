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

logger = logging.getLogger("ai-companion.embeddings")

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
                model_path, sess_options=opts, providers=["CPUExecutionProvider"],
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
        """Embed a batch of texts.  ChromaDB ``EmbeddingFunction`` protocol."""
        if not input:
            return []

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_embedding_fn: OnnxEmbeddingFunction | None = None
_ef_lock = threading.Lock()


def get_embedding_function() -> Any | None:
    """Return the configured embedding function, or ``None`` for server default.

    When ``EMBEDDING_MODEL`` matches the ChromaDB server default
    (``all-MiniLM-L6-v2``), returns ``None`` — the server handles embedding
    transparently.  Otherwise, returns an ``OnnxEmbeddingFunction`` instance.
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
