# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Knowledge-pack management endpoints.

REST surface for the knowledge-pack harness:

* ``GET /knowledge_packs/registry`` — discoverable pack catalogue
  (entries carry ``installed`` / ``installing`` flags)
* ``GET /knowledge_packs/installed`` — what the user has installed
* ``POST /knowledge_packs/{pack_id}/install`` — queue an install job
* ``DELETE /knowledge_packs/{pack_id}`` — uninstall a previously
  installed pack and remove its artifacts

Install is a queued processor job (``KnowledgePackInstallJob``). It used
to run synchronously in-process; three concurrent installs inside a
memory-capped container OOM'd the server mid-beta (2026-07-12 triage),
so the endpoint now returns 202 with a ``job_id`` and clients poll the
registry's ``installing`` flag (or ``/processor/recent``) for completion.
"""
from __future__ import annotations

import asyncio
import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from app.services.knowledge_packs import (
    active_install_jobs,
    default_registry_path,
    default_state_path,
    enqueue_install_job,
    uninstall_pack_default,
)
from core.knowledge.packs import (
    PackError,
    find_installed,
    load_install_state,
    load_registry,
)
from core.utils.swallowed import log_swallowed_error

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
    installed: bool = False
    installing: bool = False


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


class InstallQueuedResponse(BaseModel):
    """202 body — the install now runs as a background processor job."""

    job_id: str
    status: str = "queued"


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

    # Installed / installing flags — the setup wizard rendered Install
    # buttons for already-installed packs because the catalogue carried
    # no state (2026-07-12 triage). Both lookups are best-effort: a
    # broken state file or unreachable Redis must not take the catalogue
    # down with it.
    installed_ids: set[str] = set()
    try:
        installed_ids = {p.pack_id for p in load_install_state(default_state_path())}
    except Exception as exc:  # noqa: BLE001 — catalogue must render regardless
        log_swallowed_error("app.routers.knowledge_packs.install_state", exc)
    installing_ids: set[str] = set()
    try:
        installing_ids = set(await asyncio.to_thread(active_install_jobs))
    except Exception as exc:  # noqa: BLE001 — catalogue must render regardless
        log_swallowed_error("app.routers.knowledge_packs.active_jobs", exc)

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
            installed=pack.id in installed_ids,
            installing=pack.id in installing_ids,
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


@router.post(
    "/{pack_id}/install",
    status_code=202,
    response_model=InstallQueuedResponse,
    responses={200: {"description": "Pack already installed at this version"}},
)
async def install_pack_endpoint(pack_id: str):
    """Queue an install job for a pack. Idempotent.

    * 202 ``{"job_id": ..., "status": "queued"}`` — job enqueued (or an
      identical install is already queued/running; its job_id is returned).
    * 200 ``{"status": "already_installed"}`` — same id+version installed.
    """
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
        existing = find_installed(load_install_state(default_state_path()), pack_id)
    except PackError as exc:
        raise HTTPException(status_code=500, detail=f"Install state malformed: {exc}")
    if existing is not None and existing.version == pack.version:
        return JSONResponse(status_code=200, content={"status": "already_installed"})

    try:
        active = await asyncio.to_thread(active_install_jobs)
        existing_job = active.get(pack_id)
        if existing_job is not None:
            return InstallQueuedResponse(job_id=existing_job, status="queued")
        job_id = await asyncio.to_thread(enqueue_install_job, pack_id)
    except Exception as exc:
        logger.exception("Install enqueue for %s failed: %s", pack_id, exc)
        raise HTTPException(status_code=500, detail=f"Install enqueue failed: {exc}")
    return InstallQueuedResponse(job_id=job_id, status="queued")


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
