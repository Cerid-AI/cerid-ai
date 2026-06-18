# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Auto-discovery of the latest in-family model from the OpenRouter catalog.

Pure resolver + a resilient catalog fetch. Lives in ``core`` (no ``app``
imports). The *apply* side — persisting assignments + regenerating the Bifrost
config — stays in ``app/routers/models.py`` so the core↛app boundary holds.

"Latest within model family" is resolved conservatively: only models whose id
carries a dotted version token (e.g. ``claude-sonnet-4.6``) are upgradeable,
and only to another id that is identical except for a strictly-higher version
in that same position. Everything else about the id (provider, size tokens like
``70b``, variant suffixes like ``-fast`` / ``:free``) is held fixed, so a
``grok-4.1-fast`` only ever resolves to ``grok-4.2-fast`` — never to a bare
``grok-4.2`` or a different family. Ids without a dotted version (e.g.
``gpt-4o-mini``) are left pinned rather than risk a false upgrade.
"""
from __future__ import annotations

import logging
import re

import httpx

logger = logging.getLogger("ai-companion.model_catalog")

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_ROUTING_PREFIX = "openrouter/"

# First dotted-version token in an id ("4.6", "3.3", "4.20"). A bare integer
# (the "4" in "gpt-4o-mini") deliberately does NOT match — those ids have no
# clean in-family successor and stay pinned.
_VERSION_RE = re.compile(r"\d+\.\d+")


def _strip_routing_prefix(model_id: str) -> str:
    """Drop the leading ``openrouter/`` router prefix for family comparison.

    Frontend ids carry it (``openrouter/anthropic/claude-sonnet-4.6``); the
    OpenRouter catalog and backend assignments use the bare ``provider/model``
    form. Compare on the bare form, re-apply the prefix on the way out.
    """
    return model_id[len(_ROUTING_PREFIX):] if model_id.startswith(_ROUTING_PREFIX) else model_id


def _version_tuple(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.split("."))


def resolve_latest(current_id: str, catalog_ids: list[str]) -> str:
    """Return the newest same-family id in ``catalog_ids``, else ``current_id``.

    Same-family = identical id except for a strictly-higher dotted version in
    the position of ``current_id``'s first dotted-version token.
    """
    had_prefix = current_id.startswith(_ROUTING_PREFIX)
    bare = _strip_routing_prefix(current_id)

    m = _VERSION_RE.search(bare)
    if m is None:
        return current_id  # no resolvable version → never auto-upgrade

    prefix, suffix = re.escape(bare[: m.start()]), re.escape(bare[m.end():])
    family_re = re.compile(rf"^{prefix}(\d+(?:\.\d+)+){suffix}$")

    best_bare, best_ver = bare, _version_tuple(m.group())
    for cid in catalog_ids:
        cm = family_re.match(_strip_routing_prefix(cid))
        if cm is None:
            continue
        ver = _version_tuple(cm.group(1))
        if ver > best_ver:
            best_bare, best_ver = _strip_routing_prefix(cid), ver

    if best_bare == bare:
        return current_id
    return _ROUTING_PREFIX + best_bare if had_prefix else best_bare


def resolve_assignments(
    current: dict[str, str], catalog_ids: list[str], hardware_profile: str = ""
) -> dict[str, str]:
    """Resolve every role's model to the latest *compatible* in its family.

    When ``hardware_profile`` is set, the catalog is filtered through the
    hardware-compatibility guard first, so the auto-update never adopts a newer
    model that is known not to run on the platform (e.g. a Metal-crash model on
    ``amd-mac``). Already-pinned incompatible models are not silenced here — the
    config doctor (``model_compat.audit_model_config``) surfaces those.
    """
    safe_ids = catalog_ids
    if hardware_profile:
        from core.routing.model_compat import compatible_catalog_ids

        safe_ids = compatible_catalog_ids(catalog_ids, hardware_profile)
    return {role: resolve_latest(model_id, safe_ids) for role, model_id in current.items()}


def diff_assignments(
    current: dict[str, str], resolved: dict[str, str]
) -> list[dict[str, str]]:
    """List the roles whose model changed, as ``{role, from, to}`` rows."""
    return [
        {"role": role, "from": current[role], "to": resolved[role]}
        for role in current
        if current.get(role) != resolved.get(role)
    ]


async def fetch_openrouter_catalog(timeout: float = 10.0) -> list[dict]:
    """Fetch the OpenRouter model catalog. Returns ``[]`` on any failure.

    The ``/models`` endpoint is public (no API key). Failing soft keeps the
    privacy-first / offline-capable posture: no catalog → no auto-update, never
    an exception bubbling into a scheduler tick or request path.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(_OPENROUTER_MODELS_URL)
            resp.raise_for_status()
            data = resp.json().get("data", [])
        return [m for m in data if isinstance(m, dict) and m.get("id")]
    except Exception as exc:  # noqa: BLE001 — resilient fetch, see docstring
        from core.utils.swallowed import log_swallowed_error

        log_swallowed_error("model_catalog.fetch", exc)
        return []


def catalog_ids(catalog: list[dict]) -> list[str]:
    """Extract the id list from a fetched catalog payload."""
    return [m["id"] for m in catalog if m.get("id")]
