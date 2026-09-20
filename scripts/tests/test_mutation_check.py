# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Red/green probe for scripts/mutation_check.py's exit contract.

The harness is now wired into ci.yml as a merge-time gate. A gate that returns
0 no matter what it found is not a gate — and this one did exactly that: every
surviving mutant was printed and the process exited clean. A drifted anchor was
worse, printed as SKIP and counted as nothing at all.

The real harness takes tens of minutes, so ``run_tests`` is substituted here.
That is the collaborator under test in every case: what the return code says
about what it reported.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
SCRIPT = REPO / "scripts" / "mutation_check.py"


def _load():
    spec = importlib.util.spec_from_file_location("mutation_check_probe", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def harness(tmp_path, monkeypatch):
    """The module with its tree pointed at tmp_path so nothing real is mutated."""
    mod = _load()
    monkeypatch.setattr(mod, "REPO", tmp_path)
    target = tmp_path / "victim.py"
    target.write_text("STATUS = 200\n", encoding="utf-8")
    return mod, target


def _mutant(label: str = "flip the status guard"):
    return (label, "victim.py", "STATUS = 200", "STATUS = 500")


def test_returns_zero_when_every_mutant_is_killed(harness, monkeypatch) -> None:
    mod, _ = harness
    calls = {"n": 0}

    def _tests() -> bool:
        calls["n"] += 1
        return calls["n"] == 1  # baseline green, mutated run red

    monkeypatch.setattr(mod, "run_tests", _tests)
    monkeypatch.setattr(mod, "MUTANTS", [_mutant()])
    assert mod._run() == 0


def test_survivor_fails_the_run(harness, monkeypatch, capsys) -> None:
    """A mutant nothing caught is a blind spot — it must fail the job."""
    mod, _ = harness
    monkeypatch.setattr(mod, "run_tests", lambda: True)  # always green
    monkeypatch.setattr(mod, "MUTANTS", [_mutant()])

    assert mod._run() == 1
    out = capsys.readouterr().out
    assert "SURVIVED" in out
    assert "flip the status guard" in out


def test_stale_anchor_fails_the_run(harness, monkeypatch, capsys) -> None:
    """An anchor that no longer matches means the mutant was never injected."""
    mod, _ = harness
    monkeypatch.setattr(mod, "run_tests", lambda: True)
    monkeypatch.setattr(
        mod, "MUTANTS", [("guard the retired constant", "victim.py", "STATUS = 418", "STATUS = 500")],
    )

    assert mod._run() == 1
    out = capsys.readouterr().out
    assert "NOT INJECTED" in out
    assert "killed 0/1" in out


def test_red_baseline_aborts(harness, monkeypatch) -> None:
    mod, _ = harness
    monkeypatch.setattr(mod, "run_tests", lambda: False)
    monkeypatch.setattr(mod, "MUTANTS", [_mutant()])
    assert mod._run() == 2


def test_source_is_restored_even_when_the_mutant_survives(harness, monkeypatch) -> None:
    mod, target = harness
    monkeypatch.setattr(mod, "run_tests", lambda: True)
    monkeypatch.setattr(mod, "MUTANTS", [_mutant()])

    mod._run()
    assert target.read_text(encoding="utf-8") == "STATUS = 200\n"


def test_pytest_cmd_falls_back_to_the_running_interpreter(harness) -> None:
    """CI installs into the job's Python; there is no repo-local .venv there."""
    mod, _ = harness  # REPO is tmp_path, which has no .venv
    cmd = mod._pytest_cmd()
    assert cmd[-2:] == ["-m", "pytest"]
    assert Path(cmd[0]).exists()


def test_pytest_cmd_prefers_the_repo_venv(harness) -> None:
    mod, _ = harness
    venv_pytest = mod.REPO / ".venv" / "bin" / "pytest"
    venv_pytest.parent.mkdir(parents=True)
    venv_pytest.write_text("#!/bin/sh\n", encoding="utf-8")
    assert mod._pytest_cmd() == [str(venv_pytest)]
