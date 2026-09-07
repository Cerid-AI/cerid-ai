# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Environment profiles — a named bundle of existing knobs, chosen by hardware.

``CERID_ENVIRONMENT_PROFILE`` is one of ``cloud-first``, ``hybrid``,
``local-only``, or empty (no preset). A profile invents nothing: it is a
documented set of DEFAULTS for knobs that already exist, applied at settings
load only where the operator has not pinned a value. See
``tasks/2026-09-07-hardware-limited-client-configs.md`` §4b for the table and
the measurements behind it.

Two hardware classes drive the numbers:

* **A — CPU-bound local.** CPU-only hosts, and the ``amd-mac`` profile whose
  Metal path is slower than its CPU path. A 7B model runs at 5-10 tok/s here,
  so every background call costs 20-60 s.
* **B — GPU-accelerated local.** ``metal`` or ``nvidia`` with enough RAM to
  hold a 7-8B model (12 GB). 25-60 tok/s, so local is the fast path.

A third *posture* — the spec's "class C" — is not a hardware class: it is what
any host falls back to when there is no cloud key or Private Mode blocks
egress. :func:`resolve_profile` produces it by degrading a cloud profile to
``local-only``.

A profile routes a stage by setting its PROVIDER; it pins the cheap model id
only for stages the cheap tier can actually do (hardness TRIVIAL / SIMPLE /
MODERATE, and never an eval stage). A HARD or FRONTIER stage keeps the tier the
registry gives it under every profile — sending a frontier answer or a Cypher
generation to a gpt-4o-mini-class model is not a hardware adaptation, it is a
quality cut nobody asked for, and re-pointing the RAGAS judges would silently
change what every eval score means.

**Enumeration limit.** The table is built from named stages, so a runtime
``mcp_*`` stage that is not in ``STAGE_PROFILES`` gets no per-stage cloud route
from any profile — it follows the global provider. It is still treated as
interactive by the pacing gate, which classifies by prefix rather than by
enumeration.

The interactive/background split is mechanical — the two sets in
``config.stage_profiles`` are complements — which is deliberately not quite the
spec's prose list. ``memory_consolidation`` is INTERACTIVE (it is in the pacing
set: a chat turn blocks on it), and ``brief`` is BACKGROUND (nobody waits on a
digest). ``topic_extraction`` is background but has no hardness tier, so it is
listed by name there.

This module is pure policy: it reads no environment and holds no state, so
``config/settings.py`` (import time, boot Private-Mode posture) and
``app/routers/setup.py`` (request time, live Private-Mode level) can both ask
it the same question and get answers that differ only by their inputs.
"""

from __future__ import annotations

import logging
from typing import Any

from config.stage_profiles import (
    BACKGROUND_STAGES,
    INTERACTIVE_STAGES,
    MCP_STAGES,
    Hardness,
    hardness_for,
    normalize_stage,
)

logger = logging.getLogger("ai-companion.environment_profiles")

CLOUD_FIRST = "cloud-first"
HYBRID = "hybrid"
LOCAL_ONLY = "local-only"
VALID_PROFILES = frozenset({CLOUD_FIRST, HYBRID, LOCAL_ONLY})

# Profiles that route work off the box, and therefore need both a key and an
# egress-permitting Private Mode level to mean anything.
_CLOUD_PROFILES = frozenset({CLOUD_FIRST, HYBRID})

# Profiles that keep a local backend under everything they do not explicitly
# send to the cloud, and so must default INTERNAL_LLM_PROVIDER away from the
# shipped ``openrouter``.
_LOCAL_BACKED_PROFILES = frozenset({HYBRID, LOCAL_ONLY})

# Stages a profile can route to the cloud. The pacing set plus the MCP tool
# stages, which are user-blocking by the same argument.
_ROUTABLE_INTERACTIVE_STAGES = INTERACTIVE_STAGES | MCP_STAGES

_CLOUD_PROVIDER = "openrouter"

# Stages whose model choice IS the measurement. A profile may move them
# on-box or off-box, but must never change which model scores them: a judge
# swapped underneath a metric makes every number before and after the switch
# incomparable, and nothing in the run would say so.
_EVAL_STAGES = frozenset({
    "faithfulness/decompose",
    "faithfulness/score",
    "context_precision",
    "context_recall",
    "answer_relevancy",
})

# Hardness the cheap tier can actually carry. HARD and FRONTIER keep the tier
# the registry assigns them.
_CHEAP_TIER_HARDNESS = frozenset({
    Hardness.TRIVIAL,
    Hardness.SIMPLE,
    Hardness.MODERATE,
})

# Local backends cerid can drive. ``scripts/detect-gpu.sh`` recommends one per
# host and start-cerid.sh propagates it as HOST_RECOMMENDED_LOCAL_BACKEND; its
# third value, "cloud", is not a local backend, so local-only falls back to
# stock Ollama, which runs everywhere.
_LOCAL_PROVIDERS = frozenset({"ollama", "quenchforge"})
_DEFAULT_LOCAL_PROVIDER = "ollama"

# RAM below which a "fast" GPU still cannot hold a 7-8B model, so the host is
# back on the class-A latency curve regardless of what the GPU could do.
_CLASS_B_MIN_RAM_GB = 12
_CLASS_B_GPU_TYPES = frozenset({"metal", "nvidia"})


def classify_hardware(
    host: Any = None, gpu_type: str = "", ram_gb: int = 0,
) -> str:
    """Return ``"A"`` or ``"B"`` for a :class:`utils.host_info.HostHardware`.

    Pass the host snapshot, or ``gpu_type``/``ram_gb`` directly. Anything that
    is not a GPU type known to be fast, with enough RAM to hold the model, is
    class A — an unknown or undetected host gets the honest (slow) expectations
    rather than the flattering ones.
    """
    if host is not None:
        gpu_type = getattr(host, "gpu_type", "") or gpu_type
        ram_gb = getattr(host, "ram_gb", 0) or ram_gb
    if gpu_type.lower() in _CLASS_B_GPU_TYPES and ram_gb >= _CLASS_B_MIN_RAM_GB:
        return "B"
    return "A"


def suggest_profile(
    hardware_class: str, has_cloud_key: bool, private_mode_level: int,
) -> str:
    """The profile this host should run, absent an operator choice.

    Class A with cloud egress available is the only case where sending
    interactive stages off-box buys anything: it turns 20-60 s waits into
    1-3 s. Class B already has a fast local path, and a host without egress
    has no other option.
    """
    if private_mode_level >= 1 or not has_cloud_key:
        return LOCAL_ONLY
    return LOCAL_ONLY if hardware_class == "B" else HYBRID


def resolve_profile(
    requested: str,
    hardware_class: str,
    has_cloud_key: bool,
    private_mode_level: int,
) -> tuple[str, str]:
    """Resolve *requested* against what this host can actually do.

    Returns ``(effective_profile, reason)``. ``reason`` is empty when the
    request was honoured, and otherwise names why it was not — it is the text
    the caller puts in its one WARNING.
    """
    requested = (requested or "").strip()
    if not requested:
        return "", ""
    if requested not in VALID_PROFILES:
        return "", (
            f"unrecognized CERID_ENVIRONMENT_PROFILE {requested!r} "
            f"(expected one of {', '.join(sorted(VALID_PROFILES))}); no preset applied"
        )
    if requested not in _CLOUD_PROFILES:
        return requested, ""

    causes: list[str] = []
    if not has_cloud_key:
        causes.append("no OPENROUTER_API_KEY is configured")
    if private_mode_level >= 1:
        causes.append(f"Private Mode L{private_mode_level} blocks cloud egress")
    if not causes:
        return requested, ""
    return LOCAL_ONLY, " and ".join(causes)


def profile_defaults(
    effective_profile: str,
    hardware_class: str,
    recommended_local_backend: str | None = None,
) -> dict[str, str]:
    """The spec §4b knob table for an already-resolved profile.

    Keys are environment-variable names, values their string form, so the
    caller applies them with ``os.environ.setdefault`` and an operator pin
    wins by construction.

    ``local-only`` and ``hybrid`` both default ``INTERNAL_LLM_PROVIDER`` to
    *recommended_local_backend* — the host's detected backend, passed in by the
    caller rather than read here, so this stays a pure function. ``hybrid``
    needs it for the same reason ``local-only`` does: the shipped default is
    ``openrouter``, so without it the background tail hybrid promises to keep
    local would fall to the cloud global and ``INTERNAL_LLM_MODEL_BACKGROUND``
    would never be consulted. Everything hybrid sends off-box it names per
    stage, so nothing is lost by moving the global underneath it.
    ``cloud-first`` leaves the provider alone: it routes every named stage to
    the cloud already.
    """
    if effective_profile not in VALID_PROFILES:
        return {}

    defaults: dict[str, str] = {}
    if effective_profile in _CLOUD_PROFILES:
        defaults.update(_cloud_stage_defaults(_ROUTABLE_INTERACTIVE_STAGES))
    if effective_profile == CLOUD_FIRST:
        defaults.update(_cloud_stage_defaults(BACKGROUND_STAGES))

    if effective_profile in _LOCAL_BACKED_PROFILES:
        defaults["INTERNAL_LLM_PROVIDER"] = _local_provider(recommended_local_backend)

    if effective_profile == LOCAL_ONLY:
        # One permit on class A: a second concurrent call on a 5-10 tok/s slot
        # buys nothing and pushes both past their budgets.
        defaults["INTERNAL_LLM_MAX_CONCURRENCY"] = "1" if hardware_class == "A" else "2"
        defaults["VERIFY_CLAIM_MAX_CONCURRENT"] = "2"
        defaults["WIKI_REFRESH_LIVE_MAX_PER_HOUR"] = "4"
        defaults["COMMUNITY_SUMMARY_WALL_CLOCK_S"] = "240"
    else:
        defaults["INTERNAL_LLM_MAX_CONCURRENCY"] = "2"
        defaults["VERIFY_CLAIM_MAX_CONCURRENT"] = "3"
        defaults["WIKI_REFRESH_LIVE_MAX_PER_HOUR"] = "12"
        defaults["COMMUNITY_SUMMARY_WALL_CLOCK_S"] = "480"
    return defaults


def apply_environment_profile(
    profile: str,
    hardware_class: str,
    has_cloud_key: bool,
    private_mode_level: int,
    recommended_local_backend: str | None = None,
) -> dict[str, str]:
    """Resolve *profile* and return the defaults it contributes.

    The single place a degraded profile is announced — one WARNING naming the
    request, what it degraded to, and why.
    """
    effective, reason = resolve_profile(
        profile, hardware_class, has_cloud_key, private_mode_level,
    )
    if reason:
        logger.warning(
            "environment profile %r degraded to %r: %s",
            profile, effective or "none", reason,
        )
    return profile_defaults(effective, hardware_class, recommended_local_backend)


def _local_provider(recommended_local_backend: str | None) -> str:
    """The local backend to default ``INTERNAL_LLM_PROVIDER`` to.

    ``scripts/detect-gpu.sh`` also emits "cloud" for hosts with no usable
    acceleration — a recommendation ``local-only`` cannot take — so anything
    that is not a known local backend falls back to stock Ollama.
    """
    candidate = (recommended_local_backend or "").strip().lower()
    return candidate if candidate in _LOCAL_PROVIDERS else _DEFAULT_LOCAL_PROVIDER


def _cloud_stage_defaults(stages: frozenset[str] | set[str]) -> dict[str, str]:
    """Route *stages* to the cloud, pinning the cheap tier where it fits.

    Every stage gets the provider. Only a stage the cheap tier can carry also
    gets the model pin; the rest reach ``_resolve_stage_model``'s tier lookup
    unpinned and keep the model the registry assigns them. The model id is read
    from ``utils.model_registry`` rather than written here: a profile is a
    routing policy, and every model id in this codebase has exactly one home.
    """
    from utils.model_registry import get_model

    model = get_model("tiers", "cheap")
    defaults: dict[str, str] = {}
    for stage in sorted(stages):
        normalized = normalize_stage(stage)
        defaults[f"PROVIDER_STAGE_{normalized}"] = _CLOUD_PROVIDER
        if _accepts_cheap_tier(stage):
            defaults[f"PROVIDER_STAGE_{normalized}_MODEL"] = model
    return defaults


def _accepts_cheap_tier(stage: str) -> bool:
    """True when pinning *stage* to the cheap tier is a hardware adaptation
    rather than a quality cut. An unclassified stage has no tier to protect, so
    it qualifies; an eval stage never does, whatever its hardness."""
    if stage in _EVAL_STAGES:
        return False
    hardness = hardness_for(stage)
    return hardness is None or hardness in _CHEAP_TIER_HARDNESS
