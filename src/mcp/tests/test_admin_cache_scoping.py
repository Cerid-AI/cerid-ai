# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Admin-path cache invalidation must be scoped to the domain a mutation
actually touched, mirroring the ingest routes' ``domain=`` contract
(``utils.query_cache.invalidate_query_caches`` / ``_threaded`` /
``_non_blocking``). Before this, ``app/routers/kb_admin.py``,
``app/routers/artifacts.py``, and ``app/services/content_lifecycle.py`` each
called the unscoped flush on every mutation, including ones limited to a
single known domain — pinning ``cache_hit_rate`` at 0.0 the same way the
unscoped ingest paths did (see ``test_ingest_cache_scoping.py``).

Truly global mutations (rebuild-index, the test-residue sweep) and paths that
cannot learn an artifact's domain without an extra store read (``hide_content``)
deliberately keep the full flush — this file also pins that behavior so a
future change doesn't silently narrow a global mutation's cache bust.
"""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import fakeredis
import httpx
import pytest
from fastapi.testclient import TestClient

from utils.query_cache import (
    get_cached,
    invalidate_by_domain,
    invalidate_query_caches,
    set_cached,
)


def _reset_rate_limiter(app) -> None:
    """Clear the shared RateLimitMiddleware sliding window — see
    ``test_kb_admin.py``'s identical helper for why this is needed."""
    from app.middleware.rate_limit import RateLimitMiddleware

    obj = getattr(app, "middleware_stack", None)
    while obj is not None:
        if isinstance(obj, RateLimitMiddleware):
            obj._hits.clear()
            obj._locks.clear()
            return
        obj = getattr(obj, "app", None)


@pytest.fixture()
def app():
    from app.main import app as real_app

    _reset_rate_limiter(real_app)
    return real_app


@pytest.fixture()
def redis_cache():
    """Real (fake) Redis backing C1 so domain-scoped eviction is exercised
    for real, not just asserted via mock call args — same pattern as
    ``test_ingest_cache_scoping.py``."""
    redis = fakeredis.FakeRedis(decode_responses=True)
    with patch("utils.query_cache.get_redis", return_value=redis):
        # Retire the one-time legacy-sweep fallback so a domain-scoped
        # invalidation in this test evicts by index rather than full-flushing.
        invalidate_by_domain("_warmup_")
        yield redis


async def _drain_background_tasks() -> None:
    for _ in range(5):
        pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
        if not pending:
            return
        await asyncio.wait(pending, timeout=2)


async def _post(app, path: str, json: dict | None = None) -> httpx.Response:
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(path, json=json or {})


# --------------------------------------------------------------------------- #
# 1. clear-domain — scoped to the cleared domain
# --------------------------------------------------------------------------- #
class TestClearDomainCacheScoping:
    async def test_clear_domain_leaves_other_domain_cache_hot(self, app, redis_cache):
        """A clear-domain of ``finance`` must leave a C1 entry that searched
        only ``coding`` intact (the controller-gate check for this task)."""
        set_cached("coding q", "coding", 10, {"answer": "c"}, domains_searched=["coding"])

        with (
            patch(
                "app.routers.kb_admin.delete_artifacts_by_domain",
                return_value={"deleted": 1, "chunks": 2},
            ),
            patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
            patch("app.routers.kb_admin.get_chroma", return_value=MagicMock()),
        ):
            resp = await _post(app, "/admin/kb/clear-domain/finance", {"confirm": True})

        assert resp.status_code == 200, resp.text
        await _drain_background_tasks()

        kept = get_cached("coding q", "coding", 10)
        assert kept is not None and kept["answer"] == "c"

    async def test_clear_domain_scopes_semantic_cache_too(self, app, redis_cache):
        """C2 (semantic cache) must receive the same domain, not a full flush."""
        with (
            patch(
                "app.routers.kb_admin.delete_artifacts_by_domain",
                return_value={"deleted": 1, "chunks": 0},
            ),
            patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
            patch("app.routers.kb_admin.get_chroma", return_value=MagicMock()),
            patch("core.retrieval.semantic_cache.invalidate_cache") as mock_sem,
        ):
            resp = await _post(app, "/admin/kb/clear-domain/finance", {"confirm": True})

        assert resp.status_code == 200, resp.text
        await _drain_background_tasks()

        mock_sem.assert_called_once()
        args, kwargs = mock_sem.call_args
        assert args[1] == "kb_admin.clear_domain"  # trigger is positional here
        assert kwargs.get("domain") == "finance"


# --------------------------------------------------------------------------- #
# 2. artifact delete (content_lifecycle.remove_content) — scoped to the
#    deleted artifact's domain, which delete_artifact already returns.
# --------------------------------------------------------------------------- #
class TestArtifactDeleteCacheScoping:
    async def test_delete_known_domain_leaves_other_domain_cache_hot(self, redis_cache):
        from app.services import content_lifecycle

        set_cached("coding q", "coding", 10, {"answer": "c"}, domains_searched=["coding"])

        with (
            patch(
                "app.db.neo4j.artifacts.delete_artifact",
                return_value={"deleted": True, "domain": "finance", "chunk_ids": ["c1"]},
            ),
            patch.object(content_lifecycle, "bm25"),
            patch.object(content_lifecycle, "sparse_index"),
            # invalidate_caches fires the bust on a daemon thread; run it
            # synchronously here so the assertion below isn't racing it —
            # a full-flush mutant must fail this test, not slip past it.
            patch(
                "utils.query_cache.invalidate_query_caches_threaded",
                side_effect=invalidate_query_caches,
            ),
        ):
            result = content_lifecycle.remove_content(
                "art-1", neo4j=MagicMock(), chroma=MagicMock(), redis=redis_cache,
            )

        assert result.found is True
        assert result.domain == "finance"

        kept = get_cached("coding q", "coding", 10)
        assert kept is not None and kept["answer"] == "c"


# --------------------------------------------------------------------------- #
# 3. purge-test-residue (truly global) — must still flush every domain.
# --------------------------------------------------------------------------- #
class TestTrulyGlobalMutationsStayFullFlush:
    async def test_purge_test_residue_flushes_all_domains(self, app, redis_cache):
        set_cached("coding q", "coding", 10, {"answer": "c"}, domains_searched=["coding"])
        set_cached("finance q", "finance", 10, {"answer": "f"}, domains_searched=["finance"])

        applied = {
            "artifacts_found": 1, "artifacts_purged": 1,
            "entities_found": 0, "entities_purged": 0,
            "skipped_in_grace": 0, "samples": [],
        }
        with (
            patch("app.services.kb_hygiene.sweep_test_residue", return_value=applied),
            patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
            patch("app.routers.kb_admin.get_chroma", return_value=MagicMock()),
        ):
            resp = await _post(app, "/admin/kb/purge-test-residue", {"confirm": True})

        assert resp.status_code == 200, resp.text
        await _drain_background_tasks()

        assert get_cached("coding q", "coding", 10) is None
        assert get_cached("finance q", "finance", 10) is None

    async def test_rebuild_indexes_flushes_all_domains(self, app, redis_cache):
        set_cached("coding q", "coding", 10, {"answer": "c"}, domains_searched=["coding"])
        set_cached("finance q", "finance", 10, {"answer": "f"}, domains_searched=["finance"])

        with patch("app.routers.kb_admin.rebuild_bm25_all", return_value=2):
            resp = await _post(app, "/admin/kb/rebuild-index")

        assert resp.status_code == 200, resp.text
        await _drain_background_tasks()

        assert get_cached("coding q", "coding", 10) is None
        assert get_cached("finance q", "finance", 10) is None


# --------------------------------------------------------------------------- #
# 4. reingest_artifact (kb_admin) — scoped to the reingested artifact's domain.
# --------------------------------------------------------------------------- #
class TestReingestArtifactCacheScoping:
    def test_reingest_scopes_to_artifact_domain(self):
        with (
            patch(
                "app.routers.kb_admin.get_artifact",
                return_value={"filename": "f.md", "domain": "finance", "sub_category": ""},
            ),
            patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
            patch("pathlib.Path.exists", return_value=True),
            patch(
                "app.services.ingestion.ingest_file",
                new_callable=AsyncMock,
                return_value={"status": "success", "artifact_id": "art-1", "domain": "finance"},
            ),
            patch("app.routers.kb_admin._invalidate_scoped_safe") as mock_bust,
            patch("app.routers.kb_admin.get_redis", return_value=MagicMock()),
        ):
            client = TestClient(_app(), raise_server_exceptions=False)
            res = client.post("/admin/artifacts/art-1/reingest")

        assert res.status_code == 200, res.text
        mock_bust.assert_called_once_with("kb_admin.reingest_artifact", "finance")


# --------------------------------------------------------------------------- #
# 5. reindex_corpus (kb_admin) — scoped when a domain filter is given,
#    unscoped (full flush) when it is not (spans whatever the batch touched).
# --------------------------------------------------------------------------- #
class TestReindexCorpusCacheScoping:
    def test_domain_filter_scopes_invalidation(self):
        artifacts = [{"id": "a1", "filename": "", "domain": "finance", "sub_category": ""}]
        with (
            patch("app.routers.kb_admin.list_artifacts", return_value=artifacts),
            patch("app.routers.kb_admin._resolve_archive_source", return_value=None),
            patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
            patch("app.routers.kb_admin._invalidate_scoped_safe") as mock_bust,
            patch("app.routers.kb_admin.get_redis", return_value=MagicMock()),
        ):
            client = TestClient(_app(), raise_server_exceptions=False)
            res = client.post("/admin/kb/reindex", json={"domain": "finance", "limit": 5})

        assert res.status_code == 200, res.text
        mock_bust.assert_called_once()
        args, _ = mock_bust.call_args
        assert args[1] == "finance"

    def test_no_domain_filter_keeps_full_flush(self):
        artifacts = [{"id": "a1", "filename": "", "domain": "finance", "sub_category": ""}]
        with (
            patch("app.routers.kb_admin.list_artifacts", return_value=artifacts),
            patch("app.routers.kb_admin._resolve_archive_source", return_value=None),
            patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
            patch("app.routers.kb_admin._invalidate_scoped_safe") as mock_bust,
            patch("app.routers.kb_admin.get_redis", return_value=MagicMock()),
        ):
            client = TestClient(_app(), raise_server_exceptions=False)
            res = client.post("/admin/kb/reindex", json={"limit": 5})

        assert res.status_code == 200, res.text
        mock_bust.assert_called_once()
        args, _ = mock_bust.call_args
        assert args[1] is None


# --------------------------------------------------------------------------- #
# 6. repair_collection (kb_admin) — scoped to the collection's derived domain.
# --------------------------------------------------------------------------- #
class TestRepairCollectionCacheScoping:
    def test_repair_scopes_to_derived_domain(self, tmp_path, monkeypatch):
        import config

        monkeypatch.setattr(config, "DATA_DIR", str(tmp_path), raising=False)

        coll = MagicMock()
        coll.name = "domain_finance"
        coll.peek = MagicMock(return_value={"embeddings": [[0.0] * 8]})
        coll.get = MagicMock(return_value={
            "ids": ["doc_1"],
            "documents": ["doc text"],
            "metadatas": [{"filename": "note.txt", "domain": "finance"}],
        })
        chroma = MagicMock()
        chroma.get_collection.return_value = coll
        chroma.get_or_create_collection.return_value = coll

        with (
            patch("app.routers.kb_admin.get_chroma", return_value=chroma),
            patch("app.routers.kb_admin.get_neo4j", return_value=MagicMock()),
            patch("app.routers.kb_admin.get_redis", return_value=MagicMock()),
            patch("app.routers.kb_admin.count_artifacts", return_value=1),
            patch("core.utils.embeddings.get_embedding_dim", return_value=8),
            patch(
                "app.services.ingestion.ingest_content",
                return_value={"status": "success", "chunks": 1},
            ),
            patch("app.routers.kb_admin._invalidate_scoped_safe") as mock_bust,
        ):
            client = TestClient(_app(), raise_server_exceptions=False)
            res = client.post(
                "/admin/collections/repair",
                json={"collection_name": "domain_finance", "dry_run": False},
            )

        assert res.status_code == 200, res.text
        mock_bust.assert_called_once_with("kb_admin.repair_collection", "finance")


def _app():
    from app.main import app as real_app

    _reset_rate_limiter(real_app)
    return real_app


# --------------------------------------------------------------------------- #
# 7. artifacts.py recategorize — invalidates BOTH the old and new domain
#    (the artifact left one and joined the other), nothing else.
# --------------------------------------------------------------------------- #
class TestRecategorizeCacheScoping:
    def test_recategorize_invalidates_old_and_new_domain(self):
        import json as _json

        from app.routers import artifacts

        artifact = {"domain": "coding", "chunk_ids": _json.dumps(["c1"]), "filename": "f.md"}
        source = MagicMock()
        source.get.return_value = {
            "ids": ["c1"], "documents": ["d1"], "metadatas": [{"domain": "coding"}],
        }
        dest = MagicMock()
        dest.get.return_value = {"ids": ["c1"]}
        chroma = MagicMock()
        chroma.get_or_create_collection.side_effect = [source, dest]

        with (
            patch.object(artifacts, "get_neo4j", return_value=MagicMock()),
            patch.object(artifacts, "get_chroma", return_value=chroma),
            patch.object(artifacts.graph, "get_artifact", return_value=artifact),
            patch.object(
                artifacts.graph, "recategorize_artifact",
                return_value={"old_domain": "coding", "new_domain": "finance"},
            ),
            patch.object(artifacts, "get_redis", return_value=MagicMock()),
            patch.object(artifacts.cache, "log_event"),
            patch("utils.query_cache.invalidate_query_caches") as mock_invalidate,
        ):
            artifacts.recategorize("art-1", "finance")

        domains_invalidated = {kw.get("domain") for _, kw in mock_invalidate.call_args_list}
        assert domains_invalidated == {"coding", "finance"}
        for _, kw in mock_invalidate.call_args_list:
            assert kw.get("trigger") == "artifacts.recategorize"


# --------------------------------------------------------------------------- #
# 8. artifacts.py feedback — invalidates only the artifact's own domain
#    (already fetched — no extra store read needed).
# --------------------------------------------------------------------------- #
class TestArtifactFeedbackCacheScoping:
    def test_feedback_scopes_to_artifact_domain(self):
        from app.routers import artifacts

        artifact = {"quality_score": 0.5, "domain": "finance"}
        driver = MagicMock()
        with (
            patch.object(artifacts, "get_neo4j", return_value=driver),
            patch.object(artifacts.graph, "get_artifact", return_value=artifact),
            patch.object(artifacts, "get_redis", return_value=MagicMock()),
            patch.object(artifacts.cache, "log_event"),
            patch("utils.query_cache.invalidate_query_caches") as mock_invalidate,
        ):
            client = TestClient(_app(), raise_server_exceptions=False)
            res = client.post(
                "/artifacts/art-1/feedback", json={"signal": "inject", "query": "q"},
            )

        assert res.status_code == 200, res.text
        mock_invalidate.assert_called_once()
        _, kwargs = mock_invalidate.call_args
        assert kwargs.get("trigger") == "artifacts.feedback"
        assert kwargs.get("domain") == "finance"


# --------------------------------------------------------------------------- #
# 9. content_lifecycle.remove_orphan_chunks — scoped to the caller-supplied
#    domain (a required parameter, always known).
# --------------------------------------------------------------------------- #
class TestRemoveOrphanChunksCacheScoping:
    def test_scopes_to_supplied_domain(self, redis_cache):
        from app.services import content_lifecycle

        set_cached("coding q", "coding", 10, {"answer": "c"}, domains_searched=["coding"])

        with (
            patch.object(content_lifecycle, "bm25"),
            patch.object(content_lifecycle, "sparse_index"),
            # Same race as the artifact-delete test above — force the bust
            # synchronous so the assertion observes its real outcome.
            patch(
                "utils.query_cache.invalidate_query_caches_threaded",
                side_effect=invalidate_query_caches,
            ),
        ):
            content_lifecycle.remove_orphan_chunks(
                ["c1"], "finance", chroma=MagicMock(), redis=redis_cache,
            )

        kept = get_cached("coding q", "coding", 10)
        assert kept is not None and kept["answer"] == "c"


# --------------------------------------------------------------------------- #
# 10. content_lifecycle.hide_content — domain is NOT known without an extra
#     Neo4j read (``set_archived`` returns only a bool); per the task brief,
#     this stays a full flush rather than adding that read.
# --------------------------------------------------------------------------- #
class TestHideContentStaysFullFlush:
    def test_hide_content_flushes_without_domain(self):
        from app.services import content_lifecycle

        with patch("utils.query_cache.invalidate_query_caches_threaded") as mock_bust:
            content_lifecycle.hide_content("art-1", neo4j=MagicMock())

        mock_bust.assert_called_once()
        _, kwargs = mock_bust.call_args
        assert kwargs.get("domain") is None

