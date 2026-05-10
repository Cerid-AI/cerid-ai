# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Wiki enrichment orchestrator — Phase API.3.

Routes an entity to the appropriate external-API adapters based on a
best-effort entity-type classification, then aggregates the results into
a list of :class:`app.services.wiki_pages.ExternalReference` objects.

Design decisions
----------------
* Pure orchestration: no FastAPI deps, no Neo4j driver, no Redis.  May
  only import from ``core.*`` and from ``app.services.external_apis.*``.
  The ``ExternalReference`` model lives in ``app.services.wiki_pages``
  (Pydantic models are co-located with the service that owns them), so
  we import lazily to avoid a circular path at module load time.
* Entity-type inference is intentionally heuristic: it uses cheap regex
  patterns.  A higher-accuracy classifier (e.g. a small NER model or a
  Wikidata lookup round-trip) is a follow-up; the heuristic is documented
  so the caller knows what it is.
* Adapter dispatch is driven by the ``_ADAPTER_INVOKER`` registry: a
  dict from slug → ``(primary_method_name, arg_extractor)`` so new
  adapters can be wired with zero changes to ``enrich()``.
* All adapter calls are individually try/except'd.  A failing adapter
  does not block others.
* ``WIKI_ENRICHMENT_ENABLED`` is NOT checked here — callers (WikiRefreshJob)
  own that feature flag so the orchestrator stays testable in isolation.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any, Callable, Literal

from app.services.external_apis.github import GitHubAdapter
from app.services.external_apis.openlibrary import OpenLibraryAdapter
from app.services.external_apis.osm import OSMAdapter
from app.services.external_apis.packages import PackagesAdapter
from app.services.external_apis.wikidata import WikidataAdapter
from app.services.external_apis.wikipedia import WikipediaAdapter
from app.services.wiki_pages import ExternalReference
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.external_apis.wiki_enrichment")

# ---------------------------------------------------------------------------
# Entity type
# ---------------------------------------------------------------------------

EntityType = Literal[
    "person",
    "concept",
    "place",
    "book",
    "package_pypi",
    "package_npm",
    "repository",
    "unknown",
]

# ---------------------------------------------------------------------------
# Adapter routing table
# entity_type → ordered list of adapter slugs to consult
# ---------------------------------------------------------------------------

ADAPTER_ROUTING: dict[str, list[str]] = {
    "person":       ["wikipedia", "wikidata"],
    "concept":      ["wikipedia", "wikidata"],
    "place":        ["osm", "wikipedia"],
    "book":         ["openlibrary", "wikipedia"],
    "package_pypi": ["packages"],
    "package_npm":  ["packages"],
    "repository":   ["github"],
    "unknown":      ["wikipedia"],  # Wikipedia is the catch-all
}

# ---------------------------------------------------------------------------
# Entity-type inference
# ---------------------------------------------------------------------------

# Patterns used to detect well-known structural forms in the entity name.
# Anything that does not match falls through to "unknown".
_ISBN_RE = re.compile(r"^\d{9}[\dXx]$|^\d{13}$|^978\d{10}$")
_PYPI_RE = re.compile(r"^[a-zA-Z0-9]([a-zA-Z0-9._-]*[a-zA-Z0-9])?$")
_NPM_SCOPED_RE = re.compile(r"^@[a-zA-Z0-9-]+/[a-zA-Z0-9._-]+$")
_GITHUB_REPO_RE = re.compile(r"^[a-zA-Z0-9_.-]+/[a-zA-Z0-9_.-]+$")

# Place-indicator words (heuristic — not exhaustive)
_PLACE_WORDS = frozenset(
    w.lower() for w in (
        "city", "town", "village", "country", "state", "province", "region",
        "island", "mountain", "river", "lake", "ocean", "sea", "bay",
        "peninsula", "continent", "district", "county", "territory",
        "republic", "kingdom", "empire",
    )
)


def infer_entity_type(entity_name: str, related_entities: list[str] | None = None) -> EntityType:
    """Return a best-effort entity type for ``entity_name``.

    Classification is intentionally lightweight (regex + keyword heuristics).
    A higher-accuracy approach (small NER model, Wikidata type lookup) is a
    planned follow-up.  The heuristic covers the most common structural
    patterns; ambiguous names fall through to ``"unknown"``.

    Parameters
    ----------
    entity_name:
        The canonical name or slug of the entity (e.g. ``"Python (programming
        language)"``, ``"@scope/pkg"``, ``"owner/repo"``).
    related_entities:
        Optional list of related entity names — not yet used by the heuristic
        but accepted for future classifier compatibility.

    Returns
    -------
    EntityType literal.
    """
    name = entity_name.strip()

    # --- ISBN / book ----------------------------------------------------------
    # Strip common separators first (spaces, hyphens)
    isbn_clean = re.sub(r"[\s\-]", "", name)
    if _ISBN_RE.match(isbn_clean):
        return "book"

    # --- GitHub repository (owner/repo) ---------------------------------------
    if _GITHUB_REPO_RE.match(name) and "/" in name:
        return "repository"

    # --- npm scoped package (@scope/name) -------------------------------------
    if _NPM_SCOPED_RE.match(name):
        return "package_npm"

    # --- Canonical-ID prefix convention (used throughout the Cerid graph) -----
    # e.g. "person:elon-musk", "place:berlin", "package_pypi:httpx"
    if ":" in name:
        prefix, _, _ = name.partition(":")
        prefix = prefix.lower()
        if prefix == "person":
            return "person"
        if prefix in ("place", "location", "geo"):
            return "place"
        if prefix in ("book", "work"):
            return "book"
        if prefix in ("package_pypi", "pypi"):
            return "package_pypi"
        if prefix in ("package_npm", "npm"):
            return "package_npm"
        if prefix in ("repository", "repo", "github"):
            return "repository"
        if prefix in ("concept", "term", "idea"):
            return "concept"

    # --- Place keyword in name ------------------------------------------------
    lower_name = name.lower()
    for word in _PLACE_WORDS:
        if word in lower_name.split():
            return "place"

    # Disambiguation parenthetical that mentions place
    paren_match = re.search(r"\(([^)]+)\)", name)
    if paren_match:
        paren_content = paren_match.group(1).lower()
        if any(w in paren_content for w in ("city", "town", "country", "state", "province")):
            return "place"
        if any(w in paren_content for w in ("programming", "language", "software", "library", "framework", "tool")):
            return "concept"
        if any(w in paren_content for w in ("book", "novel", "film", "album", "song", "series")):
            return "book"

    # --- Pure PyPI-name heuristic: all-lowercase, short, no spaces -----------
    # This is intentionally conservative — only fires when the name looks like
    # a package slug (lowercase, uses dashes/underscores, no spaces).
    if " " not in name and len(name) <= 64 and _PYPI_RE.match(name) and name == name.lower():
        # Ambiguous — could be PyPI or npm.  Default to unknown so Wikipedia
        # is consulted; the caller can override via canonical-id prefix.
        pass  # fall through to unknown

    return "unknown"


# ---------------------------------------------------------------------------
# Adapter invoker registry
# ---------------------------------------------------------------------------
# Each entry: slug → (method_name, arg_extractor)
# arg_extractor(entity_name, entity_type) returns the positional args + kwargs
# for the adapter method.

_ArgExtractor = Callable[[str, EntityType], tuple[tuple[Any, ...], dict[str, Any]]]


def _simple_lookup(entity_name: str, entity_type: EntityType) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Pass entity_name as the sole positional arg to lookup()."""
    return (entity_name,), {}


def _github_extractor(entity_name: str, entity_type: EntityType) -> tuple[tuple[Any, ...], dict[str, Any]]:
    """Split owner/repo or pass entity_name as a username."""
    if "/" in entity_name:
        owner, _, repo = entity_name.partition("/")
        return (), {"owner": owner, "repo": repo}
    return (), {"username": entity_name}


def _packages_pypi_extractor(entity_name: str, entity_type: EntityType) -> tuple[tuple[Any, ...], dict[str, Any]]:
    # Strip canonical-id prefix if present
    pkg = entity_name.split(":")[-1] if ":" in entity_name else entity_name
    return (pkg,), {"ecosystem": "pypi"}


def _packages_npm_extractor(entity_name: str, entity_type: EntityType) -> tuple[tuple[Any, ...], dict[str, Any]]:
    pkg = entity_name.split(":")[-1] if ":" in entity_name else entity_name
    return (pkg,), {"ecosystem": "npm"}


# When entity_type is package_npm but adapter slug is "packages", we need
# to dispatch correctly.  The _ADAPTER_INVOKER uses the *adapter slug* as
# the key; entity_type is only used by arg-extractors that need it.

_ADAPTER_INVOKER: dict[str, tuple[str, _ArgExtractor]] = {
    "wikipedia": ("lookup", _simple_lookup),
    "wikidata":  ("lookup", _simple_lookup),
    "osm":       ("lookup", _simple_lookup),
    "openlibrary": ("lookup", _simple_lookup),
    "github":    ("lookup", _github_extractor),
    "packages":  ("lookup", _packages_pypi_extractor),  # default; npm overridden per entity_type
}

# Separate npm extractor: used when entity_type is package_npm
_NPM_INVOKER: tuple[str, _ArgExtractor] = ("lookup", _packages_npm_extractor)


# ---------------------------------------------------------------------------
# Result → ExternalReference converter
# ---------------------------------------------------------------------------


def _to_external_reference(
    slug: str,
    display_name: str,
    result: Any,
    entity_type: EntityType,
    fetched_at: str,
) -> ExternalReference | None:
    """Convert an adapter result dict into an ExternalReference.

    Each adapter returns a different shape; we normalise here.  Returns
    ``None`` if the result cannot produce a meaningful snippet.
    """
    if not isinstance(result, dict):
        # e.g. OSM returns a list — take first item if non-empty
        if isinstance(result, list) and result:
            result = result[0]
        else:
            return None

    # Adapter-specific field extraction
    title: str = ""
    snippet: str = ""
    url: str | None = None
    metadata: dict[str, Any] = {}

    if slug == "wikipedia":
        title = str(result.get("title") or entity_type)
        snippet = str(result.get("extract") or "")[:200]
        url = result.get("content_url")
        metadata = {"thumbnail_url": result.get("thumbnail_url"), "last_updated": result.get("last_updated")}

    elif slug == "wikidata":
        title = str(result.get("label") or result.get("entity_id") or "")
        snippet = str(result.get("description") or "")[:200]
        eid = result.get("entity_id", "")
        url = f"https://www.wikidata.org/wiki/{eid}" if eid else None
        metadata = {"claims_count": result.get("claims_count", 0)}

    elif slug == "osm":
        title = str(result.get("display_name") or "")
        snippet = f"lat: {result.get('lat')}, lon: {result.get('lon')}, type: {result.get('type')}"
        snippet = snippet[:200]
        url = None  # Nominatim has no stable page URL per result
        metadata = {"lat": result.get("lat"), "lon": result.get("lon"), "type": result.get("type")}

    elif slug == "openlibrary":
        title = str(result.get("title") or "")
        authors = result.get("authors") or []
        publish_date = result.get("publish_date") or ""
        snippet = f"Published {publish_date}. Authors: {', '.join(authors)}" if authors else f"Published {publish_date}"
        desc = str(result.get("description") or "")
        if desc and len(snippet) < 180:
            snippet = (snippet + " — " + desc)[: 200]
        url = result.get("cover_url")  # best available link
        metadata = {"subjects": result.get("subjects") or [], "publishers": result.get("publishers") or []}

    elif slug == "github":
        title = str(result.get("full_name") or result.get("login") or result.get("name") or "")
        snippet = str(result.get("description") or result.get("bio") or "")[:200]
        url = result.get("url")
        metadata = {k: v for k, v in result.items() if k not in ("description", "bio", "url", "full_name", "login")}

    elif slug == "packages":
        name = str(result.get("name") or "")
        version = str(result.get("version") or "")
        summary = str(result.get("summary") or result.get("description") or "")
        title = f"{name} {version}".strip() if version else name
        snippet = summary[:200]
        url = result.get("home_page") or result.get("homepage") or None
        metadata = {"license": result.get("license"), "author": result.get("author")}

    else:
        # Generic fallback
        title = str(result.get("title") or result.get("name") or "")
        snippet = str(result.get("extract") or result.get("description") or result.get("summary") or "")[:200]
        url = result.get("url") or result.get("content_url") or result.get("home_page")

    if not title and not snippet:
        return None

    return ExternalReference(
        source=slug,
        source_display=display_name,
        title=title,
        snippet=snippet,
        url=url or None,
        fetched_at=fetched_at,
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# Public async API
# ---------------------------------------------------------------------------


async def enrich(
    entity_name: str,
    entity_type: EntityType,
    *,
    registry: Any,
    http_lock: Any | None = None,
) -> list[ExternalReference]:
    """Enrich an entity by consulting the adapters mapped to ``entity_type``.

    Parameters
    ----------
    entity_name:
        Canonical name / slug of the entity to look up.
    entity_type:
        Inferred entity type; drives adapter selection via ``ADAPTER_ROUTING``.
    registry:
        The ``app.services.external_apis.registry`` module (passed as a
        parameter so callers can substitute a mock in tests).
    http_lock:
        Unused for now (reserved for a future inter-call rate-limiter
        shared between enrichment calls); accepted to future-proof the
        signature.

    Returns
    -------
    list[ExternalReference]
        One entry per successfully retrieved adapter result.  An empty
        list means all adapters were either disabled or failed.
    """
    # Adapter instances created per-call so tests can patch adapter classes
    # at module level (e.g. patch("...wiki_enrichment.WikipediaAdapter")).
    _ADAPTER_INSTANCES: dict[str, Any] = {
        "wikipedia": WikipediaAdapter(),
        "wikidata": WikidataAdapter(),
        "osm": OSMAdapter(),
        "openlibrary": OpenLibraryAdapter(),
        "github": GitHubAdapter(),
        "packages": PackagesAdapter(),
    }

    _DISPLAY_NAMES: dict[str, str] = {
        "wikipedia": "Wikipedia",
        "wikidata": "Wikidata",
        "osm": "OpenStreetMap",
        "openlibrary": "Open Library",
        "github": "GitHub",
        "packages": "PyPI / npm",
    }

    slugs_to_consult: list[str] = ADAPTER_ROUTING.get(entity_type, ADAPTER_ROUTING["unknown"])
    fetched_at = datetime.now(timezone.utc).isoformat()
    refs: list[ExternalReference] = []


    for slug in slugs_to_consult:
        # Check enabled state via registry module
        try:
            enabled = registry.is_enabled(slug)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(f"wiki_enrichment.{slug}.is_enabled", exc)
            continue

        if not enabled:
            logger.debug("wiki_enrichment.skip_disabled slug=%s entity=%s", slug, entity_name)
            continue

        adapter = _ADAPTER_INSTANCES.get(slug)
        if adapter is None:
            logger.warning("wiki_enrichment.unknown_adapter slug=%s", slug)
            continue

        # Determine method name + args for this adapter+entity_type
        if slug == "packages" and entity_type == "package_npm":
            method_name, arg_extractor = _NPM_INVOKER
        else:
            method_name, arg_extractor = _ADAPTER_INVOKER.get(slug, ("lookup", _simple_lookup))

        method = getattr(adapter, method_name, None)
        if method is None:
            logger.warning("wiki_enrichment.missing_method slug=%s method=%s", slug, method_name)
            continue

        try:
            args, kwargs = arg_extractor(entity_name, entity_type)
            result = await method(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            log_swallowed_error(f"wiki_enrichment.{slug}", exc)
            continue

        display_name = _DISPLAY_NAMES.get(slug, slug)
        ref = _to_external_reference(slug, display_name, result, entity_type, fetched_at)
        if ref is not None:
            refs.append(ref)
            logger.debug(
                "wiki_enrichment.enriched slug=%s entity=%s title=%r",
                slug, entity_name, ref.title,
            )

    return refs
