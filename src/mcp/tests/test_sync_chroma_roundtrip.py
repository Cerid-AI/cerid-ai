# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Export → import round-trip for the Chroma arm: the restore half of backup.

``import_chroma`` had the same blind spot that let a backup discarding 100% of
the vector store ship green: across the whole suite it appeared exactly once, as
``patch(import_mod, "import_chroma", ...)`` — patched out of existence. Nothing
ever executed it.

That matters more on this side than on export. A broken export is recoverable
while the source data still exists; a broken *restore* is discovered on the day
the data is already gone. These tests therefore assert the pair as a unit: what
``export_chroma`` writes must be exactly what ``import_chroma`` can read back,
including the embeddings, and a failed restore must be distinguishable from an
empty one.
"""
from __future__ import annotations

from typing import Any

import pytest

import app.sync.export as export_mod
import app.sync.import_ as import_mod

V1_MARKER = "/api/v1/collections"
V2_MARKER = "/api/v2/tenants/"


class _Resp:
    def __init__(self, status: int, payload: Any = None):
        self.status_code = status
        self._payload = payload if payload is not None else {}

    def json(self) -> Any:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx

            raise httpx.HTTPStatusError(
                f"HTTP {self.status_code}", request=None, response=self  # type: ignore[arg-type]
            )


class _FakeChromaServer:
    """A chromadb 1.x server just real enough to round-trip through.

    v1 endpoints answer 410 like the real thing, so a regression to the retired
    API surfaces as a failure here rather than as silent data loss.
    """

    import httpx as _real_httpx

    HTTPStatusError = _real_httpx.HTTPStatusError
    RequestError = _real_httpx.RequestError
    TimeoutException = _real_httpx.TimeoutException
    ConnectError = _real_httpx.ConnectError

    def __init__(self, seed_chunks: int = 3, fail_writes: bool = False):
        self.fail_writes = fail_writes
        self.urls: list[str] = []
        # id -> (document, metadata, embedding)
        self.stored: dict[str, tuple[str, dict, list[float]]] = {
            f"chunk-{i}": (f"document body {i}", {"artifact_id": f"a{i}"}, [0.1 * i, 0.2 * i])
            for i in range(seed_chunks)
        }
        self.written: dict[str, tuple[str, dict, list[float]]] = {}

    # --- export side -----------------------------------------------------
    def get(self, url: str, **_kw: Any) -> _Resp:
        self.urls.append(url)
        if V1_MARKER in url:
            return _Resp(410, {"error": "Unimplemented"})
        return _Resp(200, {"id": "coll-1"})

    def post(self, url: str, **kw: Any) -> _Resp:
        self.urls.append(url)
        if V1_MARKER in url:
            return _Resp(410, {"error": "Unimplemented"})
        body = kw.get("json") or {}

        if url.endswith("/add"):
            if self.fail_writes:
                return _Resp(500, {"error": "disk full"})
            for i, cid in enumerate(body.get("ids", [])):
                self.written[cid] = (
                    body["documents"][i], body["metadatas"][i], body["embeddings"][i],
                )
            return _Resp(200, {})

        if url.endswith("/get"):
            # Both sides page through /get; they differ by `include`. Export
            # asks for documents/metadatas/embeddings, import's dedup probe
            # asks for nothing but ids.
            if body.get("offset"):
                return _Resp(200, {"ids": []})  # second page: exhausted
            if body.get("include"):
                ids = list(self.stored)
                return _Resp(200, {
                    "ids": ids,
                    "documents": [self.stored[i][0] for i in ids],
                    "metadatas": [self.stored[i][1] for i in ids],
                    "embeddings": [self.stored[i][2] for i in ids],
                })
            return _Resp(200, {"ids": list(self.written)})

        return _Resp(200, {})


@pytest.fixture
def one_domain(monkeypatch):
    monkeypatch.setattr(export_mod.config, "DOMAINS", ["general"], raising=False)
    monkeypatch.setattr(import_mod.config, "DOMAINS", ["general"], raising=False)


def test_exported_chunks_restore_with_embeddings_intact(tmp_path, monkeypatch, one_domain):
    """The pair must agree on the on-disk schema — embeddings included.

    An embedding lost in the round-trip is silent: the chunk restores, the KB
    reports the right count, and semantic search never matches it again.
    """
    server = _FakeChromaServer(seed_chunks=3)
    monkeypatch.setattr(export_mod, "httpx", server)
    monkeypatch.setattr(import_mod, "httpx", server)

    exported = export_mod.export_chroma(
        chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path)
    )
    assert exported["total_chunks"] == 3, exported

    restored = import_mod.import_chroma(
        chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path), force=True
    )

    assert restored["total_added"] == 3, (
        f"restore did not re-add every exported chunk: {restored}"
    )
    assert not restored["failed_domains"], restored

    for cid, (doc, meta, emb) in server.stored.items():
        assert cid in server.written, f"chunk {cid} never made it back"
        got_doc, got_meta, got_emb = server.written[cid]
        assert got_doc == doc
        assert got_meta == meta
        assert got_emb == emb, (
            f"embedding for {cid} did not survive the round-trip: "
            f"{got_emb} != {emb}"
        )


def test_restore_uses_the_v2_api(tmp_path, monkeypatch, one_domain):
    """A regression to the retired v1 API must fail loudly here."""
    server = _FakeChromaServer(seed_chunks=2)
    monkeypatch.setattr(export_mod, "httpx", server)
    monkeypatch.setattr(import_mod, "httpx", server)

    export_mod.export_chroma(chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path))
    server.urls.clear()
    import_mod.import_chroma(
        chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path), force=True
    )

    assert server.urls, "restore made no HTTP calls at all"
    assert all(V1_MARKER not in u for u in server.urls), (
        f"restore used the retired v1 API: {server.urls}"
    )
    assert any(V2_MARKER in u for u in server.urls), server.urls


def test_failed_restore_is_not_reported_as_an_empty_one(tmp_path, monkeypatch, one_domain):
    """The defect: every batch POST fails and the caller sees a clean zero.

    ``_flush_batch`` swallows its exception and returns 0, so the outer handler
    never fires and ``total_added`` is 0 with no error anywhere in the envelope
    — identical to a restore that genuinely had nothing to do. An operator
    checking a disaster recovery would read that as success.
    """
    export_server = _FakeChromaServer(seed_chunks=3)
    monkeypatch.setattr(export_mod, "httpx", export_server)
    export_mod.export_chroma(chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path))

    failing = _FakeChromaServer(seed_chunks=0, fail_writes=True)
    monkeypatch.setattr(import_mod, "httpx", failing)

    restored = import_mod.import_chroma(
        chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path), force=True
    )

    assert restored["total_added"] == 0
    assert restored["failed_domains"], (
        "a totally failed restore reported no failed_domains — the operator "
        "cannot distinguish it from an empty backup"
    )
    assert "general" in restored["failed_domains"]


def test_chunks_without_embeddings_are_skipped_not_silently_added(
    tmp_path, monkeypatch, one_domain,
):
    """An embedding-less row must count as skipped, never as restored."""
    chroma_dir = tmp_path / "chroma"
    chroma_dir.mkdir(parents=True, exist_ok=True)
    (chroma_dir / f"{import_mod.config.collection_name('general')}.jsonl").write_text(
        '{"id": "no-emb", "document": "d", "metadata": {}}\n'
        '{"id": "with-emb", "document": "d2", "metadata": {}, "embedding": [0.5]}\n',
        encoding="utf-8",
    )

    server = _FakeChromaServer(seed_chunks=0)
    monkeypatch.setattr(import_mod, "httpx", server)

    restored = import_mod.import_chroma(
        chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path), force=True
    )

    assert restored["total_added"] == 1, restored
    assert restored["total_skipped"] == 1, restored
    assert "no-emb" not in server.written
    assert "with-emb" in server.written
