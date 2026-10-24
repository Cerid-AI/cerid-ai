# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Auto-discovery of the latest in-family model from the OpenRouter catalog.

Pure resolver + a resilient catalog fetch. Lives in ``core`` (no ``app``
imports). The *apply* side — persisting assignments + regenerating the Bifrost
config — stays in ``app/routers/models.py`` so the core↛app boundary holds.

"Latest within model family" is resolved conservatively: an id is upgradeable
when it carries a version token — dotted (``claude-sonnet-4.6``) or a bare
integer standing as its own segment (``gpt-6-luna``, ``o3-mini``) — and only to
another id that is identical except for the version in that position.
Everything else about the id (provider, size tokens like ``70b``, variant
suffixes like ``-fast`` / ``-pro`` / ``:free``) is held fixed, so a
``grok-4.1-fast`` only ever resolves to ``grok-4.2-fast``. Among the family the
newest *release* wins, by the catalog's ``created`` date: version numbers are
not ordered by time (``grok-4.20`` shipped months before ``grok-4.7``). A line a
vendor renamed (``gpt-4o-mini`` became the GPT-6 Luna line) moves through
``SUCCESSORS``, then resolves within its new family.

OpenRouter's ``:online`` web-search overlay is not a catalog id; it is set
aside while resolving and put back.

``current_model_id`` applies the same resolution at dispatch, against a catalog
snapshot the weekly job and the boot validation write, so every call — code
defaults, env settings, saved choices — goes to the newest model of its family.
"""
from __future__ import annotations

import json
import logging
import os
import re
from collections.abc import Mapping

import httpx

logger = logging.getLogger("ai-companion.model_catalog")

_OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"
_ROUTING_PREFIX = "openrouter/"

# The version token: dotted ("4.6", "3.3", "4.20"), or a bare integer that is
# its own segment ("6" in "gpt-6-luna", "3" in "o3-mini"). An integer glued to
# a letter ("4" in "gpt-4o-mini", "70" in "70b") is not a version.
_VERSION_RE = re.compile(r"\d+(?:\.\d+)+|\d+(?=-|$)")

# Lines a vendor renamed, so no version bump reaches them. The value is the
# first id of the new line; ``resolve_latest`` then follows that line.
SUCCESSORS: dict[str, str] = {
    "openai/gpt-4o-mini": "openai/gpt-6-luna",
    "openai/gpt-4o": "openai/gpt-6.1-sol",
    "openai/o3-mini": "openai/gpt-6-luna-pro",
    "openai/o4-mini": "openai/gpt-6-luna-pro",
    "meta-llama/llama-3.3-70b-instruct": "meta-llama/llama-4-maverick",
}

_ONLINE = ":online"


def _strip_routing_prefix(model_id: str) -> str:
    """Drop the leading ``openrouter/`` router prefix for family comparison.

    Frontend ids carry it (``openrouter/anthropic/claude-sonnet-4.6``); the
    OpenRouter catalog and backend assignments use the bare ``provider/model``
    form. Compare on the bare form, re-apply the prefix on the way out.
    """
    return model_id[len(_ROUTING_PREFIX):] if model_id.startswith(_ROUTING_PREFIX) else model_id


def _version_tuple(raw: str) -> tuple[int, ...]:
    return tuple(int(part) for part in raw.split("."))


def _family_latest(bare: str, catalog_ids: list[str], created: Mapping[str, float] | None) -> str:
    m = _VERSION_RE.search(bare)
    if m is None:
        return bare  # no resolvable version → never auto-upgrade
    prefix, suffix = re.escape(bare[: m.start()]), re.escape(bare[m.end():])
    family_re = re.compile(rf"^{prefix}(\d+(?:\.\d+)*){suffix}$")

    def rank(cid: str, ver: tuple[int, ...]) -> tuple[float, tuple[int, ...]]:
        return ((created or {}).get(cid, 0.0), ver)

    best, best_rank = bare, rank(bare, _version_tuple(m.group()))
    for cid in catalog_ids:
        cand = _strip_routing_prefix(cid)
        cm = family_re.match(cand)
        if cm is None:
            continue
        r = rank(cand, _version_tuple(cm.group(1)))
        if r > best_rank:
            best, best_rank = cand, r
    return best


def resolve_latest(
    current_id: str, catalog_ids: list[str], created: Mapping[str, float] | None = None
) -> str:
    """Return the newest same-family id in ``catalog_ids``, else ``current_id``.

    Same-family = identical id except for the version token in the position of
    ``current_id``'s first version token. With ``created`` (catalog id → release
    timestamp) the newest release wins; without it, the highest version.
    """
    had_prefix = current_id.startswith(_ROUTING_PREFIX)
    bare = _strip_routing_prefix(current_id)
    online = bare.endswith(_ONLINE)
    if online:
        bare = bare[: -len(_ONLINE)]

    present = {_strip_routing_prefix(c) for c in catalog_ids}
    start = SUCCESSORS.get(bare, bare)
    if start not in present:
        start = bare
    best = _family_latest(start, catalog_ids, created)
    if best == bare:
        return current_id
    out = best + (_ONLINE if online else "")
    return _ROUTING_PREFIX + out if had_prefix else out


def resolve_assignments(
    current: dict[str, str],
    catalog_ids: list[str],
    hardware_profile: str = "",
    created: Mapping[str, float] | None = None,
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
    return {role: resolve_latest(model_id, safe_ids, created) for role, model_id in current.items()}


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


def catalog_created(catalog: list[dict]) -> dict[str, float]:
    """Map each catalog id to its release timestamp (``created``, epoch seconds)."""
    out: dict[str, float] = {}
    for m in catalog:
        try:
            out[m["id"]] = float(m.get("created") or 0)
        except (KeyError, TypeError, ValueError):
            continue
    return out


_EFFORT_ORDER = ("none", "minimal", "low", "medium", "high", "xhigh", "max")


def catalog_reasoning_floor(catalog: list[dict]) -> dict[str, str]:
    """Map each model that reasons unless told otherwise to its lowest effort.

    A model that reasons by default spends ``max_tokens`` on reasoning first; at
    the small budgets internal calls use, that leaves no answer (gpt-5-nano
    returned 0 characters that way). Models that reason only on request are
    absent: sending them an effort would turn reasoning on.
    """
    out: dict[str, str] = {}
    for m in catalog:
        meta = m.get("reasoning") if isinstance(m, dict) else None
        if not isinstance(meta, dict) or not (meta.get("mandatory") or meta.get("default_enabled")):
            continue
        efforts = [e for e in _EFFORT_ORDER if e in (meta.get("supported_efforts") or [])]
        if efforts and m.get("id"):
            out[m["id"]] = efforts[0]
    return out


# ── Dispatch-time resolution ─────────────────────────────────────────────────

# Longest catalog id is well under 100 characters; anything longer is not one.
_MAX_MODEL_ID_LEN = 200
_MAX_RESOLVED = 1024

_snapshot: dict[str, float] = {}
_floors: dict[str, str] = {}
_snapshot_mtime: float = -1.0
_resolved: dict[str, str] = {}


def _snapshot_path() -> str:
    import config

    return str(getattr(config, "MODEL_CATALOG_SNAPSHOT_PATH", "") or "")


def save_catalog_snapshot(catalog: list[dict]) -> None:
    """Persist each id's release date and reasoning floor for dispatch. An empty
    catalog (offline) writes nothing, so the last good snapshot stays in force."""
    path = _snapshot_path()
    created = catalog_created(catalog)
    if not path or not created:
        return
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = f"{path}.{os.getpid()}.tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"created": created, "reasoning_floor": catalog_reasoning_floor(catalog)}, fh, sort_keys=True)
        os.replace(tmp, path)
    except OSError as exc:
        from core.utils.swallowed import log_swallowed_error

        log_swallowed_error("model_catalog.save_snapshot", exc)


def _load_snapshot() -> dict[str, float]:
    global _snapshot, _floors, _snapshot_mtime, _resolved
    path = _snapshot_path()
    try:
        mtime = os.path.getmtime(path) if path else -1.0
    except OSError:
        mtime = -1.0
    if mtime != _snapshot_mtime:
        try:
            with open(path, encoding="utf-8") as fh:
                raw = json.load(fh)
            _snapshot = {str(k): float(v) for k, v in raw["created"].items()}
            _floors = {str(k): str(v) for k, v in raw.get("reasoning_floor", {}).items()}
        except (OSError, ValueError, TypeError, KeyError, AttributeError):
            _snapshot, _floors = {}, {}
        _snapshot_mtime, _resolved = mtime, {}
    return _snapshot


def reasoning_floor(model_id: str) -> str | None:
    """The lowest reasoning effort ``model_id`` accepts, when it reasons by
    default; None when it does not, or when no snapshot is known."""
    _load_snapshot()
    bare = _strip_routing_prefix(model_id)
    return _floors.get(bare.removesuffix(_ONLINE))


def current_model_id(model_id: str) -> str:
    """The newest model of ``model_id``'s family, for an OpenRouter call.

    Identity when the auto-update is off (``MODEL_AUTO_UPDATE_ENABLED``), when no
    catalog snapshot has been written yet, or when the family has nothing newer.
    """
    import config

    if not model_id or not getattr(config, "MODEL_AUTO_UPDATE_ENABLED", False):
        return model_id
    created = _load_snapshot()
    if not created or len(model_id) > _MAX_MODEL_ID_LEN:
        return model_id
    hit = _resolved.get(model_id)
    if hit is None:
        # Chat passes the client's model string through here: bound the memo so
        # a stream of made-up ids cannot grow it without limit.
        if len(_resolved) >= _MAX_RESOLVED:
            _resolved.clear()
        hit = _resolved[model_id] = resolve_latest(model_id, list(created), created)
    return hit

