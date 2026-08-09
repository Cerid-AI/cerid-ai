# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Python stdlib HTML-release-zip adapter for the knowledge-pack harness.

Materialises 1 of the 14 Phase-6 catalog packs:

- ``python-stdlib-docs`` (HTML release at
  ``https://docs.python.org/3/archives/...-docs-html.zip``)

Composes :class:`adapters.GithubZipAdapter`-style zip handling with
:func:`adapter_html_scrape.extract_html_content` so we get a single
auditable code path for "download a zip of HTML, walk each file, write
clean markdown". The adapter only differs from ``github_zip`` in:

- the URL is taken verbatim from the recipe (no GitHub repo path
  translation);
- every kept member is HTML, processed through the html extractor
  rather than copied to the archive verbatim.
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ClassVar

from core.knowledge.adapter_hf import _slugify
from core.knowledge.adapter_html_scrape import extract_html_content
from core.knowledge.adapters import (
    FetchResult,
    PackSourceAdapter,
    register_adapter,
)
from core.knowledge.packs import BuildSpec, PackError, PackManifest

logger = logging.getLogger(
    "ai-companion.knowledge_packs.adapters.python_docs",
)


# ── Config ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class PythonDocsZipConfig:
    """Validated config for :class:`PythonDocsZipAdapter`."""

    archive_url: str
    archive_sha256: str = ""
    include_globs: tuple[str, ...] = ("library/**/*.html",)
    exclude_globs: tuple[str, ...] = ()
    content_selector: str = "div.body"  # Sphinx pages put main content here
    title_tag: str = "h1"
    min_text_chars: int = 400
    max_pages: int | None = None
    max_archive_bytes: int = 200 * 1024 * 1024

    @classmethod
    def from_build(cls, build: BuildSpec) -> "PythonDocsZipConfig":
        cfg = build.config
        url = str(cfg.get("archive_url", "")).strip()
        if not url.startswith("https://"):
            raise PackError(
                f"python_docs_zip config: archive_url must be https://..., "
                f"got {url!r}",
            )
        if not url.endswith(".zip"):
            raise PackError(
                f"python_docs_zip config: archive_url must end in .zip, got {url!r}",
            )
        sha = str(cfg.get("archive_sha256", "")).strip().lower()
        if sha and len(sha) != 64:
            raise PackError(
                f"python_docs_zip config: archive_sha256 must be 64-char hex, got {sha!r}",
            )
        includes = tuple(str(g) for g in cfg.get(
            "include_globs", ("library/**/*.html",),
        ))
        if not includes:
            raise PackError(
                "python_docs_zip config: include_globs must be non-empty",
            )
        max_pages_raw = cfg.get("max_pages")
        max_pages = int(max_pages_raw) if max_pages_raw is not None else None
        if max_pages is not None and max_pages <= 0:
            raise PackError("python_docs_zip config: max_pages must be > 0 if set")
        return cls(
            archive_url=url,
            archive_sha256=sha,
            include_globs=includes,
            exclude_globs=tuple(str(g) for g in cfg.get("exclude_globs", ())),
            content_selector=str(cfg.get("content_selector", "div.body")),
            title_tag=str(cfg.get("title_tag", "h1")),
            min_text_chars=int(cfg.get("min_text_chars", 400)),
            max_pages=max_pages,
            max_archive_bytes=int(cfg.get(
                "max_archive_bytes", 200 * 1024 * 1024,
            )),
        )


# ── HTTP layer (DI for tests) ─────────────────────────────────────────

ZipDownloader = Callable[[str, int], bytes]


def _httpx_zip_downloader(url: str, max_bytes: int) -> bytes:
    import httpx

    timeout = httpx.Timeout(300.0, connect=15.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:  # follow_redirects: fixed docs.python.org zip (https-validated); streamed
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            buf = io.BytesIO()
            total = 0
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                total += len(chunk)
                if total > max_bytes:
                    raise PackError(
                        f"python_docs_zip download exceeded max_bytes={max_bytes} "
                        f"({total} bytes)",
                    )
                buf.write(chunk)
            return buf.getvalue()


# ── Glob match (URL-style, segment-agnostic) ───────────────────────────

def _glob_match(text: str, glob: str) -> bool:
    """git-style with ``**`` recursive — same as ``adapters._glob_match``.

    Local copy to avoid importing the private helper from the parent
    module (which would make it part of the public API by accident).
    """
    parts: list[str] = []
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if i + 1 < len(glob) and glob[i + 1] == "*":
                if i + 2 < len(glob) and glob[i + 2] == "/":
                    parts.append("(?:.*/)?")
                    i += 3
                    continue
                parts.append(".*")
                i += 2
                continue
            parts.append("[^/]*")
            i += 1
            continue
        if c == "?":
            parts.append("[^/]")
            i += 1
            continue
        parts.append(re.escape(c))
        i += 1
    return bool(re.fullmatch("".join(parts), text))


def _resolve_zip_top_dir(zf: zipfile.ZipFile) -> str:
    """Return the single top-level directory inside a Sphinx-built zip.

    The Python docs zip wraps everything under ``python-3.x.x-docs-html/``.
    We strip that universally so file paths are deterministic across
    minor versions.
    """
    tops: set[str] = set()
    for name in zf.namelist():
        head = name.split("/", 1)[0]
        if head:
            tops.add(head)
    if len(tops) != 1:
        raise PackError(
            f"python_docs_zip: archive must have exactly one top-level "
            f"directory; got {sorted(tops)!r}",
        )
    return next(iter(tops)) + "/"


def _path_matches_any(path_str: str, globs) -> bool:
    return any(_glob_match(path_str, g) for g in globs)


# ── Adapter ────────────────────────────────────────────────────────────

class PythonDocsZipAdapter(PackSourceAdapter):
    """Download the Python docs HTML zip + walk each HTML file as a doc."""

    name: ClassVar[str] = "python_docs_zip"

    def __init__(
        self,
        *,
        downloader: ZipDownloader | None = None,
    ) -> None:
        self._downloader = downloader or _httpx_zip_downloader

    def fetch(self, manifest: PackManifest, *, staging_root: Path) -> FetchResult:
        if manifest.build is None:
            raise PackError(f"Pack {manifest.id!r} has no build spec")
        config = PythonDocsZipConfig.from_build(manifest.build)
        logger.info(
            "python_docs_zip: fetching %s for pack %s",
            config.archive_url, manifest.id,
        )
        archive_bytes = self._downloader(config.archive_url, config.max_archive_bytes)
        if config.archive_sha256:
            import hashlib

            actual = hashlib.sha256(archive_bytes).hexdigest()
            if actual.lower() != config.archive_sha256.lower():
                raise PackError(
                    f"python_docs_zip {manifest.id}: archive sha256 mismatch — "
                    f"expected {config.archive_sha256}, got {actual}",
                )

        content_root = staging_root / "content"
        content_root.mkdir(parents=True, exist_ok=True)
        seen_slugs: dict[str, int] = {}
        kept: list[Path] = []
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            top_dir = _resolve_zip_top_dir(zf)
            members = sorted(zf.infolist(), key=lambda m: m.filename)
            for member in members:
                if config.max_pages is not None and len(kept) >= config.max_pages:
                    break
                if member.is_dir() or not member.filename.startswith(top_dir):
                    continue
                rel = member.filename[len(top_dir):]
                if not rel.endswith(".html"):
                    continue
                if not _path_matches_any(rel, config.include_globs):
                    continue
                if config.exclude_globs and _path_matches_any(rel, config.exclude_globs):
                    continue
                html = zf.read(member.filename).decode("utf-8", errors="replace")
                title, text = extract_html_content(
                    html,
                    content_selector=config.content_selector,
                    title_tag=config.title_tag,
                )
                if len(text) < config.min_text_chars:
                    continue
                if not title:
                    title = Path(rel).stem
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
                        f"python_docs_zip {manifest.id}: page {rel!r} resolved "
                        f"outside content_root",
                    ) from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(
                    f"# {title}\n\n{text}\n\n---\nsource: {rel}\n",
                    encoding="utf-8",
                )
                kept.append(rel_path)
        if not kept:
            raise PackError(
                f"python_docs_zip {manifest.id}: zero files survived filter "
                f"(globs={list(config.include_globs)}, "
                f"min_text_chars={config.min_text_chars}). Check the recipe.",
            )
        kept.sort()
        logger.info(
            "python_docs_zip: %s — wrote %d markdown files",
            manifest.id, len(kept),
        )
        return FetchResult(content_root=content_root, files=tuple(kept))


register_adapter(PythonDocsZipAdapter())


__all__ = [
    "PythonDocsZipAdapter",
    "PythonDocsZipConfig",
]
