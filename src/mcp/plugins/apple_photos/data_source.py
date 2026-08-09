# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BUSL-1.1
"""Apple Photos DataSource — Phase G.4.

Metadata-only: dates, location, dimensions, favorite/hidden flags,
media subtypes. Never reads pixel data. Wraps the `ceridphotos` Swift
helper.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
from typing import Any

from app.data_sources.base import DataSource, DataSourceResult
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.data_sources.apple_photos")

DEFAULT_HELPER_NAME = "ceridphotos"


def _resolve_helper_path() -> str | None:
    override = os.getenv("CERID_HELPER_CERIDPHOTOS")
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


class ApplePhotosDataSource(DataSource):
    name = "apple_photos"
    description = "Apple Photos metadata via Swift PhotoKit helper"
    requires_api_key = False

    def __init__(self, helper_path: str | None = None) -> None:
        self._helper_path = helper_path or _resolve_helper_path()

    def is_configured(self) -> bool:
        return platform.system() == "Darwin" and bool(self._helper_path)

    async def _invoke_helper(self, args: list[str]) -> Any:
        if not self._helper_path:
            return None
        try:
            proc = await asyncio.create_subprocess_exec(
                self._helper_path, *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=60.0)
            if proc.returncode != 0:
                stderr_text = stderr.decode("utf-8", errors="replace").strip()
                if proc.returncode == 3:
                    logger.info("ceridphotos TCC denied: %s", stderr_text)
                else:
                    logger.warning("ceridphotos exited %d: %s", proc.returncode, stderr_text)
                return None
            return json.loads(stdout.decode("utf-8"))
        except (TimeoutError, OSError, ValueError) as exc:
            log_swallowed_error("apple_photos._invoke_helper", exc)
            return None

    async def list_assets(
        self,
        *,
        limit: int = 1000,
        since_iso: str | None = None,
    ) -> list[dict[str, Any]]:
        args = ["list", "--limit", str(limit)]
        if since_iso:
            args.extend(["--since", since_iso])
        raw = await self._invoke_helper(args)
        if not isinstance(raw, list):
            return []
        return [r for r in raw if isinstance(r, dict)]

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        # Photos query is intentionally minimal — the user query goes through
        # the standard retrieval ranker on the ingested metadata records.
        # This entry point returns recent assets for ad-hoc "what photos
        # did I take this week" questions.
        assets = await self.list_assets(limit=int(kwargs.get("max_results", 50)))
        out: list[DataSourceResult] = []
        for a in assets:
            mt = a.get("media_type", "asset")
            subtypes = a.get("media_subtypes", [])
            created = a.get("creation_date") or "unknown date"
            location = "no location"
            if a.get("location_lat") is not None and a.get("location_lon") is not None:
                location = f"{a['location_lat']:.4f},{a['location_lon']:.4f}"
            title = f"{mt.capitalize()} {created}"
            body = (
                f"Type: {mt}\n"
                f"Created: {created}\n"
                f"Dimensions: {a.get('pixel_width', 0)}x{a.get('pixel_height', 0)}\n"
                f"Location: {location}\n"
                f"Favorite: {a.get('is_favorite', False)}\n"
                f"Subtypes: {', '.join(subtypes) if subtypes else 'none'}"
            )
            out.append(
                DataSourceResult(
                    title=title,
                    content=body,
                    source_url=f"photos://{a.get('id', '')}",
                    source_name="Apple Photos",
                    confidence=0.55,
                ),
            )
        return out
