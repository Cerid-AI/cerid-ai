# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The ingest stamp is the ontology that already exists."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

import config
from app.services.ingest_ontology import OntologyError, apply_ontology
from app.services.ingestion import accepted_file_domain, ingest_content


def test_anneal_kind_becomes_the_sub_category() -> None:
    metadata = {"kind": "lesson", "source": "cerid-anneal", "repo_sig": "abc"}
    apply_ontology("anneal_lessons", metadata, strict=True)
    assert metadata["domain"] == "anneal_lessons"
    assert metadata["sub_category"] == "lesson"
    assert metadata["kind"] == "lesson"
    assert metadata["source"] == "cerid-anneal"
    tags = json.loads(metadata["tags_json"])
    assert "lesson" in tags


def test_an_explicit_unknown_sub_category_is_an_error_when_strict() -> None:
    with pytest.raises(OntologyError) as caught:
        apply_ontology(
            "anneal_lessons",
            {"sub_category": "not-a-real-shelf"},
            strict=True,
        )
    assert "lesson" in caught.value.allowed
    assert "general" in caught.value.allowed


def test_a_valid_kind_wins_over_a_classifier_sub_category_that_is_not_in_the_list() -> None:
    metadata = {"kind": "lesson", "sub_category": "python"}
    apply_ontology("anneal_lessons", metadata, strict=False)
    assert metadata["sub_category"] == "lesson"


def test_an_explicit_unknown_sub_category_becomes_general_when_not_strict() -> None:
    metadata = {"sub_category": "not-a-real-shelf"}
    apply_ontology("mail", metadata, strict=False)
    assert metadata["sub_category"] == "general"


def test_an_omitted_sub_category_is_general() -> None:
    metadata: dict = {}
    apply_ontology("mail", metadata, strict=True)
    assert metadata["sub_category"] == "general"


def test_the_collection_domain_wins_over_a_disagreeing_metadata_key() -> None:
    metadata = {"domain": "personal", "kind": "decision"}
    apply_ontology("anneal_decisions", metadata, strict=True)
    assert metadata["domain"] == "anneal_decisions"
    assert metadata["sub_category"] == "decision"


def test_a_callers_content_hash_is_not_rewritten() -> None:
    metadata = {"content_hash": "gmail-message", "sub_category": "gmail"}
    apply_ontology("mail", metadata, strict=True)
    assert metadata["content_hash"] == "gmail-message"
    assert metadata["sub_category"] == "gmail"


def test_an_unknown_domain_keeps_a_provided_sub_category_and_invents_no_tags() -> None:
    metadata = {"sub_category": "scratch", "tags": ["one"]}
    apply_ontology("not-a-domain", metadata, strict=True)
    assert metadata["sub_category"] == "scratch"
    assert "tags_json" not in metadata


def test_calendar_is_in_the_taxonomy_the_plugin_already_writes() -> None:
    assert "calendar" in config.TAXONOMY
    assert {"meetings", "deadlines", "personal", "general"} <= set(
        config.TAXONOMY["calendar"]["sub_categories"]
    )
    assert "calendar" in config.TAG_VOCABULARY
    assert "gmail" in config.TAG_VOCABULARY["mail"]
    assert "outlook" in config.TAG_VOCABULARY["mail"]


def test_a_rehydrated_taxonomy_entry_does_not_hide_the_operational_shelf(monkeypatch) -> None:
    shadowed = dict(config.TAXONOMY)
    shadowed["anneal_lessons"] = {
        "description": "persisted",
        "icon": "",
        "sub_categories": ["anneal_lessons/retro"],
    }
    monkeypatch.setattr(config, "TAXONOMY", shadowed)
    assert config.sub_categories_for("anneal_lessons") == [
        "lesson", "correction", "hypothesis", "general",
    ]


def test_operational_domains_stay_out_of_taxonomy_and_still_have_a_vocabulary() -> None:
    for domain in (
        "anneal_lessons",
        "anneal_decisions",
        "anneal_runs",
        "anneal_conversations",
        "trading",
        "boardroom_foundation",
        "boardroom_routing_examples",
        "boardroom_campaigns",
        "cerid_ai_business",
    ):
        assert domain not in config.TAXONOMY
        assert config.domain_spec(domain) is not None
        assert config.vocabulary_for(domain)


def test_ingest_writes_the_kind_as_sub_category_and_keeps_the_caller_hash() -> None:
    collection = MagicMock()
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    session.run.return_value.single.return_value = None
    with (
        patch("app.services.ingestion.get_chroma") as mock_chroma,
        patch("app.services.ingestion.get_neo4j", return_value=driver),
        patch("app.services.ingestion.get_redis", return_value=MagicMock()),
        patch("app.services.ingestion.graph") as mock_graph,
    ):
        mock_chroma.return_value.get_or_create_collection.return_value = collection
        mock_graph.find_artifact_by_filename.return_value = None
        mock_graph.create_artifact.return_value = None
        mock_graph.discover_relationships.return_value = 0
        ingest_content(
            "the gate failed closed and the row stayed out",
            domain="anneal_lessons",
            metadata={
                "kind": "lesson",
                "content_hash": "caller-hash",
                "source": "cerid-anneal",
                "repo_sig": "abc",
            },
            enrich=False,
            skip_quality=True,
        )
    metas = collection.upsert.call_args.kwargs["metadatas"]
    assert metas[0]["domain"] == "anneal_lessons"
    assert metas[0]["sub_category"] == "lesson"
    assert metas[0]["content_hash"] == "caller-hash"
    assert metas[0]["kind"] == "lesson"
    assert metas[0]["source"] == "cerid-anneal"
    tags = json.loads(metas[0]["tags_json"])
    assert "lesson" in tags


def test_a_file_domain_in_the_operational_ontology_is_not_rewritten_to_general() -> None:
    assert accepted_file_domain("anneal_lessons") == "anneal_lessons"
    assert accepted_file_domain("trading") == "trading"
    assert accepted_file_domain("mail") == "mail"
    assert accepted_file_domain("not-a-domain") == "general"
    assert accepted_file_domain("") == "general"
