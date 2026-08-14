#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Gate 8 — Host-capability honesty for plugins that declare a host binary.

Graduates Gate 8 ("Host-capability") from
tasks/2026-08-11-consolidated-audit.md §3, closing mechanism M5: the plugin
system had no concept of "this needs a host process". A plugin declares a
Swift helper (`requires.swift_helpers` in its manifest.json); on a runtime
that can never execute that helper — the MCP server's own Dockerfile is
`python:3.12-slim`, so every Linux-container deployment qualifies —
`is_configured()` returns False and the system reported "not configured
yet", indistinguishable from a user who simply hasn't clicked Connect. There
is no failure mode for *structurally impossible here*. This produced the
longest-lived defects in the reachability audit: `apple_calendar`,
`apple_photos`, `apple_reminders`, `apple_imessage`'s phantom binary.

This gate scans every `src/mcp/plugins/*/manifest.json` that declares
`requires.swift_helpers`, and fails when the plugin's `data_source.py` has no
`configured_state()` method that actually returns the exact string
`"structurally_unavailable"` — AST-checked, not a raw substring search: a
declaration nothing consumes is exactly how `apple_imessage`'s phantom
binary survived undetected (see the internal-only sibling gate, checked in
under `scripts/`, for the reverse direction: manifest claims that don't map
to a real binary). This gate is about the runtime STATE a real, present
binary claim gets reported as; that one is about whether the claim is real
at all. (A substring version was tried first and reported green against a
docstring merely mentioning the term — this gate's own plant-fault probe
caught it; see the AST-based fix in `has_structurally_unavailable_path`.)

ALLOWLIST: `scripts/host_capability_allowlist.txt` — one plugin name per
line, each with a comment documenting the tracking finding. The repo
currently contains three unremediated instances (`apple_calendar`,
`apple_photos`, `apple_mail`). `apple_reminders` was first given a real
`configured_state()` by this gate's own build, then its plugin was deleted
outright (2026-08-12) when reminders moved to the desktop bridge; it must
never appear on the allowlist. The gate also fails on a STALE entry — a
plugin that already reports `structurally_unavailable` but is still
allowlisted, which would silently re-cover a real fix.

Usage:
    python scripts/lint-host-capability.py            # report current state
    python scripts/lint-host-capability.py --check    # CI gate (same checks)
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_DIR = REPO_ROOT / "src" / "mcp" / "plugins"
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "host_capability_allowlist.txt"

# The literal the state-reporting path must produce.
_STATE_MARKER = "structurally_unavailable"
_STATE_METHOD = "configured_state"


def load_allowlist(allowlist_path: Path) -> set[str]:
    if not allowlist_path.is_file():
        return set()
    names: set[str] = set()
    for line in allowlist_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        names.add(line.split()[0])
    return names


def _scan_manifests(plugin_dir: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Returns ``(claims, unreadable)``.

    ``unreadable`` is the plugin-directory names whose ``manifest.json``
    failed to parse as JSON. This is the fix for the demonstrated bypass: a
    manifest that declares ``requires.swift_helpers`` but has a JSON syntax
    error used to vanish from the scan with only a printed ``::error::`` —
    the run still exited 0. A manifest that cannot be read is not the same
    as a manifest that declares no helpers; the gate cannot tell which one
    it is, so it fails closed and always reports it, with no allowlist
    escape hatch (fixing the JSON is the only way to clear it).
    """
    claims: dict[str, list[str]] = {}
    unreadable: list[str] = []
    for manifest_path in sorted(plugin_dir.glob("*/manifest.json")):
        try:
            data = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"::error::[host-capability] cannot read {manifest_path}: {exc}")
            unreadable.append(manifest_path.parent.name)
            continue
        requires = data.get("requires")
        helpers = requires.get("swift_helpers") if isinstance(requires, dict) else None
        if isinstance(helpers, list) and helpers:
            name = data.get("name") or manifest_path.parent.name
            claims[str(name)] = [str(h) for h in helpers]
    return claims, unreadable


def host_binary_plugins(plugin_dir: Path) -> dict[str, list[str]]:
    """Plugin name -> declared swift_helpers, for every manifest that
    declares at least one and parses cleanly.

    Unreadable manifests are silently excluded from this view — callers that
    need to know about those too (``validate``, for the fail-closed check)
    use ``_scan_manifests`` directly.
    """
    claims, _unreadable = _scan_manifests(plugin_dir)
    return claims


def _is_marker_constant(node: ast.expr | None) -> bool:
    return isinstance(node, ast.Constant) and node.value == _STATE_MARKER


def has_structurally_unavailable_path(plugin_dir: Path, plugin_name: str) -> bool:
    """True when a ``configured_state`` method in the plugin's DataSource
    actually PRODUCES the structurally_unavailable state from a branch, found
    via AST rather than a raw substring search.

    A raw substring match was tried first and is exactly wrong: a docstring
    merely *discussing* the concept ("this plugin deliberately has no
    structurally_unavailable path") satisfies a substring check without
    implementing anything — caught by this gate's own plant-fault probe,
    which planted precisely that docstring and watched the naive version
    report green. AST-walking the tree only inside ``def configured_state``
    bodies and comparing exact string-constant values closes both holes:
    comments never reach the AST at all, and a docstring's value is the
    whole paragraph, never equal to the bare marker.

    The marker must additionally sit inside an ``if`` branch of that method,
    not merely appear as an unconditional top-level statement. Without this,
    ``def configured_state(self): return "structurally_unavailable"`` — a
    stub that reports the state regardless of what the runtime actually is —
    satisfies the AST check while being exactly the dishonesty the gate
    exists to catch: the string is real, but nothing about it is conditioned
    on the runtime.

    Being inside an ``ast.If`` is necessary but was not sufficient: a
    constant sitting anywhere under the branch — including a value assigned
    to a variable nothing ever returns — used to satisfy the check too.
    ``if False: _unused = "structurally_unavailable"`` is dead code (the
    branch never executes) whose only reachable statement discards the
    string into a name the method never returns; the previous version
    reported it as a real path. The marker must therefore appear either (a)
    directly in a ``return`` statement, or (b) assigned to a variable that
    some ``return <name>`` in the same method actually returns. Neither
    proves the branch condition tests the right thing — that is a
    behavioral question this static check cannot answer — but it does prove
    the marker is on a path the function can actually hand back to a
    caller, which a bare AST-membership check did not. The real guarantee
    is the behavioral test in the suite —
    ``src/mcp/tests/test_apple_reminders_data_source.py::TestConfiguredState``
    (manifest declares ``swift_helpers``, helper absent/host non-Darwin =>
    ``configured_state()`` reports ``structurally_unavailable``) — this lint
    is the cheap static guard that catches an obviously dishonest
    implementation before that behavioral test ever runs.

    (The exemplar that behavioral test covered — the apple_reminders
    plugin — was deleted 2026-08-12 when reminders moved to the desktop
    bridge; the contract above is unchanged and applies to any plugin that
    declares a host binary.)
    """
    ds_path = plugin_dir / plugin_name / "data_source.py"
    if not ds_path.is_file():
        return False
    try:
        tree = ast.parse(ds_path.read_text(encoding="utf-8"), filename=str(ds_path))
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if not (isinstance(node, ast.FunctionDef) and node.name == _STATE_METHOD):
            continue
        returned_names = {
            ret.value.id
            for ret in ast.walk(node)
            if isinstance(ret, ast.Return) and isinstance(ret.value, ast.Name)
        }
        for if_node in ast.walk(node):
            if not isinstance(if_node, ast.If):
                continue
            for inner in ast.walk(if_node):
                if isinstance(inner, ast.Return) and _is_marker_constant(inner.value):
                    return True
                if isinstance(inner, ast.Assign) and _is_marker_constant(inner.value):
                    targets = (t.id for t in inner.targets if isinstance(t, ast.Name))
                    if any(name in returned_names for name in targets):
                        return True
    return False


class Report:
    """Result of one scan — kept as data so main() and tests share one path."""

    def __init__(
        self,
        claims: dict[str, list[str]],
        violations: list[str],
        stale: list[str],
        unknown_entries: list[str],
        unreadable: list[str],
    ) -> None:
        self.claims = claims
        self.violations = violations
        self.stale = stale
        self.unknown_entries = unknown_entries
        self.unreadable = unreadable

    @property
    def ok(self) -> bool:
        return not (self.violations or self.stale or self.unknown_entries or self.unreadable)


def validate(plugin_dir: Path, allowlist_path: Path) -> Report:
    claims, unreadable = _scan_manifests(plugin_dir)
    allowlist = load_allowlist(allowlist_path)
    violations = [
        name for name in sorted(claims)
        if not has_structurally_unavailable_path(plugin_dir, name) and name not in allowlist
    ]
    stale = [
        name for name in sorted(claims)
        if has_structurally_unavailable_path(plugin_dir, name) and name in allowlist
    ]
    unknown_entries = sorted(n for n in allowlist if n not in claims)
    return Report(claims, violations, stale, unknown_entries, sorted(unreadable))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check", action="store_true",
        help="CI gate (same checks as the default report; non-zero exit on violation)",
    )
    ap.parse_args(argv)

    report = validate(PLUGIN_DIR, ALLOWLIST_PATH)
    claims = report.claims
    if not claims and not report.unreadable:
        # Fail closed: with no oracle, every plugin looks compliant, which is
        # the exact failure this gate exists to catch — see the identical
        # empty-oracle guard in this gate's internal-only sibling.
        print(
            "::error::[host-capability] no manifest declares "
            "requires.swift_helpers — either the field moved or the scan is "
            "looking in the wrong place. Refusing to report a pass.",
        )
        return 2

    failed = False

    if report.violations:
        failed = True
        print(
            "::error::[host-capability] plugin(s) declare a host binary "
            "(requires.swift_helpers) but their DataSource has no "
            "structurally_unavailable state path — a runtime that can never "
            "execute the helper (e.g. the Linux MCP container) reports the "
            "same 'not configured' state as a user who has not connected yet:",
        )
        for name in report.violations:
            print(f"  {name}  (helpers: {', '.join(report.claims[name])})")
        print(
            "\nAdd a configured_state() method to the plugin's DataSource "
            "class returning \"structurally_unavailable\" when the current "
            "runtime cannot run the declared helper (e.g. "
            "platform.system() != \"Darwin\" for a macOS-only binary), "
            f"or add the plugin name to {ALLOWLIST_PATH.relative_to(REPO_ROOT)} "
            "with a comment naming the tracking finding.",
        )

    if report.stale:
        failed = True
        print(
            "::error::[host-capability] allowlist entries are stale — these "
            "plugins already report structurally_unavailable and must be "
            f"removed from {ALLOWLIST_PATH.relative_to(REPO_ROOT)} so a "
            "regression there can be caught again:",
        )
        for name in report.stale:
            print(f"  {name}")

    if report.unknown_entries:
        failed = True
        print(
            f"::error::[host-capability] {ALLOWLIST_PATH.relative_to(REPO_ROOT)} "
            "names plugin(s) with no requires.swift_helpers manifest claim — "
            "stale entry (helper claim was removed) or typo:",
        )
        for name in report.unknown_entries:
            print(f"  {name}")

    if report.unreadable:
        failed = True
        print(
            "::error::[host-capability] plugin(s) have a manifest.json that "
            "could not be parsed as JSON — a host-binary claim inside it "
            "cannot be confirmed compliant, so an unparseable manifest is "
            "always a failure and is NOT allowlist-able. Fix the JSON:",
        )
        for name in report.unreadable:
            print(f"  {name}")

    if failed:
        return 1

    allowlist = load_allowlist(ALLOWLIST_PATH)
    covered = len(claims) - len(allowlist)
    print(
        f"[host-capability] OK — {len(claims)} plugin(s) declare a host "
        f"binary; {covered} report structurally_unavailable, "
        f"{len(allowlist)} tracked on the allowlist.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
