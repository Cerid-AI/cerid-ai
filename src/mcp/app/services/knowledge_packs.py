# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Install / uninstall orchestration for knowledge packs.

The pure data layer lives in :mod:`core.knowledge.packs`. This module
adds the side effects: download → verify → extract → ingest → record.

Design notes
------------

* Packs are *staged* into ``${ARCHIVE_PATH}/.knowledge-packs/<id>-<ver>/``
  so the existing :func:`app.services.ingestion.ingest_file` pipeline
  picks them up unchanged — same dedup, same layout-aware parsing,
  same quality scoring, same entity backfill. We never bypass the KB
  ingestion contract.
* Install / uninstall are dependency-injectable: the public functions
  accept ``download``, ``ingest``, and ``delete`` callables so unit
  tests can drive the orchestration without httpx, chromadb, or Neo4j.
  Production wiring is in the ``_default_*`` factories at module bottom.
* State is persisted at ``${CERID_STATE_DIR}/installed_packs.json``
  (atomic rename via :func:`core.knowledge.packs.save_install_state`).
* Concurrency: only one pack install runs at a time. The shared
  ingestion semaphore in :mod:`app.services.ingestion` already throttles
  per-file writes; serialising packs avoids two concurrent installs
  racing on the same staging directory or state file.
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import tarfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

from core.knowledge.packs import (
    DEFAULT_INSTALL_CATEGORIES,
    InstalledPack,
    PackError,
    PackManifest,
    assert_archive_path_safe,
    find_installed,
    load_install_state,
    parse_pack_json,
    remove_installed,
    save_install_state,
    upsert_installed,
    verify_archive_sha256,
)
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.knowledge_packs")

# DI signatures: defining these once keeps the public ``install_pack``
# / ``uninstall_pack`` parameter lists short and readable. The ingest
# signature carries an extra ``provenance`` dict so each artifact gets
# stamped with ``source_url`` / ``license_spdx`` / ``retrieved_at`` /
# ``recipe_rev`` / ``adapter`` at chromadb-write time (Phase 8a).
DownloadFn = Callable[[str, Path], Awaitable[None]]
IngestFn = Callable[
    [Path, str, str, tuple[str, ...], dict[str, str]],
    Awaitable[dict[str, Any]],
]
DeleteFn = Callable[[str], Awaitable[dict[str, Any]]]

# Single global lock — install / uninstall never run concurrently.
_install_lock = asyncio.Lock()


# ── Public orchestration ──────────────────────────────────────────────────

async def install_pack(
    pack: PackManifest,
    *,
    state_path: Path,
    staging_root: Path,
    download: DownloadFn,
    ingest: IngestFn,
    keep_staging: bool = False,
    allowed_license_categories: frozenset[str] = DEFAULT_INSTALL_CATEGORIES,
) -> InstalledPack:
    """Install a knowledge pack: download, verify, extract, ingest, record.

    Idempotency: if ``state_path`` already records an installed pack at
    the same id+version, this is a no-op (returns the existing record).
    A different version of the same id replaces the prior entry — but
    callers that want to keep the old artifacts around should uninstall
    first.

    License gate: refuses to proceed unless ``pack.license_category``
    is in ``allowed_license_categories``. The default
    ``DEFAULT_INSTALL_CATEGORIES`` excludes ``share_alike`` so the
    operator must opt in explicitly (e.g. via ``--allow-share-alike``)
    when they're willing to inherit the share-alike obligation across
    embeddings + paraphrased outputs. ``unknown`` licenses are always
    rejected — a curator must pin a recognized SPDX id.

    The function does *not* delete on failure — the staging directory
    is preserved for inspection unless ``keep_staging`` is False, and
    even then only on success. Half-ingested artifacts will dedup
    cleanly on re-run because content_hash already protects us.
    """
    async with _install_lock:
        existing = find_installed(load_install_state(state_path), pack.id)
        if existing and existing.version == pack.version:
            logger.info(
                "Pack %s@%s already installed (%d artifacts) — skipping",
                pack.id, pack.version, len(existing.artifact_ids),
            )
            return existing
        category = pack.license_category
        if category == "unknown":
            raise PackError(
                f"Pack {pack.id!r} license {pack.license!r} is not in the "
                f"recognized SPDX list — refusing install. Curator must pin "
                f"a known SPDX id or extend core.knowledge.packs._LICENSE_CATEGORY.",
            )
        if category not in allowed_license_categories:
            raise PackError(
                f"Pack {pack.id!r} license category {category!r} is not in "
                f"the allowed set {sorted(allowed_license_categories)!r}. "
                f"Pass allowed_license_categories explicitly (e.g. include "
                f"'share_alike') to opt in.",
            )
        if not pack.download_url:
            raise PackError(
                f"Pack {pack.id!r} is in the catalog but no tarball has been "
                f"published yet (status={pack.status!r}). Wait for a curator "
                f"build, or run scripts/build_knowledge_pack to author your "
                f"own version."
            )
        pack_staging = staging_root / f"{pack.id}-{pack.version}"
        pack_staging.mkdir(parents=True, exist_ok=True)
        archive_path = pack_staging / "pack.tar.gz"
        try:
            logger.info(
                "Installing pack %s@%s from %s",
                pack.id, pack.version, pack.download_url,
            )
            await download(pack.download_url, archive_path)
            verify_archive_sha256(archive_path, pack.sha256)
            extract_dir = pack_staging / "extracted"
            content_files = _extract_pack(archive_path, extract_dir, pack=pack)

            # Per-file overrides from pack.json take precedence over the
            # pack-level defaults (sub_category, tags, domain).
            file_overrides = {f.path: f for f in pack.files}
            ingested_artifact_ids: list[str] = []
            # Pack-level provenance — stamped onto every chunk's
            # chromadb metadata via the Phase-8a `extra_metadata` channel.
            # Lower-cased keys + string values stay portable across
            # downstream filtering UIs.
            base_provenance: dict[str, str] = {
                "pack_id": pack.id,
                "pack_version": pack.version,
                "pack_license_spdx": pack.license,
                "pack_license_category": pack.license_category,
                "pack_source_url": str(pack.provenance.get("source", "")),
                "pack_curator": str(pack.provenance.get("curator", "")),
                "pack_adapter": (
                    pack.build.adapter if pack.build else "tarball"
                ),
                "pack_sha256": pack.sha256,
                "pack_retrieved_at": utcnow_iso(),
            }
            for rel_path, abs_path in content_files:
                override = file_overrides.get(rel_path)
                domain = (override.domain if override else "") or pack.domain
                sub_category = (
                    override.sub_category if override else ""
                ) or pack.sub_category
                tags = override.tags if override else pack.tags
                # Combine pack-level tags with the pack id so an operator
                # can audit what came from where without reading state.
                ingest_tags = tuple(tags) + (
                    f"pack:{pack.id}",
                    f"pack-version:{pack.version}",
                )
                file_provenance = {
                    **base_provenance,
                    "pack_file": rel_path,
                }
                try:
                    result = await ingest(
                        abs_path, domain, sub_category, ingest_tags,
                        file_provenance,
                    )
                except Exception as exc:  # noqa: BLE001 — observability boundary
                    log_swallowed_error(
                        "app.services.knowledge_packs.ingest_one", exc,
                    )
                    logger.warning(
                        "Pack %s: ingest failed for %s: %s",
                        pack.id, rel_path, exc,
                    )
                    continue
                if result.get("status") in ("success", "duplicate", "updated"):
                    aid = result.get("artifact_id")
                    if aid:
                        ingested_artifact_ids.append(str(aid))

            record = InstalledPack(
                pack_id=pack.id,
                version=pack.version,
                installed_at=utcnow_iso(),
                domain=pack.domain,
                sha256=pack.sha256,
                artifact_ids=tuple(ingested_artifact_ids),
            )
            new_state = upsert_installed(load_install_state(state_path), record)
            save_install_state(state_path, new_state)
            logger.info(
                "Pack %s@%s installed: %d artifacts (%d files in archive)",
                pack.id, pack.version, len(ingested_artifact_ids),
                len(content_files),
            )
            # Post-install recompute: pack content is ingested + extracted, but the
            # layout (compute_umap_3d, which also busts the /graph/map serving cache)
            # and trust (compute_trust_state) jobs are nightly — so freshly installed
            # packs are invisible in the Constellation + trust-less until then.
            # Enqueue them now so the pack appears promptly. Best-effort: a queue
            # hiccup must never fail an otherwise-successful install.
            try:
                from app.db.redis.processor_queue import enqueue_job  # noqa: PLC0415
                from app.processor.jobs.compute_trust_state import ComputeTrustStateJob  # noqa: PLC0415
                from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob  # noqa: PLC0415

                enqueue_job(ComputeTrustStateJob(), payload={})
                enqueue_job(ComputeUmap3DJob(), payload={})
                logger.info("install_pack.recompute_enqueued pack=%s", pack.id)
            except Exception as exc:  # noqa: BLE001 — recompute is best-effort
                log_swallowed_error("services.knowledge_packs.install_recompute", exc)

            return record
        finally:
            if not keep_staging:
                try:
                    shutil.rmtree(pack_staging, ignore_errors=True)
                except Exception as exc:  # noqa: BLE001 — cleanup best-effort
                    log_swallowed_error(
                        "app.services.knowledge_packs.cleanup_staging", exc,
                    )


async def uninstall_pack(
    pack_id: str,
    *,
    state_path: Path,
    delete: DeleteFn,
) -> dict[str, Any]:
    """Remove every artifact ingested by ``pack_id`` and drop the state record.

    Returns a summary ``{"removed": int, "missing": int, "pack_id": str}``.
    Missing artifacts (deleted out-of-band by the operator) are tolerated:
    we drop the record either way.
    """
    async with _install_lock:
        state = load_install_state(state_path)
        record = find_installed(state, pack_id)
        if record is None:
            return {"removed": 0, "missing": 0, "pack_id": pack_id, "status": "not_installed"}
        removed = 0
        missing = 0
        for aid in record.artifact_ids:
            try:
                result = await delete(aid)
            except Exception as exc:  # noqa: BLE001 — observability boundary
                log_swallowed_error(
                    "app.services.knowledge_packs.delete_one", exc,
                )
                logger.warning(
                    "Pack %s: delete failed for %s: %s", pack_id, aid, exc,
                )
                missing += 1
                continue
            if result.get("deleted"):
                removed += 1
            else:
                missing += 1
        new_state = remove_installed(state, pack_id)
        save_install_state(state_path, new_state)
        logger.info(
            "Pack %s uninstalled: %d artifacts removed, %d missing",
            pack_id, removed, missing,
        )
        return {
            "removed": removed,
            "missing": missing,
            "pack_id": pack_id,
            "status": "uninstalled",
        }


# ── Archive extraction (tar-traversal-safe) ──────────────────────────────

def _extract_pack(
    archive_path: Path, dest_dir: Path, *, pack: PackManifest,
) -> list[tuple[str, Path]]:
    """Extract a pack archive to ``dest_dir`` and return ``[(rel_path, abs_path), ...]``.

    Validates the embedded ``pack.json`` against the registry-supplied
    metadata: id + version must match. Refuses any archive member that
    tries to escape ``dest_dir`` via ``..`` or absolute paths.

    Returns only files under ``content/`` — pack.json is consumed for
    validation but never ingested.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    content_files: list[tuple[str, Path]] = []
    pack_json_seen = False
    with tarfile.open(archive_path, "r:gz") as tf:
        for member in tf.getmembers():
            if member.isdir() or member.islnk() or member.issym():
                continue  # skip dirs + links; links are a traversal vector
            if not member.isfile():
                continue
            assert_archive_path_safe(member.name, archive_root=pack.id)
            extracted_path = (dest_dir / member.name).resolve()
            # Defence in depth: even after path-safety check, ensure the
            # final resolved path is inside dest_dir.
            try:
                extracted_path.relative_to(dest_dir.resolve())
            except ValueError as exc:
                raise PackError(
                    f"Pack {pack.id}: archive member {member.name} resolves "
                    f"outside extraction root"
                ) from exc
            extracted_path.parent.mkdir(parents=True, exist_ok=True)
            fh = tf.extractfile(member)
            if fh is None:
                continue
            payload = fh.read()
            extracted_path.write_bytes(payload)
            if member.name == "pack.json":
                _validate_embedded_manifest(payload, pack)
                pack_json_seen = True
                continue
            if member.name.startswith("content/") or member.name.startswith("./content/"):
                rel = member.name.lstrip("./")
                content_files.append((rel, extracted_path))
    if not pack_json_seen:
        raise PackError(
            f"Pack {pack.id}: archive missing required pack.json member"
        )
    if not content_files:
        raise PackError(
            f"Pack {pack.id}: archive contains no content/ files to ingest"
        )
    return content_files


def _validate_embedded_manifest(blob: bytes, registry_pack: PackManifest) -> None:
    """Ensure the pack.json inside the archive matches the registry id+version."""
    embedded = parse_pack_json(blob)
    if embedded.id != registry_pack.id:
        raise PackError(
            f"Archive pack.json id {embedded.id!r} does not match registry id "
            f"{registry_pack.id!r}"
        )
    if embedded.version != registry_pack.version:
        raise PackError(
            f"Archive pack.json version {embedded.version!r} does not match "
            f"registry version {registry_pack.version!r}"
        )


# ── Default production wiring ────────────────────────────────────────────

async def _default_download(url: str, dest: Path) -> None:
    """Stream a URL to disk. Supports both ``https?://`` and ``file://`` schemes.

    ``file://`` is required so a curator can ship locally-built packs
    (e.g. via :mod:`scripts.build_knowledge_pack`) without first
    publishing them — useful for self-hosted deployments where the
    repo's own ``data/eval-corpus/v1/`` is the canonical source.
    Plain HTTP(S) is used in production / community-distribution cases.
    """
    import shutil
    import urllib.parse

    import httpx

    dest.parent.mkdir(parents=True, exist_ok=True)
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme == "file":
        # urllib.parse handles ``file:///abs/path`` correctly; reject
        # netloc-bearing forms (``file://host/path``) since we'd silently
        # ignore the host and that's a footgun.
        if parsed.netloc and parsed.netloc.lower() not in ("", "localhost"):
            raise ValueError(
                f"file:// URL with non-local netloc not supported: {url!r}"
            )
        src_path = Path(urllib.parse.unquote(parsed.path))
        if not src_path.is_file():
            raise FileNotFoundError(f"Pack source missing: {src_path}")
        await asyncio.to_thread(shutil.copyfile, src_path, dest)
        return

    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async with client.stream("GET", url) as resp:
            resp.raise_for_status()
            with open(dest, "wb") as fh:
                async for chunk in resp.aiter_bytes(chunk_size=1 << 20):
                    fh.write(chunk)


async def _default_ingest(
    file_path: Path,
    domain: str,
    sub_category: str,
    tags: tuple[str, ...],
    provenance: dict[str, str],
) -> dict[str, Any]:
    """Wrap :func:`app.services.ingestion.ingest_file` for pack content.

    ``provenance`` is forwarded through ``extra_metadata`` so each chunk
    written to chromadb carries the pack-level audit trail (Phase 8a).
    """
    from app.services.ingestion import ingest_file

    tags_json = json.dumps(list(tags))
    return await ingest_file(
        file_path=str(file_path),
        domain=domain,
        sub_category=sub_category,
        tags=tags_json,
        client_source="knowledge-pack",
        extra_metadata=dict(provenance),
    )


async def _default_delete(artifact_id: str) -> dict[str, Any]:
    """Remove an artifact from Neo4j *and* its chunks from chromadb."""
    import config
    from app.db.neo4j.artifacts import delete_artifact
    from app.deps import get_chroma, get_neo4j

    neo4j = get_neo4j()
    chroma = get_chroma()
    result = await asyncio.to_thread(delete_artifact, neo4j, artifact_id)
    chunk_ids = result.get("chunk_ids") or []
    domain = result.get("domain") or ""
    if chunk_ids and domain:
        coll_name = config.collection_name(domain)
        try:
            collection = await asyncio.to_thread(chroma.get_collection, name=coll_name)
            await asyncio.to_thread(collection.delete, ids=chunk_ids)
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error(
                "app.services.knowledge_packs.delete_chunks", exc,
            )
    return result


def default_state_path() -> Path:
    """``${CERID_STATE_DIR}/installed_packs.json`` — same dir as entity backfill."""
    import os

    base = os.getenv("CERID_STATE_DIR", ".cerid-state")
    return Path(base) / "installed_packs.json"


def default_registry_path() -> Path:
    """Path to the active knowledge-pack registry.

    Honours ``CERID_KNOWLEDGE_PACKS_REGISTRY`` so an operator can point
    at a curated/community registry without forking the repo. Default
    is the slim registry shipped at ``config/knowledge_packs.json``
    inside the package import root.
    """
    import os

    override = os.getenv("CERID_KNOWLEDGE_PACKS_REGISTRY")
    if override:
        return Path(override)
    # __file__ lives at src/mcp/app/services/knowledge_packs.py — climb to
    # the mcp package root + descend into config/.
    return Path(__file__).resolve().parents[2] / "config" / "knowledge_packs.json"


def default_staging_root() -> Path:
    """``${ARCHIVE_PATH}/.knowledge-packs`` — staging lives under the archive
    so :func:`app.services.ingestion.ingest_file` accepts the path under its
    archive-root validation guard."""
    import config

    return Path(config.ARCHIVE_PATH) / ".knowledge-packs"


async def install_pack_default(pack: PackManifest, *, keep_staging: bool = False) -> InstalledPack:
    """Production-wired :func:`install_pack`."""
    return await install_pack(
        pack,
        state_path=default_state_path(),
        staging_root=default_staging_root(),
        download=_default_download,
        ingest=_default_ingest,
        keep_staging=keep_staging,
    )


async def uninstall_pack_default(pack_id: str) -> dict[str, Any]:
    """Production-wired :func:`uninstall_pack`."""
    return await uninstall_pack(
        pack_id,
        state_path=default_state_path(),
        delete=_default_delete,
    )
