# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Wikipedia/Wikivoyage XML-dump adapter for the knowledge-pack harness.

Materialises 1 of the 14 Phase-6 catalog packs:

- ``wikivoyage-en`` (dump ``dumps.wikimedia.org/enwikivoyage/latest/...``)

Uses Wikimedia's standard pages-articles XML dump (``.xml.bz2``):
streams via :mod:`bz2` + :func:`xml.etree.ElementTree.iterparse` so a
100 MB compressed dump never lands whole in RAM. Each page's wikitext
is converted to markdown via
:func:`core.knowledge.adapter_mediawiki.wikitext_to_markdown` — the
same converter used by the live MediaWiki API adapter, so the two
paths produce structurally identical output.
"""
from __future__ import annotations

import bz2
import logging
import time
import xml.etree.ElementTree as ET
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import IO, Callable, ClassVar

from core.knowledge.adapter_hf import _slugify
from core.knowledge.adapter_mediawiki import wikitext_to_markdown
from core.knowledge.adapters import (
    FetchResult,
    PackSourceAdapter,
    register_adapter,
)
from core.knowledge.packs import (
    BuildSpec,
    PackError,
    PackManifest,
    sha256_of_file,
)

logger = logging.getLogger("ai-companion.knowledge_packs.adapters.wiki_dump")


_DEFAULT_USER_AGENT = (
    "Cerid-AI-Knowledge-Pack-Builder/1.0 "
    "(+https://github.com/Cerid-AI/cerid-ai)"
)


# ── Config ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WikiDumpConfig:
    """Validated config for :class:`WikiDumpAdapter`."""

    dump_url: str
    dump_sha256: str = ""
    include_namespaces: tuple[int, ...] = (0,)  # main only by default
    min_text_chars: int = 200
    max_pages: int | None = None
    skip_redirects: bool = True
    user_agent: str = _DEFAULT_USER_AGENT
    max_download_bytes: int = 2 * 1024 * 1024 * 1024  # 2 GiB hard cap

    @classmethod
    def from_build(cls, build: BuildSpec) -> "WikiDumpConfig":
        cfg = build.config
        dump_url = str(cfg.get("dump_url", "")).strip()
        if not dump_url.startswith("https://"):
            raise PackError(
                f"wiki_dump config: dump_url must be https://..., got "
                f"{dump_url!r}",
            )
        if not dump_url.endswith(".xml.bz2"):
            raise PackError(
                f"wiki_dump config: dump_url must end with .xml.bz2, got "
                f"{dump_url!r}",
            )
        ns_raw = cfg.get("include_namespaces", (0,))
        if not isinstance(ns_raw, (list, tuple)) or not ns_raw:
            raise PackError(
                "wiki_dump config: include_namespaces must be a non-empty list",
            )
        try:
            namespaces = tuple(int(n) for n in ns_raw)
        except (TypeError, ValueError) as exc:
            raise PackError(
                f"wiki_dump config: include_namespaces must be ints, got {ns_raw!r}",
            ) from exc
        max_pages_raw = cfg.get("max_pages")
        max_pages = int(max_pages_raw) if max_pages_raw is not None else None
        if max_pages is not None and max_pages <= 0:
            raise PackError("wiki_dump config: max_pages must be > 0 if set")
        sha = str(cfg.get("dump_sha256", "")).strip().lower()
        if sha and len(sha) != 64:
            raise PackError(
                f"wiki_dump config: dump_sha256 must be 64-char hex, got {sha!r}",
            )
        return cls(
            dump_url=dump_url,
            dump_sha256=sha,
            include_namespaces=namespaces,
            min_text_chars=int(cfg.get("min_text_chars", 200)),
            max_pages=max_pages,
            skip_redirects=bool(cfg.get("skip_redirects", True)),
            user_agent=str(cfg.get("user_agent", _DEFAULT_USER_AGENT)),
            max_download_bytes=int(
                cfg.get("max_download_bytes", 2 * 1024 * 1024 * 1024),
            ),
        )


# ── Page records ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class _ParsedPage:
    title: str
    namespace: int
    text: str
    is_redirect: bool


# ── XML parsing (stream-friendly) ─────────────────────────────────────

def iter_pages_from_dump(stream: IO[bytes]) -> Iterator[_ParsedPage]:
    """Iterate parsed pages from a Wikimedia ``.xml`` dump byte stream.

    ``stream`` is an open binary file (typically the result of
    ``bz2.open(path, 'rb')``). The function uses
    :func:`xml.etree.ElementTree.iterparse` so memory usage stays
    bounded regardless of the source dump size. Elements are cleared
    after each ``<page>`` to release intermediate parse trees.
    """
    # Wikimedia dumps have a stable namespace URI; rather than chase
    # the local-name idiom, we strip namespaces lazily via tag.endswith.
    # nosec B314 — the dump is fetched from the upstream allow-list
    # (config/knowledge_packs_allowlist.json) over https, with optional
    # sha256 verification. iterparse is the only memory-bounded option
    # for multi-100 MB Wikimedia dumps; defusedxml.iterparse is API-
    # equivalent but adds a transitive dep we'd rather avoid.
    context = ET.iterparse(stream, events=("end",))  # nosec B314
    for _event, elem in context:
        if not elem.tag.endswith("}page") and elem.tag != "page":
            continue
        title = (_find_local(elem, "title") or "").strip()
        ns_text = _find_local(elem, "ns") or "0"
        try:
            namespace = int(ns_text)
        except ValueError:
            namespace = 0
        revision = _find_local_elem(elem, "revision")
        text = ""
        if revision is not None:
            text = _find_local(revision, "text") or ""
        is_redirect = _find_local_elem(elem, "redirect") is not None
        yield _ParsedPage(
            title=title, namespace=namespace, text=text,
            is_redirect=is_redirect,
        )
        elem.clear()


def _find_local(elem: ET.Element, local_name: str) -> str | None:
    found = _find_local_elem(elem, local_name)
    return found.text if found is not None else None


def _find_local_elem(elem: ET.Element, local_name: str) -> ET.Element | None:
    for child in elem:
        tag = child.tag
        if tag.endswith("}" + local_name) or tag == local_name:
            return child
    return None


# ── HTTP layer (DI for tests) ────────────────────────────────────────

DumpDownloader = Callable[[str, Path, str, int], None]


def _httpx_stream_download(
    url: str, dest: Path, user_agent: str, max_bytes: int,
) -> None:
    """Stream a multi-100MB download to disk with size cap."""
    import httpx

    headers = {"User-Agent": user_agent}
    dest.parent.mkdir(parents=True, exist_ok=True)
    timeout = httpx.Timeout(600.0, connect=30.0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        with client.stream("GET", url, headers=headers) as resp:
            resp.raise_for_status()
            total = 0
            with open(dest, "wb") as fh:
                for chunk in resp.iter_bytes(chunk_size=1 << 20):
                    total += len(chunk)
                    if total > max_bytes:
                        raise PackError(
                            f"wiki_dump download exceeded max_bytes={max_bytes} "
                            f"({total} bytes downloaded so far)",
                        )
                    fh.write(chunk)


# ── Stream open layer (DI for tests) ──────────────────────────────────

DumpStreamOpener = Callable[[Path], IO[bytes]]


def _default_dump_opener(path: Path) -> IO[bytes]:
    """Wrap ``bz2.open`` so tests can swap in a plain-bytes opener."""
    return bz2.open(path, "rb")


# ── Adapter ─────────────────────────────────────────────────────────────

class WikiDumpAdapter(PackSourceAdapter):
    """Stream a Wikimedia ``.xml.bz2`` dump and write each page as markdown.

    DI surface:

    - ``downloader`` — fetches ``dump_url`` to a local path. Default
      uses httpx with a 2 GiB cap and 10-min timeout. Tests pass a
      callable that copies test bytes into ``dest``.
    - ``stream_opener`` — opens the downloaded file as a binary stream
      that :func:`iter_pages_from_dump` consumes. Default uses
      ``bz2.open`` so the tests can pass plain XML bytes (gzipped or
      not) by overriding to ``open(path, 'rb')``.
    - ``sleep`` — courtesy throttle hook (mostly inert for dump
      adapters since there's only one HTTP call).
    """

    name: ClassVar[str] = "wiki_dump"

    def __init__(
        self,
        *,
        downloader: DumpDownloader | None = None,
        stream_opener: DumpStreamOpener | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._downloader = downloader or _httpx_stream_download
        self._stream_opener = stream_opener or _default_dump_opener
        self._sleep = sleep

    def fetch(self, manifest: PackManifest, *, staging_root: Path) -> FetchResult:
        if manifest.build is None:
            raise PackError(f"Pack {manifest.id!r} has no build spec")
        config = WikiDumpConfig.from_build(manifest.build)
        logger.info("wiki_dump: downloading %s for pack %s", config.dump_url, manifest.id)

        dump_path = staging_root / "dump.xml.bz2"
        with _download_phase(
            self._downloader,
            config,
            dump_path,
        ):
            if config.dump_sha256:
                actual = sha256_of_file(dump_path)
                if actual.lower() != config.dump_sha256.lower():
                    raise PackError(
                        f"wiki_dump {manifest.id}: dump sha256 mismatch — "
                        f"expected {config.dump_sha256}, got {actual}",
                    )

            content_root = staging_root / "content"
            content_root.mkdir(parents=True, exist_ok=True)
            seen_slugs: dict[str, int] = {}
            kept: list[Path] = []
            ns_set = set(config.include_namespaces)
            with self._stream_opener(dump_path) as stream:
                for page in iter_pages_from_dump(stream):
                    if config.max_pages is not None and len(kept) >= config.max_pages:
                        break
                    if page.namespace not in ns_set:
                        continue
                    if config.skip_redirects and page.is_redirect:
                        continue
                    if not page.text or len(page.text) < config.min_text_chars:
                        continue
                    md = wikitext_to_markdown(page.text)
                    if len(md) < config.min_text_chars:
                        continue
                    slug = _slugify(page.title)
                    counter = seen_slugs.get(slug, 0)
                    base = slug if counter == 0 else f"{slug}-{counter}"
                    seen_slugs[slug] = counter + 1
                    rel_path = Path(f"{base}.md")
                    target = (content_root / rel_path).resolve()
                    try:
                        target.relative_to(content_root.resolve())
                    except ValueError as exc:
                        raise PackError(
                            f"wiki_dump {manifest.id}: page {page.title!r} "
                            f"resolved outside content_root",
                        ) from exc
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(
                        f"# {page.title}\n\n{md}\n", encoding="utf-8",
                    )
                    kept.append(rel_path)
        if not kept:
            raise PackError(
                f"wiki_dump {manifest.id}: zero pages survived filter "
                f"(namespaces={sorted(ns_set)}, min_text_chars="
                f"{config.min_text_chars}). Check the recipe.",
            )
        kept.sort()
        logger.info(
            "wiki_dump: %s — wrote %d markdown files (from %d-byte dump)",
            manifest.id, len(kept), dump_path.stat().st_size,
        )
        return FetchResult(content_root=content_root, files=tuple(kept))


@contextmanager
def _download_phase(
    downloader: DumpDownloader, config: WikiDumpConfig, dest: Path,
):
    """Download, yield to caller for processing, leave file on disk for inspection.

    Kept as a context manager so a future change can clean up the bz2
    blob automatically — current behavior is to leave it in place
    (under the staging dir, which the orchestrator removes on success).
    """
    downloader(
        config.dump_url, dest, config.user_agent, config.max_download_bytes,
    )
    yield


register_adapter(WikiDumpAdapter())


__all__ = [
    "WikiDumpAdapter",
    "WikiDumpConfig",
    "iter_pages_from_dump",
]
