# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Sources, claims, citations, and deletions.

``app.sync.export.export_all`` copies the live stores. This module is the
other thing: a dump whose fields are the ontology (domain, sub-category,
tags, source kind, artifact id) and a pure rebuild of an in-memory index.
Nothing here opens Chroma or Neo4j. A deletion drops the source, the claims
that hung on it, and the citations that pointed at either.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Source:
    artifact_id: str
    domain: str
    sub_category: str
    tags: tuple[str, ...]
    source_kind: str
    text: str


@dataclass(frozen=True)
class Claim:
    claim_id: str
    artifact_id: str
    domain: str
    text: str


@dataclass(frozen=True)
class Citation:
    claim_id: str
    artifact_id: str


@dataclass(frozen=True)
class Deletion:
    artifact_id: str
    reason: str


def rebuild(
    sources: list[Source],
    claims: list[Claim],
    citations: list[Citation],
    deletions: list[Deletion],
) -> dict:
    """The index those records are sufficient to reconstruct.

    A claim whose domain disagrees with its source is dropped. The stamp
    and the collection are one fact. A citation survives only when both
    the claim and the source are still live, and the citation names the
    claim's own artifact.
    """
    gone = {item.artifact_id for item in deletions}
    live_sources: dict[str, Source] = {}
    for source in sources:
        if source.artifact_id in gone:
            continue
        live_sources[source.artifact_id] = source

    live_claims: dict[str, Claim] = {}
    for claim in claims:
        origin = live_sources.get(claim.artifact_id)
        if origin is None or claim.domain != origin.domain:
            continue
        live_claims[claim.claim_id] = claim

    live_citations = [
        citation
        for citation in citations
        if citation.claim_id in live_claims
        and citation.artifact_id == live_claims[citation.claim_id].artifact_id
        and citation.artifact_id in live_sources
    ]
    return {
        "sources": live_sources,
        "claims": live_claims,
        "citations": live_citations,
        "deleted": gone,
    }
