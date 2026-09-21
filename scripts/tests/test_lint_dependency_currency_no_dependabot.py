# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""`lint-dependency-currency` must answer, not crash, when there is no dependabot.yml.

The gate's COVERAGE half asks whether every manifest directory is watched by an
entry in ``.github/dependabot.yml``. It read that file unconditionally, so on a
repo without one it died with ``FileNotFoundError`` — a crash, not a verdict, and
``make drift-check`` failed for everyone on that repo.

That repo is the public mirror, where the absence is DELIBERATE: dependency bumps
are merged in the internal tree and synced across, so the version-update PRs that
file opened were never mergeable on their own and it was removed on purpose
(public #159). The coverage invariant does not apply there; the crash was the gate
having no way to say so.

WHAT IS PINNED HERE is the distinction between the two halves. Coverage becomes
inapplicable; PINNING does not, because "are image refs pinned" is just as live a
question on a repo nothing watches. A fix that skipped the whole gate would pass
the first test here and fail the third.

These are unit tests against the module's globals rather than subprocess runs:
``REPO`` is derived from the script's own location, so running it from a temp
directory still inspects this repo and proves nothing about either case.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "lint-dependency-currency.py"


def _load():
    spec = importlib.util.spec_from_file_location("lint_dependency_currency", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def mod():
    return _load()


def test_absent_dependabot_yml_reads_as_none_not_empty(mod, tmp_path, monkeypatch) -> None:
    """None and {} are different answers: absent vs "watches nothing"."""
    monkeypatch.setattr(mod, "DEPENDABOT", tmp_path / "nope.yml")
    assert mod._dependabot_dirs() is None


_HAS_DEPENDABOT = (ROOT / ".github" / "dependabot.yml").exists()
_needs_dependabot = pytest.mark.skipif(
    not _HAS_DEPENDABOT,
    reason="this tree has no dependabot.yml (the mirror takes no version updates)",
)


@_needs_dependabot
def test_present_dependabot_yml_still_returns_its_entries(mod) -> None:
    watched = mod._dependabot_dirs()
    assert isinstance(watched, dict) and watched, "this repo has a dependabot.yml"


def test_main_is_a_verdict_not_a_crash_when_absent(mod, tmp_path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(mod, "DEPENDABOT", tmp_path / "nope.yml")
    monkeypatch.setattr(mod.sys, "argv", ["lint-dependency-currency"])
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    # Said out loud: a skipped check and a passed one are indistinguishable in a
    # green log, which is the failure class this repo keeps finding.
    assert "does not apply" in out, out


def test_pinning_still_bites_when_coverage_is_skipped(mod, tmp_path, monkeypatch, capsys) -> None:
    """The half that still applies must still fail the build."""
    monkeypatch.setattr(mod, "DEPENDABOT", tmp_path / "nope.yml")
    monkeypatch.setattr(mod, "_floating", lambda: [("stacks/docker-compose.yml", "alpine:latest")])
    monkeypatch.setattr(mod.sys, "argv", ["lint-dependency-currency"])
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "alpine:latest" in out


@_needs_dependabot
def test_coverage_is_NOT_skipped_where_the_file_exists(mod, monkeypatch, capsys) -> None:
    """Nothing here may loosen the internal tree, which has a dependabot.yml."""
    assert (ROOT / ".github" / "dependabot.yml").exists()
    monkeypatch.setattr(mod.sys, "argv", ["lint-dependency-currency"])
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "all watched by" in out
    assert "does not apply" not in out, "coverage was skipped where the file EXISTS"


def test_an_uncovered_directory_is_still_an_error_when_the_file_exists(
    mod, monkeypatch, capsys
) -> None:
    """The coverage check itself still works — planted, so it cannot pass vacuously."""
    monkeypatch.setattr(mod, "_dependabot_dirs", lambda: {})
    monkeypatch.setattr(mod, "_discover", lambda: {"docker": {"/src/mcp"}})
    monkeypatch.setattr(mod, "_floating", lambda: [])
    monkeypatch.setattr(mod.sys, "argv", ["lint-dependency-currency"])
    rc = mod.main()
    out = capsys.readouterr().out
    assert rc == 1, out
    assert "no dependabot.yml entry watches it" in out
