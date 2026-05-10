# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Knowledge-pack management endpoints.

REST surface for the knowledge-pack harness:

* ``GET /knowledge_packs/registry`` — discoverable pack catalogue
* ``GET /knowledge_packs/installed`` — what the user has installed
* ``POST /knowledge_packs/{pack_id}/install`` — start an install
* ``DELETE /knowledge_packs/{pack_id}`` — uninstall a previously
  installed pack and remove its artifacts

Install runs synchronously in-process — packs are small (< a few MB
typically) and the mcp instance already serialises bulk work via the
ingestion semaphore. If a pack ever ships large enough to exceed
request timeouts, swap to a job-id pattern (mirroring
``/ingestion/progress``).
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.knowledge_packs import (
    default_registry_path,
    default_state_path,
    install_pack_default,
    uninstall_pack_default,
)
from core.knowledge.packs import (
    PackError,
    load_install_state,
    load_registry,
)

router = APIRouter(prefix="/knowledge_packs", tags=["knowledge_packs"])
logger = logging.getLogger("ai-companion.knowledge_packs")


# ── Pydantic response shapes ────────────────────────────────────────────

class PackSummary(BaseModel):
    """Slim view of a registry entry for the discovery endpoint."""

    id: str
    name: str
    version: str
    description: str
    domain: str
    sub_category: str = ""
    tags: list[str] = Field(default_factory=list)
    license: str = ""
    size_bytes: int = 0
    artifact_count: int = 0
    download_url: str = ""
    sha256: str = ""
    provenance: dict[str, str] = Field(default_factory=dict)


class RegistryResponse(BaseModel):
    schema_version: int
    packs_by_domain: dict[str, list[PackSummary]]


class InstalledPackSummary(BaseModel):
    pack_id: str
    version: str
    installed_at: str
    domain: str
    sha256: str = ""
    artifact_count: int


class InstalledResponse(BaseModel):
    schema_version: int
    packs: list[InstalledPackSummary]


class InstallResponse(BaseModel):
    pack_id: str
    version: str
    installed_at: str
    domain: str
    artifact_count: int


class UninstallResponse(BaseModel):
    pack_id: str
    status: str
    removed: int
    missing: int


# ── Endpoints ──────────────────────────────────────────────────────────

@router.get("/registry", response_model=RegistryResponse)
async def get_registry_endpoint():
    """Return the available pack registry, grouped by target domain.

    The registry is a slim, JSON-serialisable manifest list — no
    archives are downloaded or parsed here. Use this to populate a
    "Knowledge Library" UI.
    """
    try:
        registry = load_registry(default_registry_path())
    except PackError as exc:
        logger.error("Registry load failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Registry malformed: {exc}")

    by_domain: dict[str, list[PackSummary]] = {}
    for pack in registry.values():
        summary = PackSummary(
            id=pack.id, name=pack.name, version=pack.version,
            description=pack.description, domain=pack.domain,
            sub_category=pack.sub_category, tags=list(pack.tags),
            license=pack.license, size_bytes=pack.size_bytes,
            artifact_count=pack.artifact_count,
            download_url=pack.download_url, sha256=pack.sha256,
            provenance=dict(pack.provenance),
        )
        by_domain.setdefault(pack.domain, []).append(summary)
    for entries in by_domain.values():
        entries.sort(key=lambda p: p.id)
    return RegistryResponse(schema_version=1, packs_by_domain=by_domain)


@router.get("/installed", response_model=InstalledResponse)
async def list_installed_endpoint():
    """Return the packs currently installed in this KB."""
    try:
        state = load_install_state(default_state_path())
    except PackError as exc:
        logger.error("Install-state load failed: %s", exc)
        raise HTTPException(status_code=500, detail=f"Install state malformed: {exc}")
    return InstalledResponse(
        schema_version=1,
        packs=[
            InstalledPackSummary(
                pack_id=p.pack_id, version=p.version,
                installed_at=p.installed_at, domain=p.domain,
                sha256=p.sha256, artifact_count=len(p.artifact_ids),
            )
            for p in state
        ],
    )


@router.post("/{pack_id}/install", response_model=InstallResponse)
async def install_pack_endpoint(pack_id: str):
    """Install a pack by id. Idempotent at the same version."""
    try:
        registry = load_registry(default_registry_path())
    except PackError as exc:
        raise HTTPException(status_code=500, detail=f"Registry malformed: {exc}")
    pack = registry.get(pack_id)
    if pack is None:
        raise HTTPException(
            status_code=404,
            detail=f"Pack {pack_id!r} not in registry",
        )
    try:
        record = await install_pack_default(pack)
    except PackError as exc:
        # Validation / archive integrity failure — operator must fix the pack.
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        logger.exception("Install of %s failed: %s", pack_id, exc)
        raise HTTPException(status_code=500, detail=f"Install failed: {exc}")
    return InstallResponse(
        pack_id=record.pack_id,
        version=record.version,
        installed_at=record.installed_at,
        domain=record.domain,
        artifact_count=len(record.artifact_ids),
    )


@router.delete("/{pack_id}", response_model=UninstallResponse)
async def uninstall_pack_endpoint(pack_id: str):
    """Remove a previously-installed pack along with all its artifacts."""
    try:
        summary = await uninstall_pack_default(pack_id)
    except Exception as exc:
        logger.exception("Uninstall of %s failed: %s", pack_id, exc)
        raise HTTPException(status_code=500, detail=f"Uninstall failed: {exc}")
    if summary.get("status") == "not_installed":
        raise HTTPException(
            status_code=404,
            detail=f"Pack {pack_id!r} is not installed",
        )
    return UninstallResponse(
        pack_id=summary["pack_id"],
        status=summary["status"],
        removed=summary["removed"],
        missing=summary["missing"],
    )
