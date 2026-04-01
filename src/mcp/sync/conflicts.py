# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Conflict detection and resolution for cross-machine sync."""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from enum import Enum
from typing import Any

from errors import SyncError
from sync._helpers import NEO4J_SUBDIR, _default_sync_dir, _ensure_dir

logger = logging.getLogger("ai-companion.sync")


class ConflictStrategy(str, Enum):
    """How to resolve an artifact modified on both machines since last sync."""

    REMOTE_WINS = "remote_wins"
    LOCAL_WINS = "local_wins"
    KEEP_BOTH = "keep_both"
    MANUAL_REVIEW = "manual_review"


@dataclass
class ConflictRecord:
    """One detected conflict between local and remote artifact versions."""

    artifact_id: str
    filename: str
    domain: str
    local_updated_at: str
    remote_updated_at: str
    local_content_hash: str
    remote_content_hash: str
    resolution: str = ""  # filled after resolution


def detect_conflicts(
    driver,
    remote_artifacts: list[dict[str, Any]],
    last_sync_at: str | None = None,
) -> list[ConflictRecord]:
    """
    Find artifacts modified on both machines since *last_sync_at*.

    A conflict = same artifact_id, different content_hash, both have
    updated_at newer than *last_sync_at*. If last_sync_at is None,
    no conflicts are detected (first sync).
    """
    if not last_sync_at or not remote_artifacts:
        return []

    # Build a lookup of remote artifacts that were modified after last sync
    remote_modified: dict[str, dict] = {}
    for row in remote_artifacts:
        aid = row.get("id")
        remote_updated = row.get("updated_at") or row.get("ingested_at") or ""
        if aid and remote_updated > last_sync_at:
            remote_modified[aid] = row

    if not remote_modified:
        return []

    # Query local artifacts that were also modified after last sync
    conflicts: list[ConflictRecord] = []
    ids_to_check = list(remote_modified.keys())

    try:
        with driver.session() as session:
            result = session.run(
                "UNWIND $ids AS aid "
                "MATCH (a:Artifact {id: aid}) "
                "WHERE coalesce(a.updated_at, a.ingested_at) > $since "
                "RETURN a.id AS id, a.updated_at AS updated_at, "
                "a.content_hash AS content_hash, a.filename AS filename, "
                "a.domain AS domain",
                ids=ids_to_check,
                since=last_sync_at,
            )
            for record in result:
                aid = record["id"]
                remote = remote_modified[aid]
                local_hash = record["content_hash"] or ""
                remote_hash = remote.get("content_hash", "")

                # Only a conflict if content actually differs
                if local_hash != remote_hash:
                    conflicts.append(ConflictRecord(
                        artifact_id=aid,
                        filename=record["filename"] or remote.get("filename", ""),
                        domain=record["domain"] or remote.get("domain", ""),
                        local_updated_at=record["updated_at"] or "",
                        remote_updated_at=remote.get("updated_at", ""),
                        local_content_hash=local_hash,
                        remote_content_hash=remote_hash,
                    ))
    except (SyncError, ValueError, OSError, RuntimeError) as exc:
        logger.error("Conflict detection failed: %s", exc)

    if conflicts:
        logger.info("Detected %d sync conflicts", len(conflicts))

    return conflicts


def resolve_conflicts(
    conflicts: list[ConflictRecord],
    strategy: ConflictStrategy,
) -> dict[str, ConflictStrategy]:
    """
    Apply a strategy to each conflict. Returns {artifact_id: resolution}.

    For MANUAL_REVIEW, the resolution is set but the artifact is not imported
    (it goes to the conflict log instead).
    """
    resolutions: dict[str, ConflictStrategy] = {}
    for conflict in conflicts:
        conflict.resolution = strategy.value
        resolutions[conflict.artifact_id] = strategy
    return resolutions


def write_conflict_log(
    conflicts: list[ConflictRecord],
    sync_dir: str | None = None,
) -> str:
    """Write unresolved conflicts to {sync_dir}/neo4j/conflicts.jsonl."""
    sync_dir = sync_dir or _default_sync_dir()
    out_dir = str(os.path.join(sync_dir, NEO4J_SUBDIR))
    _ensure_dir(out_dir)
    out_path = os.path.join(out_dir, "conflicts.jsonl")

    with open(out_path, "a", encoding="utf-8") as fh:
        for conflict in conflicts:
            fh.write(json.dumps(asdict(conflict), default=str) + "\n")

    logger.info("Conflict log appended: %d entries → %s", len(conflicts), out_path)
    return out_path
