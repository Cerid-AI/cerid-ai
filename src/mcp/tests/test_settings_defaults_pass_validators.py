# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Every config default + every frontend preset must pass its Pydantic validator.

Backstops the class of bug where a validator constraint (``ge=``, ``le=``,
``pattern=``) excludes the value the system actually defaults to or that a
user-facing preset sends. The 2026-04-23 incident: ``auto_inject_threshold``
had ``ge=0.5`` while the runtime default was 0.15 — so PATCH /settings 422'd
the moment a user touched any preset.

Two checks:
1. Every numeric field on ``SettingsUpdateRequest`` whose corresponding
   ``config.<NAME>`` constant exists must satisfy the field's bounds.
2. Each frontend preset payload (parsed from
   ``src/web/src/lib/settings-presets.ts`` + ``user-presets.ts``) must
   pass the request validator with no errors.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest
from pydantic import ValidationError

REPO_ROOT = Path(__file__).resolve().parents[3]
PRESETS_TS = REPO_ROOT / "src" / "web" / "src" / "lib" / "settings-presets.ts"
USER_PRESETS_TS = REPO_ROOT / "src" / "web" / "src" / "lib" / "user-presets.ts"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_preset_objects(text: str) -> list[dict]:
    """Pull every leaf-most ``{ ... }`` literal that looks like a settings
    payload (contains ``auto_inject_threshold`` or two of our enable flags)
    AND contains no nested ``{`` that itself looks like one.

    The outer wrappers in ``settings-presets.ts`` (the ``PRESETS`` map) and
    ``user-presets.ts`` (per-preset records with ``id``/``label``/``settings``)
    contain the inner payload nested — we want the inner one, not the wrapper.
    """
    candidates: list[tuple[int, int, dict]] = []
    i = 0
    n = len(text)
    while i < n:
        if text[i] != "{":
            i += 1
            continue
        depth = 1
        j = i + 1
        while j < n and depth > 0:
            c = text[j]
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        block = text[i:j + 1]
        if _looks_like_settings_payload(block):
            parsed = _ts_object_to_python(block)
            if parsed is not None:
                candidates.append((i, j, parsed))
        i += 1  # descend into nested blocks too

    # Keep only leaf-most: discard any candidate that strictly contains another.
    leaves: list[dict] = []
    for ai, aj, ap in candidates:
        if any(bi > ai and bj < aj for (bi, bj, _bp) in candidates):
            continue
        leaves.append(ap)
    return leaves


def _looks_like_settings_payload(block: str) -> bool:
    return "auto_inject_threshold" in block or (
        "enable_self_rag" in block and "enable_hallucination_check" in block
    )


def _ts_object_to_python(block: str) -> dict | None:
    """Best-effort TS-object-literal -> Python dict converter.

    Handles the subset used in our preset files: trailing commas,
    bare-identifier keys, double-quoted strings, numeric/boolean values.
    Returns ``None`` if conversion produces invalid JSON (we'll just skip
    that block; the test will still cover the well-formed presets).
    """
    # Quote bare keys: `{ enable_x: true }` -> `{ "enable_x": true }`
    quoted = re.sub(r"([{,]\s*)([A-Za-z_][A-Za-z0-9_]*)\s*:", r'\1"\2":', block)
    # Strip trailing commas before } or ]
    cleaned = re.sub(r",(\s*[}\]])", r"\1", quoted)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


# ---------------------------------------------------------------------------
# Test 1 — runtime defaults satisfy their validators
# ---------------------------------------------------------------------------


def test_runtime_defaults_satisfy_settings_validators():
    """Every config default is a value the user could PATCH and have accepted.

    Walks ``SettingsUpdateRequest`` fields, looks up the matching config
    constant (uppercase form), and validates a single-field PATCH payload.
    A failure means: the system *boots* with a value that PATCH /settings
    would *reject* — the exact 2026-04-23 ``auto_inject_threshold`` bug.
    """
    import config
    from app.routers.settings import SettingsUpdateRequest

    failures: list[str] = []
    for field_name in SettingsUpdateRequest.model_fields:
        upper = field_name.upper()
        if not hasattr(config, upper):
            continue
        value = getattr(config, upper)
        if value is None:
            continue
        try:
            SettingsUpdateRequest(**{field_name: value})
        except ValidationError as exc:
            failures.append(f"  {field_name}={value!r}: {exc.errors()[0]['msg']}")

    if failures:
        pytest.fail(
            "Config defaults rejected by their own SettingsUpdateRequest validators:\n"
            + "\n".join(failures)
            + "\n\nEither relax the validator bound or fix the default — but they "
              "must agree. (See 2026-04-23 auto_inject_threshold incident.)",
        )


# ---------------------------------------------------------------------------
# Test 2 — every frontend preset passes the validator
# ---------------------------------------------------------------------------


def _all_preset_payloads() -> list[tuple[str, dict]]:
    """Gather all preset payloads from both TS files. Returns (label, payload)."""
    out: list[tuple[str, dict]] = []
    for ts_file in (PRESETS_TS, USER_PRESETS_TS):
        if not ts_file.exists():
            continue
        text = ts_file.read_text(encoding="utf-8")
        for idx, payload in enumerate(_extract_preset_objects(text)):
            out.append((f"{ts_file.name}#{idx}", payload))
    return out


@pytest.mark.parametrize("label,payload", _all_preset_payloads())
def test_frontend_preset_payload_passes_settings_validator(label: str, payload: dict):
    """Each Quick / Balanced / Maximum preset must validate cleanly.

    The 2026-04-23 bug: clicking the Quick preset 422'd because
    ``auto_inject_threshold: 0.15`` failed ``ge=0.5``. This test catches
    that *before* a user clicks the button.
    """
    from app.routers.settings import SettingsUpdateRequest

    # Drop fields the request model doesn't know — presets sometimes carry
    # frontend-only knobs (``mmr_lambda``, ``query_decomposition_max_subqueries``)
    # that may or may not be plumbed through. We only assert the subset that
    # *is* in the contract.
    known = set(SettingsUpdateRequest.model_fields)
    filtered = {k: v for k, v in payload.items() if k in known}
    if not filtered:
        pytest.skip(f"{label}: no fields overlap SettingsUpdateRequest")
    try:
        SettingsUpdateRequest(**filtered)
    except ValidationError as exc:
        pytest.fail(
            f"Preset {label} fails SettingsUpdateRequest validator:\n"
            f"  payload subset: {filtered}\n"
            f"  errors: {exc.errors()}",
        )
