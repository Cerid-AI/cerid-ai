# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Source adapters for materialising a knowledge-pack tarball from upstream.

The harness used to support a single fetch path (``download → extract``).
For Phase 7 we lift that into a Strategy: each catalog entry's
``build.adapter`` selects how the upstream is fetched and converted
into the pack's ``content/`` tree. The orchestrator (``scripts/build_catalog``)
then reuses :func:`scripts.build_knowledge_pack._build_pack` to seal
the tarball + populate sha256.

Adapter contract
================

Each concrete adapter:

1. Reads its config dict from ``manifest.build.config``.
2. Writes plain-text/markdown files under a returned ``content_root``
   directory inside ``staging_root``.
3. Returns ``(content_root, ordered_relative_files)`` so the orchestrator
   can both seal the tarball and report a deterministic file list.

Adapters never write to the install state, never touch chromadb/Neo4j,
and never log to Sentry directly — they're pure build-time helpers
inside ``core/`` so the layer contract holds (no ``app/`` import).
"""
from __future__ import annotations

import io
import logging
import re
import zipfile
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, ClassVar, Iterable

from core.knowledge.packs import BuildSpec, PackError, PackManifest

logger = logging.getLogger("ai-companion.knowledge_packs.adapters")


@dataclass(frozen=True)
class FetchResult:
    """Output of :meth:`PackSourceAdapter.fetch`.

    ``content_root`` is the directory whose files map 1:1 onto the
    archive's ``content/`` tree. ``files`` is the ordered list of
    relative paths that should be packaged (the orchestrator can
    cross-check that nothing escaped the root).
    """

    content_root: Path
    files: tuple[Path, ...]


class PackSourceAdapter(ABC):
    """Strategy for materialising a pack from an upstream source.

    Subclasses register a unique ``name`` (matched against
    ``BuildSpec.adapter``). The orchestrator looks up the adapter via
    :func:`get_adapter`.
    """

    name: ClassVar[str] = ""

    @abstractmethod
    def fetch(self, manifest: PackManifest, *, staging_root: Path) -> FetchResult:
        """Materialise the pack's content into ``staging_root``."""
        raise NotImplementedError


_REGISTRY: dict[str, PackSourceAdapter] = {}


def register_adapter(adapter: PackSourceAdapter) -> PackSourceAdapter:
    """Register an adapter instance under its ``name``. Idempotent on re-register."""
    if not adapter.name:
        raise PackError("Adapter must declare a non-empty `name`")
    _REGISTRY[adapter.name] = adapter
    return adapter


def get_adapter(name: str) -> PackSourceAdapter:
    """Look up a registered adapter; raise :class:`PackError` if unknown."""
    try:
        return _REGISTRY[name]
    except KeyError as exc:
        raise PackError(
            f"Unknown adapter {name!r}; registered: {sorted(_REGISTRY)!r}",
        ) from exc


# ── glob helpers (sized for adapter use) ──────────────────────────────────

def _path_matches_any(path: Path, globs: Iterable[str]) -> bool:
    """``True`` iff ``path`` matches at least one git-style glob."""
    s = path.as_posix()
    return any(_glob_match(s, g) for g in globs)


def _glob_match(path_str: str, glob: str) -> bool:
    """git-style glob matching with proper ``**`` recursion.

    Translation rules:

    - ``**/`` → ``(?:.*/)?`` (zero-or-more path segments — so
      ``src/**/*.md`` matches both ``src/intro.md`` and
      ``src/sub/intro.md``).
    - ``**`` not followed by ``/`` → ``.*`` (any chars including ``/``).
    - ``*`` → ``[^/]*`` (single segment).
    - ``?`` → ``[^/]``.
    - Other characters are regex-escaped.

    ``Path.match`` doesn't handle ``**/`` as zero-or-more, and
    ``fnmatch`` lacks path-segment awareness — hence this hand-rolled
    translation.
    """
    parts: list[str] = []
    i = 0
    while i < len(glob):
        c = glob[i]
        if c == "*":
            if i + 1 < len(glob) and glob[i + 1] == "*":
                if i + 2 < len(glob) and glob[i + 2] == "/":
                    parts.append("(?:.*/)?")  # **/  → zero-or-more dirs
                    i += 3
                    continue
                parts.append(".*")  # **
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
    return bool(re.fullmatch("".join(parts), path_str))


# ── GithubZipAdapter ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class GithubZipConfig:
    """Validated config for :class:`GithubZipAdapter`.

    - ``repo`` — ``owner/name`` (e.g. ``"rust-lang/book"``)
    - ``ref``  — branch / tag / commit (default ``"main"``)
    - ``include_globs`` — fnmatch patterns relative to the archive root
      (after stripping the GitHub-zip top-level directory). At least
      one must be provided.
    - ``exclude_globs`` — patterns to drop after include filtering
      (e.g. drop ``"**/translations/**"``).
    - ``strip_prefix`` — relative prefix to drop from each kept file's
      output path. Useful for trimming GitHub-repo nesting like
      ``"src/"`` so the archive ``content/`` tree is shallow.
    """

    repo: str
    ref: str = "main"
    include_globs: tuple[str, ...] = ()
    exclude_globs: tuple[str, ...] = ()
    strip_prefix: str = ""

    @classmethod
    def from_build(cls, build: BuildSpec) -> "GithubZipConfig":
        cfg = build.config
        repo = str(cfg.get("repo", "")).strip()
        # Defence-in-depth first — typo-squatted forks may include `..`
        # or whitespace as part of a slash-count smuggle.
        if any(c in repo for c in ("..", " ", ":", "?")):
            raise PackError(f"github_zip config: repo {repo!r} has unsafe characters")
        if "/" not in repo or repo.count("/") != 1:
            raise PackError(
                f"github_zip config: repo must be 'owner/name', got {repo!r}",
            )
        includes = tuple(str(g) for g in cfg.get("include_globs", ()))
        if not includes:
            raise PackError(
                "github_zip config: include_globs must be non-empty",
            )
        return cls(
            repo=repo,
            ref=str(cfg.get("ref", "main")),
            include_globs=includes,
            exclude_globs=tuple(str(g) for g in cfg.get("exclude_globs", ())),
            strip_prefix=str(cfg.get("strip_prefix", "")).lstrip("/"),
        )

    @property
    def archive_url(self) -> str:
        return f"https://github.com/{self.repo}/archive/{self.ref}.zip"


class GithubZipAdapter(PackSourceAdapter):
    """Fetch a GitHub repo as a zip archive, filter by globs, write to staging.

    Materialises 5 of the 14 catalog packs: ``mdn-web-docs``, ``rust-book``,
    ``typescript-handbook``, ``18f-methods-guides``, ``chaoss-metrics``.
    Each is a sub-100 MB markdown corpus living in a single GitHub
    repository; the codeload zip endpoint avoids cloning history.

    The adapter is dependency-injectable: the constructor accepts a
    ``downloader`` so unit tests can drive it without httpx (and so a
    curator can swap in a cached fetcher). The default downloader uses
    httpx.
    """

    name: ClassVar[str] = "github_zip"

    def __init__(
        self,
        *,
        downloader: Callable[[str, int], bytes] | None = None,
        max_archive_bytes: int = 500 * 1024 * 1024,
    ) -> None:
        self._downloader = downloader or _httpx_zip_downloader
        self._max_archive_bytes = max_archive_bytes

    def fetch(self, manifest: PackManifest, *, staging_root: Path) -> FetchResult:
        if manifest.build is None:
            raise PackError(f"Pack {manifest.id!r} has no build spec")
        config = GithubZipConfig.from_build(manifest.build)
        logger.info(
            "github_zip: fetching %s @ %s for pack %s",
            config.repo, config.ref, manifest.id,
        )
        archive_bytes = self._downloader(config.archive_url, self._max_archive_bytes)

        content_root = staging_root / "content"
        content_root.mkdir(parents=True, exist_ok=True)
        kept: list[Path] = []
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as zf:
            # GitHub's codeload archives nest everything under a single
            # top-level directory like ``rust-lang-book-{sha}/``. We strip
            # that universally — anything else would leak the SHA into
            # archive paths, breaking determinism across runs.
            top_dir = _resolve_zip_top_dir(zf)
            for member in zf.infolist():
                if member.is_dir():
                    continue
                rel = _relative_to_top(member.filename, top_dir)
                if rel is None:
                    continue
                # Match include/exclude against the *original* archive
                # path (post-top-dir strip), so a curator can write
                # globs like ``src/**/*.md`` and still use
                # ``strip_prefix: "src/"`` to flatten the output tree.
                rel_path = Path(rel)
                if not _path_matches_any(rel_path, config.include_globs):
                    continue
                if config.exclude_globs and _path_matches_any(
                    rel_path, config.exclude_globs,
                ):
                    continue
                # Strip prefix only for the on-disk output path.
                out_rel = rel
                if config.strip_prefix and out_rel.startswith(config.strip_prefix):
                    out_rel = out_rel[len(config.strip_prefix):].lstrip("/")
                out_rel_path = Path(out_rel)
                # Defence-in-depth path-traversal guard.
                target = (content_root / out_rel_path).resolve()
                try:
                    target.relative_to(content_root.resolve())
                except ValueError as exc:
                    raise PackError(
                        f"github_zip {manifest.id}: archive member {rel!r} "
                        f"resolves outside content_root",
                    ) from exc
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member.filename))
                kept.append(out_rel_path)
        if not kept:
            raise PackError(
                f"github_zip {manifest.id}: include_globs matched zero files. "
                f"Globs: {list(config.include_globs)}; first 10 archive members: "
                f"{[m.filename for m in zf.infolist()[:10]]}",
            )
        kept.sort()
        logger.info(
            "github_zip: %s — kept %d files under content/",
            manifest.id, len(kept),
        )
        return FetchResult(content_root=content_root, files=tuple(kept))


# ── default httpx downloader ────────────────────────────────────────────────

def _httpx_zip_downloader(url: str, max_bytes: int) -> bytes:
    """Synchronous zip download with size cap.

    The zip is fully buffered in memory because zipfile needs random
    access — streaming would require a temp file anyway. ``max_bytes``
    is enforced before parsing to refuse a hostile redirect to a
    multi-GB blob.
    """
    import httpx

    # follow_redirects True because GitHub codeload redirects to the
    # actual zipball URL; same-host (codeload.github.com) so the
    # cross-host redirect risk is bounded.
    with httpx.Client(timeout=httpx.Timeout(120.0, connect=15.0), follow_redirects=True) as client:
        with client.stream("GET", url) as resp:
            resp.raise_for_status()
            buf = io.BytesIO()
            total = 0
            for chunk in resp.iter_bytes(chunk_size=1 << 20):
                total += len(chunk)
                if total > max_bytes:
                    raise PackError(
                        f"github_zip download exceeded max_bytes={max_bytes} "
                        f"({total} bytes downloaded so far)",
                    )
                buf.write(chunk)
            return buf.getvalue()


# ── zip-traversal helpers ───────────────────────────────────────────────

def _resolve_zip_top_dir(zf: zipfile.ZipFile) -> str:
    """Return the single top-level directory inside a GitHub codeload zip.

    Refuses zips with more than one top-level entry (defence against
    a fork that bundles multiple roots — extremely rare, but a
    typosquat could try to slip extra content this way).
    """
    tops: set[str] = set()
    for name in zf.namelist():
        head = name.split("/", 1)[0]
        if head:
            tops.add(head)
    if len(tops) != 1:
        raise PackError(
            f"github_zip: archive must have exactly one top-level directory; "
            f"got {sorted(tops)!r}",
        )
    return next(iter(tops)) + "/"


def _relative_to_top(name: str, top_dir: str) -> str | None:
    """Strip ``top_dir`` prefix; return None if name isn't under it."""
    if not name.startswith(top_dir):
        return None
    return name[len(top_dir):]


# Bootstrap the adapter registry. Future adapters (hf_dataset, etc.)
# register themselves the same way.
register_adapter(GithubZipAdapter())


def list_registered_adapters() -> list[str]:
    """Return registered adapter names (introspection / CLI ``--help``)."""
    # Import sibling adapter modules so they self-register at first call.
    # Lazy here (rather than at module top) so a cyclic import between
    # adapters.py and a sibling adapter module is impossible.
    import core.knowledge.adapter_gutenberg  # noqa: F401
    import core.knowledge.adapter_hf  # noqa: F401  (side-effect: register)
    import core.knowledge.adapter_html_scrape  # noqa: F401
    import core.knowledge.adapter_mediawiki  # noqa: F401
    import core.knowledge.adapter_python_docs  # noqa: F401
    import core.knowledge.adapter_wiki_dump  # noqa: F401

    return sorted(_REGISTRY.keys())


def fetch_for_manifest(manifest: PackManifest, *, staging_root: Path) -> FetchResult:
    """Look up the adapter for a manifest and run it.

    Convenience entry point used by ``scripts/build_catalog`` so the
    CLI doesn't need to know about the registry directly. Triggers
    sibling-adapter discovery via :func:`list_registered_adapters` so
    callers don't have to import every adapter module by hand.
    """
    if manifest.build is None:
        raise PackError(
            f"Pack {manifest.id!r} has no build spec — cannot materialise",
        )
    list_registered_adapters()  # triggers sibling-module registration
    adapter = get_adapter(manifest.build.adapter)
    return adapter.fetch(manifest, staging_root=staging_root)


__all__ = [
    "FetchResult",
    "GithubZipAdapter",
    "GithubZipConfig",
    "PackSourceAdapter",
    "fetch_for_manifest",
    "get_adapter",
    "list_registered_adapters",
    "register_adapter",
]
