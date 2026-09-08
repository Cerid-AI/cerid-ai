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
# Shouty-acronym / codec-alias shapes
# ---------------------------------------------------------------------------
# Moved here from app.services.external_apis.wiki_enrichment (Task 3,
# 2026-09-06) so opsrun/purge_junk_entities.py's classify_junk_entity and the
# wiki-refresh pre-enqueue filter share one definition instead of duplicating
# the rule. wiki_enrichment's per-route adapter gate imports these back.

_MAX_PLAUSIBLE_ACRONYM_LEN = 6  # NASA(4)/IBM(3)/UNESCO(6) pass; ALIASES(7)/CHARSETS(8) fail

_CODEC_ALIAS_FAMILIES = frozenset((
    "ascii", "big5", "cp", "euc", "gb", "gb2312", "gb18030", "gbk", "hz",
    "iso", "johab", "koi8", "latin", "mac", "ptcp154", "shift", "tis",
    "utf", "windows",
))


def is_shouty_acronym_shaped(name: str) -> bool:
    """ALL-CAPS pure-alpha token too long to be a plausible acronym.

    Pure-alpha keeps hyphen/digit names like "COVID-19" or "UTF-8" out of
    this check — they are judged by the codec gate or admitted.
    """
    if not name.isalpha() or not name.isupper():
        return False
    return len(name) > _MAX_PLAUSIBLE_ACRONYM_LEN


def is_codec_alias_shaped(name: str) -> bool:
    """Lowercase hyphenated token from a known codec family.

    Matches "euc-jp", "iso-2022-jp", "utf-8". Does NOT match "gpt-4" or
    "scikit-learn" — their first hyphen segment is not a codec family.
    """
    if " " in name or "-" not in name or name != name.lower():
        return False
    return name.split("-", 1)[0] in _CODEC_ALIAS_FAMILIES


def is_junk_entity(name: str, *, entity_type_unknown: bool = False) -> bool:
    """Combined junk-entity predicate.

    Mirrors opsrun/purge_junk_entities.py's classify_junk_entity, minus its
    per-class label: is_junk_entity_name's structural gate, plus the
    shouty-acronym / codec-alias family that only fires for an unknown-typed
    entity — the same gating wiki_enrichment._passes_adapter_gate applies to
    its wikipedia route. This module cannot call wiki_enrichment.infer_entity_type
    itself (core must not import app), so callers that have an opinion about
    the entity's type pass entity_type_unknown=True when it resolved to
    "unknown"; callers with none leave it False and get is_junk_entity_name
    alone.
    """
    stripped = name.strip()
    if is_junk_entity_name(stripped):
        return True
    return entity_type_unknown and (
        is_shouty_acronym_shaped(stripped) or is_codec_alias_shaped(stripped)
    )


# ---------------------------------------------------------------------------
# Quantity / date-content gate (type-aware)
# ---------------------------------------------------------------------------
# qwen2.5-3b eval (2026-09-07, 18 fixtures) ran 4x junkier than the 7B
# baseline on the identical prompt: bare measurements ("900 seconds", "5
# grams", "75°C") and leaked ATX heading markers ("# Project Orion — Budget")
# get typed as real entities. Neither shape is a proper noun, so both are
# rejected here — after the type is known, so a DATE can additionally be
# required to carry actual date content.

# "42 tokens per second" is the longest unit tail the small models produced.
_MAX_QUANTITY_UNIT_WORDS = 3

# Spelled-out counts ("fifteen minutes", "nine hundred seconds") are quantities
# too; small models emit them as often as digits.
_NUMBER_WORDS: frozenset[str] = frozenset((
    "zero", "one", "two", "three", "four", "five", "six", "seven", "eight",
    "nine", "ten", "eleven", "twelve", "thirteen", "fourteen", "fifteen",
    "sixteen", "seventeen", "eighteen", "nineteen", "twenty", "thirty",
    "forty", "fifty", "sixty", "seventy", "eighty", "ninety", "hundred",
    "thousand", "million", "half", "quarter",
))

_QUANTITY_UNIT_WORDS: frozenset[str] = frozenset((
    "ms", "s", "sec", "second", "seconds", "millisecond", "milliseconds",
    "min", "minute", "minutes", "hour", "hours", "day", "days", "week",
    "weeks", "percent", "%", "tokens", "requests", "retries", "entries",
    "items", "per", "mb", "gb", "kb", "mbps", "kbps", "gbps", "x", "°c",
    "celsius", "fahrenheit", "grams", "g", "kg", "ml", "l", "degrees",
))

# "22 grams of coffee": unit word(s), then "of", then exactly one more word.
# "24 Hours of Le Mans" doesn't match — "Le Mans" is two words after "of" —
# so it never reaches this helper; the word-count cap above rejects the
# 4-word rest first.
_QUANTITY_OF_WORD = "of"
_QUANTITY_OF_TRAILING_WORD_COUNT = 1

# "40-gram bloom": the hyphen glues the unit to the number, and the trailing
# word names a small, closed set of package/measure nouns. "100ml bottle"
# survives because "bottle" isn't in the set (and because "100ml" has no
# hyphen at all).
_HYPHEN_QUANTITY_TRAILING_WORDS: frozenset[str] = frozenset(
    ("bloom", "dose", "serving", "portion")
)
_HYPHEN_QUANTITY_WORD_COUNT = 2

# Leading digit (or leading "#digit" for a numbered-heading leak), digits/
# separators, an optional " to <number>" range, then whatever's left.
_QUANTITY_NUMBER_RE = re.compile(
    r"^#?\s*\d[\d.,\-–]*(?:\s+to\s+\d[\d.,\-–]*)?(?P<rest>.*)$",
    re.IGNORECASE,
)

_HEADING_MARKER_RE = re.compile(r"^#{1,6}\s")

_MONTH_NAMES: frozenset[str] = frozenset((
    "january", "february", "march", "april", "may", "june", "july",
    "august", "september", "october", "november", "december",
))
_WEEKDAY_NAMES: frozenset[str] = frozenset((
    "monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday",
))
_RELATIVE_DATE_WORDS: frozenset[str] = frozenset((
    "today", "yesterday", "tomorrow", "next", "last", "q1", "q2", "q3", "q4",
))
_YEAR_RE = re.compile(r"\b\d{4}\b")


def _is_quantity_of_phrase(words: list[str]) -> bool:
    """True for "<unit word(s)> of <one word>" ("grams of coffee")."""
    lowered = [w.lower() for w in words]
    try:
        of_index = lowered.index(_QUANTITY_OF_WORD)
    except ValueError:
        return False
    trailing = words[of_index + 1:]
    if of_index < 1 or len(trailing) != _QUANTITY_OF_TRAILING_WORD_COUNT:
        return False
    return all(w in _QUANTITY_UNIT_WORDS for w in lowered[:of_index])


def _is_hyphenated_quantity_phrase(name: str, rest_start: int, words: list[str]) -> bool:
    """True for "<number>-<unit> <word>" ("40-gram bloom") when the hyphen
    glues the unit to the number and the trailing word is a package/measure
    noun from :data:`_HYPHEN_QUANTITY_TRAILING_WORDS`.
    """
    if len(words) != _HYPHEN_QUANTITY_WORD_COUNT:
        return False
    if rest_start == 0 or name[rest_start - 1] != "-":
        return False
    return words[-1].lower() in _HYPHEN_QUANTITY_TRAILING_WORDS


def _is_bare_quantity(name: str) -> bool:
    """True for a number glued/paired with 1-3 unit words and nothing else.

    A trailing unit word is required — a standalone number ("2024", "747")
    is never a bare quantity on its own; that's what could make it a real
    year, model number, or ID, and is left to the DATE-content check.
    """
    words_all = name.lower().split()
    if words_all and words_all[0] in _NUMBER_WORDS:
        tail = [w for w in words_all if w not in _NUMBER_WORDS]
        return bool(tail) and len(tail) <= _MAX_QUANTITY_UNIT_WORDS and all(
            w in _QUANTITY_UNIT_WORDS for w in tail
        )
    match = _QUANTITY_NUMBER_RE.match(name)
    if not match:
        return False
    raw_rest = match.group("rest")
    rest = raw_rest.strip()
    if not rest:
        return False
    words = rest.split()
    if not words or len(words) > _MAX_QUANTITY_UNIT_WORDS:
        return False
    # "5G", "5S", "3X": a single letter glued to the number is a name, not a
    # unit; "5 g" with a space is still a quantity.
    if len(words) == 1 and len(words[0]) == 1 and not raw_rest[:1].isspace():
        return False
    if all(w.lower() in _QUANTITY_UNIT_WORDS for w in words):
        return True
    return _is_quantity_of_phrase(words) or _is_hyphenated_quantity_phrase(
        name, match.start("rest"), words
    )


def _is_punctuation_only(name: str) -> bool:
    """True when the name carries no letters or digits at all."""
    return not any(ch.isalnum() for ch in name)


def _has_date_content(name: str) -> bool:
    """True when the name contains a year, month, weekday, or relative-date word."""
    if _YEAR_RE.search(name):
        return True
    tokens = re.split(r"[^a-z0-9]+", name.lower())
    return any(
        tok in _MONTH_NAMES or tok in _WEEKDAY_NAMES or tok in _RELATIVE_DATE_WORDS
        for tok in tokens
    )


def is_junk_quantity_name(name: str, entity_type: str) -> bool:
    """Type-aware sibling to :func:`is_junk_entity_name`.

    Rejects bare quantities ("900 seconds", "75°C"), names that are only
    punctuation or a leaked markdown heading marker ("# Project Orion"),
    and DATE-typed names with no actual date content ("5 retries",
    "9:30am"). Called from :func:`_normalise_entities` once the type has
    been validated.
    """
    stripped = name.strip()
    if not stripped:
        return True
    if _HEADING_MARKER_RE.match(stripped):
        return True
    if _is_punctuation_only(stripped):
        return True
    if _is_bare_quantity(stripped):
        return True
    return entity_type == "DATE" and not _has_date_content(stripped)


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


_JSON_RETRY_INSTRUCTION = (
    "Your previous reply was not valid JSON. Reply with only the JSON "
    'object described above; every entry needs "name", "type" and '
    '"confidence".'
)


def _parse_llm_json_strict(raw: str) -> Any:
    """``parse_llm_json`` plus a shape check: caller treats both as failure."""
    parsed = parse_llm_json(raw)
    if not isinstance(parsed, (dict, list)):
        raise ValueError(f"parsed JSON is not an object or array: {type(parsed).__name__}")
    return parsed


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
        parsed = _parse_llm_json_strict(raw)
    except Exception:
        retry_messages = [*messages, {"role": "user", "content": _JSON_RETRY_INSTRUCTION}]
        try:
            retry_raw = await llm_caller(retry_messages)
        except Exception as retry_call_exc:  # noqa: BLE001 — observability boundary; fall through to []
            logger.exception("entity_extraction.llm_call_failed: %s", retry_call_exc)
            logger.info("entity_extraction.json_retry attempted=True succeeded=False")
            return []
        try:
            parsed = _parse_llm_json_strict(retry_raw)
        except Exception as retry_parse_exc:
            logger.info("entity_extraction.json_retry attempted=True succeeded=False")
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error('core.agents.entity_extraction', retry_parse_exc)
            logger.warning(
                "entity_extraction.json_parse_failed (returning [] for this chunk); "
                "first 200 chars: %r",
                retry_raw[:200] if retry_raw else "",
            )
            return []
        logger.info("entity_extraction.json_retry attempted=True succeeded=True")

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

    A model that follows the naming/typing instructions but omits the
    ``confidence`` key entirely is not the same as one that emitted 0.0 —
    treating "unreported" as 0.0 silently discarded every entity from
    small local models (qwen2.5-3b observed) that skip the field, with no
    log line explaining why. Unreported confidence is kept at the floor
    (``min_confidence``) instead; a present-but-unparseable value is still
    treated as 0.0 (it's a malformed value, not an omission).
    """
    if not isinstance(parsed, dict):
        return
    raw_list = parsed.get("entities")
    if not isinstance(raw_list, list):
        return

    seen: set[str] = set()
    kept: list[Entity] = []
    total = 0
    unreported = 0
    below_threshold = 0
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
        if is_junk_quantity_name(name, ent_type):
            logger.debug(
                "entity_extraction.rejected_junk_quantity name=%r type=%s",
                name, ent_type,
            )
            continue
        total += 1
        if "confidence" not in raw:
            unreported += 1
            confidence = min_confidence
        else:
            try:
                confidence = float(raw["confidence"])
            except (TypeError, ValueError):
                confidence = 0.0
        confidence = max(0.0, min(1.0, confidence))
        if confidence < min_confidence:
            below_threshold += 1
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
            kept.append(Entity(
                name=name,
                entity_type=ent_type,  # type: ignore[arg-type]
                canonical_id=cid,
                confidence=confidence,
            ))

    if unreported:
        logger.info(
            "entity_extraction.confidence_unreported n=%d of %d (kept at threshold %.2f)",
            unreported, total, min_confidence,
        )
    if total and below_threshold == total:
        logger.warning(
            "entity_extraction.all_below_threshold n=%d threshold=%.2f",
            total, min_confidence,
        )
    yield from kept


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
