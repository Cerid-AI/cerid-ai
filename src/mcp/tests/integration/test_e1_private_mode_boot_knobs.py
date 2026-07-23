# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-1h verifiability harness — PRIVATE-MODE BOOT-KNOBS probe.

Audit: ``docs/superpowers/plans/2026-07-19-e1-remediation-program.md`` Phase 1.
Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-011).

``.env.example`` advertises ``CERID_PRIVATE_MODE`` / ``CERID_PRIVATE_MODE_LEVEL``
and ``settings.py`` materializes them as ``PRIVATE_MODE_ENABLED`` /
``PRIVATE_MODE_LEVEL`` — but nothing outside settings.py reads either. Private mode
is enforced purely from the Redis key ``cerid:private_mode:global``, which nothing
seeds from the env, so an operator who provisions a hardened install with
``CERID_PRIVATE_MODE=true`` / ``LEVEL=2`` runs at level 0 (fail-open) until the GUI
toggle is flipped — the boot-time privacy posture is silently inert.

The fix seeds the Redis level from the env at boot, but only when the env enables
private mode AND the key is unset, so a runtime-set level (toolbar) survives a
restart. This probe drives the REAL ``seed_private_mode_from_env`` against a fake
redis and settings overrides, and confirms the seeded level flows into enforcement.

RED-then-GREEN; GREEN → preservation gates.
"""
from __future__ import annotations

import fakeredis
import pytest

PRIVATE_MODE_KEY = "cerid:private_mode:global"


def _wire(monkeypatch, *, enabled: bool, level: int):
    """Point private_mode at a fresh fake redis and override the boot env
    materialization. Returns the fake redis."""
    fr = fakeredis.FakeRedis(decode_responses=True)
    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fr)
    monkeypatch.setattr("config.settings.PRIVATE_MODE_ENABLED", enabled, raising=False)
    monkeypatch.setattr("config.settings.PRIVATE_MODE_LEVEL", level, raising=False)
    return fr


@pytest.mark.preservation
def test_seeds_redis_when_env_enables_and_key_unset(monkeypatch):
    """CERID_PRIVATE_MODE=true / LEVEL=2 on a fresh install must seed the global
    key so enforcement runs at 2 from boot. RED on HEAD: seed fn doesn't exist /
    the env is inert (CR-011)."""
    from app.services.private_mode import seed_private_mode_from_env

    fr = _wire(monkeypatch, enabled=True, level=2)
    assert fr.get(PRIVATE_MODE_KEY) is None  # precondition: fresh install

    seed_private_mode_from_env()

    assert fr.get(PRIVATE_MODE_KEY) == "2", (
        "hardened install with CERID_PRIVATE_MODE=true/LEVEL=2 did not seed the "
        "global private-mode key — server enforces level 0 until the GUI toggle "
        "(CR-011)"
    )


@pytest.mark.preservation
def test_seeded_level_is_enforced(monkeypatch):
    """The seeded level must flow into the enforcement read + threshold check —
    the whole point of the boot knob."""
    from app.services.private_mode import (
        get_private_mode_level,
        private_blocks,
        seed_private_mode_from_env,
    )

    _wire(monkeypatch, enabled=True, level=2)
    seed_private_mode_from_env()

    assert get_private_mode_level() == 2
    assert private_blocks(2) is True, "seeded L2 not enforced (skip-KB threshold)"
    assert private_blocks(3) is False, "seeded L2 must not over-block at L3"


@pytest.mark.preservation
def test_does_not_clobber_runtime_value(monkeypatch):
    """A level already set at runtime (toolbar) must survive a restart — seeding
    only fills an UNSET key, so an operator's L4 session is not silently lowered
    to the env default on reboot."""
    from app.services.private_mode import seed_private_mode_from_env

    fr = _wire(monkeypatch, enabled=True, level=2)
    fr.set(PRIVATE_MODE_KEY, "4")  # runtime-set full-ephemeral

    seed_private_mode_from_env()

    assert fr.get(PRIVATE_MODE_KEY) == "4", (
        "boot seed clobbered a runtime-set private-mode level — a user's L4 "
        "session was lowered to the env default on restart"
    )


@pytest.mark.preservation
def test_no_seed_when_env_disabled(monkeypatch):
    """Default install (CERID_PRIVATE_MODE unset/false) must not seed anything —
    the key stays unset and enforcement fails open to 0, unchanged behavior."""
    from app.services.private_mode import seed_private_mode_from_env

    fr = _wire(monkeypatch, enabled=False, level=1)

    seed_private_mode_from_env()

    assert fr.get(PRIVATE_MODE_KEY) is None, (
        "boot seed ran despite CERID_PRIVATE_MODE being disabled — a default "
        "install should leave the key untouched"
    )
