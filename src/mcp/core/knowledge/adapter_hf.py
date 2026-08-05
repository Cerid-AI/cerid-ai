# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""HuggingFace ``datasets`` adapter for the knowledge-pack harness.

Materialises 2 of the 14 Phase-6 catalog packs:

- ``wikipedia-simple-en`` (config ``wikimedia/wikipedia`` ``20231101.simple``)
- ``cosmopedia-khanacademy`` (config ``HuggingFaceTB/cosmopedia`` ``khanacademy``)

The ``datasets`` library is a heavy optional dep (pyarrow,
huggingface_hub, fsspec). Importing it at module load would force every
mcp-server boot to pay that cost even when no HF pack ships. Instead
we lazy-import inside the adapter's ``fetch`` method and raise
:class:`PackError` with install guidance if it's absent.

The default loader uses streaming mode so a 280 MB Wikipedia parquet
isn't materialised whole into RAM. The DI-injectable loader makes the
adapter unit-testable with no network and no library install — tests
pass plain ``list[dict]`` rows through the same write path.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar

from core.knowledge.adapters import (
    FetchResult,
    PackSourceAdapter,
    register_adapter,
)
from core.knowledge.packs import BuildSpec, PackError, PackManifest

logger = logging.getLogger("ai-companion.knowledge_packs.adapters.hf")


# ── HfDatasetConfig ─────────────────────────────────────────────────────

@dataclass(frozen=True)
class HfDatasetConfig:
    """Validated config for :class:`HfDatasetAdapter`.

    - ``dataset_id``    HF dataset id, ``owner/name`` (e.g. ``"wikimedia/wikipedia"``).
    - ``config_name``   HF dataset config (default config when None).
    - ``split``         Dataset split (default ``"train"``).
    - ``text_field``    Row field containing the body text (required).
    - ``title_field``   Field used to derive the filename slug (optional;
                        falls back to row index).
    - ``id_field``      Field used as a unique suffix on the filename
                        when titles collide (optional; collision counter
                        used as fallback).
    - ``min_text_chars`` Drop rows whose text is shorter than this
                        threshold (default 100). Filters out near-empty
                        Wikipedia stubs.
    - ``max_rows``      Cap the number of rows read. Default ``None`` =
                        unlimited. Useful for sub-1MB smoke tests.
    - ``markdown_template`` Template applied to every row;
                        ``{title}`` and ``{text}`` substitutions
                        (default ``"# {title}\\n\\n{text}\\n"``).
    """

    dataset_id: str
    text_field: str
    config_name: str | None = None
    split: str = "train"
    title_field: str | None = None
    id_field: str | None = None
    filter_field: str | None = None
    filter_value: str | None = None
    min_text_chars: int = 100
    max_rows: int | None = None
    markdown_template: str = "# {title}\n\n{text}\n"

    @classmethod
    def from_build(cls, build: BuildSpec) -> "HfDatasetConfig":
        cfg = build.config
        dataset_id = str(cfg.get("dataset_id", "")).strip()
        # Unsafe-char check first — a smuggled `..` may also break the
        # slash-count gate, so checking unsafe-chars first surfaces the
        # security-relevant error rather than a generic shape error.
        if any(c in dataset_id for c in ("..", " ", ":", "?")):
            raise PackError(
                f"hf_dataset config: dataset_id {dataset_id!r} has unsafe characters",
            )
        if "/" not in dataset_id or dataset_id.count("/") != 1:
            raise PackError(
                f"hf_dataset config: dataset_id must be 'owner/name', got "
                f"{dataset_id!r}",
            )
        text_field = str(cfg.get("text_field", "")).strip()
        if not text_field:
            raise PackError("hf_dataset config: text_field is required")
        max_rows_raw = cfg.get("max_rows")
        max_rows = int(max_rows_raw) if max_rows_raw is not None else None
        if max_rows is not None and max_rows <= 0:
            raise PackError("hf_dataset config: max_rows must be > 0 if set")
        filter_field = str(cfg["filter_field"]) if cfg.get("filter_field") else None
        filter_value = (
            str(cfg["filter_value"]) if cfg.get("filter_value") is not None else None
        )
        if filter_field and filter_value is None:
            raise PackError(
                "hf_dataset config: filter_value is required when filter_field is set",
            )
        return cls(
            dataset_id=dataset_id,
            text_field=text_field,
            config_name=str(cfg["config_name"]) if cfg.get("config_name") else None,
            split=str(cfg.get("split", "train")),
            title_field=str(cfg["title_field"]) if cfg.get("title_field") else None,
            id_field=str(cfg["id_field"]) if cfg.get("id_field") else None,
            filter_field=filter_field,
            filter_value=filter_value,
            min_text_chars=int(cfg.get("min_text_chars", 100)),
            max_rows=max_rows,
            markdown_template=str(
                cfg.get("markdown_template", "# {title}\n\n{text}\n"),
            ),
        )


# ── Slug helpers ──────────────────────────────────────────────────────

_SLUG_KEEP = re.compile(r"[^a-z0-9]+")
_MAX_SLUG_LEN = 80


def _slugify(text: str) -> str:
    """Normalize an arbitrary string to a safe filename stem.

    Defence against malicious upstream titles: a row whose ``title``
    contains ``../`` or null bytes must not be able to escape
    ``content_root``. We keep only ``[a-z0-9-]``, collapse runs, and
    cap length.
    """
    lower = text.strip().lower()
    cleaned = _SLUG_KEEP.sub("-", lower).strip("-")
    if not cleaned:
        return "untitled"
    return cleaned[:_MAX_SLUG_LEN]


# ── HfDatasetAdapter ──────────────────────────────────────────────────

DatasetLoader = Callable[[HfDatasetConfig], Iterable[Mapping[str, Any]]]


class HfDatasetAdapter(PackSourceAdapter):
    """Stream rows from a HuggingFace dataset, write each as a markdown file.

    The DI-injectable ``loader`` makes the adapter testable with no
    network (tests pass a list of dicts). The default loader uses
    ``datasets.load_dataset(streaming=True)`` so multi-100 MB datasets
    aren't materialised in RAM.
    """

    name: ClassVar[str] = "hf_dataset"

    def __init__(self, *, loader: DatasetLoader | None = None) -> None:
        self._loader = loader or _default_dataset_loader

    def fetch(self, manifest: PackManifest, *, staging_root: Path) -> FetchResult:
        if manifest.build is None:
            raise PackError(f"Pack {manifest.id!r} has no build spec")
        config = HfDatasetConfig.from_build(manifest.build)
        logger.info(
            "hf_dataset: loading %s (%s, split=%s) for pack %s",
            config.dataset_id, config.config_name or "<default>",
            config.split, manifest.id,
        )

        content_root = staging_root / "content"
        content_root.mkdir(parents=True, exist_ok=True)
        seen_slugs: dict[str, int] = {}
        kept: list[Path] = []

        for index, row in enumerate(self._loader(config)):
            if config.max_rows is not None and len(kept) >= config.max_rows:
                break
            # Optional field filter (e.g. court == "U.S. Supreme Court"):
            # ship only matching rows so a court-scoped subset stays
            # complete-for-claim rather than an arbitrary head() sample.
            if config.filter_field is not None:
                fv = row.get(config.filter_field)
                if fv is None or str(fv) != config.filter_value:
                    continue
            text = row.get(config.text_field)
            if not isinstance(text, str) or len(text) < config.min_text_chars:
                continue
            title = ""
            if config.title_field:
                raw = row.get(config.title_field)
                if isinstance(raw, str):
                    title = raw
            if not title:
                title = f"row-{index}"
            slug = _slugify(title)

            row_id = ""
            if config.id_field:
                raw_id = row.get(config.id_field)
                if raw_id is not None:
                    row_id = _slugify(str(raw_id))
            if row_id:
                base = f"{slug}-{row_id}"
            else:
                # Counter-based de-collision when two rows share a title
                # and no id field is configured — keeps filenames stable
                # across re-runs as long as upstream ordering is stable.
                counter = seen_slugs.get(slug, 0)
                base = slug if counter == 0 else f"{slug}-{counter}"
                seen_slugs[slug] = counter + 1
            rel_path = Path(f"{base}.md")

            target = (content_root / rel_path).resolve()
            try:
                target.relative_to(content_root.resolve())
            except ValueError as exc:
                # Defence-in-depth: slugify already strips path separators,
                # but if a future change loosens that, this keeps the
                # invariant that no row writes outside content_root.
                raise PackError(
                    f"hf_dataset {manifest.id}: row {index} resolved to "
                    f"{target!r} outside content_root",
                ) from exc

            body = config.markdown_template.format(title=title, text=text)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body, encoding="utf-8")
            kept.append(rel_path)

        if not kept:
            raise PackError(
                f"hf_dataset {manifest.id}: zero rows survived filter "
                f"(min_text_chars={config.min_text_chars}, "
                f"text_field={config.text_field!r}). Check the recipe.",
            )
        kept.sort()
        logger.info(
            "hf_dataset: %s — wrote %d markdown files under content/",
            manifest.id, len(kept),
        )
        return FetchResult(content_root=content_root, files=tuple(kept))


def _default_dataset_loader(
    config: HfDatasetConfig,
) -> Iterable[Mapping[str, Any]]:
    """Stream rows from a HuggingFace dataset.

    Imports ``datasets`` lazily so the library only needs to be
    installed when an HF-sourced pack is actually built. Surfaces a
    clear install hint in the error message.
    """
    try:
        from datasets import load_dataset  # type: ignore[import-not-found]
    except ImportError as exc:
        raise PackError(
            "hf_dataset: the `datasets` package is not installed. "
            "Install with `pip install datasets` (heavy: pulls pyarrow + "
            "huggingface_hub) before running this build.",
        ) from exc

    kwargs: dict[str, Any] = {
        "path": config.dataset_id,
        "split": config.split,
        "streaming": True,
    }
    if config.config_name:
        kwargs["name"] = config.config_name
    return load_dataset(**kwargs)


# Bootstrap registry on module import. Importing this module (even if
# ``datasets`` isn't installed) is cheap because the heavy library
# import is deferred to ``fetch``.
register_adapter(HfDatasetAdapter())


__all__ = [
    "HfDatasetAdapter",
    "HfDatasetConfig",
]
