# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Stamp one ingest with the domain ontology that already exists.

Domain, sub-category, and tags come from ``config.taxonomy`` (TAXONOMY,
TAG_VOCABULARY, and OPERATIONAL_ONTOLOGY). A caller that already sends
``metadata.kind`` (Anneal's lesson, decision, run, conversation) gets
that kind as ``sub_category`` when it is one of the domain's
sub-categories. An omitted sub-category becomes ``general``. An explicit
sub-category outside the list is an error in strict mode and ``general``
otherwise, so a best-effort classifier cannot fail the write.
"""

from __future__ import annotations

import json
from typing import Any

import config
from utils.metadata import _normalize_tags


class OntologyError(ValueError):
    def __init__(self, message: str, *, allowed: list[str]) -> None:
        super().__init__(message)
        self.allowed = allowed


def _tag_list(metadata: dict[str, Any]) -> list[str]:
    raw = metadata.get("tags")
    if isinstance(raw, list):
        return [str(t) for t in raw]
    if isinstance(raw, str) and raw.strip():
        if raw.strip().startswith("["):
            try:
                decoded = json.loads(raw)
            except json.JSONDecodeError:
                decoded = None
            if isinstance(decoded, list):
                return [str(t) for t in decoded]
        return [part.strip() for part in raw.split(",") if part.strip()]
    encoded = metadata.get("tags_json") or "[]"
    try:
        decoded = json.loads(encoded) if isinstance(encoded, str) else encoded
    except json.JSONDecodeError:
        return []
    if isinstance(decoded, list):
        return [str(t) for t in decoded]
    return []


def apply_ontology(domain: str, metadata: dict[str, Any], *, strict: bool) -> dict[str, Any]:
    """Write ``domain``, ``sub_category``, and ``tags_json`` onto ``metadata``.

    Returns the same dict. ``metadata['domain']`` cannot disagree with the
    collection the caller already chose.
    """
    if not config.corpus_domain(domain):
        raise OntologyError(f"{domain} is not a corpus domain and cannot be ingested", allowed=[])
    metadata["domain"] = domain
    spec = config.domain_spec(domain)
    kind = str(metadata.get("kind") or "").strip().lower()
    sub = str(metadata.get("sub_category") or "").strip().lower()
    if spec is None:
        if sub:
            metadata["sub_category"] = sub
        return metadata

    allowed = config.sub_categories_for(domain)
    if not sub:
        if kind in allowed:
            sub = kind
        elif "general" in allowed:
            sub = "general"
        else:
            sub = allowed[0]
    elif sub not in allowed:
        # A caller's kind is a real shelf (Anneal sends lesson, decision,
        # run, conversation). A classifier guess that is not in the list
        # does not get to replace it.
        if kind in allowed:
            sub = kind
        elif strict:
            raise OntologyError(
                f"sub_category {sub!r} is not in the {domain} ontology",
                allowed=allowed,
            )
        else:
            sub = "general" if "general" in allowed else allowed[0]
    metadata["sub_category"] = sub

    tags = _tag_list(metadata)
    if sub not in tags:
        tags.append(sub)
    metadata["tags_json"] = json.dumps(_normalize_tags(tags, domain))
    return metadata
