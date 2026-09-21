# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The setup wizard's model card must account for the in-process fallback.

F024 — ``/setup/models/{status,preload}`` branched on a ``local`` provider
value no other module produces, so with the documented default the card
reported cached weights it never probed.
F171 — with a remote provider configured the endpoints declared there was
nothing to cache and skipped the preload, which is precisely the state
where the local ONNX fallback answers every request.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def client():
    from app.routers.setup import router

    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.fixture
def cold_hf_cache():
    """No ONNX weights on disk — try_to_load_from_cache finds nothing."""
    with patch("huggingface_hub.try_to_load_from_cache", return_value=None) as probe:
        yield probe


class TestModelsStatus:
    def test_remote_provider_still_needs_the_local_fallback_cache(
        self, client, monkeypatch, cold_hf_cache
    ):
        monkeypatch.setenv("RERANK_PROVIDER", "quenchforge")
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")

        body = client.get("/setup/models/status").json()

        assert body["reranker"]["needs_local_cache"] is True, (
            "the wizard called the reranker ready without probing the weights "
            "the fallback path loads"
        )
        assert body["reranker"]["cached"] is False
        assert body["reranker"]["files"], "the cache was never probed"
        assert body["reranker"]["role"] == "fallback"
        assert body["reranker"]["provider"] == "quenchforge"
        assert cold_hf_cache.called

    def test_default_provider_is_the_real_enum(self, client, monkeypatch, cold_hf_cache):
        monkeypatch.delenv("RERANK_PROVIDER", raising=False)
        monkeypatch.delenv("EMBEDDINGS_PROVIDER", raising=False)

        body = client.get("/setup/models/status").json()

        assert body["reranker"]["provider"] == "sidecar", (
            "reported a provider value no other module produces"
        )
        assert body["reranker"]["role"] == "primary"
        assert body["reranker"]["needs_local_cache"] is True


class TestModelsPreload:
    def test_preloads_the_fallback_even_when_the_provider_is_remote(
        self, client, monkeypatch
    ):
        monkeypatch.setenv("RERANK_PROVIDER", "quenchforge")
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
        load_reranker = MagicMock()

        with (
            patch("core.retrieval.reranker._load_model", load_reranker),
            patch("core.utils.embeddings.get_embedding_function", return_value=None),
        ):
            body = client.post("/setup/models/preload").json()

        assert load_reranker.called, (
            "the local ONNX reranker — the lane's terminal fallback — was never warmed"
        )
        assert body["reranker_status"] == "loaded"
        assert body["reranker_role"] == "fallback"
        assert body["embedder_status"] == "skipped_server_side"
