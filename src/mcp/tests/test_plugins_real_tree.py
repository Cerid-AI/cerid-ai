# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The shipped plugin tree, walked as shipped (F213 / F214).

Every other plugin test builds synthetic manifests under ``tmp_path``. That
is exactly why six of the fourteen community plugins under the top-level
``plugins/`` tree shipped with manifests the loader rejects on every startup,
and why the management API could not see any of them: no test ever looked at
the real directories.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
COMMUNITY_PLUGINS = REPO_ROOT / "plugins"


def _plugin_dirs(base: Path) -> list[Path]:
    return [
        d
        for d in sorted(base.iterdir())
        if d.is_dir()
        and not d.name.startswith(("_", "."))
        and (d / "manifest.json").exists()
    ]


def _all_shipped_plugin_dirs() -> list[Path]:
    import config

    dirs = _plugin_dirs(Path(config.PLUGIN_DIR))
    dirs += _plugin_dirs(COMMUNITY_PLUGINS)
    return dirs


def test_the_tree_under_test_is_the_real_one():
    assert COMMUNITY_PLUGINS.is_dir()
    names = {d.name for d in _plugin_dirs(COMMUNITY_PLUGINS)}
    assert {"gmail", "outlook", "github-issues"} <= names, names


@pytest.mark.parametrize(
    "plugin_dir", _all_shipped_plugin_dirs(), ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_shipped_manifest_passes_loader_validation(plugin_dir: Path):
    """A manifest the loader rejects is a plugin that never loads."""
    from plugins import _validate_manifest

    manifest = json.loads((plugin_dir / "manifest.json").read_text(encoding="utf-8"))
    _validate_manifest(manifest, plugin_dir)


# Directories that advertise a plugin in the catalog but ship no plugin.py,
# so the loader raises PluginLoadError for them on every startup. Both are
# manifest + LICENSE only — there is no implementation to repair, so the
# resolution is removing the catalog entry, which is an operator decision
# (F214). Shrink-only: an entry that grows a plugin.py fails below, so this
# list cannot outlive the directories it records.
_CATALOG_ONLY = {"analytics", "workflow-builder"}


@pytest.mark.parametrize(
    "plugin_dir", _all_shipped_plugin_dirs(), ids=lambda p: f"{p.parent.name}/{p.name}"
)
def test_shipped_plugin_ships_an_entry_point(plugin_dir: Path):
    """``manifest.json`` with no ``plugin.py`` is a PluginLoadError at boot."""
    has_entry_point = (plugin_dir / "plugin.py").exists()
    if plugin_dir.name in _CATALOG_ONLY:
        assert not has_entry_point, (
            f"{plugin_dir.name} now ships a plugin.py — drop it from _CATALOG_ONLY"
        )
        pytest.skip(f"{plugin_dir.name}: catalog entry with no implementation (F214)")
    assert has_entry_point, (
        f"{plugin_dir} advertises a plugin in the catalog but ships no plugin.py"
    )


def test_management_api_sees_the_community_plugin_tree():
    """F213 — the loader scans both trees; the router must scan both too."""
    from app.routers.plugins import _discover_manifests

    discovered = _discover_manifests()
    assert "github-issues" in discovered, sorted(discovered)
    assert "gmail-connector" in discovered, sorted(discovered)
    assert discovered["github-issues"]["_dir"].startswith(str(COMMUNITY_PLUGINS))


def test_lookup_of_a_community_plugin_is_not_404():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from app.routers import plugins

    app = FastAPI()
    app.include_router(plugins.router)
    resp = TestClient(app).get("/plugins/github-issues")

    assert resp.status_code == 200, resp.text
    assert resp.json()["plugin_type"] == "connector"
