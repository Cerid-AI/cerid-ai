# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
from tokenizers import Tokenizer

import config
from core.utils.embedding_cache import get_embedding_cache
from core.utils.hf_cache import resolve_hf_file
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

# Sentinel used by sentence-transformers models (Snowflake Arctic, etc.).
# Keys include both the canonical ONNX-loadable id used as `self._model_id`
# AND any Quenchforge-served model name an operator may set via
# `QUENCHFORGE_EMBED_MODEL`. The active-prefix lookup checks the actual
# routing target so a query sent to nomic-embed never picks up Snowflake's
# prefix (verified 2026-05-17: cross-model prefix routing collapses
# LongMemEval gpu-embed-only recall to 0/60 — doc embeds skip prefix,
# query embeds prepend Snowflake's, vector spaces diverge).
_QUERY_PREFIX_MAP: dict[str, str] = {
    "Snowflake/snowflake-arctic-embed-m-v1.5": "Represent this sentence for searching relevant passages: ",
    # Nomic-embed-text-v1.5 ships *symmetric* asymmetric prefixes:
    # `search_document: ` for ingest, `search_query: ` for queries. Adding
    # only the query side here would create a new asymmetry (queries
    # prefixed, docs raw) that is just as broken as Snowflake-prefix +
    # nomic-routing was. The right fix is a doc-side prefix path in
    # `__call__` plus the query-side entry here, AND ensuring both fire
    # only when the wrapper is routing to nomic. That's a follow-up;
    # for now nomic stays absent from this map so both sides are
    # prefix-less and symmetric — measured-weak (cos_sim 0.34 on
    # "cat/mat" vs ideal ~0.65) but consistent.
}


# Pinned artifact revisions — HuggingFace commit SHAs, not branch names.
# ``main`` moves, and these weights decide every vector in the index: a repo
# owner retagging silently changes retrieval semantics on the next cold start.
# EMBEDDING_MODEL_VERSION cannot notice, because it tracks a config string
# rather than the artifact. The SHA below pins what the in-process ONNX leg
# loads; bump it deliberately, and re-embed when you do. It says nothing about
# an index built through Quenchforge (EMBEDDINGS_PROVIDER=quenchforge serves
# QUENCHFORGE_EMBED_MODEL instead — a different, orthogonal vector space at the
# same width). What verifies that queries and stored chunks share one space is
# the boot-time probe in app/startup/invariants.py, not this pin.
_PINNED_REVISIONS: dict[str, str] = {
    "Snowflake/snowflake-arctic-embed-m-v1.5":
        "e58a8f756156a1293d763f17e3aae643474e9b8a",  # pragma: allowlist secret
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
        self._revision = _PINNED_REVISIONS.get(model_id)
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

            if self._revision is None:
                logger.warning(
                    "Embedding model %s is not revision-pinned — whatever the "
                    "repo serves now decides every vector in the index. Add its "
                    "commit SHA to _PINNED_REVISIONS.",
                    self._model_id,
                )
            model_path = resolve_hf_file(
                self._model_id, self._onnx_filename, self._cache_dir,
                logger=logger, revision=self._revision,
            )
            tok_path = resolve_hf_file(
                self._model_id, "tokenizer.json", self._cache_dir,
                logger=logger, revision=self._revision,
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

        v0.96.1: a process-wide LRU cache wraps the routing chain so
        identical texts within a session don't re-embed across the
        network. Namespaced by the active provider+model so a config
        flip cannot silently mix vector spaces.
        """
        if not input:
            return []

        namespace = self._active_namespace()
        cache = get_embedding_cache()
        cached: list[np.ndarray | None] = [cache.get(namespace, t) for t in input]
        miss_indices = [i for i, v in enumerate(cached) if v is None]
        if not miss_indices:
            return [c for c in cached if c is not None]  # type: ignore[misc]

        miss_texts = [input[i] for i in miss_indices]
        # The namespace the batch is WRITTEN under is the one that actually
        # served, not the one the operator configured. A quenchforge blip drops
        # the chain onto a different model; caching those vectors under the
        # quenchforge key mixes vector spaces inside one namespace — and with
        # CERID_EMBED_CACHE_PATH set the poisoned rows outlive the process.
        miss_vectors, served_namespace = self._embed_uncached(miss_texts)
        if len(miss_vectors) != len(miss_indices):
            # Backend returned wrong cardinality — refuse to cache and
            # fall through to its result so ChromaDB sees a clean error
            # if the dim is also wrong.
            return miss_vectors if not cached or all(c is None for c in cached) else \
                self._stitch_uncached_only(input, cached, miss_indices, miss_vectors)  # type: ignore[return-value]

        result: list[np.ndarray] = []
        miss_iter = iter(zip(miss_indices, miss_vectors, strict=True))
        next_miss = next(miss_iter, None)
        for i, v in enumerate(cached):
            if v is not None:
                result.append(v)
                continue
            assert next_miss is not None and next_miss[0] == i
            vec = np.asarray(next_miss[1], dtype=np.float32)
            cache.put(served_namespace, input[i], vec)
            result.append(vec)
            next_miss = next(miss_iter, None)
        return result  # type: ignore[return-value]

    def _local_namespace(self) -> str:
        """Cache key for the sidecar / in-process ONNX legs.

        Both run the same model identity, so they share one vector space.
        """
        return f"onnx:{self._model_id}"

    def _quenchforge_namespace(self) -> str | None:
        """``qf:<model>`` when Quenchforge is the selected embedder, else ``None``."""
        try:
            from utils.quenchforge_client import is_embeddings_provider_quenchforge
            if is_embeddings_provider_quenchforge():
                qf_model = os.environ.get("QUENCHFORGE_EMBED_MODEL", "")
                if qf_model:
                    return f"qf:{qf_model}"
        except Exception as exc:  # noqa: BLE001 — namespace probe is best-effort
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error(
                "core.utils.embeddings.namespace_probe", exc,
            )
        return None

    def _active_namespace(self) -> str:
        """The namespace a cache LOOKUP should use — the configured intent.

        Writes use the namespace returned by :meth:`_embed_uncached`, which
        names the leg that actually produced the vectors.
        """
        return self._quenchforge_namespace() or self._local_namespace()

    @staticmethod
    def _stitch_uncached_only(
        input_texts: list[str],
        cached: list[np.ndarray | None],
        miss_indices: list[int],
        miss_vectors: list[np.ndarray],
    ) -> list[np.ndarray]:
        # Degraded path: backend returned fewer vectors than expected.
        # Don't cache, don't assert — return what we have so the caller
        # surfaces the dim-mismatch / count-mismatch downstream.
        _ = input_texts, cached, miss_indices
        return [np.asarray(v, dtype=np.float32) for v in miss_vectors]

    def _embed_uncached(  # noqa: A002
        self, input: list[str],
    ) -> tuple[list[np.ndarray], str]:
        """Run the existing backend chain on a cache-miss subset.

        Returns ``(vectors, namespace)`` where ``namespace`` names the leg that
        actually produced the vectors — the caller keys the cache on it so a
        silent fallback cannot write into another provider's vector space.
        """
        # Quenchforge GPU fast-path (v0.93.8) — opt-in via
        # EMBEDDINGS_PROVIDER=quenchforge.  Targets Intel Mac + AMD
        # where ONNX runtime has no GPU provider and the sidecar path
        # is CPU-only.  Falls through to the sidecar / local-ONNX
        # chain on any failure.
        quenchforge_result = self._maybe_embed_via_quenchforge(input)
        if quenchforge_result is not None:
            # ChromaDB's convert_np_embeddings_to_list calls .tolist() on
            # each row, so the row MUST be a numpy ndarray, not a plain
            # Python list. The local-ONNX path below already returns
            # ndarray rows (line ~207); match that shape here so the
            # fast-paths are drop-in replacements.  The declared return
            # type predates this fix; chromadb's EmbeddingFunction.__call__
            # actually expects List[ndarray], which is what the local-ONNX
            # path silently delivers (mypy couldn't catch it because
            # ndarray indexing returns Any).
            qf_ns = self._quenchforge_namespace() or self._local_namespace()
            return (
                [np.asarray(row, dtype=np.float32) for row in quenchforge_result],
                qf_ns,
            )

        # Sidecar fast-path — only when explicitly preferred by inference
        # detection AND reachable. Sync-bridge to async via the proven
        # ThreadPoolExecutor pattern (mirrors core.utils.contextual;
        # chromadb's EmbeddingFunction.__call__ is sync).
        sidecar_result = self._maybe_embed_via_sidecar(input)
        if sidecar_result is not None:
            # Same ndarray-row requirement as the Quenchforge branch above.
            return (
                [np.asarray(row, dtype=np.float32) for row in sidecar_result],
                self._local_namespace(),
            )

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

        return (
            [embeddings[i] for i in range(embeddings.shape[0])],
            self._local_namespace(),
        )

    def embed_query(self, input):  # noqa: A002 — chromadb protocol forces this kwarg name
        """Embed query text(s), applying the query prefix if configured.

        Polymorphic on the input shape because the OnnxEmbeddingFunction
        instance is shared across two consumer protocols that disagree
        on the embed_query signature:

          - chromadb 1.x's ``EmbeddingFunction.embed_query(input: list[str])
            -> list[list[float]]`` — batch, used internally when
            ``Collection.query(query_texts=...)`` is called.
          - neo4j-graphrag's ``Embedder.embed_query(text: str) -> list[float]``
            — single, used by ``ChromaNeo4jRetriever`` when callers pass
            ``query_text`` instead of ``query_vector``.

        The single-vs-batch decision is made on the runtime input type;
        callers in either protocol get back the shape they expect.

        The query prefix is derived from the **active routing target**, not
        the wrapper's static ``_model_id``. When the operator flips
        ``EMBEDDINGS_PROVIDER=quenchforge`` and the daemon serves a
        different model than the wrapper was configured for (e.g. nomic
        on the GPU side vs Snowflake-Arctic on the local-ONNX fallback),
        the prefix has to match the model that will actually compute the
        embedding — otherwise queries land in a different vector space
        than documents and retrieval collapses (verified 2026-05-17:
        LongMemEval gpu-embed-only went from recall=0/60 with the wrong
        prefix to a measurable number once the prefix tracked routing).
        Document-side embedding via ``__call__`` gets no prefix on any
        path; the prefix asymmetry is the documented sentence-transformer
        contract.
        """
        prefix = self._active_query_prefix()
        if isinstance(input, str):
            text = prefix + input if prefix else input
            return self.__call__([text])[0]
        if not input:
            return []
        prefixed = (
            [prefix + t for t in input]
            if prefix else list(input)
        )
        return self.__call__(prefixed)

    def _active_query_prefix(self) -> str:
        """Return the right query prefix for the model that will serve.

        Mirrors ``_active_namespace`` — the routing decision the cache
        and prefix logic both depend on lives in one place at the
        active provider. Same trade-off: best-effort fall through to
        the wrapper's configured ``self._model_id`` prefix when the
        provider probe fails (the env var probe is the only failure
        mode and it's swallowed for observability).
        """
        try:
            from utils.quenchforge_client import is_embeddings_provider_quenchforge
            if is_embeddings_provider_quenchforge():
                qf_model = os.environ.get("QUENCHFORGE_EMBED_MODEL", "")
                if qf_model:
                    return _QUERY_PREFIX_MAP.get(qf_model, "")
        except Exception as exc:  # noqa: BLE001 — best-effort probe
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error(
                "core.utils.embeddings.active_query_prefix_probe", exc,
            )
        return self._query_prefix

    # -- chromadb 1.x EmbeddingFunction contract (forward-compat) -----------
    #
    # chromadb 1.1.13+ persists the embedding function alongside the
    # collection so subsequent `get_collection` calls auto-resolve it.
    # Persistence relies on three methods declared by the chromadb
    # `EmbeddingFunction` Protocol: `name()`, `get_config()`, and
    # `build_from_config()`. They are no-ops on chromadb 0.5.x (which
    # only consults `__call__`) and become live the moment the client
    # pin lifts to >=1,<2 — added now so the actual bump becomes a
    # one-line requirements-and-lock change.

    @staticmethod
    def name() -> str:
        """Stable identifier persisted with the collection on chromadb 1.x."""
        return "cerid-onnx"

    def get_config(self) -> dict[str, Any]:
        """Return the constructor args needed to rebuild this EF.

        Mirrors the chromadb 1.x Python `EmbeddingFunction` contract; the
        returned dict round-trips through `build_from_config()`.
        """
        return {
            "model_id": self._model_id,
            "onnx_filename": self._onnx_filename,
            "cache_dir": self._cache_dir,
            "dimensions": self._dimensions,
        }

    @staticmethod
    def build_from_config(config: dict[str, Any]) -> "OnnxEmbeddingFunction":
        """Reconstruct an OnnxEmbeddingFunction from a `get_config()` dict.

        Tolerant of missing keys so a future config-schema evolution does
        not break collection load — unknown keys are silently ignored, the
        defaults from `__init__` apply when fields are absent.
        """
        return OnnxEmbeddingFunction(
            model_id=config["model_id"],
            onnx_filename=config.get("onnx_filename", "onnx/model.onnx"),
            cache_dir=config.get("cache_dir"),
            dimensions=config.get("dimensions"),
        )

    # -- Quenchforge GPU fast-path (v0.93.8) -------------------------------

    def _maybe_embed_via_quenchforge(
        self, texts: list[str],
    ) -> list[list[float]] | None:
        """Route the embedder through Quenchforge when EMBEDDINGS_PROVIDER
        is set and the daemon is configured.

        Targets Intel Mac + AMD discrete GPU — the only hardware where
        ONNX runtime can't reach the GPU (no AMD-Mac execution provider;
        ROCm is Linux-only).  Returns ``None`` to signal "fall through
        to the next provider in the chain" on any failure; never raises
        because chromadb's EmbeddingFunction contract is sync + must
        always make progress.

        The operator opts in by setting EMBEDDINGS_PROVIDER=quenchforge
        AND QUENCHFORGE_EMBED_MODEL (the loaded model name).  Dimension
        is validated on the response so a misconfigured model can't
        silently corrupt ChromaDB.
        """
        try:
            from utils.quenchforge_client import (
                is_embeddings_provider_quenchforge,
            )
        except Exception:  # noqa: BLE001 — module load failure → next provider
            return None
        if not is_embeddings_provider_quenchforge():
            return None
        qf_model = os.environ.get("QUENCHFORGE_EMBED_MODEL", "")

        # Sync-bridge via the persistent event-loop thread so the
        # cached httpx.AsyncClient inside quenchforge_client survives
        # across calls. Previously each call spawned a fresh loop, which
        # voided the client cache and added ~1s of TCP-rebuild overhead
        # per call — verified by the 14h vs 75min projected runtime
        # delta on the v0.96.0 LongMemEval canonical baseline.
        from core.utils import inference_health
        try:
            from core.utils.async_bridge import run_async
            from utils.quenchforge_client import quenchforge_embed
            result = run_async(quenchforge_embed(texts), timeout=60.0)
            inference_health.record_success("embed", provider="quenchforge")
            return result
        except Exception as exc:  # noqa: BLE001 — observability boundary
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error(
                "core.utils.embeddings.quenchforge_fallthrough", exc,
            )
            inference_health.record_fallback(
                "embed", configured="quenchforge", served_by="onnx", detail=str(exc),
            )
            # Falling through means the sidecar / local ONNX leg serves this
            # batch. Only namespaces are per-provider, and the ONLY namespaced
            # store is the in-process LRU — nothing namespaces the ChromaDB
            # collection, and both nomic and arctic are 768-dim so the dimension
            # guard cannot tell them apart. Serving a different vector space into
            # the same collection is silent corruption, not degradation, so fall
            # through ONLY when the fallback leg runs the same model identity.
            if not _same_vector_space(qf_model, config.EMBEDDING_MODEL):
                raise RuntimeError(
                    f"Quenchforge embed failed ({exc}) and the fallback leg runs "
                    f"{config.EMBEDDING_MODEL!r}, a different vector space from "
                    f"{qf_model!r}. Refusing to serve — same dimensionality is not "
                    f"the same space, and nothing namespaces the collection. "
                    f"Restore the daemon, or set EMBEDDING_MODEL to the model "
                    f"QUENCHFORGE_EMBED_MODEL names so the legs agree.",
                ) from exc
            return None

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

        # Sync-bridge via the persistent event-loop thread so the
        # cached sidecar HTTP client survives across calls. Same
        # perf-critical fix as the quenchforge branch above.
        try:
            from core.utils.async_bridge import run_async
            from utils.inference_sidecar_client import sidecar_embed
            return run_async(sidecar_embed(texts), timeout=60.0)
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


def _normalise_model_id(name: str) -> str:
    """Bare, comparable model identity — drops the HF org prefix and case."""
    return name.strip().rsplit("/", 1)[-1].lower()


def _same_vector_space(a: str, b: str) -> bool:
    """True when two model names denote the same embedding vector space."""
    return bool(a) and bool(b) and _normalise_model_id(a) == _normalise_model_id(b)


def serving_embedding_model() -> str:
    """The model identity that will actually produce vectors right now.

    ``config.EMBEDDING_MODEL`` names the in-process ONNX pin, which is NOT the
    producer when ``EMBEDDINGS_PROVIDER=quenchforge`` — the daemon's
    ``QUENCHFORGE_EMBED_MODEL`` is. Every provenance surface must read this
    rather than the local pin.
    """
    try:
        from utils.quenchforge_client import is_embeddings_provider_quenchforge
        if is_embeddings_provider_quenchforge():
            qf_model = os.environ.get("QUENCHFORGE_EMBED_MODEL", "")
            if qf_model:
                return qf_model
    except Exception as exc:  # noqa: BLE001 — provenance probe is best-effort
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error("core.utils.embeddings.serving_model_probe", exc)
    return config.EMBEDDING_MODEL


def embedding_stamp(domain: str) -> dict[str, str]:
    """Return the ``{embedding_model, embedding_model_version}`` stamp for
    a chunk about to be written to ``domain``.

    Single source of truth for chunk-metadata version stamping (Phase 4.4)
    — every chunk-write path (ingest, re-embed) merges this into its
    per-chunk metadata so a future embedding-model swap can identify which
    chunks were computed under which model without inferring it from
    vector geometry. ``embedding_model`` is the model that will actually serve
    (:func:`serving_embedding_model` — the Quenchforge model when the daemon is
    the selected embedder, otherwise the local ONNX pin);
    ``embedding_model_version`` resolves
    through the per-domain override so a staged migration
    (``EMBEDDING_MODEL_VERSIONS_PER_DOMAIN``) stamps only the domain being
    migrated.

    Chunks written before this stamp existed simply lack these two keys —
    that is treated as "unversioned legacy data" everywhere it's read, not
    filtered out or specially privileged. Absence is expected, not an error.
    """
    return {
        "embedding_model": serving_embedding_model(),
        "embedding_model_version": config.embedding_version_for_domain(domain),
    }


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
