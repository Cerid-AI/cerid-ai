# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for core.utils.nli — NLI entailment scoring."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np


def _make_fake_session(logits: np.ndarray) -> MagicMock:
    """Return a mock InferenceSession that returns ``logits`` from run()."""
    session = MagicMock()
    session.run.return_value = [logits]
    session.get_inputs.return_value = [
        MagicMock(name="input_ids"),
        MagicMock(name="attention_mask"),
        MagicMock(name="token_type_ids"),
    ]
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

        logits = np.array([
            [-2.0, 5.0, -1.0],
            [5.0, -2.0, -1.0],
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
