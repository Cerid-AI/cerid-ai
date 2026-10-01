# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The parent-child retrieval setting reaches its readers, and cannot hide content.

Two halves of one switch:

* PATCH ``enable_parent_child_retrieval`` must flip what the ingest and query
  paths actually consult, and what the recommender checks before it nags.
* With the switch on, the retrieval filter must still return chunks stored
  before ``chunk_level`` metadata existed.
"""
from __future__ import annotations

import pytest

import config
import config.features as features_mod
import utils.chunker as chunker_mod
from app.processor.jobs.config_recommender import _read_flag_state
from app.routers.settings import SettingsUpdateRequest, update_settings_endpoint
from core.agents.query_agent import _parent_child_enabled, _pc_fuse_child_filter
from core.config.recommendations import RECOMMENDATIONS, CorpusStats


@pytest.fixture
def parent_child_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start from 'off' on every surface the handler may write, and restore all of them."""
    monkeypatch.setattr(config, "SYNC_DIR", "")
    # setenv first: delenv on an unset variable registers nothing to restore.
    for key in ("ENABLE_PARENT_CHILD_RETRIEVAL", "PARENT_CHILD_ENABLED"):
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    monkeypatch.setattr(chunker_mod, "PARENT_CHILD_ENABLED", False)
    monkeypatch.setattr(features_mod, "ENABLE_PARENT_CHILD_RETRIEVAL", False)
    monkeypatch.setattr(config, "ENABLE_PARENT_CHILD_RETRIEVAL", False, raising=False)
    monkeypatch.setitem(features_mod.FEATURE_TOGGLES, "enable_parent_child_retrieval", False)
    monkeypatch.setitem(features_mod.FEATURE_FLAGS, "parent_child_retrieval", True)


async def test_patch_turns_parent_child_on_for_query_and_ingest(parent_child_off: None) -> None:
    assert _parent_child_enabled() is False
    assert chunker_mod.parent_child_enabled() is False

    await update_settings_endpoint(SettingsUpdateRequest(enable_parent_child_retrieval=True))

    assert _parent_child_enabled() is True
    assert chunker_mod.parent_child_enabled() is True


async def test_patch_turns_parent_child_off_again(parent_child_off: None) -> None:
    await update_settings_endpoint(SettingsUpdateRequest(enable_parent_child_retrieval=True))
    await update_settings_endpoint(SettingsUpdateRequest(enable_parent_child_retrieval=False))

    assert _parent_child_enabled() is False
    assert chunker_mod.parent_child_enabled() is False


async def test_recommendation_clears_only_when_the_feature_is_really_on(
    parent_child_off: None,
) -> None:
    spec = next(s for s in RECOMMENDATIONS if s.id == "parent_child_retrieval")

    def fires() -> bool:
        return spec.condition_fn(
            CorpusStats(artifact_count=10_000, flags_enabled=_read_flag_state())
        )

    assert fires() is True

    await update_settings_endpoint(SettingsUpdateRequest(**spec.enable_payload))

    assert _parent_child_enabled() is True
    assert fires() is False


# ── the retrieval filter ─────────────────────────────────────────────────────


@pytest.fixture
def mixed_collection():
    """A real Chroma collection holding every chunk_level shape found in a corpus."""
    import chromadb

    client = chromadb.EphemeralClient()
    name = "parent_child_filter_mixed_corpus"
    col = client.get_or_create_collection(name, embedding_function=None)
    col.add(
        ids=["no_level", "child", "flat", "parent"],
        embeddings=[[1.0, 0.0, 0.0], [0.9, 0.1, 0.0], [0.8, 0.2, 0.0], [0.7, 0.3, 0.0]],
        documents=["stored before chunk_level existed", "child", "flat", "parent"],
        metadatas=[
            {"domain": "general"},
            {"domain": "general", "chunk_level": "child"},
            {"domain": "general", "chunk_level": "flat"},
            {"domain": "general", "chunk_level": "parent"},
        ],
    )
    yield col
    client.delete_collection(name)


def _ids(col, where) -> set[str]:
    got = col.query(query_embeddings=[[1.0, 0.0, 0.0]], n_results=10, where=where)
    return set(got["ids"][0])


def test_chunk_without_chunk_level_is_still_retrievable(mixed_collection) -> None:
    ids = _ids(mixed_collection, _pc_fuse_child_filter(None))

    assert "no_level" in ids
    assert "child" in ids


def test_parent_chunks_are_not_ranked_directly(mixed_collection) -> None:
    assert "parent" not in _ids(mixed_collection, _pc_fuse_child_filter(None))


def test_filter_stacks_with_an_existing_clause(mixed_collection) -> None:
    where = _pc_fuse_child_filter({"domain": "general"})

    assert _ids(mixed_collection, where) == {"no_level", "child", "flat"}


def test_filter_stacks_with_an_existing_and_list(mixed_collection) -> None:
    where = _pc_fuse_child_filter(
        {"$and": [{"domain": "general"}, {"cerid_state": {"$ne": "pending"}}]}
    )

    assert _ids(mixed_collection, where) == {"no_level", "child", "flat"}
