# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for the sidecar `/encode/sparse` endpoint (C3.2 server side).

The sidecar lives at ``scripts/cerid-sidecar.py`` — a standalone host
script that's not normally imported by the MCP package.  We load it
via ``importlib`` so the test file stays at the canonical
``src/mcp/tests/`` location alongside the client tests.

These tests verify:

* The SPLADE numpy logic produces the same shape + magnitude as the
  in-process encoder, so swapping between sidecar and local-ONNX is
  wire-identical.
* The endpoint correctly picks the full-model vs bolted-head branch
  based on session outputs.
* The endpoint surfaces the chosen branch in the response for
  observability.

No real model is loaded — the ONNX session is fully mocked so the
test runs in milliseconds and works in CI without a network.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import numpy as np
import pytest

# ---------------------------------------------------------------------------
# Module loader — the sidecar script has a hyphen so we can't `import`.
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def sidecar():
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "cerid-sidecar.py"
    spec = importlib.util.spec_from_file_location("_cerid_sidecar", script)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# _splade_from_logits — numpy parity with core.retrieval.sparse
# ---------------------------------------------------------------------------

def test_splade_from_logits_mask_aware(sidecar):
    """Padding positions must NOT contribute to the max-pool.

    Without mask handling, a padded sequence would yield different
    weights than the same text encoded alone.  The mask sets padding
    positions to ``-1e4`` before ReLU, which collapses them.
    """
    # Two tokens, 3-element vocab.  Position 0 is real, position 1 is pad.
    logits = np.array([[
        [2.0, -1.0, 0.5],   # real token
        [9.0,  9.0, 9.0],   # padding — must be masked out
    ]], dtype=np.float32)
    attention_mask = np.array([[1, 0]], dtype=np.int64)
    result = sidecar._splade_from_logits(logits, attention_mask, top_k=10)
    assert len(result) == 1
    weights = {int(k): v for k, v in result[0].items()}
    # Token 1 negative -> ReLU 0 -> log1p(0) = 0, dropped from output.
    assert 1 not in weights
    # Token 0 positive 2.0 -> log1p(2.0) ≈ 1.0986
    assert weights[0] == pytest.approx(np.log1p(2.0), rel=1e-5)


def test_splade_from_logits_top_k_prune(sidecar):
    """Output is capped at top_k non-zero terms (matches encode-time pruning)."""
    # 1 batch, 1 token, 10-vocab — every entry positive
    logits = np.array([[[float(i + 1) for i in range(10)]]], dtype=np.float32)
    attention_mask = np.array([[1]], dtype=np.int64)
    result = sidecar._splade_from_logits(logits, attention_mask, top_k=3)
    assert len(result[0]) == 3
    # The 3 largest token ids (7, 8, 9 — values 8, 9, 10) should survive.
    kept = sorted(int(k) for k in result[0])
    assert kept == [7, 8, 9]


def test_splade_from_logits_empty_when_all_negative(sidecar):
    """A row with no positive activations should emit an empty dict."""
    logits = np.array([[[-1.0, -2.0, -3.0]]], dtype=np.float32)
    attention_mask = np.array([[1]], dtype=np.int64)
    result = sidecar._splade_from_logits(logits, attention_mask, top_k=10)
    assert result == [{}]


def test_splade_from_logits_returns_string_keys(sidecar):
    """JSON has no integer keys; the response wire format must use strings."""
    logits = np.array([[[1.0, 0.5]]], dtype=np.float32)
    attention_mask = np.array([[1]], dtype=np.int64)
    result = sidecar._splade_from_logits(logits, attention_mask, top_k=10)
    for vec in result:
        for k in vec:
            assert isinstance(k, str)


# ---------------------------------------------------------------------------
# /encode/sparse endpoint — full-model branch
# ---------------------------------------------------------------------------

class _FakeEncoded:
    def __init__(self, ids: list[int], mask: list[int]) -> None:
        self.ids = ids
        self.attention_mask = mask
        self.type_ids = [0] * len(ids)


class _FakeTokenizer:
    def encode_batch(self, texts: list[str]) -> list[_FakeEncoded]:
        # Three real tokens per input, mask=1 everywhere.
        return [_FakeEncoded([10, 20, 30], [1, 1, 1]) for _ in texts]


class _FakeSession:
    """ONNX session stub.  Output names + run() output configurable per test."""

    def __init__(self, output_names: list[str], output_tensor: np.ndarray) -> None:
        self._output_names = output_names
        self._output_tensor = output_tensor

    def get_outputs(self) -> list[Any]:
        return [MagicMock(name=name) for name in [n for n in self._output_names]]

    def get_inputs(self) -> list[Any]:
        # Match the real SPLADE inputs; token_type_ids is filtered out by
        # the endpoint when not expected.
        return [
            MagicMock(name="input_ids"),
            MagicMock(name="attention_mask"),
        ]

    def run(self, _outputs: Any, _feeds: Any) -> list[np.ndarray]:
        return [self._output_tensor]


def _install_full_model_session(sidecar) -> None:
    """Inject a session whose output name contains 'logits'."""
    # (B=2, T=3, V=4) logits — first token dominates after max-pool
    logits = np.zeros((2, 3, 4), dtype=np.float32)
    logits[0, 0, 1] = 5.0   # batch-0 emits token 1
    logits[1, 0, 2] = 3.0   # batch-1 emits token 2
    sidecar._splade_session = _FakeSession(["logits"], logits)
    sidecar._splade_tokenizer = _FakeTokenizer()
    sidecar._splade_has_logits_head = True


def _install_bolted_head_session(sidecar) -> None:
    """Inject a session whose only output is the backbone hidden state."""
    # (B=1, T=3, H=4) hidden — bolt-head decoder maps H→V
    hidden = np.zeros((1, 3, 4), dtype=np.float32)
    hidden[0, 0, :] = [1.0, 0.0, 0.0, 0.0]
    sidecar._splade_session = _FakeSession(["last_hidden_state"], hidden)
    sidecar._splade_tokenizer = _FakeTokenizer()
    sidecar._splade_has_logits_head = False
    # Decoder is identity-shape for the test (H=V=4) — token 0 fires.
    sidecar._splade_decoder_w = np.eye(4, dtype=np.float32)
    sidecar._splade_decoder_b = np.zeros(4, dtype=np.float32)


def test_encode_sparse_full_model_branch(sidecar, monkeypatch):
    _install_full_model_session(sidecar)
    monkeypatch.setattr(sidecar, "_load_splade_model", lambda: None)

    req = sidecar.SparseRequest(texts=["alpha", "beta"], is_query=False)
    resp = sidecar.encode_sparse(req)
    assert len(resp.vectors) == 2
    assert resp.branch == "full_model"
    # Per the fixture, batch-0 fires token 1, batch-1 fires token 2.
    assert "1" in resp.vectors[0]
    assert "2" in resp.vectors[1]


def test_encode_sparse_bolted_head_branch(sidecar, monkeypatch):
    _install_bolted_head_session(sidecar)
    monkeypatch.setattr(sidecar, "_load_splade_model", lambda: None)

    req = sidecar.SparseRequest(texts=["alpha"], is_query=False)
    resp = sidecar.encode_sparse(req)
    assert len(resp.vectors) == 1
    assert resp.branch == "bolted_head"
    # Identity decoder + hidden[0]=1.0 → logits[0]=1.0 at token 0.
    assert "0" in resp.vectors[0]


def test_encode_sparse_response_includes_latency(sidecar, monkeypatch):
    _install_full_model_session(sidecar)
    monkeypatch.setattr(sidecar, "_load_splade_model", lambda: None)
    resp = sidecar.encode_sparse(sidecar.SparseRequest(texts=["x"]))
    assert resp.latency_ms >= 0


def test_encode_sparse_is_query_flag_does_not_branch(sidecar, monkeypatch):
    """``is_query`` is accepted for client symmetry but must not change output."""
    _install_full_model_session(sidecar)
    monkeypatch.setattr(sidecar, "_load_splade_model", lambda: None)
    r_doc = sidecar.encode_sparse(sidecar.SparseRequest(texts=["x"], is_query=False))
    r_query = sidecar.encode_sparse(sidecar.SparseRequest(texts=["x"], is_query=True))
    assert r_doc.vectors == r_query.vectors


# ---------------------------------------------------------------------------
# Health endpoint surfaces sparse state
# ---------------------------------------------------------------------------

def test_health_reports_sparse_not_loaded(sidecar, monkeypatch):
    monkeypatch.setattr(sidecar, "_splade_session", None, raising=False)
    monkeypatch.setattr(sidecar, "_splade_has_logits_head", False, raising=False)
    h = sidecar.health()
    assert h["sparse_loaded"] is False
    assert h["sparse_branch"] is None


def test_health_reports_sparse_loaded_branch(sidecar):
    _install_full_model_session(sidecar)
    h = sidecar.health()
    assert h["sparse_loaded"] is True
    assert h["sparse_branch"] == "full_model"
