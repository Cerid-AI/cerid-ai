# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Sitemap-driven + URL-list HTML scraper for the knowledge-pack harness.

Materialises 2 of the 14 Phase-6 catalog packs:

- ``cfpb-ask`` (sitemap: ``consumerfinance.gov/sitemap.xml``,
  filter: ``/ask-cfpb/...``)
- ``irs-publications-curated`` (url_list: 7 hand-picked browser-friendly
  publication URLs at ``irs.gov/forms-pubs/...``)

Discovery modes
===============

- ``sitemap`` — download a single ``sitemap.xml``, optionally filter by
  glob against the URL path, optionally cap by ``max_pages``.
- ``url_list`` — explicit list of canonical URLs (used for the IRS
  curated subset where the publication landing page itself is the most
  authoritative source).

Extraction
==========

Stdlib ``html.parser`` only — no ``bs4`` / ``lxml`` dep so the adapter
is auditable and the harness install stays slim. The extractor walks
the DOM with a simple stack:

- ``strip_tags``     elements whose entire subtree is ignored (default
                     covers ``script``, ``style``, ``nav``, ``footer``,
                     ``header``, ``aside``, ``form``, ``button``,
                     ``noscript``).
- ``content_tag_id`` if set, only emit text from the subtree rooted at
                     ``<tag id="...">`` (e.g. ``main``).
- ``title_tag``      the tag whose first text yields the page title
                     (default ``h1``).

Output is plain-text-with-blank-line-paragraphs. RAG ingest tokenises
the same way regardless of markdown structure, so a clean text dump
trades typography for portability + auditability.
"""
from __future__ import annotations

import logging
import re
import time
import urllib.parse
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Callable, ClassVar

from core.knowledge.adapter_hf import _slugify
from core.knowledge.adapters import (
    FetchResult,
    PackSourceAdapter,
    register_adapter,
)
from core.knowledge.packs import BuildSpec, PackError, PackManifest

logger = logging.getLogger("ai-companion.knowledge_packs.adapters.html_scrape")


_DEFAULT_USER_AGENT = (
    "Cerid-AI-Knowledge-Pack-Builder/1.0 "
    "(+https://github.com/Cerid-AI/cerid-ai)"
)
_DEFAULT_STRIP_TAGS = frozenset({
    "script", "style", "nav", "footer", "header", "aside",
    "form", "button", "noscript", "svg", "iframe", "template",
})
# Inline-context tags whose newline-after handling would over-fragment
# paragraphs. Block tags get a trailing newline to preserve structure.
_BLOCK_TAGS = frozenset({
    "p", "div", "section", "article", "li", "tr",
    "h1", "h2", "h3", "h4", "h5", "h6",
    "blockquote", "pre", "table", "ul", "ol", "dl", "br",
})


# ── Config ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class HtmlScrapeConfig:
    """Validated config for :class:`HtmlScrapeAdapter`.

    Discovery (exactly one of):

    - ``sitemap_url``      single XML sitemap to download + parse
    - ``url_list``         explicit list of page URLs

    Extraction:

    - ``url_glob``         optional glob filter on sitemap-discovered URLs
    - ``exclude_globs``    drop URLs matching any of these globs
    - ``content_selector`` ``"tag#id"`` (e.g. ``"main#main-content"``)
                           or ``"tag.class"``; if unset, take the
                           ``<body>`` subtree minus strip_tags.
    - ``strip_tags``       additional tags to drop alongside the defaults
    - ``title_tag``        first text inside this tag is the page title
                           (default ``"h1"``; falls back to ``<title>``)
    - ``min_text_chars``   skip pages whose extracted text is shorter
    - ``max_pages``        cap discovered URLs (post-filter)
    - ``rate_limit_seconds`` between HTTP requests
    - ``user_agent``       UA string for every request
    - ``host_allowlist``   if set, every discovered URL's host must
                           match one of these prefixes (defence against
                           a sitemap that lists external links)
    """

    sitemap_url: str = ""
    url_list: tuple[str, ...] = ()
    url_glob: str = ""
    exclude_globs: tuple[str, ...] = ()
    content_selector: str = ""
    strip_tags: tuple[str, ...] = ()
    title_tag: str = "h1"
    min_text_chars: int = 200
    max_pages: int | None = None
    rate_limit_seconds: float = 1.0
    user_agent: str = _DEFAULT_USER_AGENT
    host_allowlist: tuple[str, ...] = ()

    @classmethod
    def from_build(cls, build: BuildSpec) -> "HtmlScrapeConfig":
        cfg = build.config
        sitemap_url = str(cfg.get("sitemap_url", "")).strip()
        url_list = tuple(str(u).strip() for u in cfg.get("url_list", ()))
        if bool(sitemap_url) == bool(url_list):
            raise PackError(
                "html_scrape config: exactly one of `sitemap_url` or "
                "`url_list` must be set",
            )
        if sitemap_url and not sitemap_url.startswith("https://"):
            raise PackError(
                f"html_scrape config: sitemap_url must be https://..., "
                f"got {sitemap_url!r}",
            )
        for u in url_list:
            if not u.startswith("https://"):
                raise PackError(
                    f"html_scrape config: url_list entries must be https://, "
                    f"got {u!r}",
                )
        max_pages_raw = cfg.get("max_pages")
        max_pages = int(max_pages_raw) if max_pages_raw is not None else None
        if max_pages is not None and max_pages <= 0:
            raise PackError("html_scrape config: max_pages must be > 0 if set")
        host_allowlist = tuple(str(h).strip() for h in cfg.get("host_allowlist", ()))
        for h in host_allowlist:
            if not h.startswith("https://"):
                raise PackError(
                    f"html_scrape config: host_allowlist entries must be "
                    f"https://, got {h!r}",
                )
        content_selector = str(cfg.get("content_selector", ""))
        if content_selector and not _is_valid_selector(content_selector):
            raise PackError(
                f"html_scrape config: content_selector {content_selector!r} "
                f"must be 'tag', 'tag#id', or 'tag.class'",
            )
        return cls(
            sitemap_url=sitemap_url,
            url_list=url_list,
            url_glob=str(cfg.get("url_glob", "")),
            exclude_globs=tuple(str(g) for g in cfg.get("exclude_globs", ())),
            content_selector=content_selector,
            strip_tags=tuple(str(t) for t in cfg.get("strip_tags", ())),
            title_tag=str(cfg.get("title_tag", "h1")),
            min_text_chars=int(cfg.get("min_text_chars", 200)),
            max_pages=max_pages,
            rate_limit_seconds=float(cfg.get("rate_limit_seconds", 1.0)),
            user_agent=str(cfg.get("user_agent", _DEFAULT_USER_AGENT)),
            host_allowlist=host_allowlist,
        )


_SELECTOR_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9-]*(?:[#.][a-zA-Z][a-zA-Z0-9_-]*)?$")


def _is_valid_selector(selector: str) -> bool:
    """Allow only ``tag``, ``tag#id``, or ``tag.class`` — no descendants,
    no attribute selectors. Keeps the parser stack simple + auditable."""
    return bool(_SELECTOR_RE.match(selector))


# ── HTML extraction (stdlib only) ────────────────────────────────────

@dataclass
class _Selector:
    tag: str
    id: str = ""
    class_name: str = ""

    @classmethod
    def parse(cls, raw: str) -> "_Selector":
        if not raw:
            return cls(tag="")
        if "#" in raw:
            tag, _, ident = raw.partition("#")
            return cls(tag=tag, id=ident)
        if "." in raw:
            tag, _, cls_ = raw.partition(".")
            return cls(tag=tag, class_name=cls_)
        return cls(tag=raw)

    def matches(self, tag: str, attrs: dict[str, str]) -> bool:
        if self.tag and tag != self.tag:
            return False
        if self.id and attrs.get("id") != self.id:
            return False
        if self.class_name:
            classes = (attrs.get("class") or "").split()
            if self.class_name not in classes:
                return False
        return True


class _ContentExtractor(HTMLParser):
    """DOM-walking text extractor.

    Maintains a depth counter for ``strip_tags`` so we drop the entire
    subtree (we increment depth on ``<script>``, ignore everything until
    ``</script>``). When ``content_selector`` is set, text is only
    appended while the *current* element ancestry includes a matching
    open tag.
    """

    def __init__(
        self,
        *,
        strip_tags: frozenset[str],
        content_selector: _Selector,
        title_tag: str,
    ) -> None:
        super().__init__(convert_charrefs=True)
        self._strip_tags = strip_tags
        self._content_selector = content_selector
        self._title_tag = title_tag

        self._strip_depth = 0
        self._in_content_subtree = not bool(content_selector.tag)
        self._content_open_count = 0  # nesting depth for selector matches
        self._fragment_path: list[str] = []  # tag stack for break decisions
        # Parallel to _fragment_path: True where that open element actually
        # matched the content selector. Lets handle_endtag decrement the
        # subtree count only for matching closes, not every same-named tag
        # (a plain nested </div> must not close a `div.body` content region).
        self._content_match_stack: list[bool] = []

        # h1 (or configured title_tag) wins; document <title> is fallback
        # when no in-body title is found.
        self._h1_title = ""
        self._doc_title = ""
        self._capturing_title = False
        self._title_buf: list[str] = []

        self._capturing_doc_title = False
        self._doc_title_buf: list[str] = []

        self._chunks: list[str] = []

    # --- HTMLParser overrides ----------------------------------------

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr_dict = {k: (v or "") for k, v in attrs}

        if self._strip_depth > 0:
            if tag in self._strip_tags:
                self._strip_depth += 1
            return
        if tag in self._strip_tags:
            self._strip_depth = 1
            return

        matched_content = False
        if self._content_selector.tag:
            if self._content_selector.matches(tag, attr_dict):
                matched_content = True
                self._content_open_count += 1
                self._in_content_subtree = True
        if self._in_content_subtree and not self._h1_title and tag == self._title_tag:
            self._capturing_title = True
            self._title_buf = []
        if not self._doc_title and tag == "title":
            self._capturing_doc_title = True
            self._doc_title_buf = []
        self._fragment_path.append(tag)
        self._content_match_stack.append(matched_content)
        # Block-level open: emit a newline so paragraphs separate cleanly.
        if tag in _BLOCK_TAGS and self._chunks and not self._chunks[-1].endswith("\n"):
            self._chunks.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if self._strip_depth > 0:
            if tag in self._strip_tags:
                self._strip_depth -= 1
            return

        if self._fragment_path and self._fragment_path[-1] == tag:
            self._fragment_path.pop()
            # Pop the matching content-flag in lockstep so we only close the
            # content subtree on the element that actually opened it — a plain
            # nested </div> inside `div.body` must not truncate the region.
            closed_match = self._content_match_stack.pop() if self._content_match_stack else False
            if closed_match and self._content_open_count > 0:
                self._content_open_count -= 1
                if self._content_open_count == 0:
                    self._in_content_subtree = False

        if self._capturing_title and tag == self._title_tag:
            self._capturing_title = False
            self._h1_title = " ".join("".join(self._title_buf).split()).strip()
        if self._capturing_doc_title and tag == "title":
            self._capturing_doc_title = False
            self._doc_title = " ".join("".join(self._doc_title_buf).split()).strip()
        if tag in _BLOCK_TAGS and self._chunks and not self._chunks[-1].endswith("\n"):
            self._chunks.append("\n")

    def handle_data(self, data: str) -> None:
        if self._strip_depth > 0:
            return
        if self._capturing_title:
            self._title_buf.append(data)
        if self._capturing_doc_title:
            self._doc_title_buf.append(data)
        if self._in_content_subtree and data.strip():
            # Normalise internal whitespace (newlines + tabs + multiple
            # spaces) to single spaces. Block-level breaks come from
            # explicit "\n" emitted in handle_starttag/handle_endtag.
            self._chunks.append(re.sub(r"\s+", " ", data))

    # --- Result API ---------------------------------------------------

    @property
    def title(self) -> str:
        return self._h1_title or self._doc_title

    @property
    def text(self) -> str:
        raw = "".join(self._chunks)
        # Tighten any " \n " runs to plain "\n", then cap blank-line runs.
        out = re.sub(r" *\n *", "\n", raw)
        out = re.sub(r"\n{3,}", "\n\n", out)
        return out.strip()


def extract_html_content(
    html: str, *,
    strip_tags: Iterable[str] = (),
    content_selector: str = "",
    title_tag: str = "h1",
) -> tuple[str, str]:
    """Return ``(title, text)`` for an HTML document.

    Used by :class:`HtmlScrapeAdapter` and exported for direct testing
    of the extraction layer without setting up the full adapter.
    """
    strip = frozenset(_DEFAULT_STRIP_TAGS | set(strip_tags))
    extractor = _ContentExtractor(
        strip_tags=strip,
        content_selector=_Selector.parse(content_selector),
        title_tag=title_tag,
    )
    extractor.feed(html)
    extractor.close()
    return extractor.title, extractor.text


# ── Sitemap parsing ──────────────────────────────────────────────────

_SITEMAP_NS = "{http://www.sitemaps.org/schemas/sitemap/0.9}"


def parse_sitemap_urls(xml_text: str) -> list[str]:
    """Return ``<url><loc>`` values from a sitemap XML document.

    Tolerates sitemap-index files (``<sitemapindex>``) by returning the
    nested ``<sitemap><loc>`` references — the caller can then recurse
    if needed. For the catalog packs targeted today (CFPB), the leaf
    sitemap returns the article-page URLs directly.
    """
    try:
        # nosec B314 — sitemap_url is constrained to https + the upstream
        # allow-list (config/knowledge_packs_allowlist.json). The sitemap
        # body is curator-published metadata, not user input. A
        # billion-laughs attack via wikimedia.org / consumerfinance.gov
        # / irs.gov would require compromising the upstream itself.
        root = ET.fromstring(xml_text)  # nosec B314
    except ET.ParseError as exc:
        raise PackError(f"sitemap parse failed: {exc}") from exc
    urls: list[str] = []
    for elem in root.iter():
        if elem.tag.endswith("loc") and elem.text:
            urls.append(elem.text.strip())
    return urls


# ── HTTP layer (DI for tests) ────────────────────────────────────────

HttpGet = Callable[[str, str], str]


def _httpx_text_get(url: str, user_agent: str) -> str:
    import httpx

    headers = {"User-Agent": user_agent}
    with httpx.Client(
        timeout=httpx.Timeout(60.0, connect=10.0),
        follow_redirects=True,
    ) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text


# ── Adapter ─────────────────────────────────────────────────────────────

class HtmlScrapeAdapter(PackSourceAdapter):
    """HTML scraper with sitemap or url_list discovery + stdlib extraction."""

    name: ClassVar[str] = "html_scrape"

    def __init__(
        self,
        *,
        http_get: HttpGet | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._http_get = http_get or _httpx_text_get
        self._sleep = sleep

    def fetch(self, manifest: PackManifest, *, staging_root: Path) -> FetchResult:
        if manifest.build is None:
            raise PackError(f"Pack {manifest.id!r} has no build spec")
        config = HtmlScrapeConfig.from_build(manifest.build)
        urls = self._discover_urls(config)
        if config.max_pages is not None:
            urls = urls[: config.max_pages]
        if not urls:
            raise PackError(
                f"html_scrape {manifest.id}: discovery returned zero URLs",
            )
        logger.info(
            "html_scrape: %s — %d URLs to fetch", manifest.id, len(urls),
        )

        content_root = staging_root / "content"
        content_root.mkdir(parents=True, exist_ok=True)
        seen_slugs: dict[str, int] = {}
        kept: list[Path] = []
        strip = list(config.strip_tags)
        for url in urls:
            try:
                if config.rate_limit_seconds > 0:
                    self._sleep(config.rate_limit_seconds)
                html = self._http_get(url, config.user_agent)
            except Exception as exc:  # noqa: BLE001 — observability boundary
                from core.utils.swallowed import log_swallowed_error
                log_swallowed_error(
                    "core.knowledge.adapter_html_scrape.fetch_url", exc,
                )
                logger.warning("html_scrape: skip %s (%s)", url, exc)
                continue
            title, text = extract_html_content(
                html,
                strip_tags=strip,
                content_selector=config.content_selector,
                title_tag=config.title_tag,
            )
            if not title:
                # Last-resort: derive from URL slug.
                title = urllib.parse.urlparse(url).path.strip("/").split("/")[-1] or "untitled"
            if len(text) < config.min_text_chars:
                continue
            slug = _slugify(title)
            counter = seen_slugs.get(slug, 0)
            base = slug if counter == 0 else f"{slug}-{counter}"
            seen_slugs[slug] = counter + 1
            rel_path = Path(f"{base}.md")
            target = (content_root / rel_path).resolve()
            try:
                target.relative_to(content_root.resolve())
            except ValueError as exc:
                raise PackError(
                    f"html_scrape {manifest.id}: page {url!r} resolved outside content_root",
                ) from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(
                f"# {title}\n\n{text}\n\n---\nsource: {url}\n", encoding="utf-8",
            )
            kept.append(rel_path)
        if not kept:
            raise PackError(
                f"html_scrape {manifest.id}: zero pages survived filter "
                f"(min_text_chars={config.min_text_chars}). Check the recipe.",
            )
        kept.sort()
        logger.info(
            "html_scrape: %s — wrote %d markdown files", manifest.id, len(kept),
        )
        return FetchResult(content_root=content_root, files=tuple(kept))

    # --- Discovery --------------------------------------------------------

    def _discover_urls(self, config: HtmlScrapeConfig) -> list[str]:
        if config.url_list:
            urls = list(config.url_list)
        else:
            if config.rate_limit_seconds > 0:
                self._sleep(config.rate_limit_seconds)
            xml = self._http_get(config.sitemap_url, config.user_agent)
            urls = parse_sitemap_urls(xml)

        # Optional URL-glob filter (most useful for sitemap-driven where
        # the site sitemap returns 100k URLs but we want one section).
        if config.url_glob:
            urls = [u for u in urls if _glob_match(u, config.url_glob)]
        if config.exclude_globs:
            urls = [
                u for u in urls
                if not any(_glob_match(u, g) for g in config.exclude_globs)
            ]
        # Host allow-list — defence against a hijacked sitemap that
        # smuggles cross-host URLs.
        if config.host_allowlist:
            urls = [
                u for u in urls
                if any(u.startswith(h) for h in config.host_allowlist)
            ]
        # Dedup while preserving order.
        seen: set[str] = set()
        out: list[str] = []
        for u in urls:
            if u not in seen:
                seen.add(u)
                out.append(u)
        return out


def _glob_match(text: str, glob: str) -> bool:
    """URL-style glob: ``*`` matches any chars including ``/``.

    URLs don't have path-segment semantics like file globs do — a
    user-written glob like ``https://x.gov/ask-cfpb/*`` is meant to
    match ``/ask-cfpb/q1/`` *and* ``/ask-cfpb/spanish/q1/``. So
    ``*`` is greedy in this translator (unlike the file-system
    ``_glob_match`` in :mod:`core.knowledge.adapters`).
    """
    parts: list[str] = []
    for c in glob:
        if c == "*":
            parts.append(".*")
        elif c == "?":
            parts.append(".")
        else:
            parts.append(re.escape(c))
    return bool(re.fullmatch("".join(parts), text))


register_adapter(HtmlScrapeAdapter())


__all__ = [
    "HtmlScrapeAdapter",
    "HtmlScrapeConfig",
    "extract_html_content",
    "parse_sitemap_urls",
]
