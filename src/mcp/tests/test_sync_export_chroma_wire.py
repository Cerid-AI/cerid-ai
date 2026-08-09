"""``export_chroma`` exercised against a real HTTP wire (fake transport).

Mutation testing on 2026-07-29 proved this module had **no** effective coverage:
four separate faults were injected — reverting to the retired v1 API, treating
any HTTP error as "collection missing", dropping ``failed_domains``, and never
counting chunks — and the entire suite stayed green for all four. The one test
touching this path (``test_sync_full_surface.py``) patches ``export_chroma``
out wholesale, so the HTTP call was never made.

That is exactly how a backup that silently discarded 100% of the vector store
shipped and survived review.

These tests stub ``httpx`` at the module boundary and assert the wire contract:
the URL actually requested, the chunk accounting, and — critically — that a
failure is *reported* rather than reduced to a success-shaped empty export.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

import app.sync.export as export_mod

# chromadb 1.x scopes collections under tenant + database. The 0.5-era
# /api/v1/collections endpoints answer 410 Gone.
V2_MARKER = "/api/v2/tenants/"
V1_MARKER = "/api/v1/collections"


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


class _FakeHttpx:
    """Minimal chromadb 1.x server: v2 works, v1 answers 410 like the real one.

    Stands in for the whole ``httpx`` module, so it must also re-export the
    exception types ``export.py`` catches.
    """

    import httpx as _real_httpx

    HTTPStatusError = _real_httpx.HTTPStatusError
    RequestError = _real_httpx.RequestError
    TimeoutException = _real_httpx.TimeoutException
    ConnectError = _real_httpx.ConnectError

    def __init__(self, chunks_per_domain: int = 2, fail_all: bool = False):
        self.chunks_per_domain = chunks_per_domain
        self.fail_all = fail_all
        self.urls: list[str] = []

    def get(self, url: str, **_kw: Any) -> _Resp:
        self.urls.append(url)
        if V1_MARKER in url:
            return _Resp(410, {"error": "Unimplemented", "message": "v1 is deprecated"})
        if self.fail_all:
            return _Resp(500, {})
        return _Resp(200, {"id": "coll-123"})

    def post(self, url: str, **kw: Any) -> _Resp:
        self.urls.append(url)
        if V1_MARKER in url:
            return _Resp(410, {"error": "Unimplemented"})
        offset = (kw.get("json") or {}).get("offset", 0)
        if offset or self.chunks_per_domain == 0:
            return _Resp(200, {"ids": []})  # second page: exhausted
        n = self.chunks_per_domain
        return _Resp(200, {
            "ids": [f"c{i}" for i in range(n)],
            "documents": [f"doc {i}" for i in range(n)],
            "metadatas": [{"artifact_id": f"a{i}"} for i in range(n)],
            "embeddings": [[0.1, 0.2] for _ in range(n)],
        })


@pytest.fixture
def one_domain(monkeypatch):
    """Constrain the export to a single domain for deterministic counts."""
    monkeypatch.setattr(export_mod.config, "DOMAINS", ["general"], raising=False)


def test_export_targets_the_v2_api_not_the_retired_v1(tmp_path, monkeypatch, one_domain):
    """Reverting to /api/v1/collections must fail this test."""
    fake = _FakeHttpx()
    monkeypatch.setattr(export_mod, "httpx", fake)

    result = export_mod.export_chroma(
        chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path)
    )

    assert fake.urls, "export made no HTTP calls at all"
    assert all(V1_MARKER not in u for u in fake.urls), (
        f"export used the retired v1 API: {fake.urls}"
    )
    assert any(V2_MARKER in u for u in fake.urls), (
        f"export did not use the v2 tenant/database path: {fake.urls}"
    )
    assert result["total_chunks"] == 2


def test_chunks_are_counted_and_written(tmp_path, monkeypatch, one_domain):
    """Guards the accounting: a stuck counter must fail."""
    monkeypatch.setattr(export_mod, "httpx", _FakeHttpx(chunks_per_domain=3))

    result = export_mod.export_chroma(
        chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path)
    )

    assert result["total_chunks"] == 3
    assert result["domains"]["general"] == 3

    written = list((tmp_path / "chroma").glob("*.jsonl"))
    assert written, "no JSONL produced"
    rows = [json.loads(x) for x in written[0].read_text().splitlines() if x.strip()]
    assert len(rows) == 3
    assert rows[0]["document"] == "doc 0"
    assert rows[0]["embedding"] == [0.1, 0.2], "embeddings must round-trip"


def test_failure_is_reported_not_silently_swallowed(tmp_path, monkeypatch, one_domain):
    """A failed export must not look like a successful empty one.

    This is the shipped defect: every domain errored, ``total_chunks`` stayed 0,
    and the caller received a success-shaped dict.
    """
    monkeypatch.setattr(export_mod, "httpx", _FakeHttpx(fail_all=True))

    result = export_mod.export_chroma(
        chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path)
    )

    assert result["total_chunks"] == 0
    assert result["failed_domains"], (
        "a total export failure reported no failed_domains — callers cannot "
        "distinguish it from an empty knowledge base"
    )
    assert "general" in result["failed_domains"]


def test_v1_response_is_treated_as_failure_not_missing_collection(tmp_path, monkeypatch):
    """410 (v1 retired) must be a reported failure, not a silent skip.

    Widening the not-found guard to ``status_code >= 400`` would convert silent
    data loss into silent *skip* — still no vectors, still no signal.
    """
    monkeypatch.setattr(export_mod.config, "DOMAINS", ["general"], raising=False)

    class _V1OnlyHttpx(_FakeHttpx):
        def get(self, url: str, **_kw: Any) -> _Resp:
            self.urls.append(url)
            return _Resp(410, {"error": "Unimplemented"})

    monkeypatch.setattr(export_mod, "httpx", _V1OnlyHttpx())

    result = export_mod.export_chroma(
        chroma_url="http://chroma.test:8000", sync_dir=str(tmp_path)
    )

    assert result["total_chunks"] == 0
    assert result["failed_domains"], "410 was swallowed as 'collection missing'"
