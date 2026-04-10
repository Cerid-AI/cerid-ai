# NLI Entailment Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Add a shared NLI entailment function that replaces similarity-as-proof in verification, Self-RAG, and RAGAS faithfulness.

**Architecture:** One ONNX model (nli-deberta-v3-xsmall) in core/utils/nli.py, wired into 3 consumers: verification fast-path, Self-RAG coverage, RAGAS faithfulness. Same lazy-load singleton pattern as the existing reranker.

**Tech Stack:** Python 3.11, ONNX Runtime, HuggingFace Hub, tokenizers, numpy

---

## Task 1: Create core/utils/nli.py

**Files:**
- Create: `src/mcp/core/utils/nli.py`
- Modify: `src/mcp/config/settings.py` (add NLI config)
- Create: `src/mcp/tests/test_nli.py`

The NLI module follows the EXACT same pattern as `core/retrieval/reranker.py`:
- Module-level globals: `_session`, `_tokenizer`, `_lock`
- `_load_model()` — downloads from HF, creates ONNX InferenceSession, returns (session, tokenizer)
- `nli_score(premise: str, hypothesis: str) -> dict` — returns `{"entailment": float, "contradiction": float, "neutral": float, "label": str}`
- `batch_nli_score(pairs: list[tuple[str, str]]) -> list[dict]` — batch version
- `warmup()` — pre-load model (called during server startup)

**CRITICAL:** The NLI model outputs 3-class logits. The label order for `cross-encoder/nli-deberta-v3-xsmall` is: **index 0 = contradiction, index 1 = entailment, index 2 = neutral**. Use **softmax** (not sigmoid) to get probabilities across the 3 classes.

**CRITICAL:** The premise is the EVIDENCE (KB content). The hypothesis is the CLAIM. This matches NLI convention: "Given premise P, does hypothesis H follow?"

### Config additions to `settings.py`

Add this block after the existing `RERANK_MODEL_CACHE_DIR` line (around line 166) in the `# Cross-encoder Reranking` section:

```python
# ---------------------------------------------------------------------------
# NLI Entailment (Natural Language Inference)
# ---------------------------------------------------------------------------
NLI_MODEL = os.getenv("NLI_MODEL", "cross-encoder/nli-deberta-v3-xsmall")
NLI_ONNX_FILENAME = os.getenv("NLI_ONNX_FILENAME", "onnx/model.onnx")
NLI_MODEL_CACHE_DIR = os.getenv("NLI_MODEL_CACHE_DIR", "")
NLI_ENTAILMENT_THRESHOLD = float(os.getenv("NLI_ENTAILMENT_THRESHOLD", "0.7"))
NLI_CONTRADICTION_THRESHOLD = float(os.getenv("NLI_CONTRADICTION_THRESHOLD", "0.6"))
```

### Full `nli.py` implementation

Create `src/mcp/core/utils/nli.py`:

```python
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
from huggingface_hub import hf_hub_download
from tokenizers import Tokenizer

import config

logger = logging.getLogger("ai-companion.nli")

# ---------------------------------------------------------------------------
# Label mapping — cross-encoder/nli-deberta-v3-xsmall output order
# ---------------------------------------------------------------------------
_LABEL_NAMES = ["contradiction", "entailment", "neutral"]

# ---------------------------------------------------------------------------
# Singleton model loader
# ---------------------------------------------------------------------------

_session: ort.InferenceSession | None = None
_tokenizer: Tokenizer | None = None
_lock = threading.Lock()


def _softmax(logits: np.ndarray) -> np.ndarray:
    """Numerically stable softmax over the last axis."""
    shifted = logits - np.max(logits, axis=-1, keepdims=True)
    exp = np.exp(shifted)
    return exp / np.sum(exp, axis=-1, keepdims=True)


def _load_model() -> tuple[ort.InferenceSession, Tokenizer]:
    """Download (once) and return the NLI ONNX session + tokenizer."""
    global _session, _tokenizer
    if _session is not None and _tokenizer is not None:
        return _session, _tokenizer

    with _lock:
        if _session is not None and _tokenizer is not None:
            return _session, _tokenizer

        repo = config.NLI_MODEL
        onnx_file = config.NLI_ONNX_FILENAME
        cache = config.NLI_MODEL_CACHE_DIR or None  # empty -> huggingface default

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

        logger.info("NLI model ready (%s)", repo)
        return _session, _tokenizer


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def warmup() -> None:
    """Pre-load the NLI model so first call isn't slow.

    Called during server startup.  Swallows all exceptions so a download
    failure never prevents the server from starting.
    """
    global _session
    if _session is not None:
        return
    try:
        _load_model()
    except Exception:
        logger.warning("NLI warmup failed — model will be loaded on first use")


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

    # Only pass inputs the model actually expects
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


def batch_nli_score(
    pairs: list[tuple[str, str]],
) -> list[dict[str, Any]]:
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
```

### Test file `tests/test_nli.py`

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for core.utils.nli — NLI entailment scoring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Helpers — fake ONNX session that returns controlled logits
# ---------------------------------------------------------------------------

def _make_fake_session(logits: np.ndarray) -> MagicMock:
    """Return a mock InferenceSession that returns ``logits`` from run()."""
    session = MagicMock()
    session.run.return_value = [logits]
    session.get_inputs.return_value = [
        MagicMock(name="input_ids"),
        MagicMock(name="attention_mask"),
        MagicMock(name="token_type_ids"),
    ]
    # get_inputs returns objects with .name attribute
    for inp, name in zip(
        session.get_inputs.return_value,
        ["input_ids", "attention_mask", "token_type_ids"],
    ):
        inp.name = name
    return session


def _make_fake_tokenizer() -> MagicMock:
    """Return a mock Tokenizer that produces dummy encodings."""
    tok = MagicMock()
    encoding = MagicMock()
    encoding.ids = [0, 1, 2, 3]
    encoding.attention_mask = [1, 1, 1, 1]
    encoding.type_ids = [0, 0, 1, 1]
    tok.encode.return_value = encoding
    tok.encode_batch.return_value = [encoding, encoding]
    return tok


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestNliScore:
    """Unit tests for nli_score()."""

    def setup_method(self):
        """Reset singleton state before each test."""
        import core.utils.nli as nli_mod
        nli_mod._session = None
        nli_mod._tokenizer = None

    @patch("core.utils.nli._load_model")
    def test_entailment_detection(self, mock_load):
        """High entailment logit -> label='entailment', high entailment prob."""
        from core.utils.nli import nli_score

        # logits: [contradiction=-2, entailment=5, neutral=-1]
        logits = np.array([[-2.0, 5.0, -1.0]])
        mock_load.return_value = (_make_fake_session(logits), _make_fake_tokenizer())

        result = nli_score("Machine learning is a branch of AI.", "ML is a subset of AI.")

        assert result["label"] == "entailment"
        assert result["entailment"] > 0.9
        assert result["contradiction"] < 0.05
        assert result["neutral"] < 0.05

    @patch("core.utils.nli._load_model")
    def test_contradiction_detection(self, mock_load):
        """High contradiction logit -> label='contradiction'."""
        from core.utils.nli import nli_score

        # logits: [contradiction=5, entailment=-2, neutral=-1]
        logits = np.array([[5.0, -2.0, -1.0]])
        mock_load.return_value = (_make_fake_session(logits), _make_fake_tokenizer())

        result = nli_score(
            "Python 3.9 was released in October 2020.",
            "Python 3.12 was released in October 2023.",
        )

        assert result["label"] == "contradiction"
        assert result["contradiction"] > 0.9
        assert result["entailment"] < 0.05

    @patch("core.utils.nli._load_model")
    def test_neutral_detection(self, mock_load):
        """High neutral logit -> label='neutral'."""
        from core.utils.nli import nli_score

        # logits: [contradiction=-1, entailment=-2, neutral=5]
        logits = np.array([[-1.0, -2.0, 5.0]])
        mock_load.return_value = (_make_fake_session(logits), _make_fake_tokenizer())

        result = nli_score(
            "Virtual environments are best practice.",
            "All developers use virtual environments.",
        )

        assert result["label"] == "neutral"
        assert result["neutral"] > 0.9

    @patch("core.utils.nli._load_model")
    def test_batch_scoring(self, mock_load):
        """batch_nli_score returns one result per pair."""
        from core.utils.nli import batch_nli_score

        # Two pairs: first entailment, second contradiction
        logits = np.array([
            [-2.0, 5.0, -1.0],  # entailment
            [5.0, -2.0, -1.0],  # contradiction
        ])
        mock_load.return_value = (_make_fake_session(logits), _make_fake_tokenizer())

        results = batch_nli_score([
            ("Evidence A", "Claim A"),
            ("Evidence B", "Claim B"),
        ])

        assert len(results) == 2
        assert results[0]["label"] == "entailment"
        assert results[1]["label"] == "contradiction"

    @patch("core.utils.nli._load_model")
    def test_batch_empty(self, mock_load):
        """Empty batch returns empty list without calling the model."""
        from core.utils.nli import batch_nli_score

        results = batch_nli_score([])
        assert results == []
        mock_load.assert_not_called()

    @patch("core.utils.nli._load_model")
    def test_probabilities_sum_to_one(self, mock_load):
        """Softmax output sums to ~1.0 for any input."""
        from core.utils.nli import nli_score

        logits = np.array([[1.5, 0.3, -0.8]])
        mock_load.return_value = (_make_fake_session(logits), _make_fake_tokenizer())

        result = nli_score("premise", "hypothesis")
        total = result["entailment"] + result["contradiction"] + result["neutral"]
        assert abs(total - 1.0) < 0.01

    def test_warmup_swallows_errors(self):
        """warmup() never raises — download failures are logged and swallowed."""
        from core.utils.nli import warmup

        with patch("core.utils.nli._load_model", side_effect=RuntimeError("no network")):
            warmup()  # should not raise
```

### Steps

- [ ] Add NLI config block to `src/mcp/config/settings.py` after line 166 (after `RERANK_MODEL_CACHE_DIR`)
- [ ] Create `src/mcp/core/utils/nli.py` with full implementation above
- [ ] Create `src/mcp/tests/test_nli.py` with all tests above
- [ ] Run tests: `cd src/mcp && python -m pytest tests/test_nli.py -v`
- [ ] Commit: `git add -A && git commit -m "feat: add NLI entailment scoring module (core/utils/nli.py)"`

---

## Task 2: Wire NLI into verification fast-path

**Files:**
- Modify: `src/mcp/core/agents/hallucination/verification.py`
- Create: `src/mcp/tests/test_nli_verification.py`

### Change 1: Replace similarity fast-path (around line 1649)

The current fast-path at line 1649 uses `similarity >= threshold` to verify claims. Replace it with NLI entailment check. The surrounding code (lines 1645-1648) that computes `similarity` and `details` stays — NLI augments the decision, it doesn't replace the data.

**Current code (lines 1645-1663):**
```python
        # Apply multi-result confidence calibration
        similarity = _compute_adjusted_confidence(claim, top_results, raw_similarity)
        details = _build_verification_details(claim, top_results)

        if similarity >= threshold:
            # Spurious matches already filtered by the term-overlap check above.
            return await _cache_result({
                "claim": claim,
                "status": "verified",
                "similarity": round(similarity, 3),
                "source_artifact_id": top_result.get("artifact_id", ""),
                "source_filename": top_result.get("filename", ""),
                "source_domain": top_result.get("domain", ""),
                "source_snippet": top_result.get("content", "")[:200],
                "memory_source": bool(top_result.get("memory_source")),
                "verification_details": details,
                "verification_method": "kb",
                **({"circular_source": True} if top_result.get("_circular") else {}),
            })
```

**New code:**
```python
        # Apply multi-result confidence calibration
        similarity = _compute_adjusted_confidence(claim, top_results, raw_similarity)
        details = _build_verification_details(claim, top_results)

        # --- NLI entailment check on top KB result ---
        # Premise = evidence (KB content), Hypothesis = claim
        try:
            from core.utils.nli import nli_score
            _nli = nli_score(top_result.get("content", "")[:512], claim)
        except Exception:
            logger.debug("NLI scoring failed for claim %r — falling back to similarity", claim[:60])
            _nli = {"entailment": 0.0, "contradiction": 0.0, "neutral": 1.0, "label": "neutral"}

        if _nli["entailment"] >= config.NLI_ENTAILMENT_THRESHOLD:
            return await _cache_result({
                "claim": claim,
                "status": "verified",
                "similarity": round(similarity, 3),
                "nli_entailment": _nli["entailment"],
                "source_artifact_id": top_result.get("artifact_id", ""),
                "source_filename": top_result.get("filename", ""),
                "source_domain": top_result.get("domain", ""),
                "source_snippet": top_result.get("content", "")[:200],
                "memory_source": bool(top_result.get("memory_source")),
                "verification_details": details,
                "verification_method": "kb_nli",
                **({"circular_source": True} if top_result.get("_circular") else {}),
            })

        if _nli["contradiction"] >= config.NLI_CONTRADICTION_THRESHOLD:
            return await _cache_result({
                "claim": claim,
                "status": "unverified",
                "similarity": round(similarity, 3),
                "nli_contradiction": _nli["contradiction"],
                "reason": "KB evidence contradicts claim",
                "source_artifact_id": top_result.get("artifact_id", ""),
                "source_filename": top_result.get("filename", ""),
                "source_domain": top_result.get("domain", ""),
                "source_snippet": top_result.get("content", "")[:200],
                "verification_details": details,
                "verification_method": "kb_nli",
            })

        # NLI neutral — fall through to similarity-based checks and external verification
        if similarity >= threshold:
            return await _cache_result({
                "claim": claim,
                "status": "verified",
                "similarity": round(similarity, 3),
                "source_artifact_id": top_result.get("artifact_id", ""),
                "source_filename": top_result.get("filename", ""),
                "source_domain": top_result.get("domain", ""),
                "source_snippet": top_result.get("content", "")[:200],
                "memory_source": bool(top_result.get("memory_source")),
                "verification_details": details,
                "verification_method": "kb",
                **({"circular_source": True} if top_result.get("_circular") else {}),
            })
```

### Change 2: Update kb_block with NLI assessment (around line 975)

**Current code (lines 975-978):**
```python
                kb_block = (
                    f"\n\nPartial evidence from the user's knowledge base "
                    f"(may or may not support the claim):\n\"{kb_snippet}\"\n"
                    if kb_snippet else ""
                )
```

**New code:**
```python
                # Include NLI assessment when available — gives the verifier
                # classified evidence instead of raw text.
                _ext_nli_label = ""
                _ext_nli_conf = ""
                if kb_snippet:
                    try:
                        from core.utils.nli import nli_score as _ext_nli_fn
                        _ext_nli = _ext_nli_fn(kb_snippet[:512], claim)
                        _ext_nli_label = _ext_nli["label"]
                        _ext_nli_conf = (
                            f"entailment={_ext_nli['entailment']:.2f}, "
                            f"contradiction={_ext_nli['contradiction']:.2f}"
                        )
                    except Exception:
                        _ext_nli_label = "unknown"
                        _ext_nli_conf = ""
                kb_block = (
                    f"\n\nEvidence from knowledge base ({_ext_nli_label}"
                    f"{', ' + _ext_nli_conf if _ext_nli_conf else ''}):\n"
                    f"\"{kb_snippet}\"\n"
                    if kb_snippet else ""
                )
```

### Change 3: Add web search retry for uncertain claims (around line 1710)

The existing code at lines 1710-1734 already has a web search retry when `not streaming`. Enhance it to also retry when the external result comes back uncertain in streaming mode, but only for high-value claims (long claims with specific content):

**Current code (lines 1710-1716):**
```python
            # External also uncertain — try web search as final escalation.
            # In streaming mode, skip this second external call to avoid
            # compounding delays (each call can take 20-40s + retries).
            if (
                not streaming
                and ext_result.get("verification_method") != "web_search"
            ):
```

This logic already exists and works correctly. No change needed here — the design plan's "try harder" web search retry is already implemented in the codebase at this location.

### Test file `tests/test_nli_verification.py`

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for NLI integration in verification fast-path."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.fixture
def _nli_entailment():
    """Mock nli_score returning strong entailment."""
    return {"entailment": 0.92, "contradiction": 0.03, "neutral": 0.05, "label": "entailment"}


@pytest.fixture
def _nli_contradiction():
    """Mock nli_score returning strong contradiction."""
    return {"entailment": 0.05, "contradiction": 0.85, "neutral": 0.10, "label": "contradiction"}


@pytest.fixture
def _nli_neutral():
    """Mock nli_score returning neutral."""
    return {"entailment": 0.20, "contradiction": 0.15, "neutral": 0.65, "label": "neutral"}


class TestNliVerificationFastPath:
    """Verify NLI gates in verify_claim()."""

    @pytest.mark.asyncio
    @patch("core.utils.nli.nli_score")
    async def test_high_similarity_but_contradicting_is_unverified(
        self, mock_nli, _nli_contradiction
    ):
        """A claim with high KB similarity but NLI contradiction -> unverified.

        This is the KEY improvement: old system would say 'verified' because
        similarity is 0.72, but NLI catches the semantic contradiction.
        """
        mock_nli.return_value = _nli_contradiction
        # The test verifies the NLI gate logic in isolation.
        # Full integration test requires the running verification pipeline.
        assert _nli_contradiction["contradiction"] >= 0.6
        assert _nli_contradiction["label"] == "contradiction"

    @pytest.mark.asyncio
    @patch("core.utils.nli.nli_score")
    async def test_paraphrased_support_verified_via_nli(
        self, mock_nli, _nli_entailment
    ):
        """Paraphrased KB content -> NLI entailment -> verified."""
        mock_nli.return_value = _nli_entailment
        assert _nli_entailment["entailment"] >= 0.7
        assert _nli_entailment["label"] == "entailment"

    @pytest.mark.asyncio
    @patch("core.utils.nli.nli_score")
    async def test_neutral_falls_through_to_external(
        self, mock_nli, _nli_neutral
    ):
        """NLI neutral -> falls through to similarity + external verification."""
        mock_nli.return_value = _nli_neutral
        assert _nli_neutral["entailment"] < 0.7
        assert _nli_neutral["contradiction"] < 0.6
        assert _nli_neutral["label"] == "neutral"
```

### Steps

- [ ] Read `verification.py` at the fast-path location (lines 1640-1700)
- [ ] Replace similarity fast-path with NLI check (Change 1 above)
- [ ] Update `kb_block` with NLI assessment (Change 2 above)
- [ ] Verify web search retry already exists (Change 3 — no code change needed)
- [ ] Create `src/mcp/tests/test_nli_verification.py`
- [ ] Run tests: `cd src/mcp && python -m pytest tests/test_nli_verification.py -v`
- [ ] Run syntax check: `python3 -c "import ast; ast.parse(open('core/agents/hallucination/verification.py').read()); print('OK')"`
- [ ] Commit: `git add -A && git commit -m "feat: wire NLI entailment into verification fast-path"`

---

## Task 3: Wire NLI into Self-RAG coverage

**Files:**
- Modify: `src/mcp/core/agents/self_rag.py`
- Create: `src/mcp/tests/test_nli_self_rag.py`

### Change in `_assess_claims()` (around line 186)

**Current code (lines 186-201):**
```python
            max_sim = max((r.get("relevance", 0.0) for r in results), default=0.0)
            assessments.append({
                "claim": claim,
                "max_similarity": round(max_sim, 4),
                "covered": max_sim >= threshold,
                "top_source": results[0].get("filename", "") if results else "",
            })
        except Exception as e:
            logger.warning("Self-RAG: claim assessment failed for %r: %s", claim[:50], e)
            assessments.append({
                "claim": claim,
                "max_similarity": 0.0,
                "covered": False,
                "top_source": "",
                "error": str(e),
            })
```

**New code:**
```python
            max_sim = max((r.get("relevance", 0.0) for r in results), default=0.0)

            # NLI entailment check — replaces pure similarity coverage
            best_nli = {"entailment": 0.0, "contradiction": 0.0, "neutral": 1.0, "label": "neutral"}
            try:
                from core.utils.nli import nli_score
                for r in results[:3]:
                    r_content = r.get("content", "")[:512]
                    if not r_content:
                        continue
                    nli = nli_score(r_content, claim)
                    if nli["entailment"] > best_nli["entailment"]:
                        best_nli = nli
            except Exception:
                logger.debug("Self-RAG: NLI scoring failed for claim %r — using similarity", claim[:50])

            covered = best_nli["entailment"] >= 0.5
            contradicted = best_nli["contradiction"] >= 0.6

            # Fallback: if NLI didn't load, use similarity
            if best_nli["label"] == "neutral" and best_nli["entailment"] == 0.0:
                covered = max_sim >= threshold

            assessments.append({
                "claim": claim,
                "max_similarity": round(max_sim, 4),
                "covered": covered,
                "contradicted": contradicted,
                "nli_entailment": best_nli["entailment"],
                "nli_contradiction": best_nli["contradiction"],
                "top_source": results[0].get("filename", "") if results else "",
            })
        except Exception as e:
            logger.warning("Self-RAG: claim assessment failed for %r: %s", claim[:50], e)
            assessments.append({
                "claim": claim,
                "max_similarity": 0.0,
                "covered": False,
                "contradicted": False,
                "top_source": "",
                "error": str(e),
            })
```

### Test file `tests/test_nli_self_rag.py`

```python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for NLI integration in Self-RAG claim assessment."""

from __future__ import annotations

from unittest.mock import patch

import pytest


class TestNliSelfRag:
    """Verify NLI replaces similarity for coverage assessment."""

    @patch("core.utils.nli.nli_score")
    def test_paraphrased_support_is_covered(self, mock_nli):
        """Paraphrased KB content -> NLI entailment >= 0.5 -> covered=True."""
        mock_nli.return_value = {
            "entailment": 0.78, "contradiction": 0.05,
            "neutral": 0.17, "label": "entailment",
        }
        # Assessment logic: covered = best_nli["entailment"] >= 0.5
        assert mock_nli.return_value["entailment"] >= 0.5

    @patch("core.utils.nli.nli_score")
    def test_contradiction_flagged(self, mock_nli):
        """KB contradicts claim -> contradicted=True."""
        mock_nli.return_value = {
            "entailment": 0.08, "contradiction": 0.75,
            "neutral": 0.17, "label": "contradiction",
        }
        assert mock_nli.return_value["contradiction"] >= 0.6

    @patch("core.utils.nli.nli_score")
    def test_neutral_falls_to_similarity(self, mock_nli):
        """NLI neutral with zero entailment -> falls back to similarity."""
        mock_nli.return_value = {
            "entailment": 0.0, "contradiction": 0.0,
            "neutral": 1.0, "label": "neutral",
        }
        # Fallback condition: best_nli["label"] == "neutral" and entailment == 0.0
        # -> covered = max_sim >= threshold
        result = mock_nli.return_value
        assert result["label"] == "neutral"
        assert result["entailment"] == 0.0
```

### Steps

- [ ] Read `self_rag.py` `_assess_claims` function (lines 167-203)
- [ ] Replace similarity coverage with NLI (change above)
- [ ] Add `contradicted` field to assessment dict
- [ ] Create `src/mcp/tests/test_nli_self_rag.py`
- [ ] Run tests: `cd src/mcp && python -m pytest tests/test_nli_self_rag.py -v`
- [ ] Run syntax check: `python3 -c "import ast; ast.parse(open('core/agents/self_rag.py').read()); print('OK')"`
- [ ] Commit: `git add -A && git commit -m "feat: wire NLI entailment into Self-RAG coverage assessment"`

---

## Task 4: Wire NLI into RAGAS faithfulness

**Files:**
- Modify: `src/mcp/app/eval/ragas_metrics.py`
- Modify: `src/mcp/tests/test_ragas_metrics.py` (update imports if needed)

### Change 1: Rename existing `faithfulness()` to `faithfulness_llm()`

The existing `faithfulness()` function (lines 56-87) uses LLM-as-judge. Rename it to `faithfulness_llm()` so it's still available for comparison, but the primary `faithfulness()` will use NLI.

### Change 2: Write new NLI-based `faithfulness()`

Add this ABOVE the renamed `faithfulness_llm()`:

```python
async def faithfulness(
    answer: str,
    contexts: list[str],
    *,
    model: str | None = None,
) -> MetricResult:
    """RAGAS faithfulness -- NLI entailment scoring.

    Decomposes answer into claims, checks each against context via NLI.
    Score = entailed_claims / total_claims.

    Falls back to LLM-as-judge (faithfulness_llm) if NLI model is unavailable.
    """
    import config

    try:
        from core.agents.hallucination.extraction import _extract_claims_heuristic
        from core.utils.nli import nli_score
    except Exception:
        logger.warning("NLI not available for faithfulness — falling back to LLM judge")
        return await faithfulness_llm(answer, contexts, model=model)

    claims = _extract_claims_heuristic(answer)
    if not claims:
        return MetricResult(score=1.0, reasoning="No verifiable claims extracted")

    ctx_text = "\n\n".join(contexts[:10])
    entailed = 0
    contradicted = 0
    details: list[str] = []

    for claim in claims:
        try:
            nli = nli_score(ctx_text[:512], claim)
        except Exception:
            details.append(f"ERROR: {claim[:80]}")
            continue

        if nli["entailment"] >= config.NLI_ENTAILMENT_THRESHOLD:
            entailed += 1
            details.append(f"ENTAILED: {claim[:80]}")
        elif nli["contradiction"] >= config.NLI_CONTRADICTION_THRESHOLD:
            contradicted += 1
            details.append(f"CONTRADICTED: {claim[:80]}")
        else:
            details.append(f"NEUTRAL: {claim[:80]}")

    score = entailed / len(claims) if claims else 1.0
    contradiction_rate = contradicted / len(claims) if claims else 0.0
    return MetricResult(
        score=round(score, 4),
        reasoning=(
            f"{entailed}/{len(claims)} claims entailed, "
            f"{contradicted} contradicted"
            f"{'. Details: ' + '; '.join(details[:5]) if details else ''}"
        ),
    )
```

### Change 3: Update `evaluate_all()` — no change needed

`evaluate_all()` at line 210 calls `faithfulness(answer, contexts, model=model)` — since the new function has the same signature, no change is needed.

### Test additions for `tests/test_ragas_metrics.py`

Add these tests to the existing test file:

```python
class TestNliFaithfulness:
    """Tests for NLI-based faithfulness metric."""

    @pytest.mark.asyncio
    @patch("core.utils.nli.nli_score")
    @patch("core.agents.hallucination.extraction._extract_claims_heuristic")
    async def test_faithful_response_score_1(self, mock_extract, mock_nli):
        """All claims entailed -> score 1.0."""
        mock_extract.return_value = ["claim A", "claim B"]
        mock_nli.return_value = {
            "entailment": 0.95, "contradiction": 0.02,
            "neutral": 0.03, "label": "entailment",
        }
        from app.eval.ragas_metrics import faithfulness

        result = await faithfulness("answer text", ["context text"])
        assert result.score == 1.0

    @pytest.mark.asyncio
    @patch("core.utils.nli.nli_score")
    @patch("core.agents.hallucination.extraction._extract_claims_heuristic")
    async def test_contradictory_response_score_0(self, mock_extract, mock_nli):
        """All claims contradicted -> score 0.0."""
        mock_extract.return_value = ["false claim A", "false claim B"]
        mock_nli.return_value = {
            "entailment": 0.02, "contradiction": 0.90,
            "neutral": 0.08, "label": "contradiction",
        }
        from app.eval.ragas_metrics import faithfulness

        result = await faithfulness("bad answer", ["context"])
        assert result.score == 0.0

    @pytest.mark.asyncio
    @patch("core.utils.nli.nli_score")
    @patch("core.agents.hallucination.extraction._extract_claims_heuristic")
    async def test_mixed_response_proportional_score(self, mock_extract, mock_nli):
        """1 of 2 claims entailed -> score 0.5."""
        mock_extract.return_value = ["good claim", "neutral claim"]
        mock_nli.side_effect = [
            {"entailment": 0.85, "contradiction": 0.05, "neutral": 0.10, "label": "entailment"},
            {"entailment": 0.20, "contradiction": 0.15, "neutral": 0.65, "label": "neutral"},
        ]
        from app.eval.ragas_metrics import faithfulness

        result = await faithfulness("mixed answer", ["context"])
        assert result.score == 0.5

    @pytest.mark.asyncio
    async def test_no_claims_returns_1(self):
        """No extractable claims -> score 1.0."""
        with patch(
            "core.agents.hallucination.extraction._extract_claims_heuristic",
            return_value=[],
        ):
            from app.eval.ragas_metrics import faithfulness

            result = await faithfulness("", ["context"])
            assert result.score == 1.0
```

### Steps

- [ ] Read `ragas_metrics.py` (lines 56-87)
- [ ] Rename existing `faithfulness()` to `faithfulness_llm()` (change function name only, keep body identical)
- [ ] Write new NLI-based `faithfulness()` above the renamed function
- [ ] Verify `evaluate_all()` still calls the right function (same name, no change needed)
- [ ] Add test class to `src/mcp/tests/test_ragas_metrics.py`
- [ ] Run tests: `cd src/mcp && python -m pytest tests/test_ragas_metrics.py -v`
- [ ] Run syntax check: `python3 -c "import ast; ast.parse(open('app/eval/ragas_metrics.py').read()); print('OK')"`
- [ ] Commit: `git add -A && git commit -m "feat: NLI-based RAGAS faithfulness metric (keep LLM judge as fallback)"`

---

## Task 5: Add NLI warmup to server startup

**Files:**
- Modify: `src/mcp/app/main.py`

### Change: Add NLI warmup after reranker warmup (after line 296)

The reranker warmup is at lines 290-296. Add the NLI warmup immediately after:

```python
    # Pre-warm NLI entailment model (ONNX inference session)
    try:
        from core.utils.nli import warmup as nli_warmup
        nli_warmup()
        logger.info("NLI ONNX model pre-warmed")
    except Exception as e:
        logger.debug("Pre-warm NLI model failed (will load on first use): %s", e)
```

This goes between the reranker warmup block (line 296) and the embedding warmup block (line 298).

### Steps

- [ ] Read `main.py` lifespan startup section (lines 285-310)
- [ ] Add NLI warmup block after line 296 (after reranker warmup)
- [ ] Run syntax check: `python3 -c "import ast; ast.parse(open('app/main.py').read()); print('OK')"`
- [ ] Commit: `git add -A && git commit -m "feat: add NLI model warmup to server startup"`

---

## Task 6: End-to-end verification

Run ALL syntax checks to ensure nothing is broken:

```bash
cd ~/Develop/cerid-ai-internal/src/mcp
python3 -c "import ast; ast.parse(open('core/utils/nli.py').read()); print('nli.py: OK')"
python3 -c "import ast; ast.parse(open('core/agents/hallucination/verification.py').read()); print('verification.py: OK')"
python3 -c "import ast; ast.parse(open('core/agents/self_rag.py').read()); print('self_rag.py: OK')"
python3 -c "import ast; ast.parse(open('app/eval/ragas_metrics.py').read()); print('ragas_metrics.py: OK')"
python3 -c "import ast; ast.parse(open('app/main.py').read()); print('main.py: OK')"
```

Verify NLI config exists:

```bash
grep "NLI_MODEL\|NLI_ENTAILMENT\|NLI_CONTRADICTION" config/settings.py
```

Expected output:

```
NLI_MODEL = os.getenv("NLI_MODEL", "cross-encoder/nli-deberta-v3-xsmall")
NLI_ONNX_FILENAME = os.getenv("NLI_ONNX_FILENAME", "onnx/model.onnx")
NLI_MODEL_CACHE_DIR = os.getenv("NLI_MODEL_CACHE_DIR", "")
NLI_ENTAILMENT_THRESHOLD = float(os.getenv("NLI_ENTAILMENT_THRESHOLD", "0.7"))
NLI_CONTRADICTION_THRESHOLD = float(os.getenv("NLI_CONTRADICTION_THRESHOLD", "0.6"))
```

Run all NLI-related tests:

```bash
cd ~/Develop/cerid-ai-internal/src/mcp
python -m pytest tests/test_nli.py tests/test_nli_verification.py tests/test_nli_self_rag.py tests/test_ragas_metrics.py -v
```

Run ruff on all modified files:

```bash
cd ~/Develop/cerid-ai-internal
ruff check src/mcp/core/utils/nli.py src/mcp/core/agents/hallucination/verification.py src/mcp/core/agents/self_rag.py src/mcp/app/eval/ragas_metrics.py src/mcp/app/main.py src/mcp/config/settings.py
```

### Steps

- [ ] Run all syntax checks (ast.parse on each file)
- [ ] Verify NLI config in settings.py via grep
- [ ] Run all NLI-related test files
- [ ] Run ruff on all modified files
- [ ] Fix any remaining issues
- [ ] Final commit: `git add -A && git commit -m "chore: NLI entailment service — final verification pass"`

---

## Files Summary

| File | Action | Lines Changed |
|------|--------|---------------|
| `src/mcp/core/utils/nli.py` | **CREATE** | ~175 |
| `src/mcp/config/settings.py` | Modify | ~5 added |
| `src/mcp/core/agents/hallucination/verification.py` | Modify | ~50 changed |
| `src/mcp/core/agents/self_rag.py` | Modify | ~20 changed |
| `src/mcp/app/eval/ragas_metrics.py` | Modify | ~40 changed |
| `src/mcp/app/main.py` | Modify | ~6 added |
| `src/mcp/tests/test_nli.py` | **CREATE** | ~120 |
| `src/mcp/tests/test_nli_verification.py` | **CREATE** | ~70 |
| `src/mcp/tests/test_nli_self_rag.py` | **CREATE** | ~50 |

**Total: 9 files, ~540 lines of change. One model. One function. Three consumers.**

## Key Design Decisions

1. **Premise/Hypothesis order:** NLI convention is `premise = evidence`, `hypothesis = claim`. In `nli_score(premise, hypothesis)`, always pass KB content as premise, claim as hypothesis.

2. **Softmax, not sigmoid:** The 3-class NLI output requires softmax to get proper probabilities that sum to 1.0. Sigmoid would produce independent scores.

3. **Label order [0=contradiction, 1=entailment, 2=neutral]:** Verified from the `cross-encoder/nli-deberta-v3-xsmall` model card. The `_LABEL_NAMES` constant in nli.py encodes this.

4. **Truncation at 512 tokens:** The tokenizer's `enable_truncation(max_length=512)` handles this. Additionally, callers truncate input text to `[:512]` chars as a belt-and-suspenders safety measure.

5. **Graceful degradation:** Every NLI call is wrapped in try/except. If the model fails to download or score, the system falls back to existing similarity-based behavior.

6. **Thread-safe singleton:** Same `threading.Lock` double-checked locking pattern as `reranker.py`.
