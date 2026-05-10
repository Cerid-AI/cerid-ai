# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Project Gutenberg adapter for the knowledge-pack harness.

Materialises 1 of the 14 Phase-6 catalog packs:

- ``gutenberg-classics-curated`` (a hand-picked list of public-domain
  productivity / philosophy classics — Marcus Aurelius, Franklin's
  *Autobiography*, Bennett's *How to Live on 24 Hours a Day*, etc.)

Each Gutenberg book has a plain-text mirror at
``https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt``. The adapter
downloads each, strips the Project Gutenberg license header / footer
boilerplate (which dilutes RAG signal), and writes a markdown file per
book with title + author metadata.
"""
from __future__ import annotations

import logging
import re
import time
from collections.abc import Mapping
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

logger = logging.getLogger("ai-companion.knowledge_packs.adapters.gutenberg")


_DEFAULT_USER_AGENT = (
    "Cerid-AI-Knowledge-Pack-Builder/1.0 "
    "(+https://github.com/Cerid-AI/cerid-ai)"
)
_PG_HEADER_MARKER = re.compile(
    r"\*\*\*\s*START OF (?:THE|THIS) PROJECT GUTENBERG EBOOK[^*]*\*\*\*",
    re.IGNORECASE,
)
_PG_FOOTER_MARKER = re.compile(
    r"\*\*\*\s*END OF (?:THE|THIS) PROJECT GUTENBERG EBOOK[^*]*\*\*\*",
    re.IGNORECASE,
)


# ── Config ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class GutenbergBook:
    """One curated Project Gutenberg book entry."""

    id: int
    title: str
    author: str = ""

    @classmethod
    def from_dict(cls, raw: Mapping[str, Any]) -> "GutenbergBook":
        try:
            book_id = int(raw["id"])
        except (KeyError, TypeError, ValueError) as exc:
            raise PackError(f"gutenberg config: book id missing/invalid: {raw!r}") from exc
        title = str(raw.get("title", "")).strip()
        if not title:
            raise PackError(f"gutenberg config: book id {book_id} missing title")
        return cls(id=book_id, title=title, author=str(raw.get("author", "")).strip())


@dataclass(frozen=True)
class GutenbergConfig:
    """Validated config for :class:`GutenbergAdapter`.

    - ``books``        list of ``{id, title, author?}`` entries.
    - ``url_template``  override for the per-book URL — default points at
                       ``https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt``.
    - ``min_text_chars`` per-book threshold after boilerplate strip.
    - ``rate_limit_seconds`` between downloads.
    """

    books: tuple[GutenbergBook, ...]
    url_template: str = "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt"
    min_text_chars: int = 1000
    rate_limit_seconds: float = 1.0
    user_agent: str = _DEFAULT_USER_AGENT

    @classmethod
    def from_build(cls, build: BuildSpec) -> "GutenbergConfig":
        cfg = build.config
        books_raw = cfg.get("books", [])
        if not isinstance(books_raw, list) or not books_raw:
            raise PackError(
                "gutenberg config: `books` must be a non-empty list",
            )
        books = tuple(GutenbergBook.from_dict(b) for b in books_raw)
        url_template = str(cfg.get(
            "url_template",
            "https://www.gutenberg.org/cache/epub/{id}/pg{id}.txt",
        ))
        if not url_template.startswith("https://"):
            raise PackError(
                f"gutenberg config: url_template must be https://, got "
                f"{url_template!r}",
            )
        if "{id}" not in url_template:
            raise PackError(
                f"gutenberg config: url_template must include '{{id}}' "
                f"placeholder, got {url_template!r}",
            )
        return cls(
            books=books,
            url_template=url_template,
            min_text_chars=int(cfg.get("min_text_chars", 1000)),
            rate_limit_seconds=float(cfg.get("rate_limit_seconds", 1.0)),
            user_agent=str(cfg.get("user_agent", _DEFAULT_USER_AGENT)),
        )


# ── Boilerplate stripper ──────────────────────────────────────────────

def strip_gutenberg_boilerplate(text: str) -> str:
    """Drop the Project Gutenberg license + license-footer wrapper.

    PG plain-text ebooks bracket the actual book content with marker
    lines like ``*** START OF THE PROJECT GUTENBERG EBOOK X ***`` and
    matching ``END``. The text outside is the PG license + transcriber
    credits — public domain but very dilutive when fed into RAG. If
    markers are missing we fall back to returning the input unchanged
    (some older mirrors don't include them).
    """
    start_match = _PG_HEADER_MARKER.search(text)
    end_match = _PG_FOOTER_MARKER.search(text)
    if not start_match:
        return text.strip()
    body_start = start_match.end()
    body_end = end_match.start() if end_match else len(text)
    return text[body_start:body_end].strip()


# ── HTTP layer (DI for tests) ─────────────────────────────────────────

TextDownloader = Callable[[str, str], str]


def _httpx_text_get(url: str, user_agent: str) -> str:
    import httpx

    headers = {"User-Agent": user_agent}
    with httpx.Client(
        timeout=httpx.Timeout(120.0, connect=15.0),
        follow_redirects=True,
    ) as client:
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text


# ── Adapter ──────────────────────────────────────────────────────────

class GutenbergAdapter(PackSourceAdapter):
    """Download a curated list of Project Gutenberg plain-text books."""

    name: ClassVar[str] = "gutenberg"

    def __init__(
        self,
        *,
        downloader: TextDownloader | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._downloader = downloader or _httpx_text_get
        self._sleep = sleep

    def fetch(self, manifest: PackManifest, *, staging_root: Path) -> FetchResult:
        if manifest.build is None:
            raise PackError(f"Pack {manifest.id!r} has no build spec")
        config = GutenbergConfig.from_build(manifest.build)
        logger.info(
            "gutenberg: %s — fetching %d books", manifest.id, len(config.books),
        )

        content_root = staging_root / "content"
        content_root.mkdir(parents=True, exist_ok=True)
        seen_slugs: dict[str, int] = {}
        kept: list[Path] = []
        for book in config.books:
            url = config.url_template.format(id=book.id)
            try:
                if config.rate_limit_seconds > 0:
                    self._sleep(config.rate_limit_seconds)
                raw = self._downloader(url, config.user_agent)
            except Exception as exc:  # noqa: BLE001 — observability boundary
                from core.utils.swallowed import log_swallowed_error
                log_swallowed_error(
                    "core.knowledge.adapter_gutenberg.fetch_book", exc,
                )
                logger.warning(
                    "gutenberg: skip book %d (%s): %s", book.id, book.title, exc,
                )
                continue
            body = strip_gutenberg_boilerplate(raw)
            if len(body) < config.min_text_chars:
                logger.warning(
                    "gutenberg: book %d (%s) below min_text_chars after strip "
                    "(%d < %d) — skipping",
                    book.id, book.title, len(body), config.min_text_chars,
                )
                continue
            slug = _slugify(book.title)
            counter = seen_slugs.get(slug, 0)
            base = slug if counter == 0 else f"{slug}-{counter}"
            seen_slugs[slug] = counter + 1
            rel_path = Path(f"{base}.md")
            target = (content_root / rel_path).resolve()
            try:
                target.relative_to(content_root.resolve())
            except ValueError as exc:
                raise PackError(
                    f"gutenberg {manifest.id}: book {book.title!r} resolved "
                    f"outside content_root",
                ) from exc
            target.parent.mkdir(parents=True, exist_ok=True)
            header = f"# {book.title}\n"
            if book.author:
                header += f"\n*by {book.author}*\n"
            target.write_text(
                f"{header}\n{body}\n\n---\n"
                f"source: {url} (Project Gutenberg ID {book.id})\n",
                encoding="utf-8",
            )
            kept.append(rel_path)

        if not kept:
            raise PackError(
                f"gutenberg {manifest.id}: zero books survived (all fetches "
                f"failed or returned below-threshold content)",
            )
        kept.sort()
        logger.info(
            "gutenberg: %s — wrote %d markdown files", manifest.id, len(kept),
        )
        return FetchResult(content_root=content_root, files=tuple(kept))


register_adapter(GutenbergAdapter())


__all__ = [
    "GutenbergAdapter",
    "GutenbergBook",
    "GutenbergConfig",
    "strip_gutenberg_boilerplate",
]
