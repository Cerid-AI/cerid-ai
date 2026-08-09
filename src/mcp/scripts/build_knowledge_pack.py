#!/usr/bin/env python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Build knowledge-pack archives from on-disk source content.

Produces ``<pack_id>-<version>.tar.gz`` archives + a sidecar
``registry.json`` listing them with ``file://`` URLs, so operators can
install starter packs end-to-end without first publishing them
anywhere. Reuses :mod:`core.knowledge.packs` helpers (PackManifest,
sha256_of_file, serialise_registry) — no duplicated logic.

Usage::

    # Build all five starter packs from the in-tree eval corpus.
    python -m scripts.build_knowledge_pack starter

    # Build a single pack from an arbitrary directory.
    python -m scripts.build_knowledge_pack custom \\
        --pack-id my-pack --version 1.0.0 --domain general \\
        --description "..." --source-dir /path/to/content/

After build, install via the harness::

    CERID_KNOWLEDGE_PACKS_REGISTRY=data/knowledge-packs/v1/registry.json \\
        python -m scripts.install_knowledge_pack list

The shipped repo registry stays empty — the builder writes its
side-car to a gitignored ``data/`` location so machine-specific
absolute paths never leak into git history.
"""
from __future__ import annotations

import argparse
import io
import json
import logging
import sys
import tarfile
from pathlib import Path

from core.knowledge.packs import (
    PackManifest,
    serialise_registry,
    sha256_of_file,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("knowledge-pack-builder")

# Default starter-pack catalogue — five single-domain packs sourced
# from the in-tree eval corpus. The eval-corpus is already public-
# domain (see data/eval-corpus/v1/MANIFEST.md) and organised by
# exactly the domains the harness expects.
STARTER_VERSION = "1.0.0"
STARTER_PACKS: list[dict] = [
    {
        "domain": "coding",
        "name": "Cerid Starter — Coding Reference",
        "description": (
            "Public-domain starter notes on Python typing, Docker "
            "networking, Postgres indexing, and Git workflow. Curated "
            "from the Cerid AI evaluation corpus."
        ),
        "tags": ["starter", "reference", "coding"],
    },
    {
        "domain": "finance",
        "name": "Cerid Starter — Personal Finance Basics",
        "description": (
            "Public-domain primer on index-fund investing, household "
            "budgeting, IRA comparison, and compound interest."
        ),
        "tags": ["starter", "reference", "finance"],
    },
    {
        "domain": "projects",
        "name": "Cerid Starter — Project Management Reference",
        "description": (
            "Public-domain notes on agile vs waterfall, risk registers, "
            "stakeholder communication, and project estimation."
        ),
        "tags": ["starter", "reference", "projects"],
    },
    {
        "domain": "personal",
        "name": "Cerid Starter — Personal Productivity",
        "description": (
            "Public-domain notes on time blocking, reading habits, "
            "sleep hygiene, and exercise routines."
        ),
        "tags": ["starter", "reference", "personal"],
    },
    {
        "domain": "general",
        "name": "Cerid Starter — General Knowledge Skills",
        "description": (
            "Public-domain notes on effective writing, learning "
            "techniques, critical thinking, and communication skills."
        ),
        "tags": ["starter", "reference", "general"],
    },
]
STARTER_LICENSE = "CC0-1.0"
STARTER_PROVENANCE = {
    "source": "data/eval-corpus/v1 (Workstream E Phase 1.2 corpus)",
    "curator": "Cerid AI",
}


def _repo_root() -> Path:
    """Locate the repo root by walking up from this file.

    ``scripts/`` lives at ``src/mcp/scripts/`` so the repo root is
    three parents up. We avoid the ``git rev-parse`` shellout because
    builds may run inside a Docker container that doesn't have ``git``.
    """
    return Path(__file__).resolve().parents[3]


def _eval_corpus_root() -> Path:
    return _repo_root() / "data" / "eval-corpus" / "v1"


def _default_build_dir() -> Path:
    return _repo_root() / "data" / "knowledge-packs" / "v1"


def _collect_files(source_dir: Path) -> list[Path]:
    """Return all regular files under ``source_dir`` (excluding MANIFEST)."""
    files: list[Path] = []
    for path in sorted(source_dir.rglob("*")):
        if not path.is_file():
            continue
        if path.name in ("MANIFEST.md", "MANIFEST.txt"):
            continue
        files.append(path)
    return files


def _build_pack(
    *,
    pack_id: str,
    version: str,
    name: str,
    description: str,
    domain: str,
    sub_category: str,
    tags: list[str],
    license_id: str,
    provenance: dict[str, str],
    source_dir: Path,
    build_dir: Path,
) -> PackManifest:
    """Build a single tar.gz pack from ``source_dir`` into ``build_dir``.

    Archive layout matches what :func:`app.services.knowledge_packs._extract_pack`
    expects: a top-level ``pack.json`` plus all source files under
    ``content/`` (preserving relative subdirectory structure).
    """
    if not source_dir.is_dir():
        raise FileNotFoundError(f"Pack source not found: {source_dir}")
    files = _collect_files(source_dir)
    if not files:
        raise ValueError(f"No source files under {source_dir}")
    build_dir.mkdir(parents=True, exist_ok=True)
    archive_path = build_dir / f"{pack_id}-{version}.tar.gz"

    embedded_manifest = {
        "id": pack_id,
        "name": name,
        "version": version,
        "description": description,
        "domain": domain,
        "sub_category": sub_category,
        "tags": tags,
        "license": license_id,
        "provenance": provenance,
    }

    with tarfile.open(archive_path, "w:gz") as tf:
        # pack.json at archive root.
        pack_blob = json.dumps(embedded_manifest, indent=2).encode("utf-8")
        info = tarfile.TarInfo(name="pack.json")
        info.size = len(pack_blob)
        info.mode = 0o644
        tf.addfile(info, io.BytesIO(pack_blob))
        # Source files under content/, preserving subdir structure.
        for src in files:
            rel = src.relative_to(source_dir).as_posix()
            arcname = f"content/{rel}"
            payload = src.read_bytes()
            info = tarfile.TarInfo(name=arcname)
            info.size = len(payload)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(payload))

    sha = sha256_of_file(archive_path)
    size_bytes = archive_path.stat().st_size
    manifest = PackManifest.from_dict({
        **embedded_manifest,
        "size_bytes": size_bytes,
        "artifact_count": len(files),
        # file:// URLs use absolute paths — the side-car registry is
        # gitignored, so machine-specific paths never reach git.
        "download_url": archive_path.resolve().as_uri(),
        "sha256": sha,
    })
    logger.info(
        "Built %s (%d files, %.1f KB) → %s",
        pack_id, len(files), size_bytes / 1024, archive_path,
    )
    return manifest


def _cmd_starter(args: argparse.Namespace) -> int:
    """Build the five starter packs from the in-tree eval corpus."""
    build_dir = Path(args.build_dir).expanduser().resolve()
    eval_root = _eval_corpus_root()
    if not eval_root.is_dir():
        logger.error(
            "Eval corpus not found at %s — cannot build starter packs",
            eval_root,
        )
        return 2
    manifests: list[PackManifest] = []
    for entry in STARTER_PACKS:
        domain = entry["domain"]
        source_dir = eval_root / domain
        manifest = _build_pack(
            pack_id=f"cerid-starter-{domain}",
            version=STARTER_VERSION,
            name=entry["name"],
            description=entry["description"],
            domain=domain,
            sub_category="reference",
            tags=list(entry["tags"]),
            license_id=STARTER_LICENSE,
            provenance=STARTER_PROVENANCE,
            source_dir=source_dir,
            build_dir=build_dir,
        )
        manifests.append(manifest)

    registry_path = build_dir / "registry.json"
    registry_path.write_text(serialise_registry(manifests), encoding="utf-8")
    logger.info("Wrote side-car registry → %s", registry_path)
    print(
        f"\nBuilt {len(manifests)} packs in {build_dir}\n"
        f"To use them: set CERID_KNOWLEDGE_PACKS_REGISTRY={registry_path} "
        f"in the mcp container env, then install via the Library UI or:\n"
        f"  docker exec ai-companion-mcp env "
        f"CERID_KNOWLEDGE_PACKS_REGISTRY={registry_path} "
        f"python -m scripts.install_knowledge_pack list",
    )
    return 0


def _cmd_custom(args: argparse.Namespace) -> int:
    """Build one pack from an operator-supplied directory."""
    source_dir = Path(args.source_dir).expanduser().resolve()
    build_dir = Path(args.build_dir).expanduser().resolve()
    manifest = _build_pack(
        pack_id=args.pack_id,
        version=args.version,
        name=args.name or args.pack_id,
        description=args.description,
        domain=args.domain,
        sub_category=args.sub_category,
        tags=[t.strip() for t in (args.tags or "").split(",") if t.strip()],
        license_id=args.license,
        provenance={"source": str(source_dir), "curator": args.curator},
        source_dir=source_dir,
        build_dir=build_dir,
    )
    print(json.dumps(manifest.to_dict(), indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Build knowledge-pack archives")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub_starter = sub.add_parser(
        "starter",
        help="Build the five starter packs from data/eval-corpus/v1/",
    )
    sub_starter.add_argument(
        "--build-dir",
        default=str(_default_build_dir()),
        help="Output directory for tarballs + sidecar registry "
             "(default: data/knowledge-packs/v1/)",
    )
    sub_starter.set_defaults(func=_cmd_starter)

    sub_custom = sub.add_parser(
        "custom",
        help="Build one pack from an operator-supplied directory",
    )
    sub_custom.add_argument("--pack-id", required=True)
    sub_custom.add_argument("--version", default="1.0.0")
    sub_custom.add_argument("--name", default=None)
    sub_custom.add_argument("--description", required=True)
    sub_custom.add_argument("--domain", required=True)
    sub_custom.add_argument("--sub-category", default="general")
    sub_custom.add_argument("--tags", default="")
    sub_custom.add_argument("--license", default="")
    sub_custom.add_argument("--curator", default="")
    sub_custom.add_argument("--source-dir", required=True)
    sub_custom.add_argument(
        "--build-dir", default=str(_default_build_dir()),
    )
    sub_custom.set_defaults(func=_cmd_custom)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
