# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the internal_only pattern matcher in sync-repos.py.

Regression coverage for a bug where `**/__pycache__/**` caused every path
to match because `pat.split('**')[0]` returned '' and `startswith('')`
is always True — effectively skipping every file in the sync.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "sync_repos", _ROOT / "scripts" / "sync-repos.py",
)
assert _SPEC is not None and _SPEC.loader is not None
sync_repos = importlib.util.module_from_spec(_SPEC)
sys.modules["sync_repos"] = sync_repos
_SPEC.loader.exec_module(sync_repos)

matches_internal_only = sync_repos.matches_internal_only


# The four pattern shapes used in .sync-manifest.yaml
PATTERNS = [
    "src/mcp/enterprise/**",              # dir/**
    "packages/desktop/**",                # dir/**
    "src/mcp/tests/test_trading_*.py",    # literal glob (fnmatch)
    "**/__pycache__/**",                  # **/name/**
    "src/mcp/config/settings_internal.py",  # literal path
]


class TestDirDoubleStar:
    def test_file_under_dir_matches(self):
        assert matches_internal_only("src/mcp/enterprise/abac.py", PATTERNS)

    def test_nested_file_under_dir_matches(self):
        assert matches_internal_only("src/mcp/enterprise/abac/policies/default.yaml", PATTERNS)

    def test_sibling_dir_does_not_match(self):
        assert not matches_internal_only("src/mcp/core/agents/query.py", PATTERNS)

    def test_dir_itself_as_file_does_not_false_match(self):
        # A file literally named src/mcp/enterprise (unusual but handled)
        assert matches_internal_only("src/mcp/enterprise", PATTERNS)


class TestAnyComponentDoubleStar:
    def test_pycache_anywhere_matches(self):
        assert matches_internal_only("src/mcp/__pycache__/foo.pyc", PATTERNS)
        assert matches_internal_only("src/web/src/deep/nested/__pycache__/x.pyc", PATTERNS)

    def test_path_without_pycache_does_not_match(self):
        # THE REGRESSION: this used to return True because prefix='' matched everything
        assert not matches_internal_only("docker-compose.yml", PATTERNS)
        assert not matches_internal_only("src/mcp/core/agents/verified_memory.py", PATTERNS)
        assert not matches_internal_only("scripts/start-cerid.sh", PATTERNS)
        assert not matches_internal_only("README.md", PATTERNS)

    def test_pycache_as_filename_does_not_match(self):
        # __pycache__ appearing only as basename (no directory component named that)
        assert not matches_internal_only("src/mcp/foo__pycache__bar.py", PATTERNS)


class TestLiteralGlob:
    def test_fnmatch_pattern_matches(self):
        assert matches_internal_only("src/mcp/tests/test_trading_agent.py", PATTERNS)
        assert matches_internal_only("src/mcp/tests/test_trading_proxy.py", PATTERNS)

    def test_fnmatch_pattern_does_not_over_match(self):
        assert not matches_internal_only("src/mcp/tests/test_memory.py", PATTERNS)
        assert not matches_internal_only("src/mcp/tests/test_pipeline_enhancements.py", PATTERNS)


class TestLiteralPath:
    def test_exact_path_matches(self):
        assert matches_internal_only(
            "src/mcp/config/settings_internal.py", PATTERNS,
        )

    def test_similar_path_does_not_match(self):
        assert not matches_internal_only(
            "src/mcp/config/settings.py", PATTERNS,
        )


class TestSyncSkipList:
    """Regression coverage for secret/cache files that must never enter the
    file-walk regardless of manifest contents."""

    def test_env_files_are_skipped(self):
        assert sync_repos._is_sync_skip(".env")
        assert sync_repos._is_sync_skip(".env.age")
        assert sync_repos._is_sync_skip(".env.local")
        assert sync_repos._is_sync_skip(".env.production")

    def test_env_example_is_NOT_skipped(self):
        # .env.example is a template and should still sync
        assert not sync_repos._is_sync_skip(".env.example")

    def test_cache_dirs_are_skipped(self):
        for path in [
            ".mypy_cache/cache.db",
            ".ruff_cache/0.15.2/abc",
            ".pytest_cache/v/cache/lastfailed",
            ".git/objects/pack",
            "node_modules/react/index.js",
            "packages/web/node_modules/react/index.js",
            "src/mcp/__pycache__/agents.pyc",
            ".venv/lib/python/site-packages/foo.py",
            ".DS_Store",
            "src/web/.DS_Store",
            ".next/cache/x",
            ".turbo/cache/y",
        ]:
            assert sync_repos._is_sync_skip(path), f"should skip: {path}"

    def test_normal_source_files_are_not_skipped(self):
        for path in [
            "src/mcp/core/agents/verified_memory.py",
            "docker-compose.yml",
            "scripts/start-cerid.sh",
            "CLAUDE.md",
            ".env.example",
            ".github/workflows/ci.yml",
        ]:
            assert not sync_repos._is_sync_skip(path), f"should not skip: {path}"


class TestEmptyPatterns:
    def test_empty_pattern_list(self):
        assert not matches_internal_only("anything.py", [])

    def test_none_of_patterns_match(self):
        assert not matches_internal_only("docker-compose.yml", ["foo/**", "bar.py"])


class TestRealManifestSmoke:
    """Load the actual manifest and spot-check the public-safe files from
    Batches 1-3 are NOT flagged as internal_only."""

    def test_batch_1_3_files_are_public_safe(self):
        import yaml
        manifest = yaml.safe_load(
            (_ROOT / ".sync-manifest.yaml").read_text(),
        )
        patterns = manifest.get("internal_only", [])
        public_safe = [
            "docker-compose.yml",
            "scripts/start-cerid.sh",
            "src/mcp/core/agents/verified_memory.py",
            "src/mcp/core/utils/circuit_breaker.py",
            "src/mcp/core/agents/hallucination/verification.py",
            "src/mcp/core/agents/hallucination/streaming.py",
        ]
        for path in public_safe:
            assert not matches_internal_only(path, patterns), (
                f"{path} should be public-safe but matched internal_only"
            )

    def test_known_internal_files_are_blocked(self):
        import yaml
        manifest = yaml.safe_load(
            (_ROOT / ".sync-manifest.yaml").read_text(),
        )
        patterns = manifest.get("internal_only", [])
        internal = [
            "src/mcp/app/routers/trading_proxy.py",
            "src/mcp/config/settings_internal.py",
            "packages/desktop/main.js",
        ]
        for path in internal:
            assert matches_internal_only(path, patterns), (
                f"{path} should be internal-only but was not matched"
            )
