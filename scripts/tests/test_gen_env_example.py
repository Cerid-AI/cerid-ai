# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Variables read by a name held in data still reach .env.example."""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "gen_env_example", _ROOT / "scripts" / "gen_env_example.py",
)
assert _SPEC is not None and _SPEC.loader is not None
gen = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(gen)


def test_automation_toggles_are_collected():
    sources = [(str(p), p.read_text(encoding="utf-8")) for p in gen._iter_sources()]
    entries = dict(gen.collect_env_vars(sources))
    assert entries["CERID_INBOX_TRIAGE_ENABLED"] == "false"
    assert entries["CERID_DAILY_DIGEST_ENABLED"] == "false"
    # The literal getenv in settings.py still decides the default.
    assert entries["SCHEDULE_INBOX_TRIAGE"] == "*/15 * * * *"


def test_inbox_knobs_carry_a_comment():
    for name in (
        "CERID_INBOX_ACTIONS_ENABLED",
        "CERID_RSPAMD_PASSWORD",
        "CERID_INBOX_TRIAGE_ENABLED",
        "SCHEDULE_INBOX_TRIAGE",
    ):
        assert gen.VAR_COMMENTS.get(name), name
