# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
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
    load_registry,
    parse_pack_json,
    remove_installed,
    save_install_state,
    upsert_installed,
    verify_archive_sha256,
)
from core.utils.loop_local import LoopLocal
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
_install_lock = LoopLocal(lambda: asyncio.Lock())


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
    enqueue_recompute: bool = True,
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

    Staging: the staging directory is removed after the install, on
    success or failure, unless ``keep_staging`` is True. A directory left
    by an earlier run is removed before the download: on macOS the archive
    is a bind mount, and writing over a stale tarball there fails with
    EDEADLK. Half-ingested artifacts dedup cleanly on re-run because
    content_hash already protects us.
    """
    async with _install_lock.get():
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
        if not _remove_staging(pack_staging):
            raise PackError(
                f"Pack {pack.id!r}: a staging directory from an earlier run "
                f"could not be removed ({pack_staging}). Remove it and retry."
            )
        archive_path = pack_staging / "pack.tar.gz"
        try:
            pack_staging.mkdir(parents=True, exist_ok=True)
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
            # AF-051: a version bump replaces the state record but previously left
            # the prior version's artifacts in Chroma + Neo4j as orphans. Now that
            # the new version is committed, remove the superseded artifacts through
            # the content-lifecycle coordinator (fans across all stores + busts
            # caches). Only fires on a real version change; best-effort per artifact.
            if existing is not None and existing.version != pack.version:
                from app.deps import get_neo4j
                from app.services.content_lifecycle import remove_content
                _neo4j = get_neo4j()
                _new_ids = set(ingested_artifact_ids)
                for _old_aid in existing.artifact_ids:
                    if _old_aid in _new_ids:
                        continue
                    try:
                        await asyncio.to_thread(remove_content, _old_aid, neo4j=_neo4j)
                    except Exception as _cleanup_exc:  # noqa: BLE001
                        log_swallowed_error(
                            "services.knowledge_packs.version_bump_cleanup",
                            _cleanup_exc,
                        )
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
            if not enqueue_recompute:
                return record
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
                _remove_staging(pack_staging)


def _remove_staging(path: Path) -> bool:
    """Remove a pack staging directory; True when it is gone.

    Each failure is logged rather than ignored: ``ignore_errors=True`` hid the
    removals that failed on the macOS bind mount, and the next install of the
    same pack then failed on the tarball they left behind.
    """
    if not path.exists():
        return True

    def _log_failure(_func: Callable[..., Any], failed_path: str, exc: BaseException) -> None:
        log_swallowed_error(
            "app.services.knowledge_packs.cleanup_staging", exc,
            context={"staging_path": failed_path},
        )

    shutil.rmtree(path, onexc=_log_failure)
    return not path.exists()


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
    async with _install_lock.get():
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

        # AF-055: mirror install_pack's post-install recompute — removing a
        # pack's artifacts changes the same layout (compute_umap_3d, which
        # also busts the /graph/map serving cache) and trust
        # (compute_trust_state) state that installing one does, so an
        # uninstall must trigger the same recompute. Best-effort: a queue
        # hiccup must never fail an otherwise-successful uninstall.
        try:
            from app.db.redis.processor_queue import enqueue_job  # noqa: PLC0415
            from app.processor.jobs.compute_trust_state import ComputeTrustStateJob  # noqa: PLC0415
            from app.processor.jobs.compute_umap_3d import ComputeUmap3DJob  # noqa: PLC0415

            enqueue_job(ComputeTrustStateJob(), payload={})
            enqueue_job(ComputeUmap3DJob(), payload={})
            logger.info("uninstall_pack.recompute_enqueued pack=%s", pack_id)
        except Exception as exc:  # noqa: BLE001 — recompute is best-effort
            log_swallowed_error("services.knowledge_packs.uninstall_recompute", exc)

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
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:  # follow_redirects: operator-curated pack URL; streamed to disk (exceeds 8MB guard cap)
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
    """Remove an artifact from Neo4j *and* every retrieval store (Chroma + BM25
    + SPLADE) plus the query caches, via the content-lifecycle coordinator.

    Returns the ``{"deleted": bool, ...}`` shape :func:`uninstall_pack` expects.
    """
    from app.deps import get_neo4j
    from app.services.content_lifecycle import remove_content

    neo4j = get_neo4j()
    result = await asyncio.to_thread(remove_content, artifact_id, neo4j=neo4j)
    return {
        "deleted": result.found,
        "chunk_ids": result.chunk_ids,
        "domain": result.domain,
    }


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


# ── Re-index installed pack content ──────────────────────────────────────

# Why a knowledge-pack artifact was not re-indexed; the reindex endpoint
# reports these keys in ``skipped_reasons``.
PACK_SKIP_NO_TAG = "pack_tag_missing"
PACK_SKIP_NOT_IN_REGISTRY = "pack_not_in_registry"
PACK_SKIP_VERSION_CHANGED = "pack_version_changed"
PACK_SKIP_UNPUBLISHED = "pack_unpublished"
PACK_SKIP_FILE_MISSING = "pack_file_missing"
PACK_SKIP_FORGOTTEN = "forgotten"
PACK_SKIP_NOT_REINDEXED = "not_reindexed"


def _pack_tag(tags_json: Any, prefix: str) -> str:
    """The value of the first ``<prefix><value>`` tag, or ``""``."""
    try:
        tags = json.loads(tags_json) if isinstance(tags_json, str) else tags_json
    except (TypeError, ValueError):
        return ""
    for tag in tags if isinstance(tags, list) else ():
        if isinstance(tag, str) and tag.startswith(prefix):
            return tag[len(prefix):]
    return ""


def _guarded_reindex_ingest(
    wanted: set[tuple[str, str]], results: dict[tuple[str, str], str],
) -> IngestFn:
    """An :data:`IngestFn` that force re-ingests only the ``wanted`` (domain,
    filename) pairs and records each one's outcome in ``results``."""
    from app.services.ingestion import accepted_file_domain, ingest_file

    async def _ingest(
        file_path: Path,
        domain: str,
        sub_category: str,
        tags: tuple[str, ...],
        provenance: dict[str, str],
    ) -> dict[str, Any]:
        # The domain ingest_file will file this under, so the key matches the
        # artifact's recorded domain.
        domain = accepted_file_domain(domain)
        key = (domain, file_path.name)
        if key not in wanted:
            return {"status": "skipped", "reason": "not_in_kb"}
        results[key] = "error"
        result = await ingest_file(
            file_path=str(file_path),
            domain=domain,
            sub_category=sub_category,
            tags=json.dumps(list(tags)),
            client_source="knowledge-pack",
            extra_metadata=dict(provenance),
            force_reindex=True,
            on_forgotten="skip",
        )
        if result.get("status") in ("success", "updated"):
            results[key] = "reindexed"
        elif result.get("reason") == "forgotten":
            results[key] = PACK_SKIP_FORGOTTEN
        else:
            results[key] = PACK_SKIP_NOT_REINDEXED
        return result

    return _ingest


async def reindex_pack_artifacts(artifacts: Sequence[Mapping[str, Any]]) -> dict[str, str]:
    """Re-stage the packs these artifacts came from and force re-ingest them.

    Pack staging is deleted after install, so a pack artifact has no file to
    re-read: the pack tarball is its source. The artifacts are grouped by their
    ``pack:<id>`` tag, and each pack is installed once, into a throwaway state
    file (the real one would skip an installed pack) and the default staging
    root (so each file's path matches the one it was first ingested from). The
    ingest re-indexes only the (domain, filename) pairs of the given artifacts,
    which are all in the knowledge base: a pack document deleted or forgotten
    since install is never brought back.

    Returns ``{artifact_id: outcome}``, where outcome is ``"reindexed"``,
    ``"error"`` or one of the ``PACK_SKIP_*`` reasons.
    """
    outcomes: dict[str, str] = {}
    by_pack: dict[str, list[Mapping[str, Any]]] = {}
    for a in artifacts:
        pack_id = _pack_tag(a.get("tags"), "pack:")
        if pack_id:
            by_pack.setdefault(pack_id, []).append(a)
        else:
            outcomes[str(a.get("id", ""))] = PACK_SKIP_NO_TAG
    if not by_pack:
        return outcomes

    try:
        registry = load_registry(default_registry_path())
    except PackError as exc:
        log_swallowed_error("app.services.knowledge_packs.reindex_registry", exc)
        for group in by_pack.values():
            for a in group:
                outcomes[str(a.get("id", ""))] = "error"
        return outcomes

    for pack_id, group in by_pack.items():
        pack = registry.get(pack_id)
        targets: list[Mapping[str, Any]] = []
        for a in group:
            aid = str(a.get("id", ""))
            installed_version = _pack_tag(a.get("tags"), "pack-version:")
            if pack is None:
                outcomes[aid] = PACK_SKIP_NOT_IN_REGISTRY
            elif not pack.download_url:
                outcomes[aid] = PACK_SKIP_UNPUBLISHED
            elif installed_version and installed_version != pack.version:
                # Re-staging the registry's version would replace this
                # document's text with another release's; that is an upgrade.
                outcomes[aid] = PACK_SKIP_VERSION_CHANGED
            else:
                targets.append(a)
        if pack is None or not targets:
            continue

        wanted = {(str(a.get("domain") or ""), str(a.get("filename") or "")) for a in targets}
        results: dict[tuple[str, str], str] = {}
        try:
            with tempfile.TemporaryDirectory(prefix="cerid-pack-reindex-") as tmp:
                await install_pack(
                    pack,
                    state_path=Path(tmp) / "installed_packs.json",
                    staging_root=default_staging_root(),
                    download=_default_download,
                    ingest=_guarded_reindex_ingest(wanted, results),
                    # Already installed under this licence; re-staging the
                    # same version takes on no new obligation.
                    allowed_license_categories=frozenset({pack.license_category}),
                    # Reindex runs a page at a time and re-stages a pack once per
                    # page; it adds no artifacts, so the layout and trust jobs
                    # would only stack up behind each other.
                    enqueue_recompute=False,
                )
        except Exception as exc:  # noqa: BLE001 — one pack's failure must not abort the batch
            log_swallowed_error(
                "app.services.knowledge_packs.reindex_pack", exc,
                context={"pack_id": pack_id, "artifacts": len(targets)},
            )
            for a in targets:
                outcomes[str(a.get("id", ""))] = "error"
            continue
        for a in targets:
            key = (str(a.get("domain") or ""), str(a.get("filename") or ""))
            outcomes[str(a.get("id", ""))] = results.get(key, PACK_SKIP_FILE_MISSING)
    return outcomes


# ── Processor-queue introspection (async install status) ─────────────────

def active_install_jobs(redis_client: Any | None = None) -> dict[str, str]:
    """Return ``{pack_id: job_id}`` for queued or running pack-install jobs.

    Drives the registry endpoint's ``installing`` flag and lets the
    install endpoint return the existing job instead of double-enqueueing
    when the wizard fires the same install twice. Reads the processor
    queue's own key layout (pending priority lists + running set) so no
    parallel bookkeeping can drift from the queue.
    """
    from app.db.redis.processor_queue import (  # noqa: PLC0415
        _RUNNING_KEY,
        _job_key,
        _queue_key,
    )
    from app.processor.jobs.knowledge_pack_install import (  # noqa: PLC0415
        KnowledgePackInstallJob,
    )
    from core.processor.priority import priority_order  # noqa: PLC0415

    if redis_client is None:
        from app.deps import get_redis  # noqa: PLC0415
        redis_client = get_redis()

    def _s(v: Any) -> str:
        return v.decode() if isinstance(v, bytes) else str(v)

    job_ids: list[str] = []
    for priority in priority_order():
        job_ids.extend(_s(j) for j in redis_client.lrange(_queue_key(priority), 0, -1))
    job_ids.extend(_s(j) for j in redis_client.smembers(_RUNNING_KEY))

    active: dict[str, str] = {}
    for job_id in job_ids:
        job_type = redis_client.hget(_job_key(job_id), "job_type")
        if job_type is None or _s(job_type) != KnowledgePackInstallJob.job_type:
            continue
        payload_raw = redis_client.hget(_job_key(job_id), "payload")
        if not payload_raw:
            continue
        try:
            payload = json.loads(_s(payload_raw))
        except (ValueError, TypeError) as exc:
            log_swallowed_error("app.services.knowledge_packs.active_jobs", exc)
            continue
        pack_id = payload.get("pack_id")
        if pack_id:
            active[str(pack_id)] = job_id
    return active


def enqueue_install_job(pack_id: str, redis_client: Any | None = None) -> str:
    """Enqueue a :class:`KnowledgePackInstallJob` and return its job id."""
    from app.db.redis.processor_queue import enqueue_job  # noqa: PLC0415
    from app.processor.jobs.knowledge_pack_install import (  # noqa: PLC0415
        KnowledgePackInstallJob,
    )

    return enqueue_job(
        KnowledgePackInstallJob(pack_id=pack_id),
        payload={"pack_id": pack_id},
        redis_client=redis_client,
    )
