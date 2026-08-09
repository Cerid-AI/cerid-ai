# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Wikilink graph edges and pending-artifact resolution (RAG Cycle C2.1).

A markdown chunk that contains ``[[Foo]]`` should produce an edge

    (:Artifact {source_artifact_id})-[:WIKILINKS_TO]->(:Artifact)

when an artifact named ``Foo.md`` (or with that filename stem) already
exists in the graph.  When the target is missing — the user wrote the
link before importing the target note, or the link points at a
not-yet-ingested file — we materialise a placeholder:

    (:Artifact {src})-[:WIKILINKS_TO {pending: true}]->(:PendingArtifact {name})

so the link is not lost.  When the real artifact lands later,
:func:`resolve_pending_artifacts` re-points the inbound edges to the
real ``:Artifact`` and deletes the placeholder.

The ``EMBEDS`` relationship is the embed/transclusion variant
(``![[Foo]]``); it follows the identical write+resolve protocol with
``EMBEDS`` swapped for ``WIKILINKS_TO``.

All Cypher writes are idempotent — re-running ingestion never
duplicates an edge thanks to the ``source_chunk_id`` MERGE key.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.graph.wikilinks")


def _filename_stem(filename: str) -> str:
    """Return the filename without extension (matches Obsidian semantics)."""
    return Path(filename).stem


def write_wikilink_edge(
    driver: Any,
    source_artifact_id: str,
    target_name: str,
    is_embed: bool,
    source_chunk_id: str,
    alias: str = "",
    heading: str = "",
) -> None:
    """Write one wikilink edge from ``source_artifact_id`` to ``target_name``.

    Resolution strategy (C2.1):

    1. Filename-stem match — ``MATCH (tgt:Artifact) WHERE
       tgt.filename ENDS WITH "<target>.md"``.  C2.2 will extend this
       with frontmatter ``aliases``.
    2. If resolved → ``MERGE`` a non-pending edge directly to the
       target artifact.
    3. If not resolved → ``MERGE`` a ``:PendingArtifact {name: target}``
       placeholder and write the edge to it with ``pending: true``.

    The edge type is ``WIKILINKS_TO`` for plain links and ``EMBEDS``
    for transclusions (``![[…]]``).  The ``source_chunk_id`` is the
    MERGE key so the same chunk's repeated ingestion is idempotent.

    No-ops silently on driver/Cypher errors — the caller wraps this in
    a ``log_swallowed_error`` boundary because edge-creation is an
    observability concern, not a hard ingest dependency.
    """
    rel_type = "EMBEDS" if is_embed else "WIKILINKS_TO"
    stem = _filename_stem(target_name)
    # The resolution check uses ``filename ENDS WITH "<stem>.md"`` so
    # both bare names and path-prefixed filenames match. For C2.1 we
    # only support .md targets — Obsidian's default extension. Other
    # extensions become PendingArtifacts and stay unresolved.
    stem_md = f"{stem}.md"
    now = utcnow_iso()

    resolved_cypher = (
        "MATCH (src:Artifact {id: $source_id}) "
        "OPTIONAL MATCH (tgt:Artifact) "
        "  WHERE tgt.filename ENDS WITH $stem_md AND tgt.id <> $source_id "
        "WITH src, tgt LIMIT 1 "
        f"FOREACH (_ IN CASE WHEN tgt IS NOT NULL THEN [1] ELSE [] END | "
        f"    MERGE (src)-[r:{rel_type} {{source_chunk_id: $chunk_id}}]->(tgt) "
        "    ON CREATE SET r.alias = $alias, r.heading = $heading, "
        "                  r.pending = false, r.created_at = $now "
        ") "
        "RETURN tgt IS NOT NULL AS resolved"
    )

    pending_cypher = (
        "MERGE (p:PendingArtifact {name: $target_name}) "
        "  ON CREATE SET p.created_at = $now "
        "WITH p "
        "MATCH (src:Artifact {id: $source_id}) "
        f"MERGE (src)-[r:{rel_type} {{source_chunk_id: $chunk_id}}]->(p) "
        "ON CREATE SET r.alias = $alias, r.heading = $heading, "
        "              r.pending = true, r.created_at = $now"
    )

    with driver.session() as session:
        result = session.run(
            resolved_cypher,
            source_id=source_artifact_id,
            stem_md=stem_md,
            chunk_id=source_chunk_id,
            alias=alias,
            heading=heading,
            now=now,
        )
        record = result.single()
        resolved = bool(record and record["resolved"])
        if resolved:
            logger.debug(
                "wikilink edge resolved: %s --(%s)-> %s",
                source_artifact_id[:8],
                rel_type,
                target_name,
            )
            return

        # Fall through to the pending path
        session.run(
            pending_cypher,
            target_name=target_name,
            source_id=source_artifact_id,
            chunk_id=source_chunk_id,
            alias=alias,
            heading=heading,
            now=now,
        )
        logger.debug(
            "wikilink edge pending: %s --(%s)-> PendingArtifact(%s)",
            source_artifact_id[:8],
            rel_type,
            target_name,
        )


def resolve_pending_artifacts(
    driver: Any,
    artifact_id: str,
    filename: str,
    aliases: list[str] | None = None,
) -> int:
    """Promote any :class:`PendingArtifact` matching this new artifact.

    A pending artifact created by an earlier wikilink write becomes
    resolvable when a real :Artifact lands whose filename stem (or
    any of the provided ``aliases``) matches the placeholder's
    ``name``.  This function:

    1. Finds all ``PendingArtifact`` rows where
       ``p.name == filename_stem`` OR ``p.name IN aliases``.
    2. Re-points every inbound ``WIKILINKS_TO`` edge to the real
       artifact, preserving the original ``source_chunk_id`` /
       ``alias`` / ``heading`` / ``created_at`` and flipping
       ``pending`` to false.
    3. Repeats step 2 for ``EMBEDS`` edges.
    4. Deletes any ``PendingArtifact`` that no longer has inbound
       edges (a different concurrent ingest with an orphan inbound
       edge is preserved untouched, but in practice the orphan check
       fires after both queries so it's rare).

    Returns the count of distinct PendingArtifacts promoted (across
    both relationship types). Best-effort — Cypher errors propagate to
    the caller, which wraps this call in ``log_swallowed_error``.

    For C2.1 the caller passes ``aliases=None`` (filename-stem only);
    C2.2 will thread frontmatter ``aliases`` through.
    """
    stem = _filename_stem(filename)
    # Normalise: Cypher's ``IN $aliases`` works with an empty list but
    # not None.  Always pass a list.
    alias_list = list(aliases or [])

    promoted_total = 0
    for rel_type in ("WIKILINKS_TO", "EMBEDS"):
        cypher = (
            "MATCH (p:PendingArtifact) "
            "WHERE p.name = $stem OR p.name IN $aliases "
            "MATCH (new_art:Artifact {id: $artifact_id}) "
            f"OPTIONAL MATCH (src)-[old_rel:{rel_type}]->(p) "
            "WITH p, new_art, src, old_rel WHERE old_rel IS NOT NULL "
            f"MERGE (src)-[new_rel:{rel_type} "
            "    {source_chunk_id: old_rel.source_chunk_id}]->(new_art) "
            "ON CREATE SET new_rel = properties(old_rel), "
            "              new_rel.pending = false "
            "DELETE old_rel "
            "WITH DISTINCT p "
            "WHERE NOT (p)<-[]-() "
            "DETACH DELETE p "
            "RETURN count(DISTINCT p) AS promoted"
        )
        with driver.session() as session:
            record = session.run(
                cypher,
                stem=stem,
                aliases=alias_list,
                artifact_id=artifact_id,
            ).single()
            if record and record["promoted"]:
                promoted_total += int(record["promoted"])

    if promoted_total:
        logger.info(
            "resolved %d PendingArtifact(s) for new artifact %s (%s)",
            promoted_total,
            artifact_id[:8],
            filename,
        )
    return promoted_total
