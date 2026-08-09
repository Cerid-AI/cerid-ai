# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for Custom Smart RAG weights — Phase I Day 1."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def mock_redis():
    r = MagicMock()
    storage: dict[str, dict[bytes, bytes]] = {}

    def _hgetall(key):
        return storage.get(key, {})

    def _hset(key, field, value):
        storage.setdefault(key, {})[field.encode() if isinstance(field, str) else field] = (
            value.encode() if isinstance(value, str) else value
        )

    def _delete(key):
        storage.pop(key, None)

    r.hgetall.side_effect = _hgetall
    r.hset.side_effect = _hset
    r.delete.side_effect = _delete
    r._storage = storage  # type: ignore[attr-defined]
    return r


# ── pure utility tests ─────────────────────────────────────────────────

class TestWeightStorage:
    def test_get_weights_empty_when_redis_unavailable(self):
        with patch("app.deps.get_redis", return_value=None):
            from utils.rag_weights import get_weights
            assert get_weights() == {}

    def test_get_weight_defaults_to_one(self):
        with patch("app.deps.get_redis", return_value=None):
            from utils.rag_weights import get_weight
            assert get_weight("nonexistent") == 1.0

    def test_set_then_get_roundtrip(self, mock_redis):
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.rag_weights import get_weights, set_weights
            set_weights({"gmail": 1.5, "kb:notes": 0.7})
            weights = get_weights()
            assert weights["gmail"] == 1.5
            assert weights["kb:notes"] == 0.7

    def test_clamps_out_of_range_at_read(self, mock_redis):
        # Stuff Redis directly with out-of-range values
        mock_redis._storage["cerid:rag:weights:global"] = {
            b"gmail": b"3.0",      # over MAX (2.0)
            b"kb:mail": b"-0.5",   # under MIN (0.0)
            b"valid": b"1.5",
        }
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.rag_weights import get_weights
            weights = get_weights()
        assert weights["gmail"] == 2.0
        assert weights["kb:mail"] == 0.0
        assert weights["valid"] == 1.5

    def test_clamps_out_of_range_at_write(self, mock_redis):
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.rag_weights import get_weights, set_weights
            set_weights({"gmail": 5.0, "kb:mail": -2.0})
            weights = get_weights()
        assert weights["gmail"] == 2.0
        assert weights["kb:mail"] == 0.0

    def test_reset_clears_all(self, mock_redis):
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.rag_weights import get_weights, reset_weights, set_weights
            set_weights({"gmail": 1.5})
            assert get_weights() != {}
            reset_weights()
            assert get_weights() == {}

    def test_drops_non_numeric_values(self, mock_redis):
        mock_redis._storage["cerid:rag:weights:global"] = {
            b"gmail": b"1.5",
            b"corrupted": b"not-a-number",
        }
        with patch("app.deps.get_redis", return_value=mock_redis):
            from utils.rag_weights import get_weights
            weights = get_weights()
        assert "gmail" in weights
        assert "corrupted" not in weights


# ── apply_to_result tests (hot-path helper) ────────────────────────────

class TestApplyToResult:
    def test_no_weights_returns_unchanged(self):
        from utils.rag_weights import apply_to_result
        assert apply_to_result(0.8, weights={}) == 0.8

    def test_data_source_weight_applied(self):
        from utils.rag_weights import apply_to_result
        result = apply_to_result(
            0.8,
            source_name="gmail",
            weights={"gmail": 1.5},
        )
        assert result == pytest.approx(1.2)

    def test_kb_domain_weight_applied(self):
        from utils.rag_weights import apply_to_result
        result = apply_to_result(
            0.8,
            domain="notes",
            weights={"kb:notes": 0.5},
        )
        assert result == pytest.approx(0.4)

    def test_both_weights_multiply(self):
        # A result from a DataSource that also lives in a KB domain
        from utils.rag_weights import apply_to_result
        result = apply_to_result(
            0.8,
            source_name="gmail",
            domain="mail",
            weights={"gmail": 1.5, "kb:mail": 0.5},
        )
        assert result == pytest.approx(0.8 * 1.5 * 0.5)

    def test_missing_source_uses_default(self):
        from utils.rag_weights import apply_to_result
        result = apply_to_result(
            0.8,
            source_name="unknown_source",
            weights={"gmail": 1.5},
        )
        assert result == 0.8


# ── is_active short-circuit ────────────────────────────────────────────

class TestIsActive:
    def test_false_when_feature_off(self):
        with patch("config.features.is_feature_enabled", return_value=False):
            from utils.rag_weights import is_active
            assert is_active() is False

    def test_false_when_no_weights_set(self, mock_redis):
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            from utils.rag_weights import is_active
            assert is_active() is False

    def test_false_when_all_weights_default(self, mock_redis):
        mock_redis._storage["cerid:rag:weights:global"] = {b"gmail": b"1.0"}
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            from utils.rag_weights import is_active
            assert is_active() is False

    def test_true_when_at_least_one_non_default(self, mock_redis):
        mock_redis._storage["cerid:rag:weights:global"] = {b"gmail": b"1.5"}
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            from utils.rag_weights import is_active
            assert is_active() is True


# ── REST surface ───────────────────────────────────────────────────────

def _make_app() -> FastAPI:
    from app.routers.rag_weights import router

    app = FastAPI()
    app.include_router(router)
    return app


@pytest.fixture
def client():
    return TestClient(_make_app())


class TestRESTSurface:
    def test_get_returns_empty_when_feature_off(self, client, mock_redis):
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=False),
        ):
            resp = client.get("/settings/rag/weights")
        assert resp.status_code == 200
        body = resp.json()
        assert body["feature_enabled"] is False
        assert body["weights"] == {}

    def test_get_returns_stored_weights_when_feature_on(self, client, mock_redis):
        mock_redis._storage["cerid:rag:weights:global"] = {
            b"gmail": b"1.5",
            b"kb:notes": b"0.7",
        }
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            resp = client.get("/settings/rag/weights")
        body = resp.json()
        assert body["feature_enabled"] is True
        assert body["weights"]["gmail"] == 1.5
        assert body["weights"]["kb:notes"] == 0.7

    def test_put_blocked_when_feature_off(self, client, mock_redis):
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=False),
        ):
            resp = client.put(
                "/settings/rag/weights",
                json={"weights": {"gmail": 1.5}},
            )
        assert resp.status_code == 403

    def test_put_persists_when_feature_on(self, client, mock_redis):
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            resp = client.put(
                "/settings/rag/weights",
                json={"weights": {"gmail": 1.5, "kb:notes": 0.7}},
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["weights"]["gmail"] == 1.5

    def test_delete_resets_weights(self, client, mock_redis):
        mock_redis._storage["cerid:rag:weights:global"] = {b"gmail": b"1.5"}
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            resp = client.delete("/settings/rag/weights")
        assert resp.status_code == 200
        assert resp.json()["weights"] == {}

    def test_sources_lists_data_sources_and_kb_domains(self, client, mock_redis):
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=True),
        ):
            resp = client.get("/settings/rag/weights/sources")
        assert resp.status_code == 200
        body = resp.json()
        # min/max/default present
        assert body["min_weight"] == 0.0
        assert body["max_weight"] == 2.0
        assert body["default_weight"] == 1.0
        # KB domains start with "kb:"
        kb_sources = [s for s in body["sources"] if s["kind"] == "kb_domain"]
        assert all(s["name"].startswith("kb:") for s in kb_sources)

    def test_sources_endpoint_works_when_feature_off(self, client, mock_redis):
        """The UI needs to see source names + feature_enabled=False to
        render the upgrade CTA on a realistic preview."""
        with (
            patch("app.deps.get_redis", return_value=mock_redis),
            patch("config.features.is_feature_enabled", return_value=False),
        ):
            resp = client.get("/settings/rag/weights/sources")
        body = resp.json()
        assert body["feature_enabled"] is False
        assert len(body["sources"]) > 0  # KB domains always render
