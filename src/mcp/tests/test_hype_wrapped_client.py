# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""HyPE retrieval against the client the server actually hands it.

``get_chroma()`` returns ``app.deps._EmbeddingAwareClient``, whose
``get_collection`` accepted keyword arguments only, while ``_augment_with_hype``
and ``_hydrate_hype_hits`` call it with ``name`` positionally, as chromadb's own
``ClientAPI.get_collection(name, ...)`` allows. Every HyPE query raised
``TypeError: takes 1 positional argument but 2 were given``, was swallowed per
domain, and retrieval ran without HyPE. The existing HyPE tests used a fake
client whose ``get_collection(name)`` accepted the positional call, so the
mismatch never reached a test. These run the real path against the real
wrapper around a real in-process Chroma.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

import chromadb
import numpy as np
import pytest

_VEC = [0.1, 0.2, 0.3]


class _ConstantEF:
    """Same output shape as ``OnnxEmbeddingFunction``: one float32 ndarray per text."""

    def __call__(self, input: Any) -> Any:  # noqa: A002 — chromadb's parameter name
        return [np.asarray(_VEC, dtype=np.float32) for _ in input]

    @staticmethod
    def name() -> str:
        return "cerid-test-constant"

    def get_config(self) -> dict[str, Any]:
        return {}


@pytest.fixture
def raw_chroma(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from core.utils import embeddings as emb

    ef = _ConstantEF()
    monkeypatch.setattr(emb, "get_embedding_function", lambda: ef)
    client = chromadb.PersistentClient(path=str(tmp_path / "chroma"))
    client._test_ef = ef  # type: ignore[attr-defined]
    return client


@pytest.mark.asyncio
async def test_hype_hits_are_returned_through_the_embedding_aware_client(
    raw_chroma: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import config
    from app.deps import _EmbeddingAwareClient
    from core.agents import query_agent
    from core.retrieval.hype_index import hype_collection_name

    monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
    ef = raw_chroma._test_ef
    base_name = config.collection_name("general")
    base = raw_chroma.get_or_create_collection(name=base_name, embedding_function=ef)
    base.add(
        ids=["chunk-1"],
        documents=["The real body text of the source document."],
        embeddings=[_VEC],
        metadatas=[{"filename": "notes.md", "artifact_id": "art-1",
                    "cerid_state": "committed"}],
    )
    hype = raw_chroma.get_or_create_collection(
        name=hype_collection_name(base_name), embedding_function=ef,
    )
    hype.add(
        ids=["hype-1"],
        documents=["What did the founder decide about pricing?"],
        embeddings=[_VEC],
        metadatas=[{"source_chunk_id": "chunk-1", "source_artifact_id": "art-1"}],
    )

    with patch("core.agents.query_agent.log_swallowed_error") as swallowed:
        merged = await query_agent._augment_with_hype(
            query="pricing decision", results=[],
            chroma_client=_EmbeddingAwareClient(raw_chroma), domains=["general"],
        )

    assert not swallowed.called, swallowed.call_args_list
    assert len(merged) == 1, merged
    assert merged[0]["chunk_id"] == "chunk-1"
    assert merged[0]["content"] == "The real body text of the source document."


@pytest.mark.asyncio
async def test_a_domain_with_no_hype_index_is_not_an_error(
    raw_chroma: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HyPE indexes only chunks ingested with the flag on, so most domains
    have no companion collection. Logging that as a swallowed error put one
    into /health's count on every query."""
    import config
    from app.deps import _EmbeddingAwareClient
    from core.agents import query_agent

    monkeypatch.setenv("RETRIEVAL_HYPE_ENABLED", "true")
    raw_chroma.get_or_create_collection(
        name=config.collection_name("general"), embedding_function=raw_chroma._test_ef,
    )

    with patch("core.agents.query_agent.log_swallowed_error") as swallowed:
        merged = await query_agent._augment_with_hype(
            query="pricing decision", results=[],
            chroma_client=_EmbeddingAwareClient(raw_chroma), domains=["general"],
        )

    assert merged == []
    assert not swallowed.called, swallowed.call_args_list


@pytest.mark.parametrize("method", ["get_collection", "get_or_create_collection"])
def test_wrapper_accepts_name_positionally_and_by_keyword(
    raw_chroma: Any, method: str,
) -> None:
    from app.deps import _EmbeddingAwareClient

    raw_chroma.get_or_create_collection(name="pinned", embedding_function=raw_chroma._test_ef)
    wrapper = _EmbeddingAwareClient(raw_chroma)

    assert getattr(wrapper, method)("pinned").name == "pinned"
    assert getattr(wrapper, method)(name="pinned").name == "pinned"


def test_get_collection_missing_still_raises(raw_chroma: Any) -> None:
    from app.deps import _EmbeddingAwareClient

    with pytest.raises(chromadb.errors.NotFoundError):
        _EmbeddingAwareClient(raw_chroma).get_collection("absent")


@pytest.mark.parametrize("method", ["get_collection", "get_or_create_collection"])
def test_positional_name_still_gets_the_embedding_function(method: str) -> None:
    from app.deps import _EmbeddingAwareClient

    inner = MagicMock()
    fake_ef = MagicMock()
    with patch("core.utils.embeddings.get_embedding_function", return_value=fake_ef):
        getattr(_EmbeddingAwareClient(inner), method)("coll")

    call = getattr(inner, method).call_args
    assert call.kwargs["name"] == "coll"
    assert call.kwargs["embedding_function"] is fake_ef


def test_caller_supplied_embedding_function_is_kept() -> None:
    from app.deps import _EmbeddingAwareClient

    inner = MagicMock()
    mine = MagicMock()
    with patch("core.utils.embeddings.get_embedding_function", return_value=MagicMock()):
        _EmbeddingAwareClient(inner).get_collection("coll", embedding_function=mine)

    assert inner.get_collection.call_args.kwargs["embedding_function"] is mine
