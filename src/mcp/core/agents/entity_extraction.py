# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""LLM-based named-entity extraction for the GraphRAG layer.

Workstream E Phase 4a.3. Wraps :func:`core.utils.internal_llm.call_internal_llm`
with a fixed type vocabulary and structured-JSON output. Produces
:class:`Entity` records that the persistence layer
(``app/db/neo4j/entity.py``) writes as ``(:Entity)`` nodes plus
``(:Artifact)-[:MENTIONS]->(:Entity)`` edges.

This module is layer-correct: it stays in ``core/`` and takes the LLM
caller as a parameter so tests can inject a fake without monkeypatching
the live OpenRouter path. The default caller wraps
``call_internal_llm(stage="entity_extraction", ...)``.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Iterable, Literal

from config.settings import ENTITY_MIN_CONFIDENCE
from core.agents.entity_resolution import resolve_canonical
from core.utils.llm_parsing import parse_llm_json

logger = logging.getLogger("ai-companion.entity_extraction")


EntityType = Literal["PERSON", "ORG", "ASSET", "EVENT", "DATE", "LOC", "OTHER"]

_VALID_TYPES: frozenset[str] = frozenset(
    ("PERSON", "ORG", "ASSET", "EVENT", "DATE", "LOC", "OTHER")
)

# Async LLM caller signature: messages -> JSON string.
# Mirrors the call_internal_llm contract for response_format=json_object.
LLMCaller = Callable[[list[dict[str, str]]], Awaitable[str]]


@dataclass(frozen=True)
class Entity:
    """A canonicalised named-entity record.

    ``canonical_id`` is the stable graph identifier; two extractions of
    "Elon Musk" and "elon musk" collapse to the same ``person:elon-musk``.
    ``confidence`` is the LLM's self-reported extraction confidence
    (0.0–1.0); the persistence layer stores it on the MENTIONS edge.
    """

    name: str
    entity_type: EntityType
    canonical_id: str
    confidence: float


# ---------------------------------------------------------------------------
# Canonicalisation
# ---------------------------------------------------------------------------

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def canonical_id(name: str, entity_type: str) -> str:
    """Normalise (name, type) → stable graph identifier.

    Format: ``{type_lower}:{slug}`` where ``slug`` is lowercase
    ASCII-folded with all non-alphanumeric runs collapsed to single
    hyphens.

    Examples:
        canonical_id("Elon Musk", "PERSON") → "person:elon-musk"
        canonical_id("Apple Inc.", "ORG")   → "org:apple-inc"
        canonical_id("BTC/USD", "ASSET")    → "asset:btc-usd"
    """
    slug = _SLUG_RE.sub("-", name.lower().strip()).strip("-")
    return f"{entity_type.lower()}:{slug}"


# ---------------------------------------------------------------------------
# Name-quality gate
# ---------------------------------------------------------------------------
# Live triage (2026-07-13) showed the wiki-refresh queue churning on junk
# "entities" the LLM lifts verbatim from ingested documentation: relative
# doc paths ("library/email.charset.html"), bare version strings
# ("version-3-6", "v3.6.1"), and single characters. Every admitted name
# eventually costs a 40-110s wiki_refresh job plus external-API 404s, so
# extraction is the cheapest place to stop them. The gate is structural
# and deliberately conservative — when unsure, admit.

_MIN_ENTITY_NAME_CHARS = 2  # single characters carry no entity signal

# Documentation-flavoured file extensions; a slash-containing name ending
# in one of these is a doc path, not an entity ("BTC/USD" stays).
DOC_FILE_EXTENSIONS: tuple[str, ...] = (
    ".html", ".htm", ".txt", ".md", ".rst", ".pdf",
)

# Version-token shape: optional prefix, then digits joined by separators.
_VERSION_PREFIXES: tuple[str, ...] = ("version", "ver", "v")  # longest first
_VERSION_SEPARATORS = frozenset(".-_ ")


def ends_with_doc_extension(segment: str) -> bool:
    """True when ``segment`` ends in a doc-ish file extension (case-insensitive)."""
    lowered = segment.strip().lower()
    return any(lowered.endswith(ext) for ext in DOC_FILE_EXTENSIONS)


def _is_doc_path_like(name: str) -> bool:
    """True for path-shaped names: contain a ``/`` AND end in a doc extension."""
    return "/" in name and ends_with_doc_extension(name)


def _is_version_token(name: str) -> bool:
    """True when the WHOLE name is a version string.

    Shape: optional "v"/"ver"/"version" prefix, then digits joined by
    ``.`` / ``-`` / ``_`` / space, with at least one separator between digit
    groups ("3.6", "v3.6.1", "version-3-6"). A bare number with no separator
    ("2024", "V8") is admitted — it may be a year or a product name. Plain
    string walk; no regex (DUO138).
    """
    lowered = name.lower()
    rest = lowered
    for prefix in _VERSION_PREFIXES:
        if lowered.startswith(prefix):
            rest = lowered[len(prefix):]
            break
    if rest[:1] in _VERSION_SEPARATORS:
        rest = rest[1:]
    if not rest or not rest[0].isdigit():
        return False
    has_separator = False
    for ch in rest:
        if ch.isdigit():
            continue
        if ch in _VERSION_SEPARATORS:
            has_separator = True
            continue
        return False
    return has_separator


def is_junk_entity_name(name: str) -> bool:
    """Structural junk gate for entity names.

    Shared choke-point primitive: applied at extraction time
    (:func:`_normalise_entities`), at wiki-refresh time
    (``app.processor.jobs.wiki_refresh``), and per adapter route
    (``app.services.external_apis.wiki_enrichment``). Rejects only shapes
    that cannot be real entities: empty / single characters, doc-file
    paths, and pure version tokens.
    """
    stripped = name.strip()
    if len(stripped) < _MIN_ENTITY_NAME_CHARS:
        return True
    if _is_doc_path_like(stripped):
        return True
    return _is_version_token(stripped)


# ---------------------------------------------------------------------------
# Prompt + extraction
# ---------------------------------------------------------------------------

_EXTRACTION_PROMPT = """\
Extract named entities from the text. Output ONLY valid JSON in the exact \
schema below.

Types (use ONLY these):
- PERSON: real individuals (e.g., "Elon Musk", "Tim Cook")
- ORG: companies, institutions, governments (e.g., "Apple Inc.", "Federal Reserve")
- ASSET: tradeable instruments, products, models (e.g., "BTC", "GPT-4", "Tesla Model 3")
- EVENT: dated occurrences with proper-noun identity (e.g., "2008 financial crisis", "WWDC 2024")
- DATE: discrete time periods (e.g., "Q3 2024", "March 15, 2026")
- LOC: physical or political places (e.g., "San Francisco", "Wall Street")
- OTHER: significant proper nouns that don't fit above

Schema:
{{"entities": [{{"name": "<verbatim span>", "type": "<TYPE>", "confidence": <0.0-1.0>}}, ...]}}

Skip:
- Common nouns ("the company", "they", "this product")
- Pronouns
- Generic temporal markers ("today", "yesterday", "last year")
- Single first names without surname unless globally unique (e.g., "Madonna" stays)

Text:
\"\"\"
{text}
\"\"\"

JSON:
"""


def _build_messages(text: str) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "You are a precise named-entity extractor. Always respond "
                "with valid JSON matching the requested schema. Never add "
                "explanatory prose."
            ),
        },
        {"role": "user", "content": _EXTRACTION_PROMPT.format(text=text)},
    ]


async def extract_entities_from_text(
    text: str,
    *,
    llm_caller: LLMCaller,
    max_chars: int = 8000,
    min_confidence: float = ENTITY_MIN_CONFIDENCE,
) -> list[Entity]:
    """Extract entities from a single chunk of text.

    Caller injects ``llm_caller`` so tests can stub the LLM. Production
    callers wrap :func:`core.utils.internal_llm.call_internal_llm` with
    ``stage="entity_extraction"`` and ``response_format={"type": "json_object"}``.

    Empty / blank text → empty list (no LLM call). Texts longer than
    ``max_chars`` are truncated head-only — entity-density is roughly
    uniform across long documents, and the ingest pipeline already
    chunks before calling this, so the truncation only kicks in on
    pathologically large single chunks.
    """
    cleaned = (text or "").strip()
    if not cleaned:
        return []
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]

    messages = _build_messages(cleaned)
    try:
        raw = await llm_caller(messages)
    except Exception as exc:  # noqa: BLE001 — observability boundary; fall through to []
        logger.exception("entity_extraction.llm_call_failed: %s", exc)
        return []

    try:
        parsed = parse_llm_json(raw)
    except Exception as exc:
        from core.utils.swallowed import log_swallowed_error
        log_swallowed_error('core.agents.entity_extraction', exc)
        logger.warning(
            "entity_extraction.json_parse_failed (returning [] for this chunk); "
            "first 200 chars: %r",
            raw[:200] if raw else "",
        )
        return []

    return list(_normalise_entities(parsed, min_confidence=min_confidence))


def _normalise_entities(parsed: Any, *, min_confidence: float = 0.0) -> Iterable[Entity]:
    """Apply schema validation, type-vocab filter, canonicalisation, dedup.

    Entities with ``confidence < min_confidence`` are dropped before yielding.
    """
    if not isinstance(parsed, dict):
        return
    raw_list = parsed.get("entities")
    if not isinstance(raw_list, list):
        return

    seen: set[str] = set()
    for raw in raw_list:
        if not isinstance(raw, dict):
            continue
        name = (raw.get("name") or "").strip()
        if not name:
            continue
        if is_junk_entity_name(name):
            logger.debug("entity_extraction.rejected_junk_name name=%r", name)
            continue
        ent_type = str(raw.get("type") or "").strip().upper()
        if ent_type not in _VALID_TYPES:
            continue
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        if confidence < min_confidence:
            continue

        # AF-032: no ``embed=`` here on purpose — ingest runs only Tiers A+B
        # (alias-table + string-normalize) to stay lean. The Tier-C embedding
        # merge is the deliberate maintenance sweep run out-of-band, on a
        # schedule (scheduler.py ``entity_embedding_merge`` cron, gated by
        # CERID_ENTITY_MERGE_CRON_ENABLED) or by hand via
        # ``scripts/merge_entity_aliases.py --mode embedding --apply``.
        cid = resolve_canonical(name, ent_type)
        if not cid.endswith(":"):  # at least one slug character
            if cid in seen:
                continue
            seen.add(cid)
            yield Entity(
                name=name,
                entity_type=ent_type,  # type: ignore[arg-type]
                canonical_id=cid,
                confidence=confidence,
            )


# ---------------------------------------------------------------------------
# Default LLM caller (production wiring)
# ---------------------------------------------------------------------------

async def default_llm_caller(messages: list[dict[str, str]]) -> str:
    """Production caller: routes through call_internal_llm with the
    ``entity_extraction`` stage breadcrumb so the call appears in
    structlog + Sentry scope correctly."""
    from core.utils.internal_llm import call_internal_llm

    return await call_internal_llm(
        messages,
        temperature=0.0,
        max_tokens=1024,
        response_format={"type": "json_object"},
        stage="entity_extraction",
    )
