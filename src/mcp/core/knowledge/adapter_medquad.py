# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""MedQuAD question/answer XML adapter (``qa_xml``).

MedQuAD (``github.com/abachaa/MedQuAD``) ships ~16k consumer-health Q&A
pairs as XML, not prose — so the ``github_zip`` adapter would copy raw
markup, which is poor RAG content. This adapter downloads the repo zip,
parses each ``<Document><QAPairs><QAPair><Question>/<Answer>`` entry, and
renders one clean markdown file per source document (Q&A as sections).

CC-BY-4.0 — attribution is stamped per chunk at ingest via pack
provenance. The 3 MedlinePlus-derived subsets (A.D.A.M. encyclopedia,
drugs, herbs/supplements) whose answers were removed for copyright are
dropped via ``exclude_globs`` in the recipe; any document with an empty
``<Answer>`` is also skipped defensively.
"""
from __future__ import annotations

import io
import logging
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ClassVar

from core.knowledge.adapter_hf import _slugify
from core.knowledge.adapters import (
    FetchResult,
    PackSourceAdapter,
    _httpx_zip_downloader,
    _path_matches_any,
    _relative_to_top,
    _resolve_zip_top_dir,
    register_adapter,
)
from core.knowledge.packs import BuildSpec, PackError, PackManifest
from core.utils.safe_xml import safe_fromstring

logger = logging.getLogger("ai-companion.knowledge_packs.adapters.medquad")


@dataclass(frozen=True)
class QaXmlConfig:
    """Validated config for :class:`QaXmlAdapter`.

    - ``repo`` — ``owner/name`` GitHub repo of MedQuAD-shaped Q&A XML.
    - ``ref`` — branch/tag/commit (default ``"master"``).
    - ``include_globs`` — XML members to parse (default ``("**/*.xml",)``).
    - ``exclude_globs`` — members to drop (the copyright-stripped subsets).
    - ``min_text_chars`` — drop a rendered document shorter than this.
    """

    repo: str
    ref: str = "master"
    include_globs: tuple[str, ...] = ("**/*.xml",)
    exclude_globs: tuple[str, ...] = ()
    min_text_chars: int = 80

    @classmethod
    def from_build(cls, build: BuildSpec) -> "QaXmlConfig":
        cfg = build.config
        repo = str(cfg.get("repo", "")).strip()
        if any(c in repo for c in ("..", " ", ":", "?")):
            raise PackError(f"qa_xml config: repo {repo!r} has unsafe characters")
        if "/" not in repo or repo.count("/") != 1:
            raise PackError(f"qa_xml config: repo must be 'owner/name', got {repo!r}")
        includes = tuple(str(g) for g in cfg.get("include_globs", ())) or ("**/*.xml",)
        return cls(
            repo=repo,
            ref=str(cfg.get("ref", "master")),
            include_globs=includes,
            exclude_globs=tuple(str(g) for g in cfg.get("exclude_globs", ())),
            min_text_chars=int(cfg.get("min_text_chars", 80)),
        )

    @property
    def archive_url(self) -> str:
        return f"https://github.com/{self.repo}/archive/{self.ref}.zip"


def _render_document(xml_bytes: bytes) -> tuple[str, str] | None:
    """Parse one MedQuAD ``<Document>`` → ``(title, markdown)``.

    Returns ``None`` when the XML is unparseable or contains no Q&A pair
    with both a question and a non-empty answer.
    """
    try:
        root = safe_fromstring(xml_bytes)  # XXE/billion-laughs-hardened
    except ET.ParseError:
        return None
    focus = (root.findtext("Focus") or "").strip()
    title = focus or (root.get("id") or "medquad-document")
    source = (root.get("source") or "").strip()
    url = (root.get("url") or "").strip()

    sections: list[str] = []
    for qa in root.iterfind(".//QAPair"):
        question = (qa.findtext("Question") or "").strip()
        answer = (qa.findtext("Answer") or "").strip()
        if not question or not answer:
            continue
        sections.append(f"## {question}\n\n{answer}\n")
    if not sections:
        return None

    lines = [f"# {title}"]
    provenance = " ".join(x for x in (source, url) if x).strip()
    if provenance:
        lines.append(f"\n_Source: {provenance}_")
    body = "\n".join(lines) + "\n\n" + "\n".join(sections)
    return title, body


class QaXmlAdapter(PackSourceAdapter):
    """Download a MedQuAD-shaped Q&A repo zip and render markdown per document.

    DI-injectable ``downloader`` keeps the adapter unit-testable with an
    in-memory zip and no network.
    """

    name: ClassVar[str] = "qa_xml"

    def __init__(
        self,
        *,
        downloader: Callable[[str, int], bytes] | None = None,
        max_archive_bytes: int = 200 * 1024 * 1024,
    ) -> None:
        self._downloader = downloader or _httpx_zip_downloader
        self._max_archive_bytes = max_archive_bytes

    def fetch(self, manifest: PackManifest, *, staging_root: Path) -> FetchResult:
        if manifest.build is None:
            raise PackError(f"Pack {manifest.id!r} has no build spec")
        config = QaXmlConfig.from_build(manifest.build)
        logger.info(
            "qa_xml: fetching %s @ %s for pack %s",
            config.repo, config.ref, manifest.id,
        )
        archive_bytes = self._downloader(config.archive_url, self._max_archive_bytes)

        content_root = staging_root / "content"
        content_root.mkdir(parents=True, exist_ok=True)
        seen_slugs: dict[str, int] = {}
        kept: list[Path] = []
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            top_dir = _resolve_zip_top_dir(zf)
            for member in zf.infolist():
                if member.is_dir():
                    continue
                rel = _relative_to_top(member.filename, top_dir)
                if rel is None:
                    continue
                rel_path = Path(rel)
                if not _path_matches_any(rel_path, config.include_globs):
                    continue
                if config.exclude_globs and _path_matches_any(
                    rel_path, config.exclude_globs,
                ):
                    continue
                rendered = _render_document(zf.read(member.filename))
                if rendered is None:
                    continue
                title, body = rendered
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
                        f"qa_xml {manifest.id}: rendered path {out_rel!r} escapes "
                        f"content_root",
                    ) from exc
                target.write_text(body, encoding="utf-8")
                kept.append(out_rel)
        if not kept:
            raise PackError(
                f"qa_xml {manifest.id}: no document yielded a usable Q&A pair "
                f"(include_globs={list(config.include_globs)}).",
            )
        kept.sort()
        logger.info("qa_xml: %s — wrote %d markdown files", manifest.id, len(kept))
        return FetchResult(content_root=content_root, files=tuple(kept))


register_adapter(QaXmlAdapter())


__all__ = ["QaXmlAdapter", "QaXmlConfig"]
