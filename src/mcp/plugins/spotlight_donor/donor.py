# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""CoreSpotlight donor — Phase G.4.

Sends Cerid KB artifacts to macOS Spotlight via the `ceridspotlight`
Swift helper so they're discoverable from Cmd-Space.

This is NOT a DataSource (Spotlight is write-side, not read-side). It's
invoked from background jobs (artifact ingestion completion + nightly
re-donate) and exposes a small API for the rest of the backend.

Activation: clicking a Cerid result in Spotlight fires the cerid://kb/<id>
URL scheme registered in the Electron app's Info.plist
(packages/desktop/package.json:42-48). The Electron main process routes
the `open-url` event to the renderer to focus the right Subjects entry.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
from dataclasses import asdict, dataclass
from typing import Any

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.plugins.spotlight_donor")

DEFAULT_HELPER_NAME = "ceridspotlight"


def _resolve_helper_path() -> str | None:
    override = os.getenv("CERID_HELPER_CERIDSPOTLIGHT")
    if override and os.path.exists(override):
        return override
    on_path = shutil.which(DEFAULT_HELPER_NAME)
    if on_path:
        return on_path
    repo_root = os.path.abspath(
        os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."),
    )
    dev_path = os.path.join(
        repo_root, "packages", "desktop", "swift", "build", DEFAULT_HELPER_NAME,
    )
    if os.path.exists(dev_path):
        return dev_path
    return None


@dataclass
class SpotlightItem:
    """One donatable item. Maps 1:1 to a CSSearchableItem in the helper."""

    id: str
    title: str
    domain: str = "ai.cerid.kb.default"
    content_description: str | None = None
    keywords: list[str] | None = None
    content_url: str | None = None
    expiration_days: int | None = None


async def donate(items: list[SpotlightItem]) -> dict[str, Any]:
    """Donate a batch of items to Spotlight. Returns {"donated": N}."""
    helper = _resolve_helper_path()
    if not helper or platform.system() != "Darwin":
        return {"donated": 0, "skipped": True}
    if not items:
        return {"donated": 0}

    payload = "\n".join(
        json.dumps({k: v for k, v in asdict(item).items() if v is not None})
        for item in items
    ).encode("utf-8")

    try:
        proc = await asyncio.create_subprocess_exec(
            helper, "donate",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        # Donations >5k items can take 10-15s; give generous headroom.
        stdout, _ = await asyncio.wait_for(
            proc.communicate(input=payload),
            timeout=120.0,
        )
        if proc.returncode != 0:
            logger.warning("ceridspotlight exited %d", proc.returncode)
            return {"donated": 0, "error": f"exit_{proc.returncode}"}
        try:
            return json.loads(stdout.decode("utf-8").strip().splitlines()[-1])
        except (ValueError, IndexError):
            return {"donated": len(items)}  # best-effort fallback
    except (TimeoutError, OSError) as exc:
        log_swallowed_error("spotlight_donor.donate", exc)
        return {"donated": 0, "error": str(exc)}


async def purge(domain: str) -> dict[str, Any]:
    """Remove all donated items in `domain` (e.g. for clean re-index)."""
    helper = _resolve_helper_path()
    if not helper or platform.system() != "Darwin":
        return {"purged": "", "skipped": True}
    try:
        proc = await asyncio.create_subprocess_exec(
            helper, "purge", domain,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30.0)
        if proc.returncode != 0:
            return {"purged": "", "error": f"exit_{proc.returncode}"}
        try:
            return json.loads(stdout.decode("utf-8").strip())
        except ValueError:
            return {"purged": domain}
    except (TimeoutError, OSError) as exc:
        log_swallowed_error("spotlight_donor.purge", exc)
        return {"purged": "", "error": str(exc)}
