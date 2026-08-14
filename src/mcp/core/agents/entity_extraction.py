# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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


def _is_degenerate_email(name: str) -> bool:
    """True for bare minimal email fragments like ``a@b``.

    A real address always carries a dotted domain; an @-shaped token whose
    domain part has no dot cannot be one and is extraction noise (observed
    on mail ingests: a literal ``a@b`` example became a graph entity).
    Leading-@ social handles are not address-shaped and are admitted.
    """
    stripped = name.strip()
    if stripped.startswith("@") or stripped.count("@") != 1:
        return False
    if any(ch.isspace() for ch in stripped):
        return False
    _local, _, domain = stripped.partition("@")
    if not domain:
        return True
    return "." not in domain


def is_junk_entity_name(name: str) -> bool:
    """Structural junk gate for entity names.

    Shared choke-point primitive: applied at extraction time
    (:func:`_normalise_entities`), at wiki-refresh time
    (``app.processor.jobs.wiki_refresh``), and per adapter route
    (``app.services.external_apis.wiki_enrichment``). Rejects only shapes
    that cannot be real entities: empty / single characters, doc-file
    paths, pure version tokens, and degenerate email fragments.
    """
    stripped = name.strip()
    if len(stripped) < _MIN_ENTITY_NAME_CHARS:
        return True
    if _is_doc_path_like(stripped):
        return True
    if _is_degenerate_email(stripped):
        return True
    return _is_version_token(stripped)


# ---------------------------------------------------------------------------
# Prompt + extraction
# ---------------------------------------------------------------------------

# NO NAMED EXAMPLES IN THIS PROMPT. The type list used to read
# `PERSON: real individuals (e.g., "Elon Musk", "Tim Cook")` and so on across
# every type, and the model copied those illustrations straight into its output
# as if it had found them in the text. Reproduced 2026-08-03 on a Python
# asyncio doc mentioning none of them: the extractor returned BTC, Apple Inc.,
# Tim Cook, Elon Musk, Tesla Model 3, GPT-4, WWDC, San Francisco, Wall Street
# and the Federal Reserve at confidence 0.9-1.0 — a 1:1 match with the example
# set, plus one real entity. It had been doing this on every artifact for
# months: BTC reached mention_count 117 and Wall Street 132 across documents
# that never name them, which is also why the wiki compiler produced summaries
# saying "Apple Inc. is not mentioned in the provided excerpts" — the excerpts
# genuinely didn't mention it.
#
# Types are described by their defining property instead. Any future edit that
# reintroduces a named example must keep _drop_unsupported() below, which is
# what actually enforces this.
_EXTRACTION_PROMPT = """\
Extract named entities from the text. Output ONLY valid JSON in the exact \
schema below.

Every name you output MUST appear verbatim in the text. Do not output a name \
that is not present in the text, however plausible it seems.

Types (use ONLY these):
- PERSON: named individual people
- ORG: named companies, institutions, agencies or governments
- ASSET: named tradeable instruments, products or models
- EVENT: named occurrences with a proper-noun identity
- DATE: discrete named time periods
- LOC: named physical or political places
- OTHER: significant proper nouns that don't fit above

Schema:
{{"entities": [{{"name": "<verbatim span>", "type": "<TYPE>", "confidence": <0.0-1.0>}}, ...]}}

Skip:
- Common nouns and pronouns
- Generic temporal markers
- Single first names without a surname, unless globally unambiguous

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

    supported = _drop_unsupported(
        list(_normalise_entities(parsed, min_confidence=min_confidence)),
        cleaned,
    )
    return _drop_example_row_persons(supported, cleaned)


# Corporate/legal suffixes to strip before checking presence, so "Apple Inc."
# extracted from a document that says "Apple" is kept.
# Tokens shorter than this carry no discriminating power ("of", "3", "AI") and
# would let a fabricated name pass on incidental matches.
_MIN_TOKEN_CHARS = 3

_LEGAL_SUFFIX_RE = re.compile(
    r"[,\s]+(inc|inc\.|corp|corp\.|corporation|ltd|ltd\.|llc|l\.l\.c\.|plc|"
    r"gmbh|s\.a\.|n\.v\.|co|co\.|company|limited)\s*$",
    re.I,
)


def _flatten(text: str) -> str:
    """Lowercase, strip markdown emphasis, collapse whitespace runs.

    A name is routinely written across a line break or wrapped in emphasis
    (``**Matt Butcher**``, ``Matt\\nButcher``). A raw substring test misses
    those and would delete a real entity, so both sides are flattened first.
    """
    flat = re.sub(r"[*_`]+", "", (text or "").lower())
    return re.sub(r"\s+", " ", flat)


def _is_present(name: str, haystack: str) -> bool:
    """Is this entity name supported by the (already flattened) text?

    Three widening tests, each justified by a real case:

    1. the flattened name verbatim;
    2. the name minus a legal suffix — "Apple Inc." from a page saying "Apple";
    3. every significant token present somewhere — catches names the source
       renders differently ("Azure Kubernetes Service" in a table, a person
       listed surname-first). Weaker, but the fabrications this guards against
       share NO tokens with their documents at all, so it still separates them
       cleanly.
    """
    flat = _flatten(name)
    if not flat:
        return False
    if flat in haystack:
        return True
    stripped = _LEGAL_SUFFIX_RE.sub("", flat).strip()
    if stripped and stripped in haystack:
        return True
    tokens = [t for t in re.split(r"\W+", flat) if len(t) >= _MIN_TOKEN_CHARS]
    return bool(tokens) and all(t in haystack for t in tokens)


def _drop_unsupported(entities: list[Entity], text: str) -> list[Entity]:
    """Drop entities whose name does not occur in the source text.

    The mechanical half of the prompt-example fix above. An instruction not to
    invent names is necessary but not sufficient — the local 8B model that runs
    this stage follows it only most of the time — and the failure is silent and
    permanent: a fabricated entity becomes a graph node, accrues MENTIONS edges
    to documents that never named it, inflates mention_count, and then gets a
    compiled wiki page written about it.

    Deliberately loose on the matching side so real entities are not lost:
    matching is case-insensitive and legal suffixes are stripped, so "Apple
    Inc." survives a document that only writes "Apple". An entity referred to
    ONLY by pronoun or by an alias sharing no substring with its name is
    dropped — acceptable, because this runs per-artifact on that artifact's own
    text, where anything genuinely discussed is named at least once.
    """
    if not entities:
        return []
    haystack = _flatten(text)
    kept: list[Entity] = []
    for ent in entities:
        name = (ent.name or "").strip()
        if not name:
            continue
        if _is_present(name, haystack):
            kept.append(ent)
        else:
            logger.debug(
                "entity_extraction.dropped_unsupported name=%r type=%s "
                "(not present in source text)",
                ent.name, ent.entity_type,
            )
    if len(kept) != len(entities):
        logger.info(
            "entity_extraction.dropped %d of %d extracted entities absent from "
            "the source text", len(entities) - len(kept), len(entities),
        )
    return kept


# Example-row context: an INSERT/VALUES statement line, or a bare tuple row
# from a multi-line VALUES list ("  ('John', 25),"). Sample-data personal
# names pass every other check — the name IS present in the text — so the
# gate is contextual: a PERSON whose every occurrence sits inside SQL
# example rows is sample data, not a person the corpus is about. Names that
# also appear in prose anywhere in the text are kept, which is what makes
# this safe for a conversation corpus where the same shape is legitimate.
_SQL_EXAMPLE_LINE_RE = re.compile(r"(?i)\b(?:insert\s+into|values\s*\()")
_SQL_TUPLE_ROW_RE = re.compile(r"^\s*\(\s*['\"]")


def _drop_example_row_persons(entities: list[Entity], text: str) -> list[Entity]:
    """Drop PERSON entities that only ever occur inside SQL example rows."""
    if not entities:
        return []
    lines = text.split("\n")
    kept: list[Entity] = []
    for ent in entities:
        if ent.entity_type != "PERSON":
            kept.append(ent)
            continue
        needle = ent.name.lower()
        containing = [ln for ln in lines if needle in ln.lower()]
        if containing and all(
            _SQL_EXAMPLE_LINE_RE.search(ln) or _SQL_TUPLE_ROW_RE.match(ln)
            for ln in containing
        ):
            logger.debug(
                "entity_extraction.dropped_example_row_person name=%r", ent.name,
            )
            continue
        kept.append(ent)
    return kept


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
