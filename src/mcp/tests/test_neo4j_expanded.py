# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Expanded tests for db/neo4j/ package — CRUD, relationships, taxonomy, schema."""

from unittest.mock import MagicMock

import pytest

from app.db.neo4j.artifacts import (
    create_artifact,
    find_artifact_by_filename,
    get_artifact,
    list_artifacts,
    recategorize_artifact,
    update_artifact,
)
from app.db.neo4j.relationships import (
    _batch_merge_relationships,
    create_relationship,
    discover_relationships,
    find_related_artifacts,
)
from app.db.neo4j.schema import init_schema
from app.db.neo4j.taxonomy import (
    count_tags,
    create_domain,
    create_sub_category,
    get_taxonomy,
    list_tags,
    update_artifact_taxonomy,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _mock_driver():
    """Create a mock Neo4j driver with session context manager."""
    driver = MagicMock()
    session = MagicMock()
    driver.session.return_value.__enter__ = MagicMock(return_value=session)
    driver.session.return_value.__exit__ = MagicMock(return_value=False)
    return driver, session


def _mock_record(**fields):
    """Create a dict-like mock record that supports [] access."""
    record = MagicMock()
    record.__getitem__ = lambda self, key: fields[key]
    record.get = lambda key, default=None: fields.get(key, default)
    return record


# ---------------------------------------------------------------------------
# Tests: init_schema
# ---------------------------------------------------------------------------

class TestInitSchema:
    def test_creates_constraints(self):
        driver, session = _mock_driver()
        init_schema(driver)
        # Check that session.run was called with constraint creation
        calls = [str(c) for c in session.run.call_args_list]
        constraint_calls = [c for c in calls if "CONSTRAINT" in c]
        assert len(constraint_calls) >= 4  # artifact_id, domain_name, content_hash, subcategory, tag

    def test_creates_indexes(self):
        driver, session = _mock_driver()
        init_schema(driver)
        calls = [str(c) for c in session.run.call_args_list]
        index_calls = [c for c in calls if "INDEX" in c and "CONSTRAINT" not in c]
        assert len(index_calls) >= 3  # domain, filename, sub_category

    def test_seeds_domains_from_taxonomy(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.schema.config.TAXONOMY", {
            "coding": {"description": "Code", "icon": "code", "sub_categories": ["general", "scripts"]},
            "finance": {"description": "Money", "icon": "dollar"},
        })
        driver, session = _mock_driver()
        init_schema(driver)
        # Check MERGE Domain calls
        calls = [str(c) for c in session.run.call_args_list]
        domain_merges = [c for c in calls if "MERGE (d:Domain" in c]
        assert len(domain_merges) >= 2

    def test_seeds_subcategories(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.schema.config.TAXONOMY", {
            "coding": {"sub_categories": ["general", "scripts"]},
        })
        driver, session = _mock_driver()
        init_schema(driver)
        calls = [str(c) for c in session.run.call_args_list]
        sc_merges = [c for c in calls if "MERGE (sc:SubCategory" in c]
        assert len(sc_merges) >= 2  # general + scripts

    def test_old_index_drop_failure_is_silent(self):
        driver, session = _mock_driver()

        def side_effect(query, **kwargs):
            if "DROP INDEX" in query:
                raise RuntimeError("Index does not exist")
            return MagicMock()

        session.run.side_effect = side_effect
        # Should not raise — DROP INDEX failure is caught silently
        init_schema(driver)


# ---------------------------------------------------------------------------
# Tests: create_artifact
# ---------------------------------------------------------------------------

class TestCreateArtifact:
    def test_creates_artifact_node(self):
        driver, session = _mock_driver()
        record = _mock_record(id="art-123")
        session.run.return_value.single.return_value = record

        result = create_artifact(
            driver, "art-123", "test.py", "coding",
            '["python"]', "A test file", 3, '["c1","c2","c3"]',
        )
        assert result == "art-123"
        session.run.assert_called()

    def test_links_to_domain(self):
        driver, session = _mock_driver()
        record = _mock_record(id="art-1")
        session.run.return_value.single.return_value = record

        create_artifact(driver, "art-1", "f.py", "coding", "[]", "", 1, "[]")
        # Check that BELONGS_TO query was issued
        calls = [str(c) for c in session.run.call_args_list]
        assert any("BELONGS_TO" in c for c in calls)

    def test_links_to_subcategory(self):
        driver, session = _mock_driver()
        record = _mock_record(id="art-1")
        session.run.return_value.single.return_value = record

        create_artifact(driver, "art-1", "f.py", "coding", "[]", "", 1, "[]",
                        sub_category="scripts")
        calls = [str(c) for c in session.run.call_args_list]
        assert any("CATEGORIZED_AS" in c for c in calls)

    def test_default_subcategory(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.artifacts.config.DEFAULT_SUB_CATEGORY", "general")
        driver, session = _mock_driver()
        record = _mock_record(id="art-1")
        session.run.return_value.single.return_value = record

        create_artifact(driver, "art-1", "f.py", "coding", "[]", "", 1, "[]")
        # Sub-category should default to "general" from config
        calls = [str(c) for c in session.run.call_args_list]
        assert any("coding/general" in c for c in calls)

    def test_creates_tag_nodes(self):
        driver, session = _mock_driver()
        record = _mock_record(id="art-1")
        session.run.return_value.single.return_value = record

        create_artifact(driver, "art-1", "f.py", "coding", "[]", "", 1, "[]",
                        tags_json='["important", "review"]')
        calls = [str(c) for c in session.run.call_args_list]
        tag_calls = [c for c in calls if "TAGGED_WITH" in c]
        # Batched into a single UNWIND query
        assert len(tag_calls) == 1
        assert "UNWIND" in tag_calls[0]

    def test_invalid_tags_json_handled(self):
        driver, session = _mock_driver()
        record = _mock_record(id="art-1")
        session.run.return_value.single.return_value = record

        # Invalid JSON should not crash
        result = create_artifact(driver, "art-1", "f.py", "coding", "[]", "", 1, "[]",
                                 tags_json="not json")
        assert result == "art-1"

    def test_empty_tag_strings_skipped(self):
        driver, session = _mock_driver()
        record = _mock_record(id="art-1")
        session.run.return_value.single.return_value = record

        create_artifact(driver, "art-1", "f.py", "coding", "[]", "", 1, "[]",
                        tags_json='["", "  ", "valid"]')
        calls = [str(c) for c in session.run.call_args_list]
        tag_calls = [c for c in calls if "TAGGED_WITH" in c]
        assert len(tag_calls) == 1  # Only "valid" creates a tag


# ---------------------------------------------------------------------------
# Tests: find_artifact_by_filename
# ---------------------------------------------------------------------------

class TestFindArtifactByFilename:
    def test_found(self):
        driver, session = _mock_driver()
        record = _mock_record(id="art-1", content_hash="abc123", chunk_ids='["c1"]')
        session.run.return_value.single.return_value = record

        result = find_artifact_by_filename(driver, "test.py", "coding")
        assert result == {"id": "art-1", "content_hash": "abc123", "chunk_ids": '["c1"]'}

    def test_not_found(self):
        driver, session = _mock_driver()
        session.run.return_value.single.return_value = None

        result = find_artifact_by_filename(driver, "missing.py", "coding")
        assert result is None


# ---------------------------------------------------------------------------
# Tests: update_artifact
# ---------------------------------------------------------------------------

class TestUpdateArtifact:
    def test_updates_fields(self):
        driver, session = _mock_driver()
        update_artifact(driver, "art-1", '["new"]', "Updated", 5, '["c1"]', "hash456")
        session.run.assert_called_once()
        call_args = session.run.call_args
        assert call_args.kwargs["artifact_id"] == "art-1"
        assert call_args.kwargs["summary"] == "Updated"


# ---------------------------------------------------------------------------
# Tests: get_artifact
# ---------------------------------------------------------------------------

class TestGetArtifact:
    def test_found(self):
        driver, session = _mock_driver()
        record = _mock_record(
            id="art-1", filename="test.py", domain="coding",
            sub_category="scripts", tags='["tag1"]', keywords='["python"]',
            summary="A file", chunk_count=3, chunk_ids='["c1"]',
            ingested_at="2026-02-28T12:00:00Z", recategorized_at=None,
            domain_name="coding",
        )
        session.run.return_value.single.return_value = record

        result = get_artifact(driver, "art-1")
        assert result["id"] == "art-1"
        assert result["domain"] == "coding"
        assert result["sub_category"] == "scripts"

    def test_not_found(self):
        driver, session = _mock_driver()
        session.run.return_value.single.return_value = None

        result = get_artifact(driver, "nonexistent")
        assert result is None

    def test_default_subcategory_fallback(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.artifacts.config.DEFAULT_SUB_CATEGORY", "general")
        driver, session = _mock_driver()
        record = _mock_record(
            id="art-1", filename="test.py", domain="coding",
            sub_category=None, tags=None, keywords='[]',
            summary="", chunk_count=1, chunk_ids='[]',
            ingested_at="", recategorized_at=None, domain_name="coding",
        )
        session.run.return_value.single.return_value = record

        result = get_artifact(driver, "art-1")
        assert result["sub_category"] == "general"
        assert result["tags"] == "[]"


# ---------------------------------------------------------------------------
# Tests: list_artifacts
# ---------------------------------------------------------------------------

class TestListArtifacts:
    def _setup_results(self, session, records):
        """Configure session.run to return iterable records."""
        session.run.return_value = iter(records)

    def test_empty_list(self):
        driver, session = _mock_driver()
        self._setup_results(session, [])

        result = list_artifacts(driver)
        assert result == []

    def test_returns_artifacts(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.artifacts.config.DEFAULT_SUB_CATEGORY", "general")
        driver, session = _mock_driver()
        record = _mock_record(
            id="art-1", filename="test.py", domain="coding",
            sub_category="general", tags='[]', keywords='[]',
            summary="", chunk_count=1, chunk_ids='[]',
            ingested_at="2026-01-01", recategorized_at=None,
            domain_name="coding", quality_score=0.5, client_source="",
        )
        self._setup_results(session, [record])

        result = list_artifacts(driver)
        assert len(result) == 1
        assert result[0]["id"] == "art-1"

    def test_domain_filter_in_query(self):
        driver, session = _mock_driver()
        self._setup_results(session, [])

        list_artifacts(driver, domain="coding")
        call_args = session.run.call_args
        assert "d.name = $domain" in call_args.args[0]

    def test_tag_filter_uses_tagged_with(self):
        driver, session = _mock_driver()
        self._setup_results(session, [])

        list_artifacts(driver, tag="Important")
        call_args = session.run.call_args
        assert "TAGGED_WITH" in call_args.args[0]
        # Tag should be lowercased
        assert call_args.kwargs["tag"] == "important"


# ---------------------------------------------------------------------------
# Tests: recategorize_artifact
# ---------------------------------------------------------------------------

class TestRecategorizeArtifact:
    def test_successful_recategorization(self):
        driver, session = _mock_driver()
        record = _mock_record(old_domain="coding", new_domain="finance")
        session.run.return_value.single.return_value = record

        result = recategorize_artifact(driver, "art-1", "finance")
        assert result == {"old_domain": "coding", "new_domain": "finance"}

    def test_artifact_not_found_raises(self):
        driver, session = _mock_driver()
        session.run.return_value.single.return_value = None

        with pytest.raises(ValueError, match="Artifact not found"):
            recategorize_artifact(driver, "missing-id", "finance")


# ---------------------------------------------------------------------------
# Tests: create_relationship
# ---------------------------------------------------------------------------

class TestCreateRelationship:
    def test_valid_relationship(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO", "REFERENCES"])
        driver, session = _mock_driver()
        record = _mock_record(ok=True, is_new=True)
        session.run.return_value.single.return_value = record

        result = create_relationship(driver, "a1", "a2", "RELATES_TO")
        assert result is True

    def test_invalid_rel_type_returns_false(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO"])
        driver, session = _mock_driver()

        result = create_relationship(driver, "a1", "a2", "INVALID_TYPE")
        assert result is False
        session.run.assert_not_called()

    def test_self_reference_returns_false(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO"])
        driver, session = _mock_driver()

        result = create_relationship(driver, "same-id", "same-id", "RELATES_TO")
        assert result is False

    def test_existing_relationship_returns_false(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO"])
        driver, session = _mock_driver()
        # is_new=False means MERGE found existing
        record = _mock_record(ok=True, is_new=False)
        session.run.return_value.single.return_value = record

        result = create_relationship(driver, "a1", "a2", "RELATES_TO")
        assert result is False

    def test_properties_merged(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO"])
        driver, session = _mock_driver()
        record = _mock_record(ok=True, is_new=True)
        session.run.return_value.single.return_value = record

        create_relationship(driver, "a1", "a2", "RELATES_TO",
                            properties={"reason": "test"})
        call_kwargs = session.run.call_args.kwargs
        assert call_kwargs["props"]["reason"] == "test"
        assert "created_at" in call_kwargs["props"]


# ---------------------------------------------------------------------------
# Tests: find_related_artifacts
# ---------------------------------------------------------------------------

class TestFindRelatedArtifacts:
    def test_empty_artifact_ids(self):
        driver, _ = _mock_driver()
        result = find_related_artifacts(driver, [])
        assert result == []

    def test_returns_related(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_TRAVERSAL_DEPTH", 2)
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_MAX_RELATED", 10)
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO"])

        driver, session = _mock_driver()
        record = _mock_record(
            id="related-1", filename="other.py", domain="coding",
            summary="Related file", keywords='[]', chunk_ids='[]',
            chunk_count=1, relationship_type="RELATES_TO",
            relationship_depth=1, relationship_reason="test",
        )
        session.run.return_value = iter([record])

        result = find_related_artifacts(driver, ["art-1"])
        assert len(result) == 1
        assert result[0]["id"] == "related-1"

    def test_depth_clamped(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_TRAVERSAL_DEPTH", 2)
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_MAX_RELATED", 10)
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO"])

        driver, session = _mock_driver()
        session.run.return_value = iter([])

        find_related_artifacts(driver, ["art-1"], depth=100)
        call_args = session.run.call_args
        # Depth should be clamped to max 4
        assert "*1..4" in call_args.args[0]

    def test_invalid_rel_types_returns_empty(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO"])

        driver, _ = _mock_driver()
        result = find_related_artifacts(driver, ["art-1"], rel_types=["INVALID"])
        assert result == []


# ---------------------------------------------------------------------------
# Tests: discover_relationships
# ---------------------------------------------------------------------------

def _batch_result(new_count):
    """A session.run() result whose .single() reports a batch's new_count
    (AF-090 — discover_relationships now writes one UNWIND MERGE per strategy)."""
    res = MagicMock()
    res.single.return_value = _mock_record(new_count=new_count)
    return res


class TestDiscoverRelationships:
    def test_same_directory_discovery(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_MIN_KEYWORD_OVERLAP", 2)
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO", "REFERENCES"])

        driver, session = _mock_driver()
        neighbor = _mock_record(id="neighbor-1")
        # Only strategy 1 runs (dir path, no keywords, no content):
        #   call 1 = read neighbors, call 2 = batched UNWIND MERGE.
        session.run.side_effect = [iter([neighbor]), _batch_result(1)]

        count = discover_relationships(
            driver, "art-1", "src/utils/helper.py", "coding", "[]"
        )
        assert count == 1
        # The write is a single batched UNWIND MERGE, not a per-edge loop.
        write_cypher = session.run.call_args_list[1].args[0]
        assert "UNWIND" in write_cypher and "MERGE" in write_cypher

    def test_root_file_skips_directory_strategy(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_MIN_KEYWORD_OVERLAP", 2)

        driver, session = _mock_driver()
        session.run.return_value = iter([])

        count = discover_relationships(
            driver, "art-1", "readme.md", "coding", "[]"
        )
        # Root file has no parent_dir — should skip directory strategy
        assert count == 0

    def test_keyword_overlap_discovery(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_MIN_KEYWORD_OVERLAP", 2)
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO", "REFERENCES"])

        driver, session = _mock_driver()
        other = _mock_record(id="other-1", keywords='["python", "fastapi", "rest"]')
        # Only strategy 2 runs: call 1 = read candidates, call 2 = batch write.
        session.run.side_effect = [iter([other]), _batch_result(1)]

        count = discover_relationships(
            driver, "art-1", "readme.md", "coding",
            '["python", "fastapi", "testing"]'
        )
        # python + fastapi overlap >= 2 → should create relationship
        assert count == 1

    def test_keyword_below_threshold_creates_nothing(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_MIN_KEYWORD_OVERLAP", 2)
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO", "REFERENCES"])

        driver, session = _mock_driver()
        # Only 1 shared keyword ("python") — below the overlap threshold, so no
        # edge is collected and the batch write never runs.
        other = _mock_record(id="other-1", keywords='["python", "django"]')
        session.run.side_effect = [iter([other])]

        count = discover_relationships(
            driver, "art-1", "readme.md", "coding", '["python", "fastapi"]'
        )
        assert count == 0
        assert session.run.call_count == 1  # read only, no batch write

    def test_content_reference_discovery(self, monkeypatch):
        # Disable keyword strategy
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_MIN_KEYWORD_OVERLAP", 100)
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO", "REFERENCES"])

        driver, session = _mock_driver()
        ref_match = _mock_record(id="ref-1", filename="config.py")
        session.run.side_effect = [iter([ref_match]), _batch_result(1)]

        count = discover_relationships(
            driver, "art-1", "main.py", "coding", "[]",
            content="import os\nfrom config import settings"
        )
        assert count == 1

    def test_no_content_skips_reference_strategy(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_MIN_KEYWORD_OVERLAP", 100)

        driver, session = _mock_driver()
        session.run.return_value = iter([])

        count = discover_relationships(
            driver, "art-1", "readme.md", "coding", "[]", content=""
        )
        assert count == 0


# ---------------------------------------------------------------------------
# Tests: _batch_merge_relationships (AF-090)
# ---------------------------------------------------------------------------

class TestBatchMergeRelationships:
    def test_invalid_rel_type_returns_zero_without_query(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO"])
        driver, session = _mock_driver()

        n = _batch_merge_relationships(
            driver, "a1", [{"target_id": "a2", "props": {}}], "BOGUS"
        )
        assert n == 0
        session.run.assert_not_called()

    def test_empty_edges_returns_zero_without_query(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO"])
        driver, session = _mock_driver()

        n = _batch_merge_relationships(driver, "a1", [], "RELATES_TO")
        assert n == 0
        session.run.assert_not_called()

    def test_dedups_by_target_first_wins_and_drops_self(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["RELATES_TO"])
        driver, session = _mock_driver()
        session.run.return_value = _batch_result(2)

        edges = [
            {"target_id": "t1", "props": {"reason": "first"}},
            {"target_id": "t1", "props": {"reason": "second"}},  # dup → dropped
            {"target_id": "t2", "props": {"reason": "keep"}},
            {"target_id": "a1", "props": {"reason": "self"}},     # self → dropped
        ]
        n = _batch_merge_relationships(driver, "a1", edges, "RELATES_TO")
        assert n == 2  # from the mocked new_count

        payload = session.run.call_args.kwargs["edges"]
        targets = [e["target_id"] for e in payload]
        assert targets == ["t1", "t2"]          # deduped, self-edge dropped
        first = next(e for e in payload if e["target_id"] == "t1")
        assert first["props"]["reason"] == "first"  # first-wins props
        # The write is a single batched UNWIND MERGE.
        assert "UNWIND" in session.run.call_args.args[0]

    def test_returns_query_new_count(self, monkeypatch):
        monkeypatch.setattr("app.db.neo4j.relationships.config.GRAPH_RELATIONSHIP_TYPES", ["REFERENCES"])
        driver, session = _mock_driver()
        session.run.return_value = _batch_result(0)  # all edges pre-existed

        n = _batch_merge_relationships(
            driver, "a1", [{"target_id": "t1", "props": {}}], "REFERENCES"
        )
        assert n == 0


# ---------------------------------------------------------------------------
# Tests: get_taxonomy
# ---------------------------------------------------------------------------

class TestGetTaxonomy:
    def test_returns_domains_and_tags(self):
        driver, session = _mock_driver()

        domain_record = _mock_record(
            name="coding", description="Code", icon="code", artifact_count=10
        )
        tag_record = _mock_record(name="python", usage_count=5)

        # Multiple session.run calls for domains, subcategories, tags
        session.run.side_effect = [
            iter([domain_record]),  # domains
            iter([]),               # sub-categories
            iter([tag_record]),     # tags
        ]

        result = get_taxonomy(driver)
        assert "domains" in result
        assert "tags" in result
        assert "coding" in result["domains"]
        assert result["domains"]["coding"]["artifact_count"] == 10
        assert result["tags"][0]["name"] == "python"

    def test_empty_taxonomy(self):
        driver, session = _mock_driver()
        session.run.side_effect = [iter([]), iter([]), iter([])]

        result = get_taxonomy(driver)
        assert result["domains"] == {}
        assert result["tags"] == []


# ---------------------------------------------------------------------------
# Tests: create_domain
# ---------------------------------------------------------------------------

class TestCreateDomain:
    def test_creates_domain_with_subcategories(self):
        driver, session = _mock_driver()

        result = create_domain(driver, "research", "Research domain", "book",
                               sub_categories=["papers", "notes"])
        assert result["name"] == "research"
        assert result["sub_categories"] == ["papers", "notes"]

    def test_default_subcategory_is_general(self):
        driver, session = _mock_driver()

        result = create_domain(driver, "misc")
        assert result["sub_categories"] == ["general"]

    def test_merges_domain_node(self):
        driver, session = _mock_driver()
        create_domain(driver, "test")
        calls = [str(c) for c in session.run.call_args_list]
        assert any("MERGE (d:Domain" in c for c in calls)


# ---------------------------------------------------------------------------
# Tests: create_sub_category
# ---------------------------------------------------------------------------

class TestCreateSubCategory:
    def test_creates_subcategory(self):
        driver, session = _mock_driver()
        # First run returns existing domain
        domain_record = _mock_record(name="coding")
        session.run.return_value.single.return_value = domain_record

        result = create_sub_category(driver, "coding", "scripts")
        assert result == {"domain": "coding", "sub_category": "scripts"}

    def test_domain_not_found_raises(self):
        driver, session = _mock_driver()
        session.run.return_value.single.return_value = None

        with pytest.raises(ValueError, match="Domain not found"):
            create_sub_category(driver, "nonexistent", "sub")


# ---------------------------------------------------------------------------
# Tests: list_tags
# ---------------------------------------------------------------------------

class TestListTags:
    def test_returns_tags(self):
        driver, session = _mock_driver()
        tag1 = _mock_record(name="python", usage_count=10)
        tag2 = _mock_record(name="javascript", usage_count=5)
        session.run.return_value = iter([tag1, tag2])

        result = list_tags(driver)
        assert len(result) == 2
        assert result[0]["name"] == "python"
        assert result[0]["usage_count"] == 10

    def test_empty_tags(self):
        driver, session = _mock_driver()
        session.run.return_value = iter([])

        result = list_tags(driver)
        assert result == []

    def test_limit_passed(self):
        driver, session = _mock_driver()
        session.run.return_value = iter([])

        list_tags(driver, limit=25)
        call_kwargs = session.run.call_args.kwargs
        assert call_kwargs["limit"] == 25


# ---------------------------------------------------------------------------
# Tests: count_tags — WB-23: the true distinct-tag total, unbounded by
# list_tags' `limit` cap.
# ---------------------------------------------------------------------------

class TestCountTags:
    def test_returns_total(self):
        driver, session = _mock_driver()
        session.run.return_value.single.return_value = _mock_record(total=842)

        assert count_tags(driver) == 842

    def test_no_record_returns_zero(self):
        driver, session = _mock_driver()
        session.run.return_value.single.return_value = None

        assert count_tags(driver) == 0


# ---------------------------------------------------------------------------
# Tests: update_artifact_taxonomy
# ---------------------------------------------------------------------------

class TestUpdateArtifactTaxonomy:
    def test_artifact_not_found_raises(self):
        driver, session = _mock_driver()
        session.run.return_value.single.return_value = None

        with pytest.raises(ValueError, match="Artifact not found"):
            update_artifact_taxonomy(driver, "missing-id", sub_category="new")

    def test_updates_subcategory(self):
        driver, session = _mock_driver()
        record = _mock_record(domain="coding", sub_category="general", tags="[]")
        session.run.return_value.single.return_value = record

        result = update_artifact_taxonomy(driver, "art-1", sub_category="scripts")
        assert result["sub_category"] == "scripts"
        # Should have called SET for sub_category and CATEGORIZED_AS
        calls = [str(c) for c in session.run.call_args_list]
        assert any("CATEGORIZED_AS" in c for c in calls)

    def test_updates_tags(self):
        driver, session = _mock_driver()
        record = _mock_record(domain="coding", sub_category="general", tags="[]")
        session.run.return_value.single.return_value = record

        result = update_artifact_taxonomy(driver, "art-1",
                                          tags_json='["new-tag", "other"]')
        assert result["tags"] == '["new-tag", "other"]'
        calls = [str(c) for c in session.run.call_args_list]
        tag_calls = [c for c in calls if "TAGGED_WITH" in c]
        # Should delete old + create 2 new
        assert len(tag_calls) >= 2

    def test_invalid_tags_json_handled(self):
        driver, session = _mock_driver()
        record = _mock_record(domain="coding", sub_category="general", tags="[]")
        session.run.return_value.single.return_value = record

        # Should not crash on invalid JSON
        result = update_artifact_taxonomy(driver, "art-1", tags_json="not json")
        assert result["tags"] == "not json"

    def test_both_subcategory_and_tags(self):
        driver, session = _mock_driver()
        record = _mock_record(domain="coding", sub_category="general", tags="[]")
        session.run.return_value.single.return_value = record

        result = update_artifact_taxonomy(
            driver, "art-1", sub_category="api", tags_json='["important"]'
        )
        assert result["sub_category"] == "api"
        assert result["tags"] == '["important"]'

    def test_neither_updates_nothing(self):
        driver, session = _mock_driver()
        record = _mock_record(domain="coding", sub_category="general", tags="[]")
        session.run.return_value.single.return_value = record

        result = update_artifact_taxonomy(driver, "art-1")
        assert result["sub_category"] is None
        assert result["tags"] is None
        # Only the initial MATCH query should have been called
        calls = session.run.call_args_list
        assert len(calls) == 1  # Just the lookup query
