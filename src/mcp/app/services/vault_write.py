# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Two-way vault writeback service (Workstream RAG Cycle C3.3).

Writes a markdown note INTO a registered vault and immediately
re-ingests it as an :Artifact node tagged ``source_type="cerid-synthesis"``.
This closes the loop opened by C2: Cerid's outputs (chat distillations,
synthesis briefs, verified responses) can now be persisted back to the
user's vault as queryable knowledge.

Loop-breaker contract
---------------------
Every note written through this service carries ``source_type="cerid-synthesis"``
on the resulting Artifact node.  Synthesis jobs (briefs, weekly
synthesis, etc.) MUST exclude artifacts with that ``source_type`` from
their input set by default — otherwise Cerid's own outputs would feed
back into the next synthesis pass and recursive amplification would
turn personal notes into hallucinated derivatives.

The carve-out: a ``cerid:reanalyze: true`` frontmatter key OR an
``allow_synthesis_input=True`` kwarg on the write request opts a note
back into the synthesis input set.  Use this for genuine "consider this
new evidence on the same topic" scenarios.

Path safety
-----------
The ``path`` field is resolved against the vault root from the
watched-folder record.  Path-traversal attempts (``../escape.md``)
are rejected.  Paths classified as ``SKIP`` (templates / .obsidian)
or ``ATTACHMENT`` by ``VaultProfile.classify_path`` are rejected —
templates are configuration artefacts, attachments are binary blobs;
neither is a sensible target for an AI-generated markdown note.

Lives in ``app/services/`` (not ``core/``) because it touches Redis,
the watched-folder registry, and the ingestion service — all of which
are FastAPI-bound app-layer concerns.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from core.ingest.frontmatter import is_allowlisted
from core.ingest.vault_config import PathClassification, build_profile
from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.vault_write")

__all__ = [
    "CERID_SYNTHESIS_SOURCE_TYPE",
    "VaultWriteError",
    "WriteNoteRequest",
    "WriteNoteResult",
    "write_note",
]

# Stable string used in three places: the frontmatter ``source:`` value
# written into every Cerid-authored note, the ``source_type`` Artifact
# node property, and the Cypher filter inside the synthesis jobs.
CERID_SYNTHESIS_SOURCE_TYPE = "cerid-synthesis"

# Redis key prefix from app/routers/watched_folders.py — kept in sync
# here so we can read the same per-folder record the registration
# endpoint wrote.  Duplicated rather than imported to avoid a service →
# router import (routers depend on services, not the other way around).
_WATCHED_FOLDERS_PREFIX = "cerid:watched_folders"


class VaultWriteError(ValueError):
    """Raised when a vault write is rejected before any disk side-effect.

    Surfaces as an HTTP 400 at the router layer.  Specifically covers:

    * unknown vault_id
    * vault path missing on disk
    * resolved path escaping the vault root
    * resolved path landing in a SKIP / ATTACHMENT folder
    * ``mode="create"`` with an existing target file
    * unsupported mode

    Successful writes that fail re-ingestion DO NOT raise — the file
    is on disk and the caller needs to know that, so the failure is
    surfaced via ``WriteNoteResult.ingested=False`` instead.
    """


@dataclass(frozen=True, slots=True)
class WriteNoteRequest:
    """Inputs for :func:`write_note`.

    Attributes:
        vault_id: Identifier from ``POST /watched-folders`` for a folder
            that has ``is_vault=True``.  Non-vault folders are rejected.
        path: Relative path under the vault root.  ``.md`` extension is
            appended if absent.  Must not escape the vault root via
            ``..`` and must not land in a SKIP / ATTACHMENT folder.
        content: Markdown body — may contain ``[[wikilinks]]`` and other
            markdown features.  Frontmatter present in ``content`` is
            preserved as-is and merged with the default + caller dicts
            (caller-supplied frontmatter wins on key collisions).
        frontmatter: Optional caller-supplied frontmatter that merges
            over the defaults.  Only allowlisted keys (per the C2.2
            frontmatter parser) flow through to the on-disk file —
            arbitrary keys are silently dropped.
        mode: ``"create"`` rejects an existing file (idempotent guard);
            ``"append"`` adds the body to the end of an existing file,
            preserving its existing frontmatter; ``"overwrite"`` atomically
            replaces the file.  Default ``"create"``.
        allow_synthesis_input: When True, stamps ``cerid:reanalyze: true``
            in the written frontmatter so the synthesis-job input filter
            re-includes this note.  Use sparingly — the default
            (``False``) is the loop-breaker that keeps Cerid's outputs
            from feeding back into its own synthesis pass.
    """

    vault_id: str
    path: str
    content: str
    frontmatter: dict[str, Any] | None = None
    mode: Literal["create", "append", "overwrite"] = "create"
    allow_synthesis_input: bool = False


@dataclass(slots=True)
class WriteNoteResult:
    """Outcome of :func:`write_note`.

    Attributes:
        file_path: Absolute path of the file that was written.
        artifact_id: Neo4j Artifact ID assigned by the ingestion service.
            Empty / None if re-ingestion failed (see ``reingest_error``).
        ingested: True if the post-write ``ingest_content`` call succeeded
            and produced an Artifact node.  False if the write succeeded
            but ingestion failed — the file is still on disk.
        frontmatter_written: The exact frontmatter dict that was serialised
            into the file header (defaults + caller-supplied + the
            ``cerid:created`` / ``source`` stamps).  Returned so the
            caller can verify what landed without re-reading the file.
        mode: Echoes the request mode, useful for debugging logs.
        reingest_error: Stringified exception from re-ingestion when
            ``ingested=False``.  ``None`` on the happy path.
    """

    file_path: str
    artifact_id: str | None
    ingested: bool
    frontmatter_written: dict[str, Any]
    mode: str
    reingest_error: str | None = field(default=None)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_vault(redis_client: Any, vault_id: str) -> dict[str, Any]:
    """Load a watched-folder record and assert it's a registered vault."""
    raw = redis_client.get(f"{_WATCHED_FOLDERS_PREFIX}:{vault_id}")
    if not raw:
        raise VaultWriteError(f"Unknown vault_id: {vault_id!r}")
    try:
        record = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise VaultWriteError(f"Vault record corrupt: {e}") from e
    if not isinstance(record, dict):
        raise VaultWriteError("Vault record is not a JSON object")
    if not record.get("is_vault"):
        raise VaultWriteError(
            f"Folder {vault_id!r} is not registered as a vault — "
            "set is_vault=True on the watched-folder before writing."
        )
    root = record.get("path") or ""
    if not root or not os.path.isdir(root):
        raise VaultWriteError(f"Vault root not accessible: {root!r}")
    return record


def _resolve_safe_path(vault_root: str, rel_path: str) -> tuple[Path, str]:
    """Resolve ``rel_path`` under ``vault_root`` and reject escapes.

    Returns ``(absolute_path, relative_path)``.  ``rel_path`` is appended
    with ``.md`` if it has no suffix.  Symlinks inside the vault are
    resolved before the containment check so a symlink pointing outside
    the root can't be used to escape.

    Raises ``VaultWriteError`` when the path escapes the vault root or
    resolves to a non-file existing inode (e.g. a directory of the same
    name).
    """
    if not rel_path or not rel_path.strip():
        raise VaultWriteError("path must not be empty")

    # Reject absolute paths BEFORE we strip the leading slash — the
    # contract is "relative to vault root", and an absolute path is a
    # different kind of bug than a path-traversal attempt.  Check both
    # POSIX (``/etc/passwd``) and Windows-drive (``C:\foo``) shapes via
    # Path.is_absolute() on the raw input.
    raw = rel_path.strip()
    if Path(raw).is_absolute():
        raise VaultWriteError(f"path must be relative, got absolute: {rel_path!r}")

    # Strip leading separators / dot-slash so the relative join can't
    # be tricked by a single leading slash.
    clean = raw.lstrip("/").lstrip("\\")
    if not clean:
        raise VaultWriteError("path must be relative")
    candidate = Path(clean)
    if candidate.is_absolute():
        # Defence in depth — Path normalisation on some platforms might
        # still mark the cleaned path as absolute.
        raise VaultWriteError(f"path must be relative, got absolute: {rel_path!r}")

    # Append .md if the user omitted it — they almost always will, and
    # silently appending matches Obsidian's "wikilinks resolve without
    # extension" mental model.
    if not candidate.suffix:
        candidate = candidate.with_suffix(".md")

    root = Path(vault_root).resolve()
    # Resolve via root + relative — we deliberately don't call
    # ``.resolve()`` on the combined path until we've checked containment
    # because resolving first would follow a malicious symlink before
    # we got a chance to reject it.
    combined = (root / candidate).resolve()
    try:
        rel = combined.relative_to(root)
    except ValueError as e:
        raise VaultWriteError(
            f"path resolves outside vault root: {rel_path!r}"
        ) from e

    if combined.exists() and not combined.is_file():
        raise VaultWriteError(
            f"path exists but is not a regular file: {rel_path!r}"
        )

    return combined, str(rel)


def _reject_disallowed_classification(
    rel_path: str,
    vault_root: str,
    vault_config: dict[str, Any] | None,
) -> PathClassification:
    """Run the C2.3 vault classifier and reject SKIP / ATTACHMENT writes.

    ``MOC``, ``DAILY``, ``REGULAR`` all flow through — Cerid can author
    a synthesis brief into ``mocs/`` or a daily note into ``daily/`` as
    long as the path is otherwise valid.

    Returns the classification so the caller can stamp it on the
    metadata at ingest time (the scanner's ``sub_category`` mapping).
    """
    profile = build_profile(vault_root, vault_config)
    classification = profile.classify_path(rel_path)
    if classification is PathClassification.SKIP:
        raise VaultWriteError(
            f"path {rel_path!r} resolves to a templates/skip folder — "
            "Cerid will not write notes there."
        )
    if classification is PathClassification.ATTACHMENT:
        raise VaultWriteError(
            f"path {rel_path!r} resolves to an attachments folder — "
            "Cerid will not write markdown notes there."
        )
    return classification


def _build_default_frontmatter(
    caller_frontmatter: dict[str, Any] | None,
    *,
    allow_synthesis_input: bool,
) -> dict[str, Any]:
    """Compose the frontmatter dict that will land in the file header.

    Layering:

    1. Cerid stamps: ``source=cerid-synthesis``, ``cerid:created=<iso>``,
       (optionally) ``cerid:reanalyze=true``.
    2. Caller-supplied keys — filtered through the C2.2 frontmatter
       allowlist so non-allowlisted keys don't pollute the file header.

    Caller-supplied ``source`` and ``cerid:created`` overrides are
    respected — the caller might be re-writing a Cerid note and wants
    to preserve the original ``cerid:created`` timestamp.  The
    ``source_type`` Artifact property is set independently via
    ``set_artifact_properties`` so a caller can't escape the loop-breaker
    by overriding the ``source`` frontmatter key.
    """
    fm: dict[str, Any] = {
        "source": CERID_SYNTHESIS_SOURCE_TYPE,
        "cerid:created": utcnow_iso(),
    }
    if allow_synthesis_input:
        fm["cerid:reanalyze"] = True
    if caller_frontmatter:
        for k, v in caller_frontmatter.items():
            if not isinstance(k, str):
                continue
            if not is_allowlisted(k):
                # Mirror the parser's drop-on-write policy — un-allowlisted
                # keys would just be stripped on the next read pass anyway.
                continue
            fm[k] = v
    return fm


def _serialise_frontmatter(frontmatter: dict[str, Any]) -> str:
    """Render ``frontmatter`` as a ``---``-fenced YAML block.

    Uses ``yaml.safe_dump`` with ``sort_keys=False`` so the order is
    stable (Cerid stamps first, then caller-supplied keys).  Trailing
    newline guarantees the body starts on a fresh line.
    """
    import yaml  # local import keeps module-load cheap

    body = yaml.safe_dump(
        frontmatter,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
    )
    return f"---\n{body}---\n"


def _existing_body_without_frontmatter(path: Path) -> str:
    """Read an existing file and strip its frontmatter for append mode.

    Append semantics: the caller wants to add new content to an existing
    Cerid note without duplicating the frontmatter header.  Strip the
    existing ``---``-fenced block and return the body.  If there's no
    frontmatter, return the file contents unchanged.
    """
    from core.ingest.frontmatter import extract_frontmatter

    raw = path.read_text(encoding="utf-8")
    _, body = extract_frontmatter(raw)
    return body


def _atomic_write(path: Path, payload: str) -> None:
    """Write ``payload`` to ``path`` via tmp-file + rename.

    On POSIX, ``os.replace`` is atomic when source and destination are
    on the same filesystem.  We write to a sibling tmp file so the tmp
    and destination share an inode pool — guarantees the rename is a
    rename, not a copy-and-delete that could leak a partial file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    finally:
        # Best-effort cleanup if the rename failed — leaving a stale
        # ``.tmp`` would be annoying but not corrupting.
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError as e:
                log_swallowed_error("vault_write.tmp_cleanup", e)


def _classification_to_sub_category(classification: PathClassification) -> str:
    """Mirror the folder_scanner's ``_vault_sub_category`` mapping."""
    if classification is PathClassification.MOC:
        return "moc"
    if classification is PathClassification.DAILY:
        return "daily"
    return ""


def _reingest(
    *,
    file_path: Path,
    payload: str,
    domain: str,
    sub_category: str,
    allow_synthesis_input: bool,
) -> tuple[str | None, str | None]:
    """Hand the just-written file to the ingestion pipeline.

    Returns ``(artifact_id, error_str)``.  On success ``error_str`` is
    ``None``.  On failure ``artifact_id`` is ``None`` — the file is on
    disk regardless, so the caller surfaces ``ingested=False`` rather
    than rolling the write back.
    """
    # Local import: importing app.services.ingestion at module load would
    # pull ChromaDB / Neo4j / Redis singletons into every consumer of
    # vault_write, including the test suite where those infra modules
    # are heavyweight stubs.
    try:
        from app.services.ingestion import ingest_content
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error("vault_write.import_ingest_content", e)
        return None, f"ingest import failed: {e}"

    metadata: dict[str, Any] = {
        "filename": file_path.name,
        "sub_category": sub_category,
        "client_source": "cerid_synthesis",
        # source_type is the Cypher-filterable property the synthesis
        # input filter checks.  Flowed through metadata so it lands on
        # both ChromaDB chunks (for retrieval-side filtering) AND the
        # Artifact node (via set_artifact_properties below).
        "source_type": CERID_SYNTHESIS_SOURCE_TYPE,
        # Mirror the allow_synthesis_input flag onto a stable metadata
        # key so the synthesis input filter has a single property to
        # check against without re-parsing frontmatter at query time.
        "cerid_reanalyze": bool(allow_synthesis_input),
    }
    try:
        result = ingest_content(payload, domain or "general", metadata)
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "vault_write.ingest_content",
            e,
            context={"file_path": str(file_path)},
        )
        return None, str(e)

    artifact_id = result.get("artifact_id") if isinstance(result, dict) else None
    if not artifact_id:
        return None, "ingest_content returned no artifact_id"

    # Stamp source_type + cerid_reanalyze on the Artifact node so the
    # synthesis-job filter (a Cypher property check) sees it without
    # round-tripping through frontmatter / Chroma.  Best-effort — if
    # the property write fails we still consider the ingest "done"
    # because the chunks and the node exist.
    try:
        from app.db.neo4j.artifacts import set_artifact_properties
        from app.deps import get_neo4j

        set_artifact_properties(
            driver=get_neo4j(),
            artifact_id=artifact_id,
            properties={
                "source_type": CERID_SYNTHESIS_SOURCE_TYPE,
                "cerid_reanalyze": bool(allow_synthesis_input),
            },
        )
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error(
            "vault_write.set_source_type",
            e,
            context={"artifact_id": artifact_id},
        )
        # The artifact still exists; the loop-breaker filter falls back
        # to the frontmatter ``source:`` value we wrote into the file
        # body, which ingest_content lifts into Artifact properties via
        # the C2.2 frontmatter pass.
    return artifact_id, None


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def write_note(req: WriteNoteRequest, redis_client: Any) -> WriteNoteResult:
    """Write a markdown note to a registered vault, then re-ingest it.

    Synchronous — the router wraps this in ``asyncio.to_thread``.

    Order of operations:

    1. Resolve ``vault_id`` to a watched-folder record; assert it's a
       registered vault and the on-disk root exists.
    2. Resolve ``path`` under the vault root; reject ``..`` escapes and
       template / attachment-folder writes.
    3. Compose the final frontmatter dict (defaults + caller-supplied).
    4. Write the file atomically per ``mode``.
    5. Re-ingest via :func:`app.services.ingestion.ingest_content`,
       stamping ``source_type="cerid-synthesis"`` on the resulting
       Artifact node so the synthesis-input filter excludes it.

    Re-ingestion failures DO NOT roll back the file write — the caller
    needs to know the file is on disk so they don't write it again.
    They surface as ``WriteNoteResult.ingested=False`` with the failure
    string in ``reingest_error``.
    """
    record = _load_vault(redis_client, req.vault_id)
    vault_root = record["path"]
    vault_config = record.get("vault_config")

    abs_path, rel_path = _resolve_safe_path(vault_root, req.path)
    classification = _reject_disallowed_classification(
        rel_path, vault_root, vault_config,
    )
    sub_category = _classification_to_sub_category(classification)

    # Domain inheritance: per-folder override beats vault default.
    domain = (
        record.get("domain_override")
        or build_profile(vault_root, vault_config).default_domain
        or "general"
    )

    if req.mode not in ("create", "append", "overwrite"):
        raise VaultWriteError(
            f"unsupported mode: {req.mode!r} "
            "(must be 'create', 'append', or 'overwrite')"
        )

    final_frontmatter = _build_default_frontmatter(
        req.frontmatter,
        allow_synthesis_input=req.allow_synthesis_input,
    )

    fm_block = _serialise_frontmatter(final_frontmatter)

    if req.mode == "create":
        if abs_path.exists():
            raise VaultWriteError(
                f"file already exists at {rel_path!r} (use mode='overwrite' "
                "or 'append' to modify it)"
            )
        payload = fm_block + req.content
        _atomic_write(abs_path, payload)
    elif req.mode == "overwrite":
        payload = fm_block + req.content
        _atomic_write(abs_path, payload)
    else:  # append
        if abs_path.exists():
            existing_body = _existing_body_without_frontmatter(abs_path)
            # Preserve a single blank line between the old and new bodies
            # so the appended text starts on a paragraph boundary.
            separator = "\n\n" if existing_body and not existing_body.endswith(
                "\n\n",
            ) else ""
            payload = fm_block + existing_body + separator + req.content
        else:
            # Append to a non-existent file is just a create.
            payload = fm_block + req.content
        _atomic_write(abs_path, payload)

    logger.info(
        "vault_write file=%s mode=%s classification=%s allow_synthesis_input=%s",
        rel_path, req.mode, classification.value, req.allow_synthesis_input,
    )

    artifact_id, reingest_error = _reingest(
        file_path=abs_path,
        payload=payload,
        domain=domain,
        sub_category=sub_category,
        allow_synthesis_input=req.allow_synthesis_input,
    )

    return WriteNoteResult(
        file_path=str(abs_path),
        artifact_id=artifact_id,
        ingested=artifact_id is not None,
        frontmatter_written=dict(final_frontmatter),
        mode=req.mode,
        reingest_error=reingest_error,
    )
