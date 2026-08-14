# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for scripts/lint-host-capability.py (Gate 8 — host-capability
honesty).

Covers the manifest scan (`requires.swift_helpers` detection, including the
`requires` field being a list on non-connector manifests rather than a dict),
the textual `structurally_unavailable` signal, and each failure mode: a
plugin with no state path and no allowlist entry, a stale allowlist entry
whose plugin already reports the state, and an allowlist entry naming a
plugin with no swift_helpers claim at all.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_host_capability", _ROOT / "scripts" / "lint-host-capability.py",
)
assert _SPEC is not None and _SPEC.loader is not None
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_host_capability"] = lint
_SPEC.loader.exec_module(lint)


_MANIFEST_WITH_HELPER = """\
{
  "name": "fake_connector",
  "type": "connector",
  "requires": {
    "platform": "darwin",
    "swift_helpers": ["ceridfake"]
  }
}
"""

# Mirrors voice_memos/manifest.json: `requires` is a plain string list, not a
# dict — the exact shape that raised AttributeError before the isinstance
# guard was added.
_MANIFEST_NON_DICT_REQUIRES = """\
{
  "name": "fake_parser",
  "type": "parser",
  "requires": ["pywhispercpp"]
}
"""

_DS_WITH_STATE = (
    "class FakeDataSource:\n"
    "    def configured_state(self) -> str:\n"
    "        if True:\n"
    "            return \"structurally_unavailable\"\n"
    "        return \"configured\"\n"
)

_DS_WITHOUT_STATE = (
    "class FakeDataSource:\n"
    "    def is_configured(self) -> bool:\n"
    "        return False\n"
)

# Reports the state literal unconditionally, regardless of runtime — the
# demonstrated bypass: an AST check for the bare string constant alone is
# satisfied by a stub that never actually checks anything.
_DS_UNCONDITIONAL_STUB = (
    "class FakeDataSource:\n"
    "    def configured_state(self) -> str:\n"
    "        return \"structurally_unavailable\"\n"
)

# The dead-code bypass: the marker sits inside an `ast.If`, so the earlier
# "must be inside a branch" check alone was satisfied, but the branch never
# executes (`if False`) and its only statement discards the string into a
# name the method never returns.
_DS_DEAD_CODE_STUB = (
    "class FakeDataSource:\n"
    "    def configured_state(self) -> str:\n"
    "        if False:\n"
    "            _unused = \"structurally_unavailable\"\n"
    "        return \"not_configured\"\n"
)

# Legitimate indirect form: the marker is assigned to a variable inside a
# real branch, and that variable is what the method actually returns.
_DS_ASSIGNED_THEN_RETURNED = (
    "class FakeDataSource:\n"
    "    def configured_state(self) -> str:\n"
    "        if True:\n"
    "            state = \"structurally_unavailable\"\n"
    "        else:\n"
    "            state = \"configured\"\n"
    "        return state\n"
)


def _make_plugin(plugin_dir: Path, name: str, manifest: str, data_source: str | None) -> None:
    d = plugin_dir / name
    d.mkdir(parents=True)
    (d / "manifest.json").write_text(manifest)
    if data_source is not None:
        (d / "data_source.py").write_text(data_source)


def _allowlist(tmp_path: Path, *lines: str) -> Path:
    p = tmp_path / "allowlist.txt"
    p.write_text("\n".join(lines) + "\n" if lines else "")
    return p


class TestManifestScan:
    def test_finds_a_declared_helper(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        _make_plugin(plugin_dir, "fake_connector", _MANIFEST_WITH_HELPER, _DS_WITH_STATE)
        claims = lint.host_binary_plugins(plugin_dir)
        assert claims == {"fake_connector": ["ceridfake"]}

    def test_non_dict_requires_is_skipped_not_a_crash(self, tmp_path):
        """The bug this guards: voice_memos' manifest has `requires` as a
        list, and `.get("swift_helpers")` on a list raises AttributeError."""
        plugin_dir = tmp_path / "plugins"
        _make_plugin(plugin_dir, "fake_parser", _MANIFEST_NON_DICT_REQUIRES, None)
        claims = lint.host_binary_plugins(plugin_dir)
        assert claims == {}

    def test_plugin_with_no_helpers_is_not_claimed(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        _make_plugin(
            plugin_dir, "fake_agent",
            '{"name": "fake_agent", "requires": {"env": ["X"]}}',
            None,
        )
        assert lint.host_binary_plugins(plugin_dir) == {}


class TestStateDetection:
    def test_detects_the_state_marker(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        _make_plugin(plugin_dir, "fake_connector", _MANIFEST_WITH_HELPER, _DS_WITH_STATE)
        assert lint.has_structurally_unavailable_path(plugin_dir, "fake_connector") is True

    def test_absent_marker_is_a_miss(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        _make_plugin(plugin_dir, "fake_connector", _MANIFEST_WITH_HELPER, _DS_WITHOUT_STATE)
        assert lint.has_structurally_unavailable_path(plugin_dir, "fake_connector") is False

    def test_missing_data_source_file_is_a_miss_not_a_crash(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        _make_plugin(plugin_dir, "fake_connector", _MANIFEST_WITH_HELPER, None)
        assert lint.has_structurally_unavailable_path(plugin_dir, "fake_connector") is False

    def test_unconditional_stub_does_not_satisfy_the_check(self, tmp_path):
        """The bypass an adversarial reviewer demonstrated: an AST check for
        the bare string constant alone is satisfied by
        ``def configured_state(self): return "structurally_unavailable"``,
        which reports the state regardless of the actual runtime. The marker
        must sit inside a branch, not an unconditional return."""
        plugin_dir = tmp_path / "plugins"
        _make_plugin(plugin_dir, "fake_connector", _MANIFEST_WITH_HELPER, _DS_UNCONDITIONAL_STUB)
        assert lint.has_structurally_unavailable_path(plugin_dir, "fake_connector") is False

    def test_dead_code_branch_does_not_satisfy_the_check(self, tmp_path):
        """The bypass this guards: `if False: _unused = "structurally_unavailable"`
        puts the marker inside an `ast.If`, satisfying a check that only asks
        "is this constant reachable from within some branch", but the branch
        is unreachable and the marker is never returned — assigned to a name
        the method discards. The marker must sit in a return statement, or be
        assigned to a variable the method actually returns."""
        plugin_dir = tmp_path / "plugins"
        _make_plugin(plugin_dir, "fake_connector", _MANIFEST_WITH_HELPER, _DS_DEAD_CODE_STUB)
        assert lint.has_structurally_unavailable_path(plugin_dir, "fake_connector") is False

    def test_marker_assigned_then_returned_satisfies_the_check(self, tmp_path):
        """The legitimate indirect form: the marker is assigned inside a real
        branch to a variable the method's `return` statement actually hands
        back to the caller."""
        plugin_dir = tmp_path / "plugins"
        _make_plugin(
            plugin_dir, "fake_connector", _MANIFEST_WITH_HELPER, _DS_ASSIGNED_THEN_RETURNED,
        )
        assert lint.has_structurally_unavailable_path(plugin_dir, "fake_connector") is True


class TestValidate:
    def test_covered_plugin_with_no_allowlist_entry_is_clean(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        _make_plugin(plugin_dir, "fake_connector", _MANIFEST_WITH_HELPER, _DS_WITH_STATE)
        report = lint.validate(plugin_dir, _allowlist(tmp_path))
        assert report.ok
        assert report.violations == report.stale == report.unknown_entries == []

    def test_uncovered_plugin_with_no_allowlist_entry_is_a_violation(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        _make_plugin(plugin_dir, "fake_connector", _MANIFEST_WITH_HELPER, _DS_WITHOUT_STATE)
        report = lint.validate(plugin_dir, _allowlist(tmp_path))
        assert not report.ok
        assert report.violations == ["fake_connector"]

    def test_uncovered_plugin_on_the_allowlist_is_debt_not_a_violation(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        _make_plugin(plugin_dir, "fake_connector", _MANIFEST_WITH_HELPER, _DS_WITHOUT_STATE)
        report = lint.validate(
            plugin_dir, _allowlist(tmp_path, "fake_connector  # tracked debt"),
        )
        assert report.ok

    def test_covered_plugin_still_on_the_allowlist_is_stale(self, tmp_path):
        """The regression this catches: a real fix lands, nobody removes the
        allowlist entry, and a future regression in that same plugin is
        silently re-covered by the stale entry instead of failing again."""
        plugin_dir = tmp_path / "plugins"
        _make_plugin(plugin_dir, "fake_connector", _MANIFEST_WITH_HELPER, _DS_WITH_STATE)
        report = lint.validate(
            plugin_dir, _allowlist(tmp_path, "fake_connector  # stale now"),
        )
        assert not report.ok
        assert report.stale == ["fake_connector"]

    def test_allowlist_entry_naming_an_unclaimed_plugin_is_flagged(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        report = lint.validate(
            plugin_dir, _allowlist(tmp_path, "typo_plugin  # does not exist"),
        )
        assert not report.ok
        assert report.unknown_entries == ["typo_plugin"]


class TestUnreadableManifest:
    """The bypass an adversarial reviewer demonstrated: a manifest.json that
    declares requires.swift_helpers but has a JSON syntax error used to
    vanish from `claims` with only a printed `::error::`, so `--check`
    exited 0 even though a host-binary claim inside it was never verified.
    An unparseable manifest can't be confirmed compliant, so it always
    fails — and isn't allowlist-able, since "fix the allowlist" would just
    re-open the same hole a second way."""

    def test_malformed_json_fails_closed_even_with_a_helper_claim_inside(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        d = plugin_dir / "broken_plugin"
        d.mkdir()
        # Trailing comma — invalid JSON — but a human reading the bytes can
        # see requires.swift_helpers is declared.
        (d / "manifest.json").write_text(
            '{"name": "broken_plugin", "requires": {"swift_helpers": ["x"],}}',
        )
        report = lint.validate(plugin_dir, _allowlist(tmp_path))
        assert not report.ok
        assert report.unreadable == ["broken_plugin"]
        # And it must not silently disappear from the claims the empty-oracle
        # guard checks either.
        assert "broken_plugin" not in report.claims

    def test_unreadable_manifest_cannot_be_allowlisted_away(self, tmp_path):
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        d = plugin_dir / "broken_plugin"
        d.mkdir()
        (d / "manifest.json").write_text("{not valid json at all")
        report = lint.validate(
            plugin_dir, _allowlist(tmp_path, "broken_plugin  # attempted cover"),
        )
        assert not report.ok
        assert report.unreadable == ["broken_plugin"]

    def test_host_binary_plugins_still_silently_excludes_it(self, tmp_path):
        """`host_binary_plugins` keeps its old (claims-only) contract for
        existing callers — the fail-closed behavior lives in `validate`/
        `_scan_manifests`, not in this compatibility wrapper."""
        plugin_dir = tmp_path / "plugins"
        plugin_dir.mkdir()
        d = plugin_dir / "broken_plugin"
        d.mkdir()
        (d / "manifest.json").write_text("{not valid json at all")
        assert lint.host_binary_plugins(plugin_dir) == {}


class TestRepoAtHead:
    """The real gate, against the real repo tree — pins that HEAD is green
    with exactly the seeded allowlist and nothing more."""

    def test_repo_is_green_at_head(self):
        report = lint.validate(lint.PLUGIN_DIR, lint.ALLOWLIST_PATH)
        assert report.ok, (
            f"violations={report.violations} stale={report.stale} "
            f"unknown={report.unknown_entries}"
        )

    def test_apple_reminders_plugin_is_gone_not_allowlisted(self):
        """apple_reminders' container-side plugin was DELETED outright
        (2026-08-12, reachability-audit item 1): the Linux MCP container can
        never execute ceridreminders, and the feature now ingests through the
        desktop bridge. Pin both facts — the directory stays gone, and the
        name stays off the allowlist — so a resurrected plugin must carry a
        real structurally_unavailable path to pass the gate rather than
        sliding back in via the allowlist."""
        assert not (lint.PLUGIN_DIR / "apple_reminders").exists()
        allowlist = lint.load_allowlist(lint.ALLOWLIST_PATH)
        assert "apple_reminders" not in allowlist

    def test_known_debt_is_exactly_the_three_bridge_plugins(self):
        allowlist = lint.load_allowlist(lint.ALLOWLIST_PATH)
        assert allowlist == {"apple_calendar", "apple_photos", "apple_mail"}
