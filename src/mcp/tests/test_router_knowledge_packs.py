# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``app.routers.knowledge_packs``.

Calls the endpoint functions directly with the service layer monkey-
patched — keeps the test fast and avoids spinning up FastAPI's full
ASGI stack just to exercise four endpoints.
"""
from __future__ import annotations

import json

import pytest
from fastapi import HTTPException

from app.routers import knowledge_packs as router_mod
from core.knowledge.packs import InstalledPack, PackError, PackManifest

VALID_PACK = {
    "id": "router-test",
    "name": "Router test pack",
    "version": "1.0.0",
    "description": "Fixture for router tests",
    "domain": "general",
    "sub_category": "reference",
    "tags": ["fixture"],
    "license": "CC0-1.0",
    "size_bytes": 1234,
    "artifact_count": 3,
    "download_url": "https://example.org/pack.tar.gz",
    "sha256": "a" * 64,
    "provenance": {"source": "test"},
}


def _write_registry(tmp_path, entries):
    p = tmp_path / "registry.json"
    p.write_text(json.dumps({"schema_version": 1, "packs": entries}))
    return p


# ── /knowledge_packs/registry ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_get_registry_groups_by_domain(tmp_path, monkeypatch):
    pack_a = {**VALID_PACK, "id": "pack-a", "domain": "general"}
    pack_b = {**VALID_PACK, "id": "pack-b", "domain": "coding"}
    pack_c = {**VALID_PACK, "id": "pack-c", "domain": "general"}
    registry_path = _write_registry(tmp_path, [pack_a, pack_b, pack_c])
    monkeypatch.setattr(router_mod, "default_registry_path", lambda: registry_path)

    resp = await router_mod.get_registry_endpoint()
    assert resp.schema_version == 1
    assert set(resp.packs_by_domain) == {"general", "coding"}
    general_ids = [p.id for p in resp.packs_by_domain["general"]]
    assert general_ids == ["pack-a", "pack-c"]  # sorted by id within domain
    assert resp.packs_by_domain["coding"][0].id == "pack-b"


@pytest.mark.asyncio
async def test_get_registry_translates_pack_error_to_500(tmp_path, monkeypatch):
    bad_path = tmp_path / "bad.json"
    bad_path.write_text("{ not json")
    monkeypatch.setattr(router_mod, "default_registry_path", lambda: bad_path)
    with pytest.raises(HTTPException) as exc_info:
        await router_mod.get_registry_endpoint()
    assert exc_info.value.status_code == 500


# ── /knowledge_packs/installed ──────────────────────────────────────────

@pytest.mark.asyncio
async def test_list_installed_returns_summaries(tmp_path, monkeypatch):
    state_path = tmp_path / "state.json"
    record = InstalledPack(
        pack_id="x", version="1.0.0",
        installed_at="2026-05-10T00:00:00+00:00",
        domain="general", sha256="a" * 64,
        artifact_ids=("art-1", "art-2"),
    )
    state_path.write_text(json.dumps({
        "schema_version": 1,
        "packs": [record.to_dict()],
    }))
    monkeypatch.setattr(router_mod, "default_state_path", lambda: state_path)

    resp = await router_mod.list_installed_endpoint()
    assert resp.schema_version == 1
    assert len(resp.packs) == 1
    assert resp.packs[0].pack_id == "x"
    assert resp.packs[0].artifact_count == 2


@pytest.mark.asyncio
async def test_list_installed_handles_empty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(router_mod, "default_state_path", lambda: tmp_path / "missing.json")
    resp = await router_mod.list_installed_endpoint()
    assert resp.packs == []


# ── /knowledge_packs/{pack_id}/install ──────────────────────────────────

@pytest.mark.asyncio
async def test_install_endpoint_404_when_pack_missing(tmp_path, monkeypatch):
    registry_path = _write_registry(tmp_path, [])
    monkeypatch.setattr(router_mod, "default_registry_path", lambda: registry_path)

    with pytest.raises(HTTPException) as exc_info:
        await router_mod.install_pack_endpoint("missing")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_install_endpoint_happy_path(tmp_path, monkeypatch):
    registry_path = _write_registry(tmp_path, [VALID_PACK])
    monkeypatch.setattr(router_mod, "default_registry_path", lambda: registry_path)

    captured: dict = {}

    async def fake_install(pack: PackManifest, *, keep_staging: bool = False):
        captured["pack_id"] = pack.id
        return InstalledPack(
            pack_id=pack.id, version=pack.version,
            installed_at="2026-05-10T00:00:00+00:00",
            domain=pack.domain, sha256=pack.sha256,
            artifact_ids=("a", "b", "c"),
        )

    monkeypatch.setattr(router_mod, "install_pack_default", fake_install)

    resp = await router_mod.install_pack_endpoint("router-test")
    assert resp.pack_id == "router-test"
    assert resp.artifact_count == 3
    assert captured["pack_id"] == "router-test"


@pytest.mark.asyncio
async def test_install_endpoint_translates_pack_error_to_422(tmp_path, monkeypatch):
    registry_path = _write_registry(tmp_path, [VALID_PACK])
    monkeypatch.setattr(router_mod, "default_registry_path", lambda: registry_path)

    async def fake_install(pack: PackManifest, *, keep_staging: bool = False):
        raise PackError("archive sha256 mismatch")

    monkeypatch.setattr(router_mod, "install_pack_default", fake_install)
    with pytest.raises(HTTPException) as exc_info:
        await router_mod.install_pack_endpoint("router-test")
    assert exc_info.value.status_code == 422


# ── /knowledge_packs/{pack_id} (DELETE) ────────────────────────────────

@pytest.mark.asyncio
async def test_uninstall_endpoint_happy_path(monkeypatch):
    async def fake_uninstall(pack_id: str):
        assert pack_id == "router-test"
        return {
            "pack_id": pack_id, "status": "uninstalled",
            "removed": 5, "missing": 1,
        }

    monkeypatch.setattr(router_mod, "uninstall_pack_default", fake_uninstall)
    resp = await router_mod.uninstall_pack_endpoint("router-test")
    assert resp.status == "uninstalled"
    assert resp.removed == 5


@pytest.mark.asyncio
async def test_uninstall_endpoint_404_when_not_installed(monkeypatch):
    async def fake_uninstall(pack_id: str):
        return {
            "pack_id": pack_id, "status": "not_installed",
            "removed": 0, "missing": 0,
        }

    monkeypatch.setattr(router_mod, "uninstall_pack_default", fake_uninstall)
    with pytest.raises(HTTPException) as exc_info:
        await router_mod.uninstall_pack_endpoint("nope")
    assert exc_info.value.status_code == 404
