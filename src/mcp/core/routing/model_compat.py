# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Hardware-aware model compatibility guard + config audit.

Pure ``core`` module (no ``app`` imports). It keeps Cerid on the most capable
model that is *actually compatible* with the operator's hardware, and keeps that
choice current as models move — without ever auto-adopting a model that can't
run on the platform.

It encodes:

* a per-hardware-profile **incompatibility denylist** — models known (from
  reproduced failures, not speculation) NOT to run correctly on a profile, e.g.
  ``llama3.2-3b`` crashes the Vega II (``amd-mac``) Metal stack;
* a curated **known-good local set** per profile — quenchforge/Ollama have no
  versioned catalog, so local currency is a maintained map + on-device check;
* **candidate local upgrades** the doctor surfaces but never auto-applies —
  GPU/Metal compatibility can only be proven by loading on the device;
* :func:`audit_model_config`, which turns the live config into findings.

**Broad multi-hardware flexibility is intentional.** Everything is keyed by
``CERID_HARDWARE_PROFILE`` (``nvidia | amd | amd-mac | metal | cpu``) with a safe
baseline for unspecified profiles, so the same machinery serves every backend
without hard-coding one platform's assumptions. The denylist is deliberately
*narrow* (only documented crashes) and *fail-open* on unknown profiles — we
never block a model we can't prove is incompatible.
"""
from __future__ import annotations

# --- hardware incompatibility denylist --------------------------------------
# Substrings matched against the normalized bare model id. Add ONLY models with
# a documented, reproduced failure on that profile.
_INCOMPATIBLE_BY_PROFILE: dict[str, tuple[str, ...]] = {
    # Vega II (Intel Mac + discrete AMD) on the patched llama.cpp Metal stack:
    # llama3.2-3b crashes with GGML_ASSERT(buf_dst) (CLAUDE.md gotcha #4).
    "amd-mac": ("llama3.2-3b", "llama-3.2-3b"),
}

_INCOMPAT_REASON: dict[str, str] = {
    "amd-mac": (
        "crashes the Vega II Metal stack (GGML_ASSERT(buf_dst)); "
        "use a known-good model such as llama3.1-8b"
    ),
}

# --- known-good local models per profile ------------------------------------
# Proven-stable defaults. Upgrades are surfaced as candidates, not baked in.
_SAFE_BASELINE_LOCAL: dict[str, str] = {
    "chat": "llama3.1-8b",
    "embed": "nomic-embed-text-v1.5",
    "code_embed": "jina-embeddings-v2-base-code",
    "rerank": "bge-reranker-v2-m3",
}
_KNOWN_GOOD_LOCAL: dict[str, dict[str, str]] = {
    "amd-mac": _SAFE_BASELINE_LOCAL,
    "metal": _SAFE_BASELINE_LOCAL,
    "nvidia": _SAFE_BASELINE_LOCAL,
    "amd": _SAFE_BASELINE_LOCAL,
    "cpu": {
        "chat": "llama3.1-8b",
        "embed": "nomic-embed-text-v1.5",
        "rerank": "bge-reranker-v2-m3",
    },
}

# --- candidate local upgrades (doctor-surfaced; validate-on-device) ---------
# Each carries a `validate` note — Metal/GPU compat is proven by loading, never
# by benchmark rank, so these are recommendations the operator confirms.
_CANDIDATE_LOCAL_UPGRADES: dict[str, list[dict[str, str]]] = {
    "chat": [
        {
            "model": "qwen3-8b",
            "why": "stronger sub-14B instruct (MMLU-Redux / IFEval / Arena-Hard) than llama3.1-8b",
            "validate": "pull the GGUF + load on quenchforge; watch for GGML_ASSERT on the Metal/AMD path before adopting",
        },
    ],
    "embed": [
        {
            "model": "qwen3-embedding-0.6b",
            "why": "higher MTEB retrieval than nomic-embed-text-v1.5",
            "validate": "confirm the GGUF embeds and the dimension matches the corpus before re-indexing",
        },
    ],
    "rerank": [
        {
            "model": "qwen3-reranker-0.6b",
            "why": "SOTA open reranker on MTEB-R",
            "validate": "llama.cpp GGUF rerank conversion is fragile — verify rank-pooling scores look sane before adopting",
        },
    ],
}


def _normalize(model_id: str) -> str:
    """Lowercase the trailing model name and unify ``:`` / ``_`` separators.

    ``meta-llama/llama-3.2-3b-instruct`` → ``llama-3.2-3b-instruct``;
    ``llama3.2:3b`` → ``llama3.2-3b``.
    """
    tail = model_id.lower().split("/")[-1]
    return tail.replace(":", "-").replace("_", "-")


def is_incompatible(model_id: str, hardware_profile: str) -> bool:
    """True iff ``model_id`` is on the denylist for ``hardware_profile``.

    Fail-open: an empty/unknown profile, or a profile with no denylist, never
    blocks (we can't prove incompatibility, so we don't get in the way).
    """
    deny = _INCOMPATIBLE_BY_PROFILE.get(hardware_profile or "")
    if not deny:
        return False
    norm = _normalize(model_id)
    return any(sig in norm for sig in deny)


def incompatible_reason(model_id: str, hardware_profile: str) -> str | None:
    """Human-readable reason a model is incompatible, or None if it's fine."""
    if not is_incompatible(model_id, hardware_profile):
        return None
    return _INCOMPAT_REASON.get(hardware_profile, "known-incompatible on this hardware")


def compatible_catalog_ids(catalog_ids: list[str], hardware_profile: str) -> list[str]:
    """Drop catalog ids incompatible with ``hardware_profile``.

    Used to filter the OpenRouter catalog before the auto-update resolver runs,
    so a newly-listed-but-incompatible model is never adopted. No-op when the
    profile has no denylist.
    """
    if not _INCOMPATIBLE_BY_PROFILE.get(hardware_profile or ""):
        return list(catalog_ids)
    return [cid for cid in catalog_ids if not is_incompatible(cid, hardware_profile)]


def known_good_local(hardware_profile: str) -> dict[str, str]:
    """The curated known-good local model set for a profile (baseline fallback)."""
    return dict(_KNOWN_GOOD_LOCAL.get(hardware_profile or "", _SAFE_BASELINE_LOCAL))


def candidate_local_upgrades(role: str) -> list[dict[str, str]]:
    """Doctor-surfaced upgrade candidates for a local role (validate-on-device)."""
    return [dict(c) for c in _CANDIDATE_LOCAL_UPGRADES.get(role, [])]


def _looks_like_remote_id(model_id: str) -> bool:
    """A ``provider/model`` id (OpenRouter/Bifrost), not a local Ollama tag."""
    return "/" in model_id


def audit_model_config(
    *,
    configured: dict[str, str],
    hardware_profile: str,
    catalog_ids: list[str],
    local_roles: dict[str, str] | None = None,
) -> list[dict[str, str]]:
    """Audit the live model config into actionable findings.

    Args:
        configured: ``{config_role: model_id}`` currently in effect.
        hardware_profile: active ``CERID_HARDWARE_PROFILE``.
        catalog_ids: live OpenRouter catalog ids (``[]`` when offline → dead-pin
            checks are skipped, never false-positived).
        local_roles: optional ``{config_role: canonical_local_role}`` map (e.g.
            ``{"INTERNAL_LLM_MODEL": "chat"}``) so local pins can be checked
            against the known-good set.

    Returns findings: ``{kind, severity, role, model, detail}`` where kind is
        ``incompatible`` (error) · ``dead_pin`` (warn) · ``local_currency`` (info).
    """
    local_roles = local_roles or {}
    findings: list[dict[str, str]] = []
    catalog_norm = {_strip_router_prefix(c) for c in catalog_ids}

    for role, model_id in configured.items():
        if not model_id:
            continue
        # 1. Hardware incompatibility (hard error).
        if is_incompatible(model_id, hardware_profile):
            findings.append({
                "kind": "incompatible",
                "severity": "error",
                "role": role,
                "model": model_id,
                "detail": incompatible_reason(model_id, hardware_profile) or "incompatible",
            })
            continue
        # 2. Dead remote pin (warn) — a provider/model id absent from the live catalog.
        if catalog_ids and _looks_like_remote_id(model_id):
            if _strip_router_prefix(model_id) not in catalog_norm:
                findings.append({
                    "kind": "dead_pin",
                    "severity": "warn",
                    "role": role,
                    "model": model_id,
                    "detail": "not present in the live OpenRouter catalog — pin is dead; repoint or let the auto-update resolve a successor",
                })
                continue
        # 3. Local-model currency (info) — pinned local model isn't the known-good.
        canonical = local_roles.get(role)
        if canonical:
            kg = known_good_local(hardware_profile).get(canonical)
            if kg and _normalize(model_id) != _normalize(kg):
                cands = candidate_local_upgrades(canonical)
                detail = f"not the known-good local {canonical} model ({kg}) for {hardware_profile or 'this hardware'}"
                if cands:
                    detail += f"; validated-upgrade candidates exist ({', '.join(c['model'] for c in cands)})"
                findings.append({
                    "kind": "local_currency",
                    "severity": "info",
                    "role": role,
                    "model": model_id,
                    "detail": detail,
                })

    return findings


def _strip_router_prefix(model_id: str) -> str:
    return model_id[len("openrouter/"):] if model_id.startswith("openrouter/") else model_id


def build_compat_report(
    *,
    configured: dict[str, str],
    hardware_profile: str,
    catalog_ids: list[str],
    local_roles: dict[str, str] | None = None,
) -> dict[str, object]:
    """Audit the config and package it for the doctor endpoint / setup wizard /
    Settings UX: findings + an ``ok`` flag + the known-good local set + the
    validate-on-device upgrade candidates. Pure — the app layer supplies the
    live settings + catalog."""
    findings = audit_model_config(
        configured=configured,
        hardware_profile=hardware_profile,
        catalog_ids=catalog_ids,
        local_roles=local_roles,
    )
    return {
        "hardware_profile": hardware_profile or "unknown",
        "ok": not any(f["severity"] == "error" for f in findings),
        "findings": findings,
        "known_good_local": known_good_local(hardware_profile),
        "candidate_upgrades": {
            role: candidate_local_upgrades(role) for role in ("chat", "embed", "rerank")
        },
        "catalog_size": len(catalog_ids),
    }
