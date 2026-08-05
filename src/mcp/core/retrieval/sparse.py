# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""
SPLADE++ learned-sparse encoder for hybrid retrieval (Cycle 3.2).

Produces sparse term-weight vectors keyed by BERT-vocab token id. The
formula is ``log(1 + ReLU(max_pool_over_tokens(MLM_logits)))`` applied
to the per-token logits emitted by the sparse MLM model — default
``Qdrant/Splade_PP_en_v1`` (Apache-2.0, ungated; swapped from the
CC-BY-NC-SA gated ``naver/splade-v3`` on 2026-07-10 so commercial
deployments can actually enable the feature). The output is
trimmed to ``SPLADE_TOP_K_TERMS`` non-zero terms per document — the
empirical sweet-spot from the SPLADE-v3 paper (Formal et al. 2024)
that keeps storage in line with BM25 while preserving recall.

Two execution branches, picked once at init by inspecting the loaded
ONNX session:

* **full-model export** — the head is baked into the graph, so the
  session yields the (B, T, V) logits tensor directly. Used when
  ``session.get_outputs()`` exposes a ``logits``-shaped output.
* **backbone-only export** — the session yields the BERT
  ``last_hidden_state``; the SPLADE head is then bolted in numpy
  using the MLM ``decoder.weight`` + ``decoder.bias`` cached as a
  ``.npz`` on first use.

Public callers should treat sparse vectors as opaque ``dict[int, float]``
mappings (token_id → weight) and use the module-level :func:`encode_text`,
:func:`encode_batch`, and :func:`dot` helpers. The :func:`is_available`
guard lets callers cheap-skip when the model isn't installed (default
state in v0.93.x — opt-in once the operator enables it).

The flag ``RETRIEVAL_SPARSE_ENABLED`` ungates the feature; with the
flag off no model load is ever attempted, keeping cold-start cost
free for the default install.
"""

from __future__ import annotations

import logging
import os
import threading
from pathlib import Path
from typing import Any

import sentry_sdk

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.sparse")


# ---------------------------------------------------------------------------
# Module-level env reads
# ---------------------------------------------------------------------------

def _flag_enabled() -> bool:
    """Return True when ``RETRIEVAL_SPARSE_ENABLED`` is truthy.

    Mirrors the ``RETRIEVAL_HYPE_ENABLED`` / ``PARENT_CHILD_ENABLED``
    pattern — module-level env read, no settings.py coupling, so tests
    can monkeypatch the constant directly.
    """
    val = os.getenv("RETRIEVAL_SPARSE_ENABLED", "false").strip().lower()
    return val in {"1", "true", "yes", "on"}


SPARSE_ENABLED = _flag_enabled()
SPLADE_MODEL_PATH = os.getenv("SPLADE_MODEL_PATH", "data/models/splade-pp-en-v1")  # env-capture-allowed: SPLADE model path — startup-only model location
SPLADE_ONNX_FILENAME = os.getenv("SPLADE_ONNX_FILENAME", "model.onnx")  # env-capture-allowed: SPLADE ONNX filename — startup-only model location
SPLADE_TOP_K_TERMS = int(os.getenv("SPLADE_TOP_K_TERMS", "256"))


# ---------------------------------------------------------------------------
# Optional dependencies (graceful degradation)
# ---------------------------------------------------------------------------

_onnxruntime_available = True
_tokenizers_available = True
_numpy_available = True

try:
    import onnxruntime as _ort  # noqa: F401
except ImportError:
    _onnxruntime_available = False

try:
    from tokenizers import Tokenizer  # noqa: F401
except ImportError:
    _tokenizers_available = False

try:
    import numpy as _np  # noqa: F401
except ImportError:
    _numpy_available = False


def _deps_available() -> bool:
    return _onnxruntime_available and _tokenizers_available and _numpy_available


# ---------------------------------------------------------------------------
# Lazy singleton encoder
# ---------------------------------------------------------------------------

class SpladeEncoder:
    """Lazy-init, thread-safe SPLADE-v3 encoder.

    The encoder picks its execution branch once at init by inspecting
    ``session.get_outputs()``:

    * If any output name contains ``logits``, the head is baked into
      the ONNX graph (``_encode_full_model``).
    * Otherwise the session emits ``last_hidden_state`` only and the
      head must be bolted in numpy (``_encode_with_bolted_head``).

    Both branches return ``dict[int, float]`` mappings of
    BERT-vocab-token-id → SPLADE weight, top-k pruned per
    ``SPLADE_TOP_K_TERMS``.
    """

    def __init__(self, model_path: str, onnx_filename: str = "model.onnx") -> None:
        if not _deps_available():
            raise RuntimeError(
                "Sparse retrieval requires onnxruntime + tokenizers + numpy",
            )

        import numpy as np
        import onnxruntime as ort
        from tokenizers import Tokenizer

        self._model_path = Path(model_path)
        self._onnx_path = self._model_path / onnx_filename
        if not self._onnx_path.exists():
            raise FileNotFoundError(
                f"SPLADE ONNX file not found at {self._onnx_path}. "
                "See docs/MODEL_PRELOAD.md for the download step.",
            )

        self._np = np
        # tokenizers (not transformers): the fast tokenizer ships in the
        # runtime image already — this keeps the in-process encode path
        # deployable without the heavyweight transformers dependency.
        tok_path = self._model_path / "tokenizer.json"
        if not tok_path.exists():
            raise FileNotFoundError(
                f"SPLADE tokenizer not found at {tok_path}. "
                "See docs/MODEL_PRELOAD.md for the download step.",
            )
        self._tokenizer = Tokenizer.from_file(str(tok_path))
        self._tokenizer.enable_truncation(max_length=512)
        self._tokenizer.enable_padding()
        sess_opts = ort.SessionOptions()
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        self._session = ort.InferenceSession(
            str(self._onnx_path),
            sess_opts,
            providers=["CPUExecutionProvider"],
        )

        # Pick the execution branch by inspecting outputs.
        output_names = [o.name for o in self._session.get_outputs()]
        self._output_names = output_names
        self._has_logits_head = any("logits" in name.lower() for name in output_names)
        # np.ndarray when populated; Any avoids a hard dependency on
        # numpy types at module import time.
        self._bolted_decoder_w: Any = None
        self._bolted_decoder_b: Any = None

        if not self._has_logits_head:
            self._load_bolted_head()

        self._lock = threading.Lock()
        logger.info(
            "sparse.encoder_ready",
            extra={
                "branch": "full_model" if self._has_logits_head else "bolted_head",
                "outputs": output_names,
                "vocab_size": self._tokenizer.get_vocab_size(),
                "top_k_terms": SPLADE_TOP_K_TERMS,
            },
        )

    # -- bolted-head support -------------------------------------------------

    def _load_bolted_head(self) -> None:
        """Load the MLM ``decoder`` weight + bias from cache or HuggingFace.

        The cache lives at ``{model_path}/mlm_head.npz``; the first run
        materializes it from ``AutoModelForMaskedLM`` so subsequent
        loads are pure-numpy and torch-free. The cache makes the
        bolted-head branch competitive with full-model exports on
        cold start.
        """
        np = self._np
        cache = self._model_path / "mlm_head.npz"
        if cache.exists():
            data = np.load(str(cache))
            self._bolted_decoder_w = data["w"]
            self._bolted_decoder_b = data["b"]
            return

        try:
            from transformers import AutoModelForMaskedLM
        except ImportError as exc:
            raise RuntimeError(
                "Bolted-head SPLADE init requires the optional `torch` extra. "
                "Install `transformers[torch]` or supply a full-model ONNX export.",
            ) from exc

        model = AutoModelForMaskedLM.from_pretrained(str(self._model_path))
        decoder = model.cls.predictions.decoder
        w = decoder.weight.detach().cpu().numpy()
        b = decoder.bias.detach().cpu().numpy() if decoder.bias is not None else np.zeros(w.shape[0], dtype=np.float32)
        np.savez(str(cache), w=w, b=b)
        self._bolted_decoder_w = w
        self._bolted_decoder_b = b

    # -- encode --------------------------------------------------------------

    def encode_batch(self, texts: list[str]) -> list[dict[int, float]]:
        if not texts:
            return []

        with self._lock:
            np = self._np
            encs = self._tokenizer.encode_batch(texts)
            input_ids = np.asarray([e.ids for e in encs], dtype="int64")
            attention_mask = np.asarray(
                [e.attention_mask for e in encs], dtype="int64",
            )
            feeds = {
                "input_ids": input_ids,
                "attention_mask": attention_mask,
                "token_type_ids": np.asarray(
                    [e.type_ids for e in encs], dtype="int64",
                ),
            }

            # Some exports only expect input_ids + attention_mask; filter to
            # what the session actually requires.
            expected = {i.name for i in self._session.get_inputs()}
            feeds = {k: v for k, v in feeds.items() if k in expected}

            outputs = self._session.run(None, feeds)

            if self._has_logits_head:
                # outputs[0]: (B, T, V) logits
                logits = outputs[0]
            else:
                # outputs[0]: (B, T, H) hidden — bolt the head in numpy.
                hidden = outputs[0]
                w = self._bolted_decoder_w
                b = self._bolted_decoder_b
                assert w is not None and b is not None, "bolted head must be loaded"
                logits = hidden @ w.T + b

            return self._splade_from_logits(logits, attention_mask)

    def encode_text(self, text: str) -> dict[int, float]:
        results = self.encode_batch([text])
        return results[0] if results else {}

    def _splade_from_logits(self, logits, attention_mask) -> list[dict[int, float]]:
        """Apply ``log(1 + ReLU(max_pool(logits)))`` mask-aware, top-k prune.

        The mask zeros out padding positions before the max-pool so
        sequences in a padded batch produce identical outputs to
        single-text encoding. This is the standard SPLADE
        per-sequence aggregation.
        """
        np = self._np

        # logits: (B, T, V), attention_mask: (B, T)
        mask = attention_mask[..., None].astype("float32")  # (B, T, 1)
        masked = logits * mask + (1.0 - mask) * -1e4
        relu = np.maximum(masked, 0.0)
        pooled = relu.max(axis=1)  # (B, V)
        weights = np.log1p(pooled)  # (B, V)

        results: list[dict[int, float]] = []
        for row in weights:
            nz_idx = np.where(row > 0)[0]
            if nz_idx.size == 0:
                results.append({})
                continue
            nz_vals = row[nz_idx]
            if nz_idx.size > SPLADE_TOP_K_TERMS:
                # argpartition is O(V); we want the SPLADE_TOP_K_TERMS largest weights.
                cut = np.argpartition(-nz_vals, SPLADE_TOP_K_TERMS)[:SPLADE_TOP_K_TERMS]
                nz_idx = nz_idx[cut]
                nz_vals = nz_vals[cut]
            results.append({int(tid): float(w) for tid, w in zip(nz_idx, nz_vals)})
        return results


# ---------------------------------------------------------------------------
# Module-level singleton + helpers
# ---------------------------------------------------------------------------

_encoder: SpladeEncoder | None = None
_encoder_init_failed = False
_encoder_lock = threading.Lock()


def _get_encoder() -> SpladeEncoder | None:
    """Lazy-init the singleton. Returns ``None`` on any init failure."""
    global _encoder, _encoder_init_failed
    if _encoder is not None or _encoder_init_failed:
        return _encoder
    with _encoder_lock:
        if _encoder is not None or _encoder_init_failed:
            return _encoder
        try:
            _encoder = SpladeEncoder(SPLADE_MODEL_PATH, SPLADE_ONNX_FILENAME)
        except FileNotFoundError as exc:
            # Expected when the model isn't downloaded — quietly disable.
            logger.info("sparse.model_missing", extra={"path": str(exc)})
            _encoder_init_failed = True
        except Exception as exc:  # noqa: BLE001 - one-shot init must not crash callers
            log_swallowed_error("core.retrieval.sparse.encoder_init", exc)
            sentry_sdk.capture_exception()
            _encoder_init_failed = True
    return _encoder


def reset_encoder_for_test() -> None:
    """Clear the singleton — used by unit tests that monkeypatch the model path."""
    global _encoder, _encoder_init_failed
    with _encoder_lock:
        _encoder = None
        _encoder_init_failed = False


def is_available() -> bool:
    """True when sparse retrieval is wired AND deps + model are present.

    The check is staged so the cheap conditions short-circuit:

    1. flag on
    2. python deps installed
    3. encoder constructible (model file exists)

    Callers should treat this as a fast probe — it's safe to call from
    hot paths.
    """
    if not _flag_enabled():
        return False
    if not _deps_available():
        return False
    return _get_encoder() is not None


def encode_path_available() -> bool:
    """True when SOME sparse encode path could run once the flag is enabled.

    Unlike :func:`is_available` this ignores the flag and the model file —
    it answers "would enabling sparse retrieval actually do anything on
    this deployment?" (V1 Task 4.3: the recommendation card must not
    steer operators into a silent no-op). The sidecar downloads the model
    itself; the in-process path documents the download in
    docs/MODEL_PRELOAD.md — deps are the honest minimum either way.
    """
    if _deps_available():
        return True
    try:
        from utils.inference_config import get_inference_config
        cfg = get_inference_config()
    except Exception as exc:  # noqa: BLE001 — probe failure → path unavailable
        log_swallowed_error("core.retrieval.sparse.encode_path_probe", exc)
        return False
    return cfg.provider == "fastembed-sidecar" and bool(cfg.sidecar_available)


def _try_sidecar_encode_batch(texts: list[str]) -> list[dict[int, float]] | None:
    """Route sparse encoding through the cerid sidecar when it's selected.

    The sidecar's ``/encode/sparse`` endpoint (shipped in v0.93.4) gives
    GPU acceleration on Mac ARM64 (CoreML) and Linux (CUDA/ROCm).  On
    Intel Mac + AMD, the sidecar's ONNX runtime falls to CPU, so the
    fast-path is no better than the in-process branch — Quenchforge
    would be the AMD-Mac path, but Quenchforge has no sparse endpoint
    per the upstream gateway routing table.

    Returns ``None`` on any opt-out or failure so the caller falls
    through to the in-process encoder.  Never raises into the query
    or ingest paths.
    """
    try:
        from utils.inference_config import get_inference_config
        cfg = get_inference_config()
    except Exception:  # noqa: BLE001 — module load failure → next provider
        return None
    if cfg.provider != "fastembed-sidecar" or not cfg.sidecar_available:
        return None

    # Sync-bridge to the async sidecar client.  Same ThreadPoolExecutor
    # pattern as core/utils/embeddings.py uses to call async APIs from
    # the sync sparse code paths.
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    def _runner() -> list[dict[int, float]]:
        from utils.inference_sidecar_client import sidecar_encode_sparse
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            return loop.run_until_complete(sidecar_encode_sparse(texts))
        finally:
            loop.close()

    try:
        with ThreadPoolExecutor(max_workers=1) as executor:
            return executor.submit(_runner).result()
    except Exception as exc:  # noqa: BLE001 — fall through to in-process
        log_swallowed_error("core.retrieval.sparse.sidecar_fallthrough", exc)
        return None


def encode_text(text: str) -> dict[int, float]:
    """Encode a single text. Returns ``{}`` when encoder unavailable."""
    results = encode_batch([text])
    return results[0] if results else {}


def encode_batch(texts: list[str]) -> list[dict[int, float]]:
    """Encode a batch with the three-tier dispatch.

    Dispatch order:

    1. **Sidecar fast-path** (v0.93.8) — when the operator runs the
       cerid sidecar AND it's reachable.  Gives GPU acceleration on
       Mac ARM64 (CoreML) and Linux (CUDA/ROCm).
    2. **In-process ONNX** — always available when SPLADE is enabled
       and the model is on disk.  CPU on Intel Mac + AMD.

    Returns ``[]`` when SPLADE is disabled or the encoder is
    unavailable.  Quenchforge intentionally not in this chain — it has
    no sparse endpoint per the upstream gateway routing table.
    """
    if not texts:
        return []

    # ── Sidecar fast-path (v0.93.8) ──────────────────────────────────
    sidecar_result = _try_sidecar_encode_batch(texts)
    if sidecar_result is not None:
        return sidecar_result

    # ── In-process ONNX fallback ─────────────────────────────────────
    enc = _get_encoder()
    if enc is None:
        return []
    return enc.encode_batch(texts)


def dot(a: dict[int, float], b: dict[int, float]) -> float:
    """Sparse dot product. Iterates the smaller dict for O(min(|a|,|b|))."""
    if not a or not b:
        return 0.0
    if len(a) > len(b):
        a, b = b, a
    return sum(weight * b.get(tid, 0.0) for tid, weight in a.items())
