#!/usr/bin/env python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Build catalog packs from upstream sources via registered source adapters.

Phase 7 orchestrator. Walks the shipped registry
(``config/knowledge_packs.json``), and for each entry with a ``build``
spec, runs the adapter to fetch upstream content, seals it into a
tar.gz under ``data/knowledge-packs/v1/``, and emits a sidecar
``registry.json`` with concrete sha256 + size + file:// URL.

Usage::

    # Build every pack in the catalog whose `build.adapter` is registered.
    python -m scripts.build_catalog --all

    # Build a single pack by id.
    python -m scripts.build_catalog --pack-id rust-book

    # Dry-run: list what would be built, no fetches.
    python -m scripts.build_catalog --all --dry-run

The output mirrors the side-car shape from
:mod:`scripts.build_knowledge_pack` so an operator can point
``CERID_KNOWLEDGE_PACKS_REGISTRY`` at the result and install via the
existing harness without further plumbing.
"""
from __future__ import annotations

import argparse
import logging
import shutil
import sys
import tempfile
from pathlib import Path

from app.services.knowledge_packs import default_registry_path
from core.knowledge.adapters import (
    fetch_for_manifest,
    list_registered_adapters,
)
from core.knowledge.packs import (
    PackError,
    PackManifest,
    load_registry,
    serialise_registry,
)

# Reuse the existing tarball-sealing helper rather than re-implementing
# the pack.json + content/ packing layout. This keeps a single source
# of truth for archive shape between the user-authored builder and
# the catalog orchestrator.
from scripts.build_knowledge_pack import _build_pack

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("catalog-builder")


def _default_build_dir() -> Path:
    """Mirrors :func:`scripts.build_knowledge_pack._default_build_dir`."""
    return Path(__file__).resolve().parents[3] / "data" / "knowledge-packs" / "v1"


def _materialise_one(
    manifest: PackManifest,
    *,
    build_dir: Path,
    pii_scan: bool = False,
) -> PackManifest:
    """Run the manifest's adapter, seal a tarball, return an updated manifest.

    The returned manifest has ``download_url`` (file://), ``sha256``,
    ``size_bytes``, and ``artifact_count`` populated from the materialised
    archive. ``provenance.status`` is set to ``built``.

    When ``pii_scan`` is True, runs the Presidio PII gate over the
    fetched content before sealing the tarball. Findings raise
    :class:`PackError` with the redacted snippets so the curator can
    fix the source.
    """
    if manifest.build is None:
        raise PackError(
            f"Pack {manifest.id!r}: no build spec — cannot materialise",
        )
    with tempfile.TemporaryDirectory(prefix=f"cerid-catalog-{manifest.id}-") as tmp:
        staging_root = Path(tmp)
        result = fetch_for_manifest(manifest, staging_root=staging_root)
        if not result.files:
            raise PackError(
                f"Pack {manifest.id!r}: adapter produced zero files",
            )
        if pii_scan:
            from core.knowledge.pii_gate import scan_directory

            report = scan_directory(result.content_root)
            logger.info("%s — %s", manifest.id, report.summary_text())
            if not report.is_clean:
                snippets = "\n".join(
                    f"  {f.file_path}:{f.line_number} [{f.entity_type} "
                    f"score={f.score:.2f}]  {f.snippet}"
                    for f in report.findings[:25]
                )
                raise PackError(
                    f"Pack {manifest.id!r}: PII gate found "
                    f"{len(report.findings)} finding(s); refusing to seal "
                    f"the tarball.\nFirst findings:\n{snippets}",
                )
        # _build_pack reads from a single source directory and emits
        # tar.gz + matching PackManifest with sha/size/url populated.
        # We pass the adapter's content_root as source_dir so layout
        # mirrors the on-disk shape the adapter wrote.
        materialised = _build_pack(
            pack_id=manifest.id,
            version=manifest.version if manifest.version != "0.0.0" else "1.0.0",
            name=manifest.name,
            description=manifest.description,
            domain=manifest.domain,
            sub_category=manifest.sub_category,
            tags=list(manifest.tags),
            license_id=manifest.license,
            provenance={
                **manifest.provenance,
                "status": "built",
                "adapter": manifest.build.adapter,
            },
            source_dir=result.content_root,
            build_dir=build_dir,
        )
    # Carry through metadata that _build_pack doesn't preserve
    # (the BuildSpec, FileOverrides) so the output registry is a
    # complete superset of the input.
    return PackManifest.from_dict({
        **materialised.to_dict(),
        "files": [f.__dict__ for f in manifest.files],
        "build": manifest.build.to_dict(),
    })


def _cmd(args: argparse.Namespace) -> int:
    registry_path = Path(args.registry) if args.registry else default_registry_path()
    registry = load_registry(registry_path)
    if not registry:
        logger.error("Registry %s is empty — nothing to build", registry_path)
        return 1

    if args.pack_id:
        if args.pack_id not in registry:
            logger.error(
                "Pack %s not in registry. Available: %s",
                args.pack_id, ", ".join(sorted(registry)),
            )
            return 2
        targets = [registry[args.pack_id]]
    else:
        targets = [m for m in registry.values() if m.build is not None]
        if not targets:
            logger.error(
                "No catalog entries have a build spec yet. Add `build` to "
                "config/knowledge_packs.json. Registered adapters: %s",
                list_registered_adapters(),
            )
            return 1

    build_dir = Path(args.build_dir).expanduser().resolve()
    if args.dry_run:
        for pack in targets:
            adapter = pack.build.adapter if pack.build else "(none)"
            print(f"  {pack.id:35s} adapter={adapter:14s} → {build_dir}")
        return 0

    if args.clean and build_dir.exists():
        logger.info("Removing existing build dir %s", build_dir)
        shutil.rmtree(build_dir)
    build_dir.mkdir(parents=True, exist_ok=True)

    materialised: list[PackManifest] = []
    failures: list[tuple[str, str]] = []
    for pack in targets:
        try:
            updated = _materialise_one(
                pack, build_dir=build_dir, pii_scan=args.pii_scan,
            )
            materialised.append(updated)
            logger.info(
                "✓ %s@%s built (%d artifacts, %.1f KB, sha256=%s…)",
                updated.id, updated.version, updated.artifact_count,
                updated.size_bytes / 1024, updated.sha256[:12],
            )
        except Exception as exc:  # noqa: BLE001 — observability boundary
            from core.utils.swallowed import log_swallowed_error
            log_swallowed_error(
                "scripts.build_catalog.materialise_one", exc,
            )
            logger.error("✗ %s failed: %s", pack.id, exc)
            failures.append((pack.id, str(exc)))

    if materialised:
        out_path = build_dir / "registry.json"
        out_path.write_text(serialise_registry(materialised), encoding="utf-8")
        logger.info("Wrote %d-pack side-car registry → %s", len(materialised), out_path)

    if failures:
        logger.error("Build failed for %d pack(s):", len(failures))
        for pid, reason in failures:
            logger.error("  %s: %s", pid, reason)
        return 1

    print(
        f"\nBuilt {len(materialised)} pack(s) in {build_dir}\n"
        f"Install via:\n"
        f"  CERID_KNOWLEDGE_PACKS_REGISTRY={build_dir / 'registry.json'} \\\n"
        f"    docker exec ai-companion-mcp python -m scripts.install_knowledge_pack list",
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Materialise catalog packs from upstream sources",
    )
    parser.add_argument(
        "--registry", default=None,
        help="Override path to the input registry "
             "(default: config/knowledge_packs.json)",
    )
    parser.add_argument(
        "--pack-id", default=None,
        help="Build a single pack by id (default: build every pack with a build spec)",
    )
    parser.add_argument("--all", action="store_true", help="(default) Build all packs")
    parser.add_argument(
        "--build-dir", default=str(_default_build_dir()),
        help="Output directory for tarballs + sidecar registry "
             "(default: data/knowledge-packs/v1/)",
    )
    parser.add_argument(
        "--clean", action="store_true",
        help="Remove the build dir before starting (forces re-fetch of all packs)",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="List packs that would be built, then exit",
    )
    parser.add_argument(
        "--pii-scan", action="store_true",
        help="Run the Presidio PII gate over each pack's content before "
             "sealing the tarball (Phase 8b). Requires `presidio-analyzer` "
             "to be installed.",
    )
    args = parser.parse_args()
    return _cmd(args)


if __name__ == "__main__":
    sys.exit(main())
