# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The automation enable switches reach .env.example although no literal reads them.

``utils/pro_automations.py`` reads ``CERID_DAILY_DIGEST_ENABLED`` and
``CERID_INBOX_TRIAGE_ENABLED`` through ``os.getenv(spec["env_enabled"])``, a
variable rather than a string constant, so the AST walk in
``gen_env_example.py`` cannot see them. ``.env.example`` listed each
automation's SCHEDULE_* cron and not the switch that lets the cron do
anything: an operator who copied the file got a schedule that never fired.

The names are taken from the registry itself (parsed, not imported, so this
runs without the MCP runtime), so a third automation added to AUTOMATIONS
without a generator entry fails here.
"""
from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("gen_env_example", _ROOT / "scripts" / "gen_env_example.py")
gen = importlib.util.module_from_spec(_SPEC)
assert _SPEC.loader is not None
_SPEC.loader.exec_module(gen)

_REGISTRY = _ROOT / "src" / "mcp" / "utils" / "pro_automations.py"


def _registry_enable_switches() -> set[str]:
    names: set[str] = set()
    for node in ast.walk(ast.parse(_REGISTRY.read_text())):
        if not isinstance(node, ast.Dict):
            continue
        for key, value in zip(node.keys, node.values):
            if (
                isinstance(key, ast.Constant) and key.value == "env_enabled"
                and isinstance(value, ast.Constant) and isinstance(value.value, str)
            ):
                names.add(value.value)
    return names


def test_the_registry_has_the_two_known_switches() -> None:
    assert _registry_enable_switches() >= {"CERID_DAILY_DIGEST_ENABLED", "CERID_INBOX_TRIAGE_ENABLED"}


def test_every_automation_enable_switch_is_generated() -> None:
    sources = [(str(p), p.read_text(encoding="utf-8")) for p in gen._iter_sources()]
    rendered = gen.render_env_example(gen.collect_env_vars(sources))
    missing = {name for name in _registry_enable_switches() if f"\n{name}=false\n" not in rendered}
    assert not missing, f"not generated into .env.example: {sorted(missing)}"


def test_the_tracked_env_example_carries_the_switches() -> None:
    text = (_ROOT / ".env.example").read_text()
    missing = {name for name in _registry_enable_switches() if f"\n{name}=" not in text}
    assert not missing, f"absent from .env.example: {sorted(missing)}"
