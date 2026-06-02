# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""GA P0.5 B2a — flag weak KB signal so the UI can hedge.

The CRAG threshold only decided whether to *fire* external sources; the KB
result itself was never marked. ``_kb_low_confidence`` mirrors that rule and is
surfaced as ``low_confidence`` on the response. Pure signal, no behaviour change.
"""
from __future__ import annotations

from app.routers.agents import _kb_low_confidence


def test_empty_results_is_low():
    assert _kb_low_confidence({"results": []}, 0.4) is True


def test_missing_results_key_is_low():
    assert _kb_low_confidence({}, 0.4) is True


def test_non_dict_is_low():
    assert _kb_low_confidence(None, 0.4) is True


def test_best_relevance_below_threshold_is_low():
    assert _kb_low_confidence({"results": [{"relevance": 0.3}, {"relevance": 0.1}]}, 0.4) is True


def test_best_relevance_at_or_above_threshold_not_low():
    assert _kb_low_confidence({"results": [{"relevance": 0.5}, {"relevance": 0.3}]}, 0.4) is False
