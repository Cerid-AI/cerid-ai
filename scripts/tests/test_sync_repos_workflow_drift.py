# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for check_workflow_drift() in sync-repos.py.

Regression coverage for the class of bug where internal added Trivy
`ignore-unfixed: true` (2026-05-31) but the public mirror's ci.yml was
never updated to match, so public main CI went red on unfixable Debian
CVEs. `check_workflow_drift()` is a warn-level, non-fatal detector wired
into `sync-repos.py validate` that catches this class of drift before it
can silently recur: it compares the set of lines containing
`ignore-unfixed`, `severity:`, and pinned-action refs (`uses: x@y`)
between the internal and public `.github/workflows/ci.yml` and reports
any one-sided entries (present in one repo, absent in the other).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "sync_repos", _ROOT / "scripts" / "sync-repos.py",
)
assert _SPEC is not None and _SPEC.loader is not None
sync_repos = importlib.util.module_from_spec(_SPEC)
sys.modules["sync_repos"] = sync_repos
_SPEC.loader.exec_module(sync_repos)

check_workflow_drift = sync_repos.check_workflow_drift

_WORKFLOW_REL = Path(".github/workflows/ci.yml")


def _write_ci_yml(repo_root: Path, content: str) -> None:
    ci_path = repo_root / _WORKFLOW_REL
    ci_path.parent.mkdir(parents=True, exist_ok=True)
    ci_path.write_text(content)


_BASE_TRIVY_STEP = """\
name: CI
on: [push]
jobs:
  docker:
    steps:
      - uses: actions/checkout@v6
      - name: Trivy scan
        uses: aquasecurity/trivy-action@ed142fd0673e97e23eac54620cfb913e5ce36c25
        with:
          severity: CRITICAL,HIGH
{ignore_unfixed_line}
"""


def test_public_missing_ignore_unfixed_flags_drift(tmp_path):
    internal = tmp_path / "internal"
    public = tmp_path / "public"
    _write_ci_yml(
        internal,
        _BASE_TRIVY_STEP.format(ignore_unfixed_line="          ignore-unfixed: true"),
    )
    _write_ci_yml(public, _BASE_TRIVY_STEP.format(ignore_unfixed_line=""))

    drift = check_workflow_drift(internal, public)

    assert drift, "expected non-empty one-sided drift summary"
    assert any("ignore-unfixed" in entry for entry in drift)
    assert any(entry.startswith("internal-only:") for entry in drift)


def test_matching_pair_reports_no_drift(tmp_path):
    internal = tmp_path / "internal"
    public = tmp_path / "public"
    content = _BASE_TRIVY_STEP.format(ignore_unfixed_line="          ignore-unfixed: true")
    _write_ci_yml(internal, content)
    _write_ci_yml(public, content)

    assert check_workflow_drift(internal, public) == []


def test_differing_severity_flags_drift(tmp_path):
    internal = tmp_path / "internal"
    public = tmp_path / "public"
    _write_ci_yml(internal, "steps:\n  - run: x\n    severity: CRITICAL,HIGH\n")
    _write_ci_yml(public, "steps:\n  - run: x\n    severity: CRITICAL\n")

    drift = check_workflow_drift(internal, public)

    assert any("severity:" in entry for entry in drift)


def test_differing_pinned_action_ref_flags_drift(tmp_path):
    internal = tmp_path / "internal"
    public = tmp_path / "public"
    _write_ci_yml(internal, "steps:\n  - uses: actions/checkout@v6\n")
    _write_ci_yml(public, "steps:\n  - uses: actions/checkout@v5\n")

    drift = check_workflow_drift(internal, public)

    assert any("actions/checkout@v6" in entry for entry in drift)
    assert any("actions/checkout@v5" in entry for entry in drift)


def test_unrelated_line_differences_are_not_flagged(tmp_path):
    internal = tmp_path / "internal"
    public = tmp_path / "public"
    _write_ci_yml(internal, "name: CI (internal)\nsteps:\n  - run: echo internal\n")
    _write_ci_yml(public, "name: CI (public)\nsteps:\n  - run: echo public\n")

    assert check_workflow_drift(internal, public) == []


def test_missing_public_ci_yml_returns_empty_not_crash(tmp_path):
    internal = tmp_path / "internal"
    public = tmp_path / "public"
    _write_ci_yml(internal, "steps:\n  - uses: actions/checkout@v6\n")
    public.mkdir()

    assert check_workflow_drift(internal, public) == []


def test_missing_internal_ci_yml_returns_empty_not_crash(tmp_path):
    internal = tmp_path / "internal"
    public = tmp_path / "public"
    internal.mkdir()
    _write_ci_yml(public, "steps:\n  - uses: actions/checkout@v6\n")

    assert check_workflow_drift(internal, public) == []
