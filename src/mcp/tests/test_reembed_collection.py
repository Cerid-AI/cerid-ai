# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for scripts/reembed_collection.py (Workstream E Phase 5c).

Covers the pure helpers (slug, target naming, version routing). The
end-to-end ChromaDB roundtrip is exercised manually per the playbook
in ``docs/EMBEDDING_MIGRATIONS.md`` — wiring full mocks here would add
maintenance cost without catching bugs the dry-run mode already prevents.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

# scripts/ isn't on the package path; add it explicitly.
_SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(_SCRIPTS))

import reembed_collection as rec  # noqa: E402

import config  # noqa: E402


def test_slug_strips_path_separators():
    """Forward slashes from HuggingFace IDs should not appear in collection names."""
    assert "/" not in rec._slug("Snowflake/snowflake-arctic-embed-l-v2.0")


def test_slug_preserves_safe_chars():
    """Letters, digits, dots, dashes, underscores survive."""
    assert rec._slug("arctic-embed_l.v2.0-test") == "arctic-embed_l.v2.0-test"


def test_slug_replaces_unsafe():
    """Anything outside [A-Za-z0-9_.-] becomes underscore."""
    assert rec._slug("foo/bar:baz qux") == "foo_bar_baz_qux"


def test_target_collection_name_shape():
    """<source>__<slug(version)> — separator is double underscore."""
    name = rec._target_collection_name("code", "arctic-embed-l-v2.0")
    assert name.endswith("__arctic-embed-l-v2.0")
    assert config.collection_name("code") in name
    # Operator can grep for the suffix to find all migration targets
    assert "__" in name


def test_target_collection_name_sanitizes_version():
    """Slashes in the version label get sanitized in the collection name."""
    name = rec._target_collection_name("code", "Snowflake/foo:bar")
    assert "/" not in name
    assert ":" not in name


# ---------------------------------------------------------------------------
# Per-domain version routing (config helper added in Phase 5c)
# ---------------------------------------------------------------------------


def test_embedding_version_for_domain_falls_back_to_global():
    """Domains without an override use EMBEDDING_MODEL_VERSION."""
    # No mutation needed — the dict is empty by default
    assert (
        config.embedding_version_for_domain("nonexistent_domain")
        == config.EMBEDDING_MODEL_VERSION
    )


def test_embedding_version_for_domain_honors_override(monkeypatch):
    """Per-domain override wins over the global.

    The helper lives in ``config.settings`` and reads the dict from its
    own module globals — so we monkeypatch the dict's *contents* via
    ``setitem`` rather than rebinding the module attribute, which keeps
    both the package re-export and the original module reference
    pointing at the same mutated dict.
    """
    from config import settings as _settings

    monkeypatch.setitem(
        _settings.EMBEDDING_MODEL_VERSIONS_PER_DOMAIN,
        "code",
        "arctic-embed-l-v2.0",
    )
    assert _settings.embedding_version_for_domain("code") == "arctic-embed-l-v2.0"
    # Other domains still get the global
    assert (
        _settings.embedding_version_for_domain("finance")
        == _settings.EMBEDDING_MODEL_VERSION
    )


# ---------------------------------------------------------------------------
# CLI contract (argparse smoke)
# ---------------------------------------------------------------------------


def test_main_requires_domain():
    """Missing required args raises SystemExit (argparse default)."""
    with pytest.raises(SystemExit):
        rec.main(["--target-model", "x", "--target-version", "y"])


def test_main_requires_target_model():
    """Missing --target-model exits via argparse."""
    with pytest.raises(SystemExit):
        rec.main(["--domain", "code", "--target-version", "v1"])


def test_main_requires_target_version():
    """Missing --target-version exits via argparse."""
    with pytest.raises(SystemExit):
        rec.main(["--domain", "code", "--target-model", "fake/model"])


def test_main_dry_and_execute_mutually_exclusive():
    """argparse rejects both flags."""
    with pytest.raises(SystemExit):
        rec.main([
            "--domain", "code",
            "--target-model", "fake",
            "--target-version", "v1",
            "--dry-run",
            "--execute",
        ])


# NOTE: end-to-end dry-run requires a live ChromaDB; the operator runs
# that path inside the docker container per the playbook. Wiring a full
# ChromaDB mock here would not catch the bugs the playbook's manual
# pre-flight checklist already prevents.
