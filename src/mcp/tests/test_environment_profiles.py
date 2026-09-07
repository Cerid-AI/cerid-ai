# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Environment profiles — the §4b knob table, its degradation rules, and the
background-model slot.

Pins the contract in ``config/environment_profiles.py``:

- ``classify_hardware`` separates class A (CPU-bound: CPU-only or the
  Metal-hostile ``amd-mac``) from class B (``metal`` / ``nvidia`` with
  enough RAM to hold a 7-8B model).
- ``profile_defaults`` returns the spec §4b table verbatim, referencing the
  registry's ``cheap`` tier rather than a hard-coded model id.
- ``resolve_profile`` degrades a cloud profile to ``local-only`` when there
  is no cloud key or Private Mode blocks egress, naming why exactly once.
- Applying a profile at settings load never overwrites an operator pin.
- Background stages (the complement of the interactive set) prefer
  ``INTERNAL_LLM_MODEL_BACKGROUND`` when the gateway actually serves it.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from config.environment_profiles import (
    apply_environment_profile,
    classify_hardware,
    profile_defaults,
    resolve_profile,
    suggest_profile,
)
from config.stage_profiles import (
    BACKGROUND_STAGES,
    INTERACTIVE_STAGES,
    STAGE_PROFILES,
    Hardness,
    hardness_for,
    is_background_stage,
    normalize_stage,
)
from utils.model_registry import get_model

MCP_ROOT = Path(__file__).resolve().parents[1]


def _cheap() -> str:
    return get_model("tiers", "cheap")


# ---------------------------------------------------------------------------
# Hardware classifier
# ---------------------------------------------------------------------------


class _Hw:
    """HostHardware-like stand-in (the classifier only reads two fields)."""

    def __init__(self, gpu_type: str, ram_gb: int) -> None:
        self.gpu_type = gpu_type
        self.ram_gb = ram_gb


@pytest.mark.parametrize(
    ("gpu_type", "ram_gb", "expected"),
    [
        ("amd-mac", 160, "A"),   # the reference Mac Pro
        ("cpu", 64, "A"),
        ("", 32, "A"),           # undetected — assume the slow path
        ("amd", 32, "A"),        # non-Mac AMD: no CUDA, still the slow path
        ("metal", 16, "B"),
        ("metal", 8, "A"),       # too little RAM to hold a 7-8B model
        ("nvidia", 12, "B"),     # boundary is inclusive
        ("nvidia", 11, "A"),
    ],
)
def test_classify_hardware(gpu_type, ram_gb, expected):
    assert classify_hardware(_Hw(gpu_type, ram_gb)) == expected
    assert classify_hardware(gpu_type=gpu_type, ram_gb=ram_gb) == expected


# ---------------------------------------------------------------------------
# Suggestion + resolution
# ---------------------------------------------------------------------------


def test_reference_host_suggests_hybrid():
    """amd-mac + a cloud key + Private Mode L0 is the spec's §4b example."""
    hw_class = classify_hardware(_Hw("amd-mac", 160))
    assert suggest_profile(hw_class, has_cloud_key=True, private_mode_level=0) == "hybrid"


@pytest.mark.parametrize(
    ("hw_class", "has_key", "level", "expected"),
    [
        ("A", True, 0, "hybrid"),
        ("B", True, 0, "local-only"),   # class B runs 7-8B locally at speed
        ("A", False, 0, "local-only"),  # class C: no key
        ("A", True, 1, "local-only"),   # class C: Private Mode blocks egress
        ("B", False, 2, "local-only"),
    ],
)
def test_suggest_profile(hw_class, has_key, level, expected):
    assert suggest_profile(hw_class, has_cloud_key=has_key, private_mode_level=level) == expected


def test_resolve_profile_passes_through_when_usable():
    effective, reason = resolve_profile("hybrid", "A", has_cloud_key=True, private_mode_level=0)
    assert effective == "hybrid"
    assert reason == ""


def test_resolve_profile_unset_applies_nothing():
    effective, reason = resolve_profile("", "A", has_cloud_key=True, private_mode_level=0)
    assert effective == ""
    assert reason == ""
    assert profile_defaults(effective, "A") == {}


def test_resolve_profile_unknown_value_applies_nothing():
    effective, reason = resolve_profile(
        "turbo", "A", has_cloud_key=True, private_mode_level=0,
    )
    assert effective == ""
    assert "turbo" in reason


@pytest.mark.parametrize("requested", ["hybrid", "cloud-first"])
def test_no_cloud_key_degrades_to_local_only(requested):
    effective, reason = resolve_profile(
        requested, "A", has_cloud_key=False, private_mode_level=0,
    )
    assert effective == "local-only"
    assert "OPENROUTER_API_KEY" in reason


@pytest.mark.parametrize("requested", ["hybrid", "cloud-first"])
def test_private_mode_degrades_to_local_only(requested):
    effective, reason = resolve_profile(
        requested, "A", has_cloud_key=True, private_mode_level=2,
    )
    assert effective == "local-only"
    assert "Private Mode" in reason


def test_local_only_is_never_degraded():
    effective, reason = resolve_profile(
        "local-only", "A", has_cloud_key=False, private_mode_level=3,
    )
    assert effective == "local-only"
    assert reason == ""


def test_degradation_logs_exactly_one_warning(caplog):
    with caplog.at_level(logging.WARNING, logger="ai-companion.environment_profiles"):
        defaults = apply_environment_profile(
            "hybrid", "A", has_cloud_key=False, private_mode_level=0,
        )
    warnings = [
        r for r in caplog.records
        if r.name == "ai-companion.environment_profiles" and r.levelno == logging.WARNING
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "hybrid" in message
    assert "local-only" in message
    assert "OPENROUTER_API_KEY" in message
    # …and the knobs that actually land are the local-only ones.
    assert defaults["INTERNAL_LLM_MAX_CONCURRENCY"] == "1"
    assert not [k for k in defaults if k.startswith("PROVIDER_STAGE_")]


def test_no_warning_when_the_profile_is_usable(caplog):
    with caplog.at_level(logging.WARNING, logger="ai-companion.environment_profiles"):
        apply_environment_profile("hybrid", "A", has_cloud_key=True, private_mode_level=0)
    assert not [
        r for r in caplog.records if r.name == "ai-companion.environment_profiles"
    ]


# ---------------------------------------------------------------------------
# Stage sets
# ---------------------------------------------------------------------------


def test_background_set_is_the_complement_of_the_interactive_set():
    expected = {
        s for s in STAGE_PROFILES
        if s not in INTERACTIVE_STAGES and not s.startswith("mcp_")
    }
    # ...plus the background stages that carry no hardness tier, so the
    # complement above cannot see them.
    expected |= {"topic_extraction", "session_summary", "entity_merge_adjudication"}
    assert BACKGROUND_STAGES == expected
    assert BACKGROUND_STAGES.isdisjoint(INTERACTIVE_STAGES)
    assert "wiki_summary" in BACKGROUND_STAGES
    assert "entity_extraction" in BACKGROUND_STAGES
    assert "community_summary" in BACKGROUND_STAGES
    assert "brief" in BACKGROUND_STAGES  # deliberate: nobody waits on a digest
    assert "memory_extract" not in BACKGROUND_STAGES  # interactive
    assert "memory_consolidation" not in BACKGROUND_STAGES  # a chat turn blocks on it
    assert "mcp_summarize_domain" not in BACKGROUND_STAGES


@pytest.mark.parametrize(
    "stage", ["topic_extraction", "session_summary", "entity_merge_adjudication"],
)
def test_unclassified_background_stages_are_still_background(stage):
    """These route through PIPELINE_PROVIDERS or the plain default, not a
    hardness tier, so a complement over STAGE_PROFILES cannot see them."""
    assert stage not in STAGE_PROFILES
    assert is_background_stage(stage)
    defaults = profile_defaults("cloud-first", "A")
    assert defaults[f"PROVIDER_STAGE_{normalize_stage(stage)}"] == "openrouter"
    # No tier to protect, so the cheap pin is a hardware adaptation here.
    assert defaults[f"PROVIDER_STAGE_{normalize_stage(stage)}_MODEL"] == _cheap()


def test_is_background_stage_handles_sub_stages():
    assert is_background_stage("wiki_summary")
    assert is_background_stage("brief/daily")  # sub-stage resolves to its parent
    assert not is_background_stage("memory_extract")
    assert not is_background_stage("mcp_summarize_domain")
    assert not is_background_stage(None)
    assert not is_background_stage("totally_unknown_stage")


# ---------------------------------------------------------------------------
# The §4b table
# ---------------------------------------------------------------------------


def _stage_keys(stages) -> set[str]:
    keys: set[str] = set()
    for stage in stages:
        keys.add(f"PROVIDER_STAGE_{normalize_stage(stage)}")
        keys.add(f"PROVIDER_STAGE_{normalize_stage(stage)}_MODEL")
    return keys


def _interactive_routable() -> set[str]:
    return set(INTERACTIVE_STAGES) | {s for s in STAGE_PROFILES if s.startswith("mcp_")}


_EVAL_STAGES = {
    "faithfulness/decompose", "faithfulness/score",
    "context_precision", "context_recall", "answer_relevancy",
}


def _accepts_cheap(stage: str) -> bool:
    if stage in _EVAL_STAGES:
        return False
    hardness = hardness_for(stage)
    return hardness in (None, Hardness.TRIVIAL, Hardness.SIMPLE, Hardness.MODERATE)


def test_cloud_first_routes_interactive_and_background_to_the_cheap_tier():
    defaults = profile_defaults("cloud-first", "A")
    for stage in _interactive_routable() | set(BACKGROUND_STAGES):
        norm = normalize_stage(stage)
        assert defaults[f"PROVIDER_STAGE_{norm}"] == "openrouter"
        if _accepts_cheap(stage):
            assert defaults[f"PROVIDER_STAGE_{norm}_MODEL"] == _cheap()
        else:
            assert f"PROVIDER_STAGE_{norm}_MODEL" not in defaults
    assert defaults["INTERNAL_LLM_MAX_CONCURRENCY"] == "2"
    assert defaults["VERIFY_CLAIM_MAX_CONCURRENT"] == "3"
    assert defaults["WIKI_REFRESH_LIVE_MAX_PER_HOUR"] == "12"
    assert defaults["COMMUNITY_SUMMARY_WALL_CLOCK_S"] == "480"


def test_hybrid_routes_only_interactive_to_the_cheap_tier():
    defaults = profile_defaults("hybrid", "A")
    for stage in _interactive_routable():
        norm = normalize_stage(stage)
        assert defaults[f"PROVIDER_STAGE_{norm}"] == "openrouter"
        if _accepts_cheap(stage):
            assert defaults[f"PROVIDER_STAGE_{norm}_MODEL"] == _cheap()
        else:
            assert f"PROVIDER_STAGE_{norm}_MODEL" not in defaults
    # Background stays local — the profile emits no override for it, so the
    # operator's INTERNAL_LLM_PROVIDER decides and the background model slot
    # applies.
    assert _stage_keys(BACKGROUND_STAGES).isdisjoint(defaults)
    assert defaults["INTERNAL_LLM_MAX_CONCURRENCY"] == "2"
    assert defaults["VERIFY_CLAIM_MAX_CONCURRENT"] == "3"
    assert defaults["WIKI_REFRESH_LIVE_MAX_PER_HOUR"] == "12"
    assert defaults["COMMUNITY_SUMMARY_WALL_CLOCK_S"] == "480"


def test_local_only_routes_nothing_to_cloud():
    defaults = profile_defaults("local-only", "A")
    assert not [k for k in defaults if k.startswith("PROVIDER_STAGE_")]
    assert defaults["INTERNAL_LLM_PROVIDER"] == "ollama"
    assert defaults["INTERNAL_LLM_MAX_CONCURRENCY"] == "1"
    assert defaults["VERIFY_CLAIM_MAX_CONCURRENT"] == "2"
    assert defaults["WIKI_REFRESH_LIVE_MAX_PER_HOUR"] == "4"
    assert defaults["COMMUNITY_SUMMARY_WALL_CLOCK_S"] == "240"


def test_local_only_keeps_two_permits_on_class_b():
    assert profile_defaults("local-only", "B")["INTERNAL_LLM_MAX_CONCURRENCY"] == "2"
    assert profile_defaults("local-only", "A")["INTERNAL_LLM_MAX_CONCURRENCY"] == "1"


@pytest.mark.parametrize(
    ("recommended", "expected"),
    [
        ("quenchforge", "quenchforge"),
        ("ollama", "ollama"),
        ("cloud", "ollama"),   # detect-gpu.sh's third value is not a local backend
        ("", "ollama"),
        (None, "ollama"),
    ],
)
@pytest.mark.parametrize("profile", ["local-only", "hybrid"])
def test_local_backed_profiles_default_the_provider_to_the_hosts_backend(
    profile, recommended, expected,
):
    assert profile_defaults(profile, "A", recommended)["INTERNAL_LLM_PROVIDER"] == expected


def test_cloud_first_leaves_the_provider_alone():
    """It routes every named stage to the cloud already."""
    assert "INTERNAL_LLM_PROVIDER" not in profile_defaults("cloud-first", "A", "quenchforge")


def test_hybrid_backs_its_background_tail_with_the_local_provider():
    """The shipped default is openrouter, so without this the tail hybrid
    promises to keep local falls to the cloud global."""
    assert profile_defaults("hybrid", "A", "quenchforge")["INTERNAL_LLM_PROVIDER"] == "quenchforge"


def test_no_profile_table_hard_codes_a_model_id():
    """The cloud tier is the registry's, not a literal in the profile table."""
    for profile in ("cloud-first", "hybrid"):
        models = {
            v for k, v in profile_defaults(profile, "A").items()
            if k.endswith("_MODEL")
        }
        assert models == {_cheap()}


@pytest.mark.parametrize("profile", ["cloud-first", "hybrid"])
@pytest.mark.parametrize(
    "stage",
    [
        "faithfulness/score", "faithfulness/decompose", "context_precision",
        "context_recall", "answer_relevancy",   # the judges: the model IS the metric
        "brief", "mcp_answer_with_citations",   # FRONTIER
        "longshot_cypher",                      # HARD
    ],
)
def test_eval_and_hard_stages_keep_their_own_tier(profile, stage):
    defaults = profile_defaults(profile, "A")
    key = f"PROVIDER_STAGE_{normalize_stage(stage)}_MODEL"
    assert key not in defaults, f"{profile} would repoint {stage} to the cheap tier"


# ---------------------------------------------------------------------------
# Applied at settings load — operator pins win
# ---------------------------------------------------------------------------


# Imports config for real, so what it reports is what a booting process sees:
# the settings constants, the os.environ defaults the profile applied, and what
# the LIVE resolvers in core.utils.internal_llm make of them. The served-list
# fetch is the one thing stubbed — there is no gateway in a test tier.
_PROBE = """
import asyncio, json, os, sys
sys.path.insert(0, %r)
import config
import core.utils.internal_llm as il

async def _served():
    return ["local-chat", "qwen2.5-3b"], 1.0

il._fetch_served_models_async = _served


def _resolved(stage):
    return {
        "provider": il._resolve_stage_provider(stage, config.INTERNAL_LLM_PROVIDER),
        "model": il._resolve_stage_model(stage),
        "local_model": asyncio.run(il._local_model_for_stage(stage)),
    }


print("CERID_PROBE" + json.dumps({
    "requested": config.CERID_ENVIRONMENT_PROFILE,
    "max_concurrency": config.INTERNAL_LLM_MAX_CONCURRENCY,
    "verify": config.VERIFY_CLAIM_MAX_CONCURRENT,
    "wiki": config.WIKI_REFRESH_LIVE_MAX_PER_HOUR,
    "community": config.COMMUNITY_SUMMARY_WALL_CLOCK_S,
    "background_model": config.INTERNAL_LLM_MODEL_BACKGROUND,
    "provider": os.environ.get("INTERNAL_LLM_PROVIDER", ""),
    "memory_extract_provider": os.environ.get("PROVIDER_STAGE_MEMORY_EXTRACT", ""),
    "wiki_summary_provider": os.environ.get("PROVIDER_STAGE_WIKI_SUMMARY", ""),
    "resolved": {s: _resolved(s) for s in (
        "wiki_summary", "entity_extraction", "faithfulness/score", "brief",
    )},
}))
""" % str(MCP_ROOT)


def _import_settings_with(env: dict[str, str]) -> dict[str, Any]:
    child_env = {
        k: v for k, v in os.environ.items()
        if not k.startswith(("PROVIDER_STAGE_", "CERID_", "INTERNAL_LLM_", "HOST_"))
    }
    child_env.pop("OPENROUTER_API_KEY", None)
    child_env.pop("VERIFY_CLAIM_MAX_CONCURRENT", None)
    child_env.pop("WIKI_REFRESH_LIVE_MAX_PER_HOUR", None)
    child_env.pop("COMMUNITY_SUMMARY_WALL_CLOCK_S", None)
    child_env.update(env)
    proc = subprocess.run(
        [sys.executable, "-c", _PROBE],
        capture_output=True, text=True, env=child_env, timeout=120, check=False,
    )
    assert proc.returncode == 0, proc.stderr
    line = next(x for x in proc.stdout.splitlines() if x.startswith("CERID_PROBE"))
    return json.loads(line.removeprefix("CERID_PROBE"))


def test_hybrid_profile_is_applied_at_settings_load():
    result = _import_settings_with({
        "CERID_ENVIRONMENT_PROFILE": "hybrid",
        "OPENROUTER_API_KEY": "sk-test",  # pragma: allowlist secret
        "HOST_GPU_TYPE": "amd-mac",
        "HOST_MEMORY_GB": "160",
    })
    assert result["requested"] == "hybrid"
    assert result["max_concurrency"] == 2
    assert result["memory_extract_provider"] == "openrouter"
    assert result["wiki_summary_provider"] == ""  # background stays local


def test_operator_pins_beat_the_profile():
    result = _import_settings_with({
        "CERID_ENVIRONMENT_PROFILE": "local-only",
        "HOST_GPU_TYPE": "amd-mac",
        "HOST_MEMORY_GB": "160",
        # Operator pins, all of which the local-only table would otherwise set.
        "INTERNAL_LLM_MAX_CONCURRENCY": "4",
        "VERIFY_CLAIM_MAX_CONCURRENT": "9",
        "WIKI_REFRESH_LIVE_MAX_PER_HOUR": "99",
        "COMMUNITY_SUMMARY_WALL_CLOCK_S": "900",
    })
    assert result["max_concurrency"] == 4
    assert result["verify"] == 9
    assert result["wiki"] == 99
    assert result["community"] == 900.0


def test_no_profile_leaves_every_default_alone():
    result = _import_settings_with({"HOST_GPU_TYPE": "amd-mac", "HOST_MEMORY_GB": "160"})
    assert result["requested"] == ""
    assert result["max_concurrency"] == 2
    assert result["verify"] == 3
    assert result["wiki"] == 12
    assert result["community"] == 480.0
    assert result["memory_extract_provider"] == ""
    assert result["background_model"] == ""
    assert result["provider"] == ""  # settings' own default, not a profile's


def test_cloud_profile_without_a_key_lands_on_the_local_only_table():
    result = _import_settings_with({
        "CERID_ENVIRONMENT_PROFILE": "hybrid",
        "HOST_GPU_TYPE": "amd-mac",
        "HOST_MEMORY_GB": "160",
        "HOST_RECOMMENDED_LOCAL_BACKEND": "quenchforge",
    })
    assert result["max_concurrency"] == 1
    assert result["verify"] == 2
    assert result["wiki"] == 4
    assert result["community"] == 240.0
    assert result["memory_extract_provider"] == ""
    assert result["provider"] == "quenchforge"


def test_local_only_switches_the_provider_to_the_detected_backend():
    """local-only has to MEAN local — the default INTERNAL_LLM_PROVIDER is
    openrouter, so leaving it alone would keep sending work off the box."""
    result = _import_settings_with({
        "CERID_ENVIRONMENT_PROFILE": "local-only",
        "HOST_GPU_TYPE": "amd-mac",
        "HOST_MEMORY_GB": "160",
        "HOST_RECOMMENDED_LOCAL_BACKEND": "quenchforge",
    })
    assert result["provider"] == "quenchforge"


def test_operator_provider_beats_the_local_only_default():
    result = _import_settings_with({
        "CERID_ENVIRONMENT_PROFILE": "local-only",
        "HOST_GPU_TYPE": "amd-mac",
        "HOST_MEMORY_GB": "160",
        "HOST_RECOMMENDED_LOCAL_BACKEND": "quenchforge",
        "INTERNAL_LLM_PROVIDER": "ollama",
    })
    assert result["provider"] == "ollama"


def test_hybrid_keeps_the_background_tail_on_the_local_backend():
    """The finding this test exists for: with INTERNAL_LLM_PROVIDER left at its
    shipped ``openrouter``, hybrid's background stages fell to the cloud global
    and INTERNAL_LLM_MODEL_BACKGROUND was never consulted, while .env.example
    said the tail stayed local."""
    result = _import_settings_with({
        "CERID_ENVIRONMENT_PROFILE": "hybrid",
        "OPENROUTER_API_KEY": "sk-test",  # pragma: allowlist secret
        "HOST_GPU_TYPE": "amd-mac",
        "HOST_MEMORY_GB": "160",
        "HOST_RECOMMENDED_LOCAL_BACKEND": "quenchforge",
        "INTERNAL_LLM_MODEL": "local-chat",
        "INTERNAL_LLM_MODEL_BACKGROUND": "qwen2.5-3b",
    })
    for stage in ("wiki_summary", "entity_extraction"):
        assert result["resolved"][stage]["provider"] == "quenchforge"
        assert result["resolved"][stage]["local_model"] == "qwen2.5-3b"
    # …while the interactive stages hybrid names still go to the cheap tier.
    assert result["memory_extract_provider"] == "openrouter"


def test_cloud_first_does_not_repoint_the_judges_or_the_frontier_stages():
    """A profile that silently re-scored every eval on gpt-4o-mini-class would
    make numbers either side of the switch incomparable, with nothing saying so."""
    result = _import_settings_with({
        "CERID_ENVIRONMENT_PROFILE": "cloud-first",
        "OPENROUTER_API_KEY": "sk-test",  # pragma: allowlist secret
        "HOST_GPU_TYPE": "amd-mac",
        "HOST_MEMORY_GB": "160",
    })
    assert result["resolved"]["faithfulness/score"]["model"] == get_model("tiers", "research")
    assert result["resolved"]["brief"]["model"] == get_model("tiers", "expert")
    # Both still routed off-box — it is the TIER that is preserved, not the box.
    assert result["resolved"]["faithfulness/score"]["provider"] == "openrouter"
    assert result["resolved"]["brief"]["provider"] == "openrouter"
    # …and a cheap-eligible background stage does get the cheap tier.
    assert result["resolved"]["wiki_summary"]["model"] == get_model("tiers", "cheap")
