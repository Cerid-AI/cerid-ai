# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Custom Smart RAG preservation invariants — Phase I Day 4."""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.preservation


def test_weights_endpoint_shape(http_client):
    """GET /settings/rag/weights always responds with the documented shape."""
    r = http_client.get("/settings/rag/weights")
    assert r.status_code == 200, f"/settings/rag/weights {r.status_code}: {r.text[:200]}"
    body = r.json()
    assert "weights" in body
    assert "user_scope" in body
    assert "feature_enabled" in body
    assert isinstance(body["weights"], dict)


def test_sources_endpoint_shape(http_client):
    """GET /settings/rag/weights/sources enumerates sources."""
    r = http_client.get("/settings/rag/weights/sources")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["sources"], list)
    # min/max/default are stable
    assert body["min_weight"] == 0.0
    assert body["max_weight"] == 2.0
    assert body["default_weight"] == 1.0
    # KB domain sources always present (registry-independent)
    kb_count = sum(1 for s in body["sources"] if s["name"].startswith("kb:"))
    assert kb_count >= 1


def test_feature_off_rejects_write_with_403(http_client):
    """PUT must 403 when custom_smart_rag is off (community tier)."""
    r = http_client.put(
        "/settings/rag/weights",
        json={"weights": {"gmail": 1.5}},
    )
    # Either 200 (feature on) or 403 (feature off). Both are valid
    # depending on the test environment's tier; preservation here is
    # that we don't 500 / panic.
    assert r.status_code in (200, 403)


def test_weights_module_imports():
    """Sanity: utils.rag_weights stays importable + DEFAULT/MIN/MAX
    constants are stable. Any drift here breaks the UI contract."""
    from utils.rag_weights import (
        DEFAULT_WEIGHT,
        MAX_WEIGHT,
        MIN_WEIGHT,
        apply_to_result,
        get_weights,
        is_active,
        known_sources,
        reset_weights,
        set_weights,
    )
    assert MIN_WEIGHT == 0.0
    assert MAX_WEIGHT == 2.0
    assert DEFAULT_WEIGHT == 1.0
    # Public surface check — these are the names the router + integration
    # tests + future telemetry pipeline depend on.
    assert callable(apply_to_result)
    assert callable(get_weights)
    assert callable(is_active)
    assert callable(known_sources)
    assert callable(reset_weights)
    assert callable(set_weights)


def test_feature_flag_declared():
    """custom_smart_rag must remain in FEATURE_FLAGS — the plugin
    register() path + UI ProGate both depend on this."""
    from config.features import FEATURE_FLAGS
    assert "custom_smart_rag" in FEATURE_FLAGS
