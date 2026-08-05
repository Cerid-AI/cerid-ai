# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Mocked unit tests for the SPLADE-v3 encoder module (C3.2)."""

from __future__ import annotations

import pytest

from core.retrieval import sparse


def test_is_available_returns_false_when_flag_off(monkeypatch):
    """Flag-off short-circuits without touching deps or the model."""
    monkeypatch.setattr(sparse, "_flag_enabled", lambda: False)
    sparse.reset_encoder_for_test()
    assert sparse.is_available() is False


def test_is_available_returns_false_when_model_missing(monkeypatch, tmp_path):
    """Missing ONNX file is the default state — no exception, no encoder."""
    monkeypatch.setattr(sparse, "_flag_enabled", lambda: True)
    monkeypatch.setattr(sparse, "SPLADE_MODEL_PATH", str(tmp_path / "nope"))
    sparse.reset_encoder_for_test()
    assert sparse.is_available() is False


def test_dot_product_handles_disjoint_vectors():
    assert sparse.dot({1: 0.5}, {2: 0.5}) == 0.0


def test_dot_product_iterates_smaller_side():
    """The optimization in dot() shouldn't change the answer."""
    a = {1: 0.5, 2: 0.5, 3: 0.5}
    b = {2: 1.0}
    # Manual: 0.5 * 1.0 = 0.5
    assert sparse.dot(a, b) == pytest.approx(0.5)
    assert sparse.dot(b, a) == pytest.approx(0.5)


def test_dot_product_empty_inputs():
    assert sparse.dot({}, {1: 1.0}) == 0.0
    assert sparse.dot({1: 1.0}, {}) == 0.0
    assert sparse.dot({}, {}) == 0.0


def test_encode_text_returns_empty_when_encoder_unavailable(monkeypatch):
    """The public encode_text helper must degrade quietly, not raise."""
    monkeypatch.setattr(sparse, "_get_encoder", lambda: None)
    assert sparse.encode_text("anything") == {}


def test_encode_batch_returns_empty_when_encoder_unavailable(monkeypatch):
    monkeypatch.setattr(sparse, "_get_encoder", lambda: None)
    assert sparse.encode_batch(["a", "b"]) == []


def test_encoder_init_failure_sets_one_shot_flag(monkeypatch, tmp_path):
    """A FileNotFoundError on init disables the singleton without crashing.

    Subsequent calls return None without re-attempting the load — that's
    the guarantee that lets is_available() be cheap on a hot path.
    """
    monkeypatch.setattr(sparse, "SPLADE_MODEL_PATH", str(tmp_path / "missing"))
    sparse.reset_encoder_for_test()
    # First call attempts init and fails cleanly.
    assert sparse._get_encoder() is None
    assert sparse._encoder_init_failed is True

    # Second call must not re-attempt — the singleton lock guarantees
    # one-shot.  We assert by patching to a real path AFTER the first
    # failure and confirming the encoder is still None.
    monkeypatch.setattr(sparse, "SPLADE_MODEL_PATH", str(tmp_path))
    assert sparse._get_encoder() is None
