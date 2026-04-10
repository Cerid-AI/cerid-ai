# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for hierarchical taxonomy system."""
from __future__ import annotations

import json
import sys
from types import ModuleType
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Dependency stubs (tiktoken, httpx, spacy, etc.) are handled by conftest.py

# ---------------------------------------------------------------------------
# Test-specific stubs: FastAPI + Pydantic (not shared across all test files)
# ---------------------------------------------------------------------------


def _ensure_stub(name, stub_module):
    """Register a stub module if the real one isn't available."""
    if name not in sys.modules:
        sys.modules[name] = stub_module


# fastapi + pydantic stubs
_fastapi = ModuleType("fastapi")


class _APIRouter:
    def __init__(self, **kwargs):
        pass

    def get(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator

    def post(self, *args, **kwargs):
        def decorator(func):
            return func
        return decorator


class _HTTPException(Exception):
    def __init__(self, status_code=500, detail=""):
        self.status_code = status_code
        self.detail = detail


def _Query(*args, **kwargs):
    return kwargs.get("default", None)


_fastapi.APIRouter = _APIRouter
_fastapi.HTTPException = _HTTPException
_fastapi.Query = _Query
_ensure_stub("fastapi", _fastapi)

_pydantic = ModuleType("pydantic")


class _BaseModel:
    def __init__(self, **kwargs):
        for key, value in kwargs.items():
            setattr(self, key, value)
        # Apply defaults from class variables for unset attributes
        for key in getattr(self.__class__, "__annotations__", {}):
            if not hasattr(self, key):
                default = getattr(self.__class__, key, None)
                setattr(self, key, default)


_pydantic.BaseModel = _BaseModel
_ensure_stub("pydantic", _pydantic)


class TestTaxonomyConfig:
    """Test TAXONOMY config and backward compatibility."""

    def test_taxonomy_has_all_domains(self):
        """TAXONOMY contains all expected public domains."""
        import config

        expected = {
            "coding", "finance", "projects", "personal", "general",
            "conversations",
        }
        assert expected == set(config.TAXONOMY.keys())

    def test_domains_derived_from_taxonomy(self):
        """DOMAINS list is derived from TAXONOMY keys."""
        import config

        assert set(config.DOMAINS) == set(config.TAXONOMY.keys())

    def test_each_domain_has_sub_categories(self):
        """Each domain in TAXONOMY has at least one sub-category."""
        import config

        for domain, info in config.TAXONOMY.items():
            assert "sub_categories" in info, f"{domain} missing sub_categories"
            assert len(info["sub_categories"]) >= 1, f"{domain} has no sub-categories"
            assert "general" in info["sub_categories"], (
                f"{domain} missing 'general' sub-category"
            )

    def test_each_domain_has_description_and_icon(self):
        """Each domain has description and icon fields."""
        import config

        for domain, info in config.TAXONOMY.items():
            assert "description" in info, f"{domain} missing description"
            assert "icon" in info, f"{domain} missing icon"
            assert len(info["description"]) > 0, f"{domain} has empty description"

    def test_default_sub_category(self):
        """DEFAULT_SUB_CATEGORY is 'general'."""
        import config

        assert config.DEFAULT_SUB_CATEGORY == "general"

    @patch.dict("os.environ", {"CERID_CUSTOM_DOMAINS": '{"research": {"description": "Research", "icon": "book", "sub_categories": ["papers", "general"]}}'})
    def test_custom_domains_from_env(self):
        """Custom domains can be added via CERID_CUSTOM_DOMAINS env var."""
        import importlib
        import os

        import config
        import config.taxonomy

        importlib.reload(config.taxonomy)
        importlib.reload(config)

        assert "research" in config.TAXONOMY
        assert "papers" in config.TAXONOMY["research"]["sub_categories"]

        # Clean up: reload to restore original state
        os.environ.pop("CERID_CUSTOM_DOMAINS", None)
        importlib.reload(config.taxonomy)
        importlib.reload(config)


class TestTagVocabulary:
    """Test per-domain tag vocabulary."""

    def test_tag_vocabulary_has_all_domains(self):
        """TAG_VOCABULARY covers all TAXONOMY domains."""
        import config

        for domain in config.TAXONOMY:
            assert domain in config.TAG_VOCABULARY, f"Missing vocabulary for domain: {domain}"

    def test_vocabulary_tags_are_lowercase_strings(self):
        """All vocabulary tags are lowercase strings."""
        import config

        for domain, tags in config.TAG_VOCABULARY.items():
            assert isinstance(tags, list), f"{domain} tags is not a list"
            for tag in tags:
                assert isinstance(tag, str), f"Non-string tag in {domain}: {tag}"
                assert tag == tag.lower(), f"Non-lowercase tag in {domain}: {tag}"

    def test_vocabulary_has_reasonable_size(self):
        """Each domain has 10-25 vocabulary tags."""
        import config

        for domain, tags in config.TAG_VOCABULARY.items():
            assert len(tags) >= 10, f"{domain} has too few tags: {len(tags)}"
            assert len(tags) <= 25, f"{domain} has too many tags: {len(tags)}"

    def test_no_duplicate_tags_per_domain(self):
        """No duplicate tags within a single domain vocabulary."""
        import config

        for domain, tags in config.TAG_VOCABULARY.items():
            assert len(tags) == len(set(tags)), f"Duplicate tags in {domain}"


class TestTagScoring:
    """Test tag quality scoring."""

    def test_score_empty_tags(self):
        """Empty tag list scores 0.0."""
        from utils.metadata import score_tags

        assert score_tags([], "coding") == 0.0

    def test_score_all_vocabulary_tags(self):
        """All vocabulary tags score high."""
        from utils.metadata import score_tags

        score = score_tags(["python", "docker", "api", "testing", "git"], "coding")
        assert score == 1.0  # 5 vocab tags * 0.2 = 1.0

    def test_score_all_freeform_tags(self):
        """Free-form tags score lower than vocabulary tags."""
        from utils.metadata import score_tags

        score = score_tags(["custom-tag", "another-tag"], "coding")
        assert score == 0.2  # 2 freeform * 0.1 = 0.2

    def test_score_mixed_tags(self):
        """Mixed vocabulary and free-form tags score appropriately."""
        from utils.metadata import score_tags

        score = score_tags(["python", "custom-tag", "docker"], "coding")
        assert score == 0.5  # 2 vocab * 0.2 + 1 freeform * 0.1 = 0.5

    def test_score_capped_at_one(self):
        """Score is capped at 1.0 even with many tags."""
        from utils.metadata import score_tags

        score = score_tags(
            ["python", "javascript", "docker", "api", "cli", "testing", "git"],
            "coding",
        )
        assert score == 1.0

    def test_score_unknown_domain(self):
        """Tags for unknown domain are all free-form."""
        from utils.metadata import score_tags

        score = score_tags(["tag1", "tag2"], "nonexistent")
        assert score == 0.2  # 2 freeform * 0.1 = 0.2


class TestAICategorization:
    """Test AI categorization with taxonomy-aware prompts."""

    def test_build_taxonomy_prompt_section(self):
        """Taxonomy prompt section includes domains, sub-categories, and preferred tags."""
        from utils.metadata import _build_taxonomy_prompt_section

        result = _build_taxonomy_prompt_section()
        assert "coding" in result
        assert "python" in result
        assert "finance" in result
        assert "tax" in result
        assert "sub-categories" in result
        # vocabulary tags included
        assert "preferred tags" in result

    @pytest.mark.asyncio
    async def test_ai_categorize_returns_sub_category(self):
        """AI categorization returns sub_category and tags when available."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "domain": "coding",
                        "sub_category": "python",
                        "tags": ["web-scraping", "beautifulsoup"],
                        "keywords": ["scraping", "html"],
                        "summary": "A Python web scraping script",
                    })
                }
            }]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("utils.metadata.httpx.AsyncClient") as MockAsyncClient:
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)

            from utils.metadata import ai_categorize

            result = await ai_categorize(
                "import requests\nfrom bs4 import BeautifulSoup",
                "scraper.py",
                "smart",
            )

        assert result["suggested_domain"] == "coding"
        assert result["sub_category"] == "python"
        assert "web-scraping" in result["tags"]
        assert "beautifulsoup" in result["tags"]

    @pytest.mark.asyncio
    async def test_ai_categorize_invalid_subcategory_defaults(self):
        """Invalid sub_category falls back to DEFAULT_SUB_CATEGORY."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "domain": "coding",
                        "sub_category": "nonexistent_category",
                        "keywords": ["test"],
                        "summary": "A test",
                    })
                }
            }]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("utils.metadata.httpx.AsyncClient") as MockAsyncClient:
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)

            from utils.metadata import ai_categorize

            result = await ai_categorize("test content", "test.py", "smart")

        assert result["sub_category"] == "general"

    @pytest.mark.asyncio
    async def test_ai_categorize_old_format_fallback(self):
        """Old-format AI response (domain only) still works with defaults."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "choices": [{
                "message": {
                    "content": json.dumps({
                        "domain": "finance",
                        "keywords": ["tax"],
                        "summary": "Tax document",
                    })
                }
            }]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)

        with patch("utils.metadata.httpx.AsyncClient") as MockAsyncClient:
            MockAsyncClient.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            MockAsyncClient.return_value.__aexit__ = AsyncMock(return_value=None)

            from utils.metadata import ai_categorize

            result = await ai_categorize("tax deductions", "taxes.pdf", "smart")

        assert result["suggested_domain"] == "finance"
        assert result["sub_category"] == "general"
        assert result.get("tags") == []


class TestWatcherDomainDetection:
    """Test folder-based domain and sub-category detection."""

    def test_detect_domain_simple(self):
        """Domain detected from first-level folder."""
        from scripts.watch_ingest import _detect_domain

        with patch("config.WATCH_FOLDER", "/watch"):
            domain, sub = _detect_domain("/watch/coding/file.py")
            assert domain == "coding"
            assert sub == ""

    def test_detect_domain_with_subcategory(self):
        """Sub-category detected from second-level folder."""
        from scripts.watch_ingest import _detect_domain

        with patch("config.WATCH_FOLDER", "/watch"):
            domain, sub = _detect_domain("/watch/coding/python/file.py")
            assert domain == "coding"
            assert sub == "python"

    def test_detect_domain_invalid_subcategory(self):
        """Invalid sub-category folder ignored."""
        from scripts.watch_ingest import _detect_domain

        with patch("config.WATCH_FOLDER", "/watch"):
            domain, sub = _detect_domain("/watch/coding/nonexistent_sub/file.py")
            assert domain == "coding"
            assert sub == ""

    def test_detect_domain_inbox(self):
        """Inbox folder returns empty domain (triggers AI)."""
        from scripts.watch_ingest import _detect_domain

        with patch("config.WATCH_FOLDER", "/watch"):
            domain, sub = _detect_domain("/watch/inbox/file.pdf")
            assert domain == ""
            assert sub == ""

    def test_detect_domain_unknown(self):
        """Unknown folder returns empty domain."""
        from scripts.watch_ingest import _detect_domain

        with patch("config.WATCH_FOLDER", "/watch"):
            domain, sub = _detect_domain("/watch/unknown/file.txt")
            assert domain == ""
            assert sub == ""


class TestTaxonomyRouter:
    """Test taxonomy API endpoint logic."""

    def test_create_domain_request_model(self):
        """CreateDomainRequest has correct defaults."""
        from app.routers.taxonomy import CreateDomainRequest

        req = CreateDomainRequest(name="test")
        assert req.name == "test"
        assert req.icon == "file"
        assert req.sub_categories == ["general"]

    def test_create_subcategory_request_model(self):
        """CreateSubCategoryRequest requires domain and name."""
        from app.routers.taxonomy import CreateSubCategoryRequest

        req = CreateSubCategoryRequest(domain="coding", name="rust")
        assert req.domain == "coding"
        assert req.name == "rust"

    def test_update_artifact_taxonomy_request(self):
        """UpdateArtifactTaxonomyRequest handles optional fields."""
        from app.routers.taxonomy import UpdateArtifactTaxonomyRequest

        req = UpdateArtifactTaxonomyRequest(
            artifact_id="abc-123",
            sub_category="python",
            tags=["data-pipeline", "etl"],
        )
        assert req.artifact_id == "abc-123"
        assert req.sub_category == "python"
        assert req.tags == ["data-pipeline", "etl"]

    def test_update_artifact_taxonomy_request_partial(self):
        """UpdateArtifactTaxonomyRequest allows updating just sub_category or tags."""
        from app.routers.taxonomy import UpdateArtifactTaxonomyRequest

        req_sub = UpdateArtifactTaxonomyRequest(
            artifact_id="abc-123", sub_category="devops"
        )
        assert req_sub.sub_category == "devops"
        assert req_sub.tags is None

        req_tags = UpdateArtifactTaxonomyRequest(
            artifact_id="abc-123", tags=["ci-cd"]
        )
        assert req_tags.sub_category is None
        assert req_tags.tags == ["ci-cd"]

    def test_merge_tags_request_model(self):
        """MergeTagsRequest requires source and target."""
        from app.routers.taxonomy import MergeTagsRequest

        req = MergeTagsRequest(source_tag="py", target_tag="python")
        assert req.source_tag == "py"
        assert req.target_tag == "python"


class TestSuggestTagsEndpoint:
    """Test tag suggestion endpoint."""

    @pytest.mark.asyncio
    async def test_suggest_tags_with_domain(self):
        """Suggest tags returns vocabulary tags for a specific domain."""
        with patch("app.routers.taxonomy.get_neo4j") as mock_neo4j:
            mock_driver = MagicMock()
            mock_session = MagicMock()
            mock_result = MagicMock()
            mock_result.__iter__ = MagicMock(return_value=iter([]))
            mock_session.run.return_value = mock_result
            mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
            mock_neo4j.return_value = mock_driver

            from app.routers.taxonomy import suggest_tags_endpoint

            result = await suggest_tags_endpoint(domain="coding", prefix="", limit=10)
            assert len(result) > 0
            assert all(r["source"] == "vocabulary" for r in result)
            tag_names = [r["name"] for r in result]
            assert "python" in tag_names

    @pytest.mark.asyncio
    async def test_suggest_tags_with_prefix(self):
        """Suggest tags filters by prefix."""
        with patch("app.routers.taxonomy.get_neo4j") as mock_neo4j:
            mock_driver = MagicMock()
            mock_session = MagicMock()
            mock_result = MagicMock()
            mock_result.__iter__ = MagicMock(return_value=iter([]))
            mock_session.run.return_value = mock_result
            mock_driver.session.return_value.__enter__ = MagicMock(return_value=mock_session)
            mock_driver.session.return_value.__exit__ = MagicMock(return_value=False)
            mock_neo4j.return_value = mock_driver

            from app.routers.taxonomy import suggest_tags_endpoint

            result = await suggest_tags_endpoint(domain="coding", prefix="py", limit=30)
            assert len(result) > 0
            assert all(r["name"].startswith("py") for r in result)


class TestIngestionWithTaxonomy:
    """Test that ingestion pipeline passes taxonomy fields through."""

    def test_ingest_file_request_has_sub_category(self):
        """IngestFileRequest model accepts sub_category."""
        try:
            from app.routers.ingestion import IngestFileRequest
        except ImportError:
            pytest.skip("routers.ingestion not importable (missing host deps)")
            return

        req = IngestFileRequest(
            file_path="/archive/coding/python/script.py",
            domain="coding",
            sub_category="python",
            tags="data-pipeline,etl",
        )
        assert req.sub_category == "python"
        assert req.tags == "data-pipeline,etl"

    def test_recategorize_request_has_sub_category(self):
        """RecategorizeRequest model accepts sub_category."""
        try:
            from app.routers.artifacts import RecategorizeRequest
        except ImportError:
            pytest.skip("routers.artifacts not importable (missing host deps)")
            return

        req = RecategorizeRequest(
            artifact_id="abc-123",
            new_domain="projects",
            sub_category="active",
        )
        assert req.sub_category == "active"
