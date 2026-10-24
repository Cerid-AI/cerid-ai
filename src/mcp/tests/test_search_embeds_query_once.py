# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""A multi-domain search embeds its query once (audit F80).

Measured on the live MLX access log (2026-10-10): every search sent the same
query to the embedder 8-9 times, once per domain collection, because each
collection was handed the text and embedded it again.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

import core.agents.query_agent as qa

DOMAINS = ["coding", "general", "finance"]


class _CountingEmbedder:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, texts):
        self.calls.append(list(texts))
        if self.fail:
            raise RuntimeError("Quenchforge embed failed (ConnectTimeout)")
        return [[0.1, 0.2, 0.3] for _ in texts]


class _Collection:
    def __init__(self, name: str, seen: list[dict], embedder: _CountingEmbedder) -> None:
        self.name = name
        self._seen = seen
        self._embedding_function = embedder  # as chromadb's Collection holds it

    def count(self):
        return 5

    def query(self, **kwargs):
        self._seen.append(kwargs)
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}


class _Client:
    def __init__(self, embedder: _CountingEmbedder) -> None:
        self.seen: list[dict] = []
        self.embedder = embedder

    def list_collections(self):
        return [SimpleNamespace(name=f"domain_{d}") for d in DOMAINS]

    def get_collection(self, name):
        # The app's client hands every collection the same embedding function.
        return _Collection(name, self.seen, self.embedder)


def _search(embedder: _CountingEmbedder, client: _Client):
    qa._collection_count_cache.clear()

    async def run():
        qa._domain_failures.set([])
        await qa.multi_domain_query("what is the retry policy", domains=DOMAINS, chroma_client=client)
        return qa.domain_failure_reason(len(DOMAINS))

    with patch("core.retrieval.bm25.is_available", return_value=False):
        return asyncio.run(run())


def test_the_query_is_embedded_once_for_every_domain():
    embedder = _CountingEmbedder()
    client = _Client(embedder)

    _search(embedder, client)

    assert embedder.calls == [["what is the retry policy"]]
    assert len(client.seen) == len(DOMAINS)
    assert all(k.get("query_embeddings") == [[0.1, 0.2, 0.3]] and "query_texts" not in k for k in client.seen)


def test_a_failed_embedding_still_degrades_every_domain():
    embedder = _CountingEmbedder(fail=True)
    client = _Client(embedder)

    reason = _search(embedder, client)

    assert len(embedder.calls) == 1
    assert client.seen == []
    assert reason.startswith(f"Searching {len(DOMAINS)} of {len(DOMAINS)} knowledge-base domains failed")
