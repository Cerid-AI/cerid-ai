#!/usr/bin/env python
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Knowledge-pack CLI — list / install / installed / uninstall.

Runs *inside* the mcp container so it can use the live ingestion
service, e.g.::

    docker exec ai-companion-mcp python -m scripts.install_knowledge_pack list
    docker exec ai-companion-mcp python -m scripts.install_knowledge_pack \\
        install cerid-starter-general
    docker exec ai-companion-mcp python -m scripts.install_knowledge_pack installed
    docker exec ai-companion-mcp python -m scripts.install_knowledge_pack \\
        uninstall cerid-starter-general

The registry is read from ``${CERID_KNOWLEDGE_PACKS_REGISTRY}`` if set,
otherwise from ``config/knowledge_packs.json`` shipped with the repo.
The slim shipped registry is the *default* — operators can point at a
larger curated registry (e.g. a community-maintained file) by setting
the env var.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from collections import defaultdict

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
logger = logging.getLogger("knowledge-pack-cli")


def _cmd_list(args: argparse.Namespace) -> int:
    from app.services.knowledge_packs import default_registry_path
    from core.knowledge.packs import load_registry

    registry = load_registry(default_registry_path())
    if not registry:
        print(
            "No knowledge packs in registry "
            f"({default_registry_path()}). Set CERID_KNOWLEDGE_PACKS_REGISTRY "
            "to point at a curated registry, or commit packs to the repo registry."
        )
        return 0
    by_domain: dict[str, list] = defaultdict(list)
    for pack in registry.values():
        by_domain[pack.domain].append(pack)
    for domain in sorted(by_domain):
        print(f"\n[{domain}]")
        for pack in sorted(by_domain[domain], key=lambda p: p.id):
            size_mb = pack.size_bytes / (1024 * 1024) if pack.size_bytes else 0
            print(
                f"  {pack.id:35s} v{pack.version:8s} "
                f"{pack.artifact_count or '?':>4} artifacts  {size_mb:6.1f} MB"
            )
            if args.verbose:
                if pack.description:
                    print(f"    {pack.description}")
                if pack.license:
                    print(f"    license: {pack.license}")
                if pack.download_url:
                    print(f"    url: {pack.download_url}")
    return 0


def _cmd_installed(args: argparse.Namespace) -> int:
    from app.services.knowledge_packs import default_state_path
    from core.knowledge.packs import load_install_state

    state = load_install_state(default_state_path())
    if not state:
        print(f"No knowledge packs installed (state: {default_state_path()}).")
        return 0
    print(f"Installed knowledge packs ({len(state)}):\n")
    for rec in state:
        print(
            f"  {rec.pack_id:35s} v{rec.version:8s} "
            f"{len(rec.artifact_ids):>4} artifacts  domain={rec.domain}"
        )
        if args.verbose:
            print(f"    installed_at: {rec.installed_at}")
            if rec.sha256:
                print(f"    sha256: {rec.sha256}")
    return 0


async def _cmd_install_async(args: argparse.Namespace) -> int:
    from app.services.knowledge_packs import (
        default_registry_path,
        install_pack_default,
    )
    from core.knowledge.packs import load_registry

    registry = load_registry(default_registry_path())
    pack = registry.get(args.pack_id)
    if pack is None:
        logger.error(
            "Pack %s not found in registry %s. "
            "Available: %s",
            args.pack_id, default_registry_path(), ", ".join(sorted(registry)) or "(empty)",
        )
        return 2
    record = await install_pack_default(pack, keep_staging=args.keep_staging)
    print(
        f"Installed {record.pack_id}@{record.version}: "
        f"{len(record.artifact_ids)} artifacts → domain {record.domain}"
    )
    return 0


def _cmd_install(args: argparse.Namespace) -> int:
    return asyncio.run(_cmd_install_async(args))


async def _cmd_uninstall_async(args: argparse.Namespace) -> int:
    from app.services.knowledge_packs import uninstall_pack_default

    summary = await uninstall_pack_default(args.pack_id)
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary.get("status") == "uninstalled" else 1


def _cmd_uninstall(args: argparse.Namespace) -> int:
    return asyncio.run(_cmd_uninstall_async(args))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Cerid knowledge-pack CLI: list / install / installed / uninstall",
    )
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub_list = sub.add_parser("list", help="List packs available in the registry")
    sub_list.set_defaults(func=_cmd_list)

    sub_inst = sub.add_parser("installed", help="List packs already installed")
    sub_inst.set_defaults(func=_cmd_installed)

    sub_ins = sub.add_parser("install", help="Install a pack by id")
    sub_ins.add_argument("pack_id")
    sub_ins.add_argument(
        "--keep-staging", action="store_true",
        help="Preserve the staging directory (debugging / inspection)",
    )
    sub_ins.set_defaults(func=_cmd_install)

    sub_un = sub.add_parser("uninstall", help="Uninstall a previously-installed pack")
    sub_un.add_argument("pack_id")
    sub_un.set_defaults(func=_cmd_uninstall)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
