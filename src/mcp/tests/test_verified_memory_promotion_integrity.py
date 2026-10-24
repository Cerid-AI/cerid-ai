# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""What ``promote_verified_facts`` reports, and what it hands on, must be true.

Only the stores are faked: a Chroma client, a Neo4j driver that records what
is run against it, and nothing inside the promoter itself.
"""
from __future__ import annotations

import asyncio
from typing import Any

from core.agents.verified_memory import promote_verified_facts


class _Collection:
    def __init__(self, add_error: Exception | None = None) -> None:
        self.add_error = add_error
        self.added: list[dict[str, Any]] = []

    def query(self, **_kw: Any) -> dict[str, Any]:
        return {"ids": [[]], "documents": [[]], "metadatas": [[]], "distances": [[]]}

    def upsert(self, **kw: Any) -> None:
        if self.add_error is not None:
            raise self.add_error
        self.added.append(kw)


class _Chroma:
    def __init__(self, collection: _Collection) -> None:
        self.collection = collection

    def get_or_create_collection(self, name: str) -> _Collection:
        return self.collection


class _Session:
    def __init__(self, runs: list[tuple[str, dict[str, Any]]], exists: bool = False) -> None:
        self._runs = runs
        self._exists = exists

    def __enter__(self) -> "_Session":
        return self

    def __exit__(self, *exc: Any) -> bool:
        return False

    def run(self, cypher: str, **params: Any) -> "_Session":
        self._runs.append((cypher, params))
        return self

    def single(self) -> Any:
        last = self._runs[-1][0] if self._runs else ""
        if "RETURN count(m) AS n" in last:
            return {"n": 1 if self._exists else 0}
        return None


class _Driver:
    def __init__(self) -> None:
        self.runs: list[tuple[str, dict[str, Any]]] = []
        self.exists = False

    def session(self) -> _Session:
        return _Session(self.runs, self.exists)


_KB_CLAIM = {
    "claim": "The relay service listens on port 7443",
    "status": "verified",
    "similarity": 0.94,
    "claim_type": "factual",
    "nli_entailment": 0.92,
    "source_artifact_id": "art-relay",
    "verification_method": "kb_nli",
}


# The envelope every external-verification path returns
# (hallucination/verification.py::_external_verdict): URLs, no artifact.
_EXTERNAL_CLAIM = {
    "claim": "Tokyo has a population of about 14 million people",
    "status": "verified",
    "similarity": 0.93,
    "reason": "confirmed by the cited page",
    "verification_method": "web_search",
    "verification_model": "placeholder-model",
    "source_urls": ["https://example.org/tokyo", "https://example.org/japan"],
    # The promoter requires an entailment score; without one the claim is
    # skipped_no_entailment and this test would exercise nothing.
    "nli_entailment": 0.9,
}


def _promote(claim: dict[str, Any], chroma: _Chroma, driver: _Driver, create_fn: Any, cid: str = "") -> dict[str, int]:
    return asyncio.run(promote_verified_facts(
        {"conversation_id": cid, "claims": [claim]},
        chroma, driver, create_memory_fn=create_fn,
    ))


def test_a_memory_whose_vector_write_failed_is_not_counted_promoted():
    """Recall reads the vector store, so a memory that never reached it
    cannot be recalled and was not promoted."""
    collection = _Collection(add_error=RuntimeError("vector store unavailable"))

    counts = _promote(_KB_CLAIM, _Chroma(collection), _Driver(), lambda _d, _m: "mem-1")

    assert counts["promoted"] == 0, counts
    assert counts["errors"] == 1, counts


def test_a_memory_written_to_both_stores_is_counted_promoted():
    collection = _Collection()

    counts = _promote(_KB_CLAIM, _Chroma(collection), _Driver(), lambda _d, _m: "mem-1")

    assert len(collection.added) == 1
    assert counts["promoted"] == 1, counts
    assert counts["errors"] == 0, counts


def test_an_externally_verified_memory_keeps_its_source_urls():
    """A claim verified against the web has no source artifact; the URLs are
    its only provenance and must reach the stored memory node."""
    from app.db.neo4j.memory import create_memory_node

    driver = _Driver()

    counts = _promote(_EXTERNAL_CLAIM, _Chroma(_Collection()), driver, create_memory_node)

    assert counts["promoted"] == 1, counts
    stored = [
        params for cypher, params in driver.runs
        if "Memory" in cypher and _EXTERNAL_CLAIM["source_urls"] in params.values()
    ]
    assert stored, (
        "no write to the memory node carried the source URLs: "
        f"{[params for _c, params in driver.runs]}"
    )


def test_a_claim_is_one_memory_wherever_it_is_promoted():
    """Forget spec §6.3: the id comes from the claim, the row is {id}_{h16},
    and the memory names the conversation it came from."""
    from core.retrieval.artifact_rows import verified_memory_id

    collection = _Collection()
    seen: list[dict[str, Any]] = []
    counts = _promote(_KB_CLAIM, _Chroma(collection), _Driver(), lambda _d, m: seen.append(m) or m["id"], cid="conv-1")
    assert counts["promoted"] == 1
    memory_id = verified_memory_id(_KB_CLAIM["claim"])
    assert seen[0]["id"] == memory_id and seen[0]["conversation_id"] == "conv-1"
    row_id = collection.added[0]["ids"][0]
    assert row_id.startswith(f"{memory_id}_") and len(row_id) == len(memory_id) + 17
    assert collection.added[0]["metadatas"][0]["conversation_id"] == "conv-1"


def test_a_claim_the_person_forgot_is_not_promoted_again(monkeypatch):
    from core.retrieval.artifact_rows import verified_memory_id

    gone = verified_memory_id(_KB_CLAIM["claim"])
    monkeypatch.setattr("core.forget.registry.is_forgotten", lambda kind, i: kind == "memory" and i == gone)
    collection = _Collection()
    counts = _promote(_KB_CLAIM, _Chroma(collection), _Driver(), lambda _d, m: m["id"])
    assert counts["promoted"] == 0 and counts["skipped_forgotten"] == 1 and not collection.added


def test_a_claim_whose_memory_exists_is_not_promoted_again():
    """Its memory may have been superseded or merged since; a fresh row would
    bring it back as current."""
    driver = _Driver()
    driver.exists = True
    collection = _Collection()
    counts = _promote(_KB_CLAIM, _Chroma(collection), driver, lambda _d, m: m["id"])
    assert counts["promoted"] == 0 and counts["skipped_duplicate"] == 1 and not collection.added
