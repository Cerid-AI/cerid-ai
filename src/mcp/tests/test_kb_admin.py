# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for KB admin endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient


def _reset_rate_limiter(app) -> None:
    """Clear the shared RateLimitMiddleware sliding window.

    ``app.main.app`` is module-global, so its middleware instance persists
    across the whole pytest session — earlier tests' POSTs against ``/admin/*``
    consume the window and later multi-call tests (e.g. the paginated
    reindex loop) 429 under a full-suite run while passing standalone.
    """
    from app.middleware.rate_limit import RateLimitMiddleware

    obj = getattr(app, "middleware_stack", None)
    while obj is not None:
        if isinstance(obj, RateLimitMiddleware):
            obj._hits.clear()
            obj._locks.clear()
            return
        obj = getattr(obj, "app", None)


@pytest.fixture()
def client():
    """Create a test client with mocked dependencies."""
    from app.main import app  # noqa: E402 — triggers router imports

    _reset_rate_limiter(app)
    with (
        patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
        patch("app.routers.kb_admin.get_chroma", return_value=MagicMock()),
        # Phase 2.2 — the semantic-cache invalidation hook calls get_redis();
        # mocked here so every kb_admin test stays off live infrastructure
        # instead of retrying against a real Redis for several seconds.
        patch("app.routers.kb_admin.get_redis", return_value=MagicMock()),
    ):
        yield TestClient(app, raise_server_exceptions=False)


class TestRebuildIndexes:
    def test_rebuild_indexes_success(self, client: TestClient):
        with patch("app.routers.kb_admin.rebuild_bm25_all", return_value=5):
            with patch("app.routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock):
                res = client.post("/admin/kb/rebuild-index")
        assert res.status_code == 200
        data = res.json()
        assert data["domains_rebuilt"] == 5
        assert "5 domains" in data["message"]

    def test_rebuild_indexes_failure(self, client: TestClient):
        with patch("app.routers.kb_admin.rebuild_bm25_all", side_effect=RuntimeError("disk error")):
            res = client.post("/admin/kb/rebuild-index")
        assert res.status_code == 500
        assert "disk error" in res.json()["detail"]


class TestRescore:
    def test_rescore_all(self, client: TestClient):
        mock_result = {
            "artifacts_scored": 42,
            "avg_quality_score": 0.75,
            "artifacts_stored": 42,
            "synopses_generated": 0,
            "score_distribution": {"excellent": 10, "good": 20, "fair": 10, "poor": 2},
            "domains_scored": ["code"],
            "low_quality_artifacts": [],
            "timestamp": "2026-03-09T00:00:00Z",
            "mode": "audit",
        }
        with patch("app.routers.kb_admin.curate", new_callable=AsyncMock, return_value=mock_result):
            with patch("app.routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock):
                res = client.post("/admin/kb/rescore")
        assert res.status_code == 200
        data = res.json()
        assert data["artifacts_scored"] == 42
        assert data["avg_quality_score"] == 0.75

    def test_rescore_with_domain_filter(self, client: TestClient):
        mock_result = {
            "artifacts_scored": 10,
            "avg_quality_score": 0.80,
            "artifacts_stored": 10,
            "synopses_generated": 0,
            "score_distribution": {"excellent": 5, "good": 5, "fair": 0, "poor": 0},
            "domains_scored": ["finance"],
            "low_quality_artifacts": [],
            "timestamp": "2026-03-09T00:00:00Z",
            "mode": "audit",
        }
        with patch("app.routers.kb_admin.curate", new_callable=AsyncMock, return_value=mock_result):
            with patch("app.routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock):
                res = client.post(
                    "/admin/kb/rescore",
                    json={"domains": ["finance"], "max_artifacts": 50},
                )
        assert res.status_code == 200
        assert res.json()["artifacts_scored"] == 10


class TestRegenerateSummaries:
    def test_regenerate_success(self, client: TestClient):
        mock_result = {
            "artifacts_scored": 20,
            "avg_quality_score": 0.65,
            "artifacts_stored": 20,
            "synopses_generated": 8,
            "score_distribution": {"excellent": 5, "good": 10, "fair": 5, "poor": 0},
            "domains_scored": ["code"],
            "low_quality_artifacts": [],
            "timestamp": "2026-03-09T00:00:00Z",
            "mode": "audit",
        }
        with patch("app.routers.kb_admin.curate", new_callable=AsyncMock, return_value=mock_result):
            with patch("app.routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock):
                res = client.post("/admin/kb/regenerate-summaries")
        assert res.status_code == 200
        data = res.json()
        assert data["synopses_generated"] == 8


class TestClearDomain:
    def test_clear_requires_confirm(self, client: TestClient):
        res = client.post(
            "/admin/kb/clear-domain/code",
            json={"confirm": False},
        )
        assert res.status_code == 400
        assert "confirm" in res.json()["detail"].lower()

    def test_clear_unknown_domain(self, client: TestClient):
        res = client.post(
            "/admin/kb/clear-domain/nonexistent_domain_xyz",
            json={"confirm": True},
        )
        assert res.status_code == 404

    def test_clear_domain_success(self, client: TestClient):
        mock_artifacts = [
            {"id": "art-1", "filename": "test.py"},
            {"id": "art-2", "filename": "test2.py"},
        ]
        delete_result = {"deleted": True, "artifact_id": "art-1", "domain": "code", "filename": "test.py", "chunk_ids": ["c1", "c2"]}

        with (
            patch("app.routers.kb_admin.list_artifacts", return_value=mock_artifacts),
            patch("app.routers.kb_admin.delete_artifact", return_value=delete_result),
            patch("app.routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock),
            patch("app.routers.kb_admin.get_chroma"),
            patch("app.routers.kb_admin.get_neo4j"),
            patch("app.routers.kb_admin.config") as mock_config,
        ):
            mock_config.DOMAINS = ["code", "finance"]
            mock_config.collection_name.return_value = "domain_code"
            res = client.post(
                "/admin/kb/clear-domain/code",
                json={"confirm": True},
            )

        assert res.status_code == 200
        data = res.json()
        assert data["artifacts_deleted"] == 2
        assert data["domain"] == "code"


class TestDeleteArtifact:
    def test_delete_not_found(self, client: TestClient):
        with (
            patch("app.routers.kb_admin.get_neo4j"),
            patch("app.routers.kb_admin.get_chroma"),
            patch("app.routers.kb_admin.delete_artifact", return_value={"deleted": False, "reason": "not_found"}),
        ):
            res = client.delete("/admin/artifacts/nonexistent-id")
        assert res.status_code == 404

    def test_delete_success(self, client: TestClient):
        delete_result = {
            "deleted": True,
            "artifact_id": "art-123",
            "domain": "code",
            "filename": "test.py",
            "chunk_ids": ["c1", "c2", "c3"],
        }
        mock_collection = MagicMock()
        mock_chroma = MagicMock()
        mock_chroma.get_collection.return_value = mock_collection

        with (
            patch("app.routers.kb_admin.get_neo4j"),
            patch("app.routers.kb_admin.get_chroma", return_value=mock_chroma),
            patch("app.routers.kb_admin.delete_artifact", return_value=delete_result),
            patch("app.routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock),
            patch("app.routers.kb_admin.config") as mock_config,
        ):
            mock_config.collection_name.return_value = "domain_code"
            res = client.delete("/admin/artifacts/art-123")

        assert res.status_code == 200
        data = res.json()
        assert data["deleted"] is True
        assert data["chunks_removed"] == 3


class TestKBStats:
    def test_stats_success(self, client: TestClient):
        # kb_stats now reads a single grouped Cypher aggregation via the
        # domain_artifact_stats helper (no per-domain 10k row pull).
        dom_stats = {
            "code": {"artifacts": 2, "avg_quality": 0.6, "synopsis_candidates": 1},
        }
        mock_collection = MagicMock()
        mock_collection.count.return_value = 10

        with (
            patch("app.routers.kb_admin.get_neo4j"),
            patch("app.routers.kb_admin.get_chroma") as mock_chroma_fn,
            patch("app.routers.kb_admin.domain_artifact_stats", return_value=dom_stats),
            patch("app.routers.kb_admin.config") as mock_config,
        ):
            mock_config.DOMAINS = ["code"]
            mock_config.collection_name.return_value = "domain_code"
            mock_chroma_fn.return_value.get_collection.return_value = mock_collection
            res = client.get("/admin/kb/stats")

        assert res.status_code == 200
        data = res.json()
        assert data["total_artifacts"] == 2
        assert data["total_chunks"] == 10
        assert "code" in data["domains"]
        assert data["domains"]["code"]["artifacts"] == 2
        assert data["domains"]["code"]["chunks"] == 10
        assert data["domains"]["code"]["synopsis_candidates"] == 1


class TestSemanticCacheInvalidationHook:
    """Phase 2.2 — admin mutation endpoints must also invalidate the
    semantic query cache. Before this, only the flat query cache
    (``invalidate_cache_non_blocking``) was invalidated here — the
    semantic cache had no production caller anywhere, leaving stale
    ``/agent/query`` results for up to its TTL after any admin purge or
    rebuild.
    """

    def test_delete_artifact_invalidates_semantic_cache(self, client: TestClient):
        delete_result = {
            "deleted": True,
            "artifact_id": "art-123",
            "domain": "code",
            "filename": "test.py",
            "chunk_ids": ["c1"],
        }
        mock_collection = MagicMock()
        mock_chroma = MagicMock()
        mock_chroma.get_collection.return_value = mock_collection

        with (
            patch("app.routers.kb_admin.get_neo4j"),
            patch("app.routers.kb_admin.get_chroma", return_value=mock_chroma),
            patch("app.routers.kb_admin.delete_artifact", return_value=delete_result),
            patch("app.routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock),
            patch("app.routers.kb_admin.invalidate_semantic_cache") as mock_sem_invalidate,
            patch("app.routers.kb_admin.config") as mock_config,
        ):
            mock_config.collection_name.return_value = "domain_code"
            res = client.delete("/admin/artifacts/art-123")

        assert res.status_code == 200
        mock_sem_invalidate.assert_called_once()
        _, kwargs = mock_sem_invalidate.call_args
        assert kwargs.get("trigger") == "kb_admin.delete_single_artifact"

    def test_clear_domain_invalidates_semantic_cache(self, client: TestClient):
        mock_artifacts = [{"id": "art-1", "filename": "test.py"}]
        delete_result = {
            "deleted": True, "artifact_id": "art-1", "domain": "code",
            "filename": "test.py", "chunk_ids": [],
        }

        with (
            patch("app.routers.kb_admin.list_artifacts", return_value=mock_artifacts),
            patch("app.routers.kb_admin.delete_artifact", return_value=delete_result),
            patch("app.routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock),
            patch("app.routers.kb_admin.invalidate_semantic_cache") as mock_sem_invalidate,
            patch("app.routers.kb_admin.get_chroma"),
            patch("app.routers.kb_admin.get_neo4j"),
            patch("app.routers.kb_admin.config") as mock_config,
        ):
            mock_config.DOMAINS = ["code", "finance"]
            mock_config.collection_name.return_value = "domain_code"
            res = client.post("/admin/kb/clear-domain/code", json={"confirm": True})

        assert res.status_code == 200
        mock_sem_invalidate.assert_called_once()
        _, kwargs = mock_sem_invalidate.call_args
        assert kwargs.get("trigger") == "kb_admin.clear_domain"

    def test_rebuild_indexes_invalidates_semantic_cache(self, client: TestClient):
        with (
            patch("app.routers.kb_admin.rebuild_bm25_all", return_value=3),
            patch("app.routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock),
            patch("app.routers.kb_admin.invalidate_semantic_cache") as mock_sem_invalidate,
        ):
            res = client.post("/admin/kb/rebuild-index")

        assert res.status_code == 200
        mock_sem_invalidate.assert_called_once()
        _, kwargs = mock_sem_invalidate.call_args
        assert kwargs.get("trigger") == "kb_admin.rebuild_indexes"

    def test_invalidation_hook_failure_does_not_break_endpoint(self, client: TestClient):
        """Best-effort observability boundary — a raising semantic-cache
        invalidation must not fail the admin endpoint."""
        with (
            patch("app.routers.kb_admin.rebuild_bm25_all", return_value=1),
            patch("app.routers.kb_admin.invalidate_cache_non_blocking", new_callable=AsyncMock),
            patch(
                "app.routers.kb_admin.invalidate_semantic_cache",
                side_effect=RuntimeError("cache backend unreachable"),
            ),
        ):
            res = client.post("/admin/kb/rebuild-index")

        assert res.status_code == 200


class TestKBAggregationHelpers:
    """The perf fix: kb duplicates/stats compute via grouped Cypher
    aggregation instead of per-domain 10k-row in-memory scans."""

    @staticmethod
    def _driver(rows, capture):
        drv = MagicMock()
        sess = MagicMock()

        def _run(cypher, **kw):
            capture["cypher"] = cypher
            res = MagicMock()
            res.data.return_value = rows
            return res

        sess.run.side_effect = _run
        drv.session.return_value.__enter__.return_value = sess
        return drv

    def test_list_duplicate_artifacts_returns_only_groups(self):
        from app.db.neo4j.artifacts import list_duplicate_artifacts

        cap: dict = {}
        rows = [
            {"content_hash": "h1", "id": "a1", "filename": "f1"},
            {"content_hash": "h1", "id": "a2", "filename": "f2"},
        ]
        out = list_duplicate_artifacts(self._driver(rows, cap))
        assert out == rows
        # Aggregates + filters to dupes in-DB, not a per-domain row pull.
        assert "collect(a)" in cap["cypher"]
        assert "size(arts) >= 2" in cap["cypher"]

    def test_domain_artifact_stats_aggregates_and_mirrors_heuristic(self):
        from app.db.neo4j.artifacts import domain_artifact_stats

        cap: dict = {}
        rows = [
            {"domain": "code", "artifacts": 5, "avg_quality": 0.6123, "synopsis_candidates": 2},
        ]
        out = domain_artifact_stats(self._driver(rows, cap))
        assert out["code"]["artifacts"] == 5
        assert out["code"]["avg_quality"] == 0.6123
        assert out["code"]["synopsis_candidates"] == 2
        # In-DB aggregation + the truncated-summary predicate (mirrors
        # core.agents.curator._is_truncated_summary: empty / <50 / no .!?).
        assert "count(*)" in cap["cypher"]
        assert "avg(q)" in cap["cypher"]
        assert "size(s) < 50" in cap["cypher"]
        assert "[.!?]" in cap["cypher"]

    def test_domain_artifact_stats_null_avg_defaults_zero(self):
        from app.db.neo4j.artifacts import domain_artifact_stats

        rows = [{"domain": "code", "artifacts": 0, "avg_quality": None, "synopsis_candidates": 0}]
        out = domain_artifact_stats(self._driver(rows, {}))
        assert out["code"]["avg_quality"] == 0.0


class TestReindexCorpus:
    """POST /admin/kb/reindex — resumable, idempotent retroactive re-index."""

    def test_reindexes_file_backed_and_skips_orphans(self, client: TestClient):
        from pathlib import Path

        artifacts = [
            {"id": "a1", "filename": "f1.md", "domain": "coding", "sub_category": "x"},
            {"id": "a2", "filename": "", "domain": "coding", "sub_category": ""},
            {"id": "a3", "filename": "f3.md", "domain": "coding", "sub_category": ""},
        ]

        def _resolve(filename, domain):
            # Only the first artifact has a resolvable source file on disk.
            return Path("/archive/f1.md") if filename == "f1.md" else None

        with (
            patch("app.routers.kb_admin.list_artifacts", return_value=artifacts),
            patch("app.routers.kb_admin._resolve_archive_source", side_effect=_resolve),
            patch(
                "app.services.ingestion.ingest_file",
                new_callable=AsyncMock,
                return_value={"status": "updated"},
            ) as mock_ingest,
            patch(
                "app.routers.kb_admin.invalidate_cache_non_blocking",
                new_callable=AsyncMock,
            ),
        ):
            res = client.post("/admin/kb/reindex", json={"limit": 3})

        assert res.status_code == 200
        data = res.json()
        assert data["requested"] == 3
        assert data["reindexed"] == 1
        assert data["skipped"] == 2  # one no-filename, one no source file
        assert data["errors"] == 0
        # The one re-index went through the force_reindex path.
        assert mock_ingest.call_count == 1
        assert mock_ingest.call_args.kwargs["force_reindex"] is True
        # A full page (requested == limit) advances the resumable cursor.
        assert data["next_offset"] == 3

    def test_short_page_ends_pagination(self, client: TestClient):
        artifacts = [{"id": "a1", "filename": "", "domain": "coding", "sub_category": ""}]
        with (
            patch("app.routers.kb_admin.list_artifacts", return_value=artifacts),
            patch("app.routers.kb_admin._resolve_archive_source", return_value=None),
            patch(
                "app.routers.kb_admin.invalidate_cache_non_blocking",
                new_callable=AsyncMock,
            ),
        ):
            res = client.post("/admin/kb/reindex", json={"limit": 25})
        assert res.status_code == 200
        data = res.json()
        # requested (1) < limit (25) → no more pages.
        assert data["next_offset"] is None
        assert data["skipped"] == 1

    def test_per_artifact_error_does_not_abort_batch(self, client: TestClient):
        from pathlib import Path

        artifacts = [
            {"id": "a1", "filename": "f1.md", "domain": "coding", "sub_category": ""},
            {"id": "a2", "filename": "f2.md", "domain": "coding", "sub_category": ""},
        ]

        def _ingest(*_a, **_k):
            if _k.get("file_path", "").endswith("f1.md"):
                raise RuntimeError("parse boom")
            return {"status": "updated"}

        with (
            patch("app.routers.kb_admin.list_artifacts", return_value=artifacts),
            patch(
                "app.routers.kb_admin._resolve_archive_source",
                side_effect=lambda fn, d: Path(f"/archive/{fn}"),
            ),
            patch(
                "app.services.ingestion.ingest_file",
                new_callable=AsyncMock,
                side_effect=_ingest,
            ),
            patch(
                "app.routers.kb_admin.invalidate_cache_non_blocking",
                new_callable=AsyncMock,
            ),
        ):
            res = client.post("/admin/kb/reindex", json={"limit": 2})

        assert res.status_code == 200
        data = res.json()
        assert data["errors"] == 1
        assert data["reindexed"] == 1  # a2 still succeeded


class TestReembedEndpoint:
    """POST /admin/kb/reembed — enqueues the managed re-embed job (Phase 4.4).

    ``RedisJobQueue`` is imported locally inside the endpoint (mirrors the
    semantic-cache invalidation hook's local-import pattern elsewhere in
    this router), so it's patched at its home module
    ``app.db.redis.processor_queue``, not at ``app.routers.kb_admin``.
    """

    def _mock_queue(self, job_id):
        mock_cls = MagicMock()
        mock_cls.return_value.enqueue_if_absent = AsyncMock(return_value=job_id)
        return mock_cls

    def test_enqueues_job_for_domain(self, client: TestClient):
        mock_cls = self._mock_queue("job-123")
        with patch("app.db.redis.processor_queue.RedisJobQueue", mock_cls):
            res = client.post("/admin/kb/reembed", json={"domain": "coding"})

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "enqueued"
        assert data["job_id"] == "job-123"
        assert data["domain"] == "coding"
        mock_cls.return_value.enqueue_if_absent.assert_called_once()

    def test_bodyless_post_defaults_to_all_domains(self, client: TestClient):
        mock_cls = self._mock_queue("job-456")
        with patch("app.db.redis.processor_queue.RedisJobQueue", mock_cls):
            res = client.post("/admin/kb/reembed")

        assert res.status_code == 200
        data = res.json()
        assert data["domain"] is None
        assert "all domains" in data["message"]

    def test_duplicate_call_collapses_to_already_running(self, client: TestClient):
        """enqueue_if_absent returning None means a matching job is already
        pending/running — the endpoint must not report a fake job_id."""
        mock_cls = self._mock_queue(None)
        with patch("app.db.redis.processor_queue.RedisJobQueue", mock_cls):
            res = client.post("/admin/kb/reembed", json={"domain": "coding"})

        assert res.status_code == 200
        data = res.json()
        assert data["status"] == "already_running"
        assert data["job_id"] is None

    def test_unknown_domain_404s(self, client: TestClient):
        res = client.post("/admin/kb/reembed", json={"domain": "not-a-real-domain"})
        assert res.status_code == 404

    def test_force_flag_reflected_in_message(self, client: TestClient):
        mock_cls = self._mock_queue("job-force")
        with patch("app.db.redis.processor_queue.RedisJobQueue", mock_cls):
            res = client.post(
                "/admin/kb/reembed", json={"domain": "coding", "force": True},
            )

        assert res.status_code == 200
        assert "force=true" in res.json()["message"]

    def test_enqueue_failure_returns_500(self, client: TestClient):
        mock_cls = MagicMock()
        mock_cls.return_value.enqueue_if_absent = AsyncMock(
            side_effect=RuntimeError("redis unavailable")
        )
        with patch("app.db.redis.processor_queue.RedisJobQueue", mock_cls):
            res = client.post("/admin/kb/reembed", json={"domain": "coding"})

        assert res.status_code == 500


class TestEmbeddingVersionsEndpoint:
    """GET /admin/kb/embedding-versions — per-domain version distribution.

    ``_domain_version_distribution`` does its own paginated Chroma scan;
    these tests patch it directly rather than building a fake multi-page
    Chroma collection, matching how ``TestKBAggregationHelpers`` mocks the
    Neo4j aggregation helper in ``kb_stats``.
    """

    def test_mixed_corpus_detected(self, client: TestClient):
        with patch(
            "app.routers.kb_admin._domain_version_distribution",
            return_value={"total": 3, "versions": {"v1": 2, "v2": 1}},
        ):
            res = client.get("/admin/kb/embedding-versions", params={"domain": "coding"})

        assert res.status_code == 200
        dist = res.json()["domains"]["coding"]
        assert dist["total"] == 3
        assert dist["versions"] == {"v1": 2, "v2": 1}
        assert dist["mixed"] is True

    def test_single_version_not_mixed(self, client: TestClient):
        import config as cfg

        current = cfg.embedding_version_for_domain("coding")
        with patch(
            "app.routers.kb_admin._domain_version_distribution",
            return_value={"total": 5, "versions": {current: 5}},
        ):
            res = client.get("/admin/kb/embedding-versions", params={"domain": "coding"})

        assert res.json()["domains"]["coding"]["mixed"] is False

    def test_empty_collection_not_mixed(self, client: TestClient):
        with patch(
            "app.routers.kb_admin._domain_version_distribution",
            return_value={"total": 0, "versions": {}},
        ):
            res = client.get("/admin/kb/embedding-versions", params={"domain": "coding"})

        dist = res.json()["domains"]["coding"]
        assert dist["total"] == 0
        assert dist["mixed"] is False

    def test_all_domains_when_domain_omitted(self, client: TestClient):
        import config as cfg

        with patch(
            "app.routers.kb_admin._domain_version_distribution",
            return_value={"total": 0, "versions": {}},
        ):
            res = client.get("/admin/kb/embedding-versions")

        assert res.status_code == 200
        assert set(res.json()["domains"].keys()) == set(cfg.DOMAINS)

    def test_unknown_domain_404s(self, client: TestClient):
        res = client.get("/admin/kb/embedding-versions", params={"domain": "not-a-real-domain"})
        assert res.status_code == 404
