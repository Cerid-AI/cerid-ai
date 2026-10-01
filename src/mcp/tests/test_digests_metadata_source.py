# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""/digests reports what the digest writer stored.

The writer posts its metadata to /ingest/structured. Ingest keeps that
metadata on the artifact's chunks in the vector store; the graph node's
``tags`` holds a JSON list of tag names. These tests send the writer's own
payload through stores shaped that way and read it back over the API.
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import app.deps
import config
import config.features
from app.routers.digests import router
from core.agents import daily_digest
from core.agents.daily_digest import DigestResult


class _Stores:
    """The graph and vector stores, holding what an ingest leaves behind."""

    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.chunks: dict[str, dict[str, Any]] = {}

    def ingest(self, payload: dict[str, Any]) -> str:
        artifact_id = f"artifact-{len(self.rows) + 1}"
        chunk_id = f"{artifact_id}_chunk_0"
        self.chunks[chunk_id] = {
            "domain": payload["domain"],
            "artifact_id": artifact_id,
            **payload["metadata"],
        }
        self.rows.insert(0, {
            "id": artifact_id,
            "filename": payload["content"].splitlines()[0],
            "domain_name": payload["domain"],
            "sub_category": None,
            "tags": "[]",
            "keywords": "[]",
            "summary": "",
            "chunk_count": 1,
            "chunk_ids": json.dumps([chunk_id]),
            "ingested_at": payload["metadata"]["generated_at"],
            "recategorized_at": None,
            "quality_score": 0.5,
            "client_source": "daily_digest",
        })
        return artifact_id

    # graph driver
    def session(self) -> _Stores:
        return self

    def __enter__(self) -> _Stores:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def run(self, query: str, **params: Any) -> list[dict[str, Any]]:
        return self.rows[: params["limit"]]

    # vector client
    def get_collection(self, name: str) -> _Stores:
        assert name == config.collection_name("digests")
        return self

    def get(self, ids: list[str], include: list[str]) -> dict[str, Any]:
        found = [i for i in ids if i in self.chunks]
        return {"ids": found, "metadatas": [self.chunks[i] for i in found]}


@pytest.fixture
def stores(monkeypatch):
    fake = _Stores()

    def handle(request: httpx.Request) -> httpx.Response:
        artifact_id = fake.ingest(json.loads(request.content))
        return httpx.Response(200, json={"artifact_id": artifact_id})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient",
        lambda **kw: real_client(transport=httpx.MockTransport(handle), **kw),
    )
    monkeypatch.setattr(app.deps, "get_neo4j", lambda: fake)
    monkeypatch.setattr(app.deps, "get_chroma", lambda: fake)
    monkeypatch.setattr(config.features, "is_feature_enabled", lambda name: True)
    return fake


@pytest.fixture
def client():
    api = FastAPI()
    api.include_router(router)
    return TestClient(api)


def _write_digest(day: str, **fields: Any) -> str:
    result = DigestResult(generated_at=f"{day}T07:00:00+00:00", window_hours=48, **fields)
    artifact_id = asyncio.run(daily_digest._persist(result, "http://cerid.test"))
    assert artifact_id is not None
    return artifact_id


def test_latest_reports_the_counts_the_writer_stored(stores, client):
    artifact_id = _write_digest(
        "2026-09-14", artifact_count=12, flagged_count=2, inbox_urgent_count=3,
    )

    body = client.get("/digests/latest").json()

    assert body["persisted_artifact_id"] == artifact_id
    assert body["generated_at"] == "2026-09-14T07:00:00+00:00"
    assert body["window_hours"] == 48
    assert body["artifact_count"] == 12
    assert body["flagged_count"] == 2
    assert body["inbox_urgent_count"] == 3
    assert body["has_urgent"] is True


def test_digest_is_found_by_its_date(stores, client):
    _write_digest("2026-09-13", artifact_count=4)
    wanted = _write_digest("2026-09-14", artifact_count=9)
    _write_digest("2026-09-15", artifact_count=1)

    body = client.get("/digests/2026-09-14").json()

    assert body is not None
    assert body["persisted_artifact_id"] == wanted
    assert body["artifact_count"] == 9
    assert client.get("/digests/2026-09-01").json() is None


def test_recent_lists_each_digest_with_its_own_counts(stores, client):
    _write_digest("2026-09-13", artifact_count=4)
    _write_digest("2026-09-14", artifact_count=9)

    body = client.get("/digests/recent").json()

    assert [(d["generated_at"][:10], d["artifact_count"]) for d in body] == [
        ("2026-09-14", 9),
        ("2026-09-13", 4),
    ]


def test_unreadable_metadata_is_an_outage_not_a_blank_digest(stores, client, monkeypatch):
    _write_digest("2026-09-14", artifact_count=9)

    def unreachable():
        raise RuntimeError("vector store down")

    monkeypatch.setattr(app.deps, "get_chroma", unreachable)

    assert client.get("/digests/latest").status_code == 503


def test_a_partial_digest_is_served_as_partial_with_its_reason(stores, client):
    _write_digest(
        "2026-09-14", artifact_count=9, partial=True, partial_reason="model_call_failed",
    )

    body = client.get("/digests/latest").json()

    assert body["partial"] is True
    assert body["partial_reason"] == "model_call_failed"
    assert body["artifact_count"] == 9


def test_a_complete_digest_is_served_as_not_partial(stores, client):
    _write_digest("2026-09-14", artifact_count=9)

    body = client.get("/digests/latest").json()

    assert body["partial"] is False
    assert body["partial_reason"] is None


def test_a_digest_saved_before_partial_was_recorded_does_not_claim_either(stores, client):
    _write_digest("2026-09-14", artifact_count=9)
    for chunk in stores.chunks.values():
        chunk.pop("partial", None)

    body = client.get("/digests/latest").json()

    assert body["partial"] is None
    assert body["artifact_count"] == 9


def test_the_writer_records_what_the_reader_reports(stores, client):
    categories = [
        {"domain": "notes", "count": 7, "highlight": "meeting notes"},
        {"domain": "finance", "count": 2, "highlight": ""},
    ]
    _write_digest(
        "2026-09-14",
        digest_id="digest-0914",
        artifact_count=9,
        top_categories=categories,
        action_items=["file the return", "renew the lease"],
    )

    body = client.get("/digests/latest").json()

    assert body["digest_id"] == "digest-0914"
    assert body["top_categories"] == categories
    assert body["has_action_items"] is True


def test_a_digest_with_no_action_items_says_so(stores, client):
    _write_digest("2026-09-14", digest_id="digest-0914", artifact_count=9)

    body = client.get("/digests/latest").json()

    assert body["top_categories"] == []
    assert body["has_action_items"] is False


def test_a_partial_digest_does_not_claim_it_had_no_action_items(stores, client):
    categories = [{"domain": "notes", "count": 7, "highlight": ""}]
    _write_digest(
        "2026-09-14",
        digest_id="digest-0914",
        artifact_count=7,
        top_categories=categories,
        partial=True,
        partial_reason="model_call_failed",
    )

    body = client.get("/digests/latest").json()

    assert body["has_action_items"] is None
    assert body["top_categories"] == categories
    assert body["digest_id"] == "digest-0914"


def test_every_metadata_value_the_writer_posts_is_a_string(stores, client):
    _write_digest(
        "2026-09-14",
        digest_id="digest-0914",
        top_categories=[{"domain": "notes", "count": 7, "highlight": ""}],
        action_items=["file the return"],
    )

    (chunk,) = stores.chunks.values()
    posted = {k: v for k, v in chunk.items() if k not in ("domain", "artifact_id")}
    assert {k: type(v) for k, v in posted.items()} == dict.fromkeys(posted, str)
