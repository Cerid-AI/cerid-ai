# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""``embedding_model_version`` names the serving ARTIFACT, not a config string.

Ruling D13-A. Every vector on the personal stack came from nomic through the
local server, yet ~700 chunks say Snowflake and every chunk's version says the
ONNX config name, because the version was ``EMBEDDING_MODEL_VERSION`` (default:
the ONNX model name) whatever leg produced the vector. A version that does not
move when the artifact moves cannot drive a re-embed, so the stamp now records
what served: the served model plus the server's own version (or model digest)
for the local server, the loaded revision for in-process ONNX, the model id
when the store embeds server-side.
"""

from __future__ import annotations

import httpx
import pytest

from core.utils import embeddings as emb

QF_MODEL = "nomic-embed-text-v1.5"
ONNX_MODEL = "Snowflake/snowflake-arctic-embed-m-v1.5"
ONNX_PIN = emb._PINNED_REVISIONS[ONNX_MODEL]
SERVER = "http://embed-server.test:11434"


@pytest.fixture(autouse=True)
def _cold_version_cache():
    emb._reset_serving_version_cache_for_testing()
    yield
    emb._reset_serving_version_cache_for_testing()


class _FakeServer:
    """Answers ``httpx.get`` for one base URL; anything else is a transport error."""

    def __init__(self, routes: dict[str, tuple[int, object]]) -> None:
        self.routes = routes
        self.calls: list[str] = []

    def get(self, url: str, **_: object) -> httpx.Response:
        self.calls.append(url)
        path = url[len(SERVER):] if url.startswith(SERVER) else None
        if path is None or path not in self.routes:
            raise httpx.ConnectError("refused", request=httpx.Request("GET", url))
        status, body = self.routes[path]
        req = httpx.Request("GET", url)
        if isinstance(body, str):
            return httpx.Response(status, text=body, request=req)
        return httpx.Response(status, json=body, request=req)


def _serve(monkeypatch, routes: dict[str, tuple[int, object]]) -> _FakeServer:
    server = _FakeServer(routes)
    monkeypatch.setattr(httpx, "get", server.get)
    return server


def _local_server(monkeypatch, model: str = QF_MODEL) -> None:
    monkeypatch.setenv("EMBEDDINGS_PROVIDER", "quenchforge")
    monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", model)
    monkeypatch.setenv("QUENCHFORGE_URL", SERVER)


class TestLocalServerVersion:
    def test_mlx_server_version_is_the_artifact(self, monkeypatch):
        _local_server(monkeypatch)
        _serve(monkeypatch, {"/api/version": (200, {"version": "cerid-mlx-7"})})

        assert emb.serving_embedding_version() == f"{QF_MODEL}@cerid-mlx-7"

    def test_stamp_carries_model_and_server_version(self, monkeypatch):
        _local_server(monkeypatch)
        _serve(monkeypatch, {"/api/version": (200, {"version": "cerid-mlx-7"})})

        assert emb.embedding_stamp("coding") == {
            "embedding_model": QF_MODEL,
            "embedding_model_version": f"{QF_MODEL}@cerid-mlx-7",
        }

    def test_quenchforge_uses_the_model_digest_when_exposed(self, monkeypatch):
        _local_server(monkeypatch)
        _serve(monkeypatch, {
            "/api/version": (404, {"error": "no such route"}),
            "/": (200, {"service": "quenchforge", "version": "0.93.8"}),
            "/api/tags": (200, {"models": [
                {"name": "other-model", "digest": "sha256:ffff"},
                {"name": QF_MODEL, "digest": "sha256:abc123"},
            ]}),
        })

        assert emb.serving_embedding_version() == f"{QF_MODEL}@sha256:abc123"

    def test_quenchforge_falls_back_to_its_version_without_a_digest(self, monkeypatch):
        _local_server(monkeypatch)
        _serve(monkeypatch, {
            "/api/version": (404, {"error": "no such route"}),
            "/": (200, {"service": "quenchforge", "version": "0.93.8"}),
            "/api/tags": (200, {"models": [{"name": QF_MODEL}]}),
        })

        assert emb.serving_embedding_version() == f"{QF_MODEL}@quenchforge-0.93.8"

    def test_unreachable_server_is_unknown_and_not_cached(self, monkeypatch):
        _local_server(monkeypatch)
        server = _serve(monkeypatch, {})

        assert emb.serving_embedding_version() == f"{QF_MODEL}@unknown"

        server.routes["/api/version"] = (200, {"version": "cerid-mlx-8"})
        assert emb.serving_embedding_version() == f"{QF_MODEL}@cerid-mlx-8"


class TestCachedPerServingIdentity:
    def test_second_call_does_not_probe_again(self, monkeypatch):
        _local_server(monkeypatch)
        server = _serve(monkeypatch, {"/api/version": (200, {"version": "cerid-mlx-7"})})

        emb.serving_embedding_version()
        emb.serving_embedding_version()

        assert server.calls == [f"{SERVER}/api/version"]

    def test_a_different_served_model_invalidates(self, monkeypatch):
        _local_server(monkeypatch)
        server = _serve(monkeypatch, {"/api/version": (200, {"version": "cerid-mlx-7"})})
        assert emb.serving_embedding_version() == f"{QF_MODEL}@cerid-mlx-7"

        monkeypatch.setenv("QUENCHFORGE_EMBED_MODEL", "bge-m3")
        assert emb.serving_embedding_version() == "bge-m3@cerid-mlx-7"
        assert len(server.calls) == 2

    def test_switching_to_the_local_leg_invalidates(self, monkeypatch):
        _local_server(monkeypatch)
        _serve(monkeypatch, {"/api/version": (200, {"version": "cerid-mlx-7"})})
        assert emb.serving_embedding_version() == f"{QF_MODEL}@cerid-mlx-7"

        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "sidecar")
        monkeypatch.setattr(emb.config, "EMBEDDING_MODEL", ONNX_MODEL)
        assert emb.serving_embedding_version() == f"{ONNX_MODEL}@{ONNX_PIN}"


class TestOnnxVersion:
    def test_pinned_model_reports_its_revision_without_touching_the_network(self, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "sidecar")
        monkeypatch.setattr(emb.config, "EMBEDDING_MODEL", ONNX_MODEL)
        server = _serve(monkeypatch, {})

        assert emb.serving_embedding_version() == f"{ONNX_MODEL}@{ONNX_PIN}"
        assert server.calls == []

    def test_unpinned_model_reports_the_cached_snapshot_revision(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "sidecar")
        monkeypatch.setattr(emb.config, "EMBEDDING_MODEL", "acme/tiny-embed")
        monkeypatch.setattr(emb.config, "EMBEDDING_ONNX_FILENAME", "onnx/model.onnx")
        monkeypatch.setattr(emb.config, "EMBEDDING_MODEL_CACHE_DIR", str(tmp_path))
        commit = "0123456789abcdef0123456789abcdef01234567"  # pragma: allowlist secret
        repo = tmp_path / "models--acme--tiny-embed"
        (repo / "refs").mkdir(parents=True)
        (repo / "refs" / "main").write_text(commit)
        (repo / "snapshots" / commit / "onnx").mkdir(parents=True)
        (repo / "snapshots" / commit / "onnx" / "model.onnx").write_bytes(b"onnx")

        assert emb.serving_embedding_version() == f"acme/tiny-embed@{commit}"

    def test_unpinned_model_not_yet_downloaded_is_unknown(self, monkeypatch, tmp_path):
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "sidecar")
        monkeypatch.setattr(emb.config, "EMBEDDING_MODEL", "acme/tiny-embed")
        monkeypatch.setattr(emb.config, "EMBEDDING_MODEL_CACHE_DIR", str(tmp_path))

        assert emb.serving_embedding_version() == "acme/tiny-embed@unknown"

    def test_store_side_default_model_is_identified_by_its_id(self, monkeypatch):
        monkeypatch.setenv("EMBEDDINGS_PROVIDER", "sidecar")
        monkeypatch.setattr(emb.config, "EMBEDDING_MODEL", emb._SERVER_DEFAULT_MODEL)

        assert emb.serving_embedding_version() == emb._SERVER_DEFAULT_MODEL
