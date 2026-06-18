# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Idempotent-ingest contract: chunk IDs are deterministic from artifact_id.

ingest_content sets `artifact_id = content_hash` (content-addressed), and the
chunker derives chunk IDs as `{artifact_id}_...`. Together with `collection.upsert`
+ `create_artifact` MERGE, re-delivery of identical content overwrites the same
rows instead of duplicating them. This guards the determinism half of that
contract (the end-to-end no-duplicate-chunks behaviour is verified live).
"""
from __future__ import annotations

from utils.chunker import chunk_with_parents

_TEXT = "Cerid idempotency contract sentence number {n}. ".format(n=1) * 400


def test_chunk_ids_are_deterministic_for_a_fixed_artifact_id():
    a = chunk_with_parents(_TEXT, artifact_id="abc123")
    b = chunk_with_parents(_TEXT, artifact_id="abc123")
    assert [c["chunk_id"] for c in a] == [c["chunk_id"] for c in b]
    assert len(a) > 1  # multi-chunk, so the contract covers >1 chunk
    assert all(c["chunk_id"].startswith("abc123") for c in a)


def test_different_artifact_ids_yield_different_chunk_ids():
    a = chunk_with_parents(_TEXT, artifact_id="abc123")
    c = chunk_with_parents(_TEXT, artifact_id="xyz789")
    assert {x["chunk_id"] for x in a}.isdisjoint({x["chunk_id"] for x in c})


def test_content_addressed_artifact_id_is_stable():
    # ingest_content uses _content_hash(content) as artifact_id → re-delivery of
    # identical content yields the same id → the same chunk IDs → upsert.
    from app.services.ingestion import _content_hash

    assert _content_hash("hello world") == _content_hash("hello world")
    assert _content_hash("hello world") != _content_hash("hello worlD")
