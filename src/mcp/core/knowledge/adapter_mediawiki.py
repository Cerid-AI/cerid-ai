# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""MediaWiki Action API adapter for the knowledge-pack harness.

Materialises 1 of the 14 Phase-6 catalog packs:

- ``bogleheads-wiki`` (host ``bogleheads.org``, categories Personal_finance + Investing)

Uses the MediaWiki Action API (``/w/api.php``) rather than ``Special:Export``
because the API path:

- returns clean JSON (no XML wikitext-mixed-in-CDATA dance);
- supports content batching (50 titles per request) and pagination;
- exposes namespace + redirect controls via flat query params;
- works against any MediaWiki site without site-specific XML schemas.

The adapter intentionally avoids ``mwparserfromhell``: the conversion
needed for RAG ingest is shallow (headers, bold, links, kill templates),
and the regex layer is short enough to audit. Adding a transitive
``mwparserfromhell`` dep for one adapter would force every install to
pull the lib (it has no light import path).
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar

from core.knowledge.adapter_hf import _slugify
from core.knowledge.adapters import (
    FetchResult,
    PackSourceAdapter,
    register_adapter,
)
from core.knowledge.packs import BuildSpec, PackError, PackManifest

logger = logging.getLogger("ai-companion.knowledge_packs.adapters.mediawiki")


# ── Config ─────────────────────────────────────────────────────────────

# MediaWiki recommends a descriptive UA per their etiquette page; default
# string identifies the project + commit URL so a wiki admin can route
# bug reports back without scraping logs.
_DEFAULT_USER_AGENT = (
    "Cerid-AI-Knowledge-Pack-Builder/1.0 "
    "(+https://github.com/Cerid-AI/cerid-ai)"
)


@dataclass(frozen=True)
class MediaWikiApiConfig:
    """Validated config for :class:`MediaWikiApiAdapter`.

    - ``host``           e.g. ``"https://www.bogleheads.org"`` (https only).
    - ``api_path``       default ``"/w/api.php"`` (non-default sites override).
    - ``categories``     list of category titles (no ``Category:`` prefix);
                         each is expanded via ``categorymembers`` API.
    - ``pages``          explicit page titles to fetch in addition to
                         category-resolved pages.
    - ``min_text_chars`` skip pages whose wikitext is shorter than this.
    - ``max_pages``      cap pages returned (default unlimited).
    - ``page_batch_size`` titles per ``query&prop=revisions`` request
                         (MW API accepts up to 50 for unauth users).
    - ``rate_limit_seconds`` delay between API calls — courtesy throttle
                         to stay polite to small wikis.
    - ``user_agent``     UA string sent on every request (MW API requires
                         a non-default UA per etiquette).
    """

    host: str
    api_path: str = "/w/api.php"
    categories: tuple[str, ...] = ()
    pages: tuple[str, ...] = ()
    min_text_chars: int = 100
    max_pages: int | None = None
    page_batch_size: int = 20
    rate_limit_seconds: float = 1.0
    user_agent: str = _DEFAULT_USER_AGENT

    @classmethod
    def from_build(cls, build: BuildSpec) -> "MediaWikiApiConfig":
        cfg = build.config
        host = str(cfg.get("host", "")).strip().rstrip("/")
        if not host.startswith("https://"):
            raise PackError(
                f"mediawiki_api config: host must be https://..., got {host!r}",
            )
        if any(c in host for c in ("..", " ", "?")):
            raise PackError(
                f"mediawiki_api config: host {host!r} has unsafe characters",
            )
        categories = tuple(str(c).strip() for c in cfg.get("categories", ()))
        pages = tuple(str(p).strip() for p in cfg.get("pages", ()))
        if not categories and not pages:
            raise PackError(
                "mediawiki_api config: at least one of `categories` or "
                "`pages` must be set",
            )
        max_pages_raw = cfg.get("max_pages")
        max_pages = int(max_pages_raw) if max_pages_raw is not None else None
        if max_pages is not None and max_pages <= 0:
            raise PackError("mediawiki_api config: max_pages must be > 0 if set")
        page_batch_size = int(cfg.get("page_batch_size", 20))
        if page_batch_size <= 0 or page_batch_size > 50:
            raise PackError(
                "mediawiki_api config: page_batch_size must be 1..50 "
                "(MediaWiki API caps unauthenticated requests at 50)",
            )
        return cls(
            host=host,
            api_path=str(cfg.get("api_path", "/w/api.php")),
            categories=categories,
            pages=pages,
            min_text_chars=int(cfg.get("min_text_chars", 100)),
            max_pages=max_pages,
            page_batch_size=page_batch_size,
            rate_limit_seconds=float(cfg.get("rate_limit_seconds", 1.0)),
            user_agent=str(cfg.get("user_agent", _DEFAULT_USER_AGENT)),
        )

    @property
    def api_url(self) -> str:
        return f"{self.host}{self.api_path}"


# ── Wikitext → Markdown ───────────────────────────────────────────────

_HEADER_LEVELS = (6, 5, 4, 3, 2, 1)


def wikitext_to_markdown(wt: str) -> str:
    """Convert a MediaWiki wikitext blob to RAG-friendly markdown.

    Handles the subset that matters for retrieval:

    - ``{{Template}}`` and ``<ref>...</ref>`` stripped (inline citation
      noise that hurts retrieval more than it helps).
    - Headers ``==X==`` → ``## X`` (recursive level mapping).
    - ``'''bold'''`` → ``**bold**``; ``''italic''`` → ``*italic*``.
    - Internal links ``[[Page|alias]]`` → ``alias``; ``[[Page]]`` → ``Page``.
    - External links ``[https://x text]`` → ``[text](https://x)``.
    - HTML tags stripped.
    - Runs of blank lines collapsed to single blank.

    This is intentionally a regex layer (~25 substitutions) so it can
    be audited without pulling ``mwparserfromhell``. For pages that
    rely heavily on templates or tables, the output may be lossy —
    callers should accept that the goal is RAG-quality, not perfect
    typography.
    """
    out = wt
    # Strip templates (greedy nested handling — the simplest pattern
    # that works for two levels, which covers Bogleheads almost
    # universally; deeper nesting is rare and acceptable as residue).
    for _ in range(4):
        new = re.sub(r"\{\{[^{}]*\}\}", "", out, flags=re.DOTALL)
        if new == out:
            break
        out = new
    # Strip references.
    out = re.sub(r"<ref[^>]*?>.*?</ref>", "", out, flags=re.DOTALL)
    out = re.sub(r"<ref[^>/]*/>", "", out)
    # Headers (process deepest first to avoid clobbering ``==`` inside ``===``).
    for n in _HEADER_LEVELS:
        marker = "=" * n
        md = "#" * n
        out = re.sub(
            rf"^{marker}\s*(.+?)\s*{marker}\s*$",
            rf"{md} \1",
            out,
            flags=re.MULTILINE,
        )
    # Bold + italic.
    out = re.sub(r"'''(.+?)'''", r"**\1**", out)
    out = re.sub(r"''(.+?)''", r"*\1*", out)
    # Internal links.
    out = re.sub(r"\[\[([^\]|]+)\|([^\]]+)\]\]", r"\2", out)
    out = re.sub(r"\[\[([^\]]+)\]\]", r"\1", out)
    # External links.
    out = re.sub(
        r"\[(https?://\S+)\s+([^\]]+)\]",
        r"[\2](\1)",
        out,
    )
    # Bare external URLs in single brackets → keep URL only.
    out = re.sub(r"\[(https?://\S+)\]", r"\1", out)
    # Strip remaining HTML.
    out = re.sub(r"<[^>]+>", "", out)
    # Collapse blank-line runs.
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.strip()


# ── HTTP client (DI for tests) ────────────────────────────────────────

JsonHttpGet = Callable[[str, dict[str, Any], str], dict[str, Any]]


def _httpx_json_get(url: str, params: dict[str, Any], user_agent: str) -> dict[str, Any]:
    """Default JSON GET with httpx. Imports httpx lazily (non-test path only)."""
    import httpx

    headers = {"User-Agent": user_agent, "Accept": "application/json"}
    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=True,
    ) as client:
        resp = client.get(url, params=params, headers=headers)
        resp.raise_for_status()
        return resp.json()


# ── Adapter ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class _PageRecord:
    """Resolved page (title + text) ready for write."""

    title: str
    text: str


class MediaWikiApiAdapter(PackSourceAdapter):
    """Fetch pages from a MediaWiki site via its Action API and convert to markdown.

    Materialises ``bogleheads-wiki``. The DI HTTP getter makes the
    adapter unit-testable with no network and no httpx import. The
    default getter uses httpx and respects rate_limit_seconds between
    requests so a small wiki isn't hammered during a build.
    """

    name: ClassVar[str] = "mediawiki_api"

    def __init__(
        self,
        *,
        http_get: JsonHttpGet | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http_get = http_get or _httpx_json_get
        self._sleep = sleep

    def fetch(self, manifest: PackManifest, *, staging_root: Path) -> FetchResult:
        if manifest.build is None:
            raise PackError(f"Pack {manifest.id!r} has no build spec")
        config = MediaWikiApiConfig.from_build(manifest.build)
        logger.info(
            "mediawiki_api: building %s — %d categories, %d explicit pages "
            "(host=%s)",
            manifest.id, len(config.categories), len(config.pages), config.host,
        )

        # Stage 1: enumerate page titles (categories → titles + explicit).
        titles = self._enumerate_titles(config)
        if config.max_pages is not None:
            titles = titles[: config.max_pages]
        if not titles:
            raise PackError(
                f"mediawiki_api {manifest.id}: page enumeration returned zero titles",
            )
        logger.info("mediawiki_api: %d titles to fetch", len(titles))

        # Stage 2: batched content fetch.
        records: list[_PageRecord] = []
        for batch_start in range(0, len(titles), config.page_batch_size):
            batch = titles[batch_start : batch_start + config.page_batch_size]
            records.extend(self._fetch_content_batch(config, batch))

        # Stage 3: write markdown.
        content_root = staging_root / "content"
        content_root.mkdir(parents=True, exist_ok=True)
        seen_slugs: dict[str, int] = {}
        kept: list[Path] = []
        for record in records:
            if len(record.text) < config.min_text_chars:
                continue
            md = wikitext_to_markdown(record.text)
            if len(md) < config.min_text_chars:
                # Conversion sometimes shrinks pages below threshold
                # (heavy template noise stripped) — re-check post-convert.
                continue
            slug = _slugify(record.title)
            counter = seen_slugs.get(slug, 0)
            base = slug if counter == 0 else f"{slug}-{counter}"
            seen_slugs[slug] = counter + 1
            rel_path = Path(f"{base}.md")
            target = (content_root / rel_path).resolve()
            try:
                target.relative_to(content_root.resolve())
            except ValueError as exc:
                raise PackError(
                    f"mediawiki_api {manifest.id}: page {record.title!r} "
                    f"resolved outside content_root",
                ) from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f"# {record.title}\n\n{md}\n", encoding="utf-8",
            )
            kept.append(rel_path)

        if not kept:
            raise PackError(
                f"mediawiki_api {manifest.id}: zero pages survived filter "
                f"(min_text_chars={config.min_text_chars}). Check the recipe.",
            )
        kept.sort()
        logger.info("mediawiki_api: %s — wrote %d markdown files", manifest.id, len(kept))
        return FetchResult(content_root=content_root, files=tuple(kept))

    # --- enumeration ------------------------------------------------------

    def _enumerate_titles(self, config: MediaWikiApiConfig) -> list[str]:
        """Resolve all page titles to fetch (categories + explicit)."""
        titles: list[str] = []
        seen: set[str] = set()
        for category in config.categories:
            for title in self._iter_category_members(config, category):
                if title not in seen:
                    seen.add(title)
                    titles.append(title)
        for page in config.pages:
            if page and page not in seen:
                seen.add(page)
                titles.append(page)
        return titles

    def _iter_category_members(
        self, config: MediaWikiApiConfig, category: str,
    ) -> Iterable[str]:
        """Yield main-namespace page titles in a category, paginated."""
        cm_continue: str | None = None
        while True:
            params: dict[str, Any] = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": f"Category:{category}",
                "cmlimit": 500,
                "cmnamespace": 0,  # main namespace only — drops Talk, User, etc.
                "format": "json",
                "formatversion": 2,
            }
            if cm_continue:
                params["cmcontinue"] = cm_continue
            payload = self._call(config, params)
            for member in payload.get("query", {}).get("categorymembers", []) or []:
                title = member.get("title")
                if title:
                    yield str(title)
            cont = payload.get("continue") or {}
            cm_continue = cont.get("cmcontinue")
            if not cm_continue:
                break

    # --- content batch fetch --------------------------------------------

    def _fetch_content_batch(
        self, config: MediaWikiApiConfig, titles: list[str],
    ) -> list[_PageRecord]:
        if not titles:
            return []
        params = {
            "action": "query",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": "|".join(titles),
            "format": "json",
            "formatversion": 2,
            "redirects": 1,
        }
        payload = self._call(config, params)
        out: list[_PageRecord] = []
        for page in payload.get("query", {}).get("pages", []) or []:
            if page.get("missing") or page.get("invalid"):
                continue
            title = page.get("title", "")
            revisions = page.get("revisions") or []
            if not revisions:
                continue
            slot = (revisions[0].get("slots") or {}).get("main") or {}
            text = slot.get("content") or ""
            if not isinstance(text, str):
                continue
            out.append(_PageRecord(title=str(title), text=text))
        return out

    # --- HTTP --------------------------------------------------------------

    def _call(self, config: MediaWikiApiConfig, params: dict[str, Any]) -> dict[str, Any]:
        if config.rate_limit_seconds > 0:
            self._sleep(config.rate_limit_seconds)
        return self._http_get(config.api_url, params, config.user_agent)


# Bootstrap registry on module import.
register_adapter(MediaWikiApiAdapter())


__all__ = [
    "MediaWikiApiAdapter",
    "MediaWikiApiConfig",
    "wikitext_to_markdown",
]
