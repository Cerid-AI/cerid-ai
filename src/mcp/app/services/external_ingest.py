# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Generic external ingest service — Phase API.4.

Accepts arbitrary JSON payloads from any external source and maps them to
canonical ingest items via caller-supplied :class:`FieldMappings`. No code
path is special-cased for any source type; the ``source_type`` field is
stored as provenance metadata only.

Public surface
--------------
* :class:`FieldMappings`     — dotted-path mapping config
* :class:`ExternalIngestRequest` — full request model
* :class:`NormalizedItem`    — canonical shape handed to ingest pipeline
* :class:`IngestResult`      — counts: accepted / skipped / errors
* :class:`MappingError`      — raised on unresolvable required paths
* :func:`apply_mappings`     — pure transform; returns ``list[NormalizedItem]``
* :func:`ingest_external`    — orchestrates apply + ingest pipeline

Dotted-path syntax
------------------
Plain path:    ``"text"``          — payload["text"]
Nested:        ``"meta.source"``   — payload["meta"]["source"]
Array spread:  ``"highlights[].text"`` — one item per element of payload["highlights"],
               taking ["text"] from each element. The array segment must be the
               *only* spread in the path (one level of fan-out per call).

The resolver is ~60 lines of stdlib; no external JSON-path library is
pulled in.
"""
from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from core.utils.swallowed import log_swallowed_error
from core.utils.time import utcnow_iso

logger = logging.getLogger("ai-companion.external_ingest")


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class MappingError(ValueError):
    """Raised when a required dotted-path cannot be resolved in the payload."""


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------


class FieldMappings(BaseModel):
    """Declares how to extract canonical fields from an arbitrary payload dict.

    Each value is a dotted-path string (optionally with ``[]`` for array
    fan-out) into the incoming ``payload``.  Only ``content`` and
    ``source_uri`` are required; the others default gracefully.
    """

    content: str = Field(
        ...,
        description=(
            "Dotted path to the main text content.  "
            "Use ``highlights[].text`` to fan-out one item per array element."
        ),
        examples=["text", "highlights[].text", "body.content"],
    )
    source_uri: str = Field(
        ...,
        description="Dotted path to the canonical URI / URL for this item.",
        examples=["url", "source.url", "highlights[].url"],
    )
    ts: str | None = Field(
        default=None,
        description=(
            "Optional dotted path to an ISO-8601 timestamp.  "
            "Defaults to current UTC time when absent or unresolvable."
        ),
        examples=["highlighted_at", "created_at", "meta.ts"],
    )
    tags: str | None = Field(
        default=None,
        description=(
            "Optional dotted path to a list[str] of tags.  "
            "Resolved value must be a Python list; absent path → empty list."
        ),
        examples=["tags", "highlights[].tags", "meta.labels"],
    )
    title: str | None = Field(
        default=None,
        description="Optional dotted path to a human-readable title string.",
        examples=["title", "book_title", "subject"],
    )
    id: str | None = Field(
        default=None,
        description=(
            "Optional dotted path to an external ID.  "
            "When absent, the ID is derived from ``source_uri``."
        ),
        examples=["id", "highlight_id", "guid"],
    )


class NormalizedItem(BaseModel):
    """Canonical shape handed off to the ingest pipeline."""

    content: str
    source_uri: str
    ts: str
    tags: list[str] = Field(default_factory=list)
    title: str | None = None
    external_id: str | None = None
    source_type: str = "unknown"


class ExternalIngestRequest(BaseModel):
    """Request body for ``POST /sdk/v1/ingest/external``."""

    source_type: str = Field(
        ...,
        description=(
            "Free-form label for the originating service "
            "(e.g. ``readwise``, ``pocket``, ``telegram-bot``).  "
            "Stored as provenance metadata; never branched on in code."
        ),
        examples=["readwise", "pocket", "telegram-bot", "raindrop"],
    )
    payload: dict[str, Any] = Field(
        ...,
        description="Raw JSON payload from the external service.",
    )
    field_mappings: FieldMappings = Field(
        ...,
        description="Mapping config that declares how to extract canonical fields from ``payload``.",
    )


class IngestResult(BaseModel):
    """Per-batch result counts returned by ``ingest_external``."""

    accepted: int = 0
    skipped: int = 0
    errors: list[dict[str, Any]] = Field(default_factory=list)
    source_type: str = "unknown"


# ---------------------------------------------------------------------------
# Pure dotted-path resolver (stdlib only, ~60 lines)
# ---------------------------------------------------------------------------

_ARRAY_MARKER = "[]"


def _resolve_path(obj: Any, path: str) -> Any:
    """Walk a dotted path (no array spread) into *obj*.

    Raises :class:`KeyError` / :class:`TypeError` on missing segments.
    """
    for segment in path.split("."):
        if isinstance(obj, dict):
            obj = obj[segment]
        else:
            raise TypeError(f"Expected dict at segment '{segment}', got {type(obj).__name__}")
    return obj


def _resolve_dotted(payload: dict[str, Any], path: str) -> list[Any]:
    """Resolve a dotted-path (with optional single ``[]`` array fan-out).

    Returns a list of resolved values — exactly one element for a plain
    path, one element per array item for a fan-out path.

    Raises :class:`MappingError` on unresolvable paths.
    """
    if _ARRAY_MARKER not in path:
        # Plain path — single value
        try:
            return [_resolve_path(payload, path)]
        except (KeyError, TypeError, IndexError) as exc:
            raise MappingError(f"Path '{path}' not found in payload: {exc}") from exc

    # Fan-out path: split at the first occurrence of "[].".
    # e.g. "highlights[].text"  →  array_part="highlights", tail="text"
    array_segment, _, tail = path.partition(_ARRAY_MARKER + ".")
    if not array_segment:
        raise MappingError(
            f"Path '{path}' uses '[]' at the start — "
            "the array segment must be a non-empty key name."
        )

    try:
        array_value = _resolve_path(payload, array_segment)
    except (KeyError, TypeError, IndexError) as exc:
        raise MappingError(
            f"Array path segment '{array_segment}' not found in payload: {exc}"
        ) from exc

    if not isinstance(array_value, list):
        raise MappingError(
            f"Path '{array_segment}' resolved to {type(array_value).__name__}, expected list"
        )

    if not tail:
        # The path ended at the array itself — return elements as-is.
        return list(array_value)

    results: list[Any] = []
    for i, element in enumerate(array_value):
        try:
            results.append(_resolve_path(element, tail))
        except (KeyError, TypeError, IndexError) as exc:
            raise MappingError(
                f"Path '{tail}' not found in array element [{i}]: {exc}"
            ) from exc
    return results


def _resolve_optional(payload: dict[str, Any], path: str | None, default: Any = None) -> Any:
    """Resolve a dotted path that may be absent; returns *default* on miss."""
    if path is None:
        return default
    try:
        values = _resolve_dotted(payload, path)
        # For optional scalar fields, return the first element.
        return values[0] if values else default
    except MappingError:
        return default


def _resolve_optional_list(payload: dict[str, Any], path: str | None) -> list[str]:
    """Resolve a dotted path that should produce a list[str] of tags.

    Returns ``[]`` on any miss or type mismatch.
    """
    if path is None:
        return []
    try:
        values = _resolve_dotted(payload, path)
        first = values[0] if values else []
        if not isinstance(first, list):
            return []
        return [str(t) for t in first if t is not None]
    except MappingError:
        return []


# ---------------------------------------------------------------------------
# Mapping transform
# ---------------------------------------------------------------------------


def apply_mappings(
    payload: dict[str, Any],
    mappings: FieldMappings,
    source_type: str = "unknown",
) -> list[NormalizedItem]:
    """Apply *mappings* to *payload* and return a list of :class:`NormalizedItem`.

    A single payload can yield N items when ``content`` or ``source_uri``
    uses an array fan-out path (``highlights[].text``).  Both required
    paths must fan-out to the same length — or be plain paths — or a
    :class:`MappingError` is raised.

    Raises
    ------
    MappingError
        When a required path (``content`` or ``source_uri``) cannot be
        resolved or the two fan-outs produce lists of different lengths.
    """
    # Resolve both required fields first so we fail fast on bad mappings.
    content_values = _resolve_dotted(payload, mappings.content)
    source_uri_values = _resolve_dotted(payload, mappings.source_uri)

    if len(content_values) != len(source_uri_values):
        raise MappingError(
            f"Fan-out mismatch: 'content' path '{mappings.content}' resolved to "
            f"{len(content_values)} items but 'source_uri' path '{mappings.source_uri}' "
            f"resolved to {len(source_uri_values)} items.  "
            "Both paths must fan-out to the same length."
        )

    n = len(content_values)
    now = utcnow_iso()

    items: list[NormalizedItem] = []
    for i in range(n):
        content_val = content_values[i]
        source_uri_val = source_uri_values[i]

        if not isinstance(content_val, str) or not content_val.strip():
            raise MappingError(
                f"Item [{i}]: 'content' resolved to an empty or non-string value "
                f"(got {type(content_val).__name__!r}: {content_val!r})"
            )
        if not isinstance(source_uri_val, str) or not source_uri_val.strip():
            raise MappingError(
                f"Item [{i}]: 'source_uri' resolved to an empty or non-string value "
                f"(got {type(source_uri_val).__name__!r}: {source_uri_val!r})"
            )

        # Optional fields: resolve from payload (may differ per-item only
        # for fan-out paths; plain paths return the same value for every item).
        ts_raw = _resolve_optional(payload, mappings.ts, default=now)
        ts = str(ts_raw) if ts_raw else now

        tags = _resolve_optional_list(payload, mappings.tags)
        title_raw = _resolve_optional(payload, mappings.title)
        title = str(title_raw) if title_raw is not None else None
        id_raw = _resolve_optional(payload, mappings.id)
        external_id = str(id_raw) if id_raw is not None else None

        items.append(
            NormalizedItem(
                content=content_val,
                source_uri=source_uri_val,
                ts=ts,
                tags=tags,
                title=title,
                external_id=external_id,
                source_type=source_type,
            )
        )

    return items


# ---------------------------------------------------------------------------
# Ingest orchestration
# ---------------------------------------------------------------------------


async def ingest_external(
    request: ExternalIngestRequest,
    tenant: str,
) -> IngestResult:
    """Map *request* payload to normalized items and pass each to the ingest pipeline.

    Canonical ingest entry point: :func:`app.services.ingestion.ingest_content`.
    Each :class:`NormalizedItem` is forwarded as a plain-text ingest with
    ``source_type`` stamped onto the metadata for provenance tracking.

    Returns :class:`IngestResult` with per-item counts.  Individual item
    failures are recorded in ``errors`` and do not abort the batch.
    """
    import asyncio

    # Lazy import keeps app/ store drivers out of module-level import time.
    from app.services.ingestion import ingest_content

    result = IngestResult(source_type=request.source_type)

    try:
        items = apply_mappings(
            request.payload,
            request.field_mappings,
            source_type=request.source_type,
        )
    except MappingError as exc:
        # Mapping failure means zero items can be processed — surface the
        # error as a single entry so callers get a structured error body
        # rather than a 500.
        result.errors.append({"index": None, "error": str(exc), "phase": "mapping"})
        return result

    for index, item in enumerate(items):
        try:
            metadata: dict[str, Any] = {
                "filename": item.title or item.source_uri,
                "source_uri": item.source_uri,
                "source_type": item.source_type,
                "ingested_at": item.ts,
                "client_source": f"external_ingest:{request.source_type}",
            }
            if item.tags:
                import json
                metadata["tags_json"] = json.dumps(item.tags)
            if item.external_id:
                metadata["external_id"] = item.external_id
            if item.title:
                metadata["title"] = item.title

            ingest_result = await asyncio.to_thread(
                ingest_content,
                item.content,
                "general",
                metadata,
                skip_quality=False,
            )
            status = ingest_result.get("status", "unknown")
            if status in ("success", "updated"):
                result.accepted += 1
            elif status == "duplicate":
                result.skipped += 1
            else:
                result.errors.append({
                    "index": index,
                    "error": ingest_result.get("error", f"Unexpected status: {status}"),
                    "phase": "ingest",
                    "source_uri": item.source_uri,
                })
        except Exception as exc:  # noqa: BLE001 — observability boundary
            log_swallowed_error("external_ingest.item_ingest", exc)
            result.errors.append({
                "index": index,
                "error": str(exc),
                "phase": "ingest",
                "source_uri": item.source_uri if item else "(unknown)",
            })

    return result
