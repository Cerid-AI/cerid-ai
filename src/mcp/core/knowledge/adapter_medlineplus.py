# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""MedlinePlus health-topics XML adapter (``medlineplus_xml``).

MedlinePlus publishes its NIH/NLM-authored health topics as one XML file
(``mplus_topics_compressed.xml``) of ``<health-topic>`` elements whose
``<full-summary>`` holds HTML prose. NLM-authored topic summaries are
US-government public domain; the copyrighted A.D.A.M. encyclopedia and
drug/supplement monographs live in *separate* feeds, not this one — but
this adapter still filters defensively by title prefix and topic group so
the recipe's completeness claim ("NLM PD health topics") holds.

The summary HTML is reduced to prose with the stdlib-only
``extract_html_content`` (no bs4/lxml dep), and the whole document is
parsed with ``safe_fromstring`` (XXE/billion-laughs-hardened).
"""
from __future__ import annotations

import gzip
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ClassVar
from xml.etree.ElementTree import ParseError

from core.knowledge.adapter_hf import _slugify
from core.knowledge.adapter_html_scrape import extract_html_content
from core.knowledge.adapters import (
    FetchResult,
    PackSourceAdapter,
    _httpx_zip_downloader,
    register_adapter,
)
from core.knowledge.packs import BuildSpec, PackError, PackManifest
from core.utils.safe_xml import safe_fromstring

logger = logging.getLogger("ai-companion.knowledge_packs.adapters.medlineplus")


@dataclass(frozen=True)
class MedlineplusXmlConfig:
    """Validated config for :class:`MedlineplusXmlAdapter`.

    - ``source_url`` — the MedlinePlus topics XML (``https://``).
    - ``language`` — keep only topics in this language (default English).
    - ``exclude_element_prefixes`` — drop a topic whose title starts with
      any of these (e.g. ``"A.D.A.M."``).
    - ``exclude_categories`` — drop a topic whose ``<group>`` text matches
      any of these keywords (e.g. drugs, herbs-and-supplements).
    - ``min_text_chars`` — skip a topic whose summary prose is shorter.
    """

    source_url: str
    language: str = "English"
    exclude_element_prefixes: tuple[str, ...] = ()
    exclude_categories: tuple[str, ...] = ()
    min_text_chars: int = 200

    @classmethod
    def from_build(cls, build: BuildSpec) -> "MedlineplusXmlConfig":
        cfg = build.config
        source_url = str(cfg.get("source_url", "")).strip()
        if not source_url.startswith("https://"):
            raise PackError(
                f"medlineplus_xml config: source_url must be https://, got "
                f"{source_url!r}",
            )
        return cls(
            source_url=source_url,
            language=str(cfg.get("language", "English")),
            exclude_element_prefixes=tuple(
                str(p) for p in cfg.get("exclude_element_prefixes", ())
            ),
            exclude_categories=tuple(
                str(c).lower() for c in cfg.get("exclude_categories", ())
            ),
            min_text_chars=int(cfg.get("min_text_chars", 200)),
        )


def _maybe_gunzip(raw: bytes) -> bytes:
    """Transparently gunzip a gzip-magic payload; pass plain XML through."""
    if raw[:2] == b"\x1f\x8b":
        return gzip.decompress(raw)
    return raw


def _render_topic(
    title: str, summary_html: str, *, also_called: str = "",
) -> str:
    """Render one health topic to markdown (HTML summary → prose)."""
    _, text = extract_html_content(summary_html)
    lines = [f"# {title}"]
    if also_called:
        lines.append(f"\n_Also called: {also_called}_")
    return "\n".join(lines) + "\n\n" + text + "\n"


class MedlineplusXmlAdapter(PackSourceAdapter):
    """Download the MedlinePlus topics XML and render one markdown file per topic.

    DI-injectable ``downloader`` keeps the adapter unit-testable with an
    in-memory XML document and no network.
    """

    name: ClassVar[str] = "medlineplus_xml"

    def __init__(
        self,
        *,
        downloader: Callable[[str, int], bytes] | None = None,
        max_bytes: int = 200 * 1024 * 1024,
    ) -> None:
        self._downloader = downloader or _httpx_zip_downloader
        self._max_bytes = max_bytes

    def fetch(self, manifest: PackManifest, *, staging_root: Path) -> FetchResult:
        if manifest.build is None:
            raise PackError(f"Pack {manifest.id!r} has no build spec")
        config = MedlineplusXmlConfig.from_build(manifest.build)
        logger.info("medlineplus_xml: fetching %s for pack %s", config.source_url, manifest.id)
        raw = self._downloader(config.source_url, self._max_bytes)
        try:
            root = safe_fromstring(_maybe_gunzip(raw))
        except ParseError as exc:
            raise PackError(
                f"medlineplus_xml {manifest.id}: topic XML parse failed: {exc}",
            ) from exc

        content_root = staging_root / "content"
        content_root.mkdir(parents=True, exist_ok=True)
        seen_slugs: dict[str, int] = {}
        kept: list[Path] = []
        for topic in root.iter("health-topic"):
            if topic.get("language", "English") != config.language:
                continue
            title = (topic.get("title") or "").strip()
            if not title:
                continue
            if any(title.startswith(p) for p in config.exclude_element_prefixes):
                continue
            groups = [(g.text or "").strip().lower() for g in topic.iter("group")]
            if config.exclude_categories and any(
                any(cat in grp for grp in groups) for cat in config.exclude_categories
            ):
                continue
            summary_html = topic.findtext("full-summary") or ""
            also_called = ", ".join(
                (ac.text or "").strip() for ac in topic.iter("also-called") if ac.text
            )
            body = _render_topic(title, summary_html, also_called=also_called)
            if len(body) < config.min_text_chars:
                continue
            slug = _slugify(title)
            counter = seen_slugs.get(slug, 0)
            base = slug if counter == 0 else f"{slug}-{counter}"
            seen_slugs[slug] = counter + 1
            out_rel = Path(f"{base}.md")
            target = (content_root / out_rel).resolve()
            try:
                target.relative_to(content_root.resolve())
            except ValueError as exc:
                raise PackError(
                    f"medlineplus_xml {manifest.id}: topic {title!r} escapes content_root",
                ) from exc
            target.write_text(body, encoding="utf-8")
            kept.append(out_rel)
        if not kept:
            raise PackError(
                f"medlineplus_xml {manifest.id}: no topic survived filters "
                f"(language={config.language!r}, min_text_chars={config.min_text_chars}).",
            )
        kept.sort()
        logger.info("medlineplus_xml: %s — wrote %d topics", manifest.id, len(kept))
        return FetchResult(content_root=content_root, files=tuple(kept))


register_adapter(MedlineplusXmlAdapter())


__all__ = ["MedlineplusXmlAdapter", "MedlineplusXmlConfig"]
