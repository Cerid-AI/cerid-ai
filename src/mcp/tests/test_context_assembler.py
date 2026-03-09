# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for intelligent context assembly module."""

import pytest

from utils.context_assembler import extract_facets, intelligent_assemble


class TestExtractFacets:
    """Tests for extract_facets()."""

    def test_empty_query(self):
        assert extract_facets("") == []

    def test_single_facet(self):
        facets = extract_facets("How does the ingestion pipeline work?")
        assert len(facets) == 1

    def test_conjunction_split(self):
        facets = extract_facets("explain chunking and how does embedding work")
        assert len(facets) >= 2

    def test_comma_split(self):
        facets = extract_facets("tell me about Redis caching, Neo4j graph queries, and vector search")
        assert len(facets) >= 2

    def test_also_split(self):
        facets = extract_facets("explain the API also show me the configuration")
        assert len(facets) >= 2

    def test_filters_short_fragments(self):
        facets = extract_facets("explain the architecture, ok?")
        for f in facets:
            assert len(f) >= 8

    def test_question_boundary_split(self):
        facets = extract_facets("What is Redis? How does caching work?")
        assert len(facets) >= 2


class TestIntelligentAssemble:
    """Tests for intelligent_assemble()."""

    def test_empty_results(self):
        context, sources, meta = intelligent_assemble([], "test query")
        assert context == ""
        assert sources == []
        assert meta["facets_total"] == 0
        assert meta["coverage_ratio"] == 0.0

    def test_basic_assembly(self):
        results = [
            {"content": "Redis is an in-memory data store used for caching",
             "relevance": 0.9, "artifact_id": "a1", "filename": "redis.md", "domain": "infra"},
            {"content": "Neo4j is a graph database for relationships",
             "relevance": 0.8, "artifact_id": "a2", "filename": "neo4j.md", "domain": "infra"},
        ]
        context, sources, meta = intelligent_assemble(results, "Redis caching")
        assert "Redis" in context
        assert len(sources) >= 1

    def test_max_chars_respected(self):
        results = [
            {"content": "A" * 100, "relevance": 0.9, "artifact_id": "a1", "filename": "a.md", "domain": "d"},
            {"content": "B" * 100, "relevance": 0.8, "artifact_id": "a2", "filename": "b.md", "domain": "d"},
            {"content": "C" * 100, "relevance": 0.7, "artifact_id": "a3", "filename": "c.md", "domain": "d"},
        ]
        context, sources, meta = intelligent_assemble(results, "test", max_chars=250)
        assert len(sources) <= 3

    def test_coverage_metadata(self):
        results = [
            {"content": "Redis caching layer stores frequently accessed data",
             "relevance": 0.9, "artifact_id": "a1", "filename": "r.md", "domain": "d"},
            {"content": "Neo4j graph database stores relationships between entities",
             "relevance": 0.8, "artifact_id": "a2", "filename": "n.md", "domain": "d"},
        ]
        context, sources, meta = intelligent_assemble(
            results, "explain Redis caching and Neo4j graph relationships"
        )
        assert "facets_total" in meta
        assert "facets_covered" in meta
        assert "coverage_ratio" in meta
        assert meta["facets_total"] >= 1

    def test_facet_diversity_selection(self):
        results = [
            {"content": "Redis is an in-memory caching solution for data",
             "relevance": 0.95, "artifact_id": "a1", "filename": "r1.md", "domain": "d"},
            {"content": "Redis configuration and memory management tuning",
             "relevance": 0.90, "artifact_id": "a2", "filename": "r2.md", "domain": "d"},
            {"content": "Neo4j graph database Cypher queries and traversals",
             "relevance": 0.85, "artifact_id": "a3", "filename": "n1.md", "domain": "d"},
        ]
        context, sources, meta = intelligent_assemble(
            results, "Redis caching and Neo4j graph queries"
        )
        domains_covered = set()
        for s in sources:
            content = s.get("content", "")
            if "Redis" in content:
                domains_covered.add("redis")
            if "Neo4j" in content:
                domains_covered.add("neo4j")
        assert len(domains_covered) >= 2

    def test_sources_include_result_fields(self):
        results = [
            {"content": "test content for verification purposes",
             "relevance": 0.9, "artifact_id": "abc123", "filename": "test.md", "domain": "code"},
        ]
        context, sources, meta = intelligent_assemble(results, "test content")
        assert len(sources) == 1
        assert sources[0]["artifact_id"] == "abc123"

    def test_missing_content_handled(self):
        results = [
            {"relevance": 0.9, "artifact_id": "a1", "filename": "a.md", "domain": "d"},
        ]
        context, sources, meta = intelligent_assemble(results, "test query")
        assert isinstance(context, str)

    def test_single_facet_query(self):
        results = [
            {"content": "chunking splits documents into smaller pieces for embedding",
             "relevance": 0.9, "artifact_id": "a1", "filename": "c.md", "domain": "d"},
        ]
        context, sources, meta = intelligent_assemble(results, "explain chunking")
        assert meta["facets_total"] == 1
