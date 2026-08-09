# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Red/green probes for scripts/lint-license-denylist.py (GATE-03/04/05).

The 2026-08-05 GA audit found these gates verified only by one-shot planted
faults recorded in commit messages. Worse, three shapes passed green at HEAD
on 2026-08-07, reproduced before writing this file:

  * "GPL-3.0; UNKNOWN" — the UNKNOWN alternative defeated the GPL hit,
    because alternatives folded with all() and UNKNOWN evaluated as
    not-denied. license-checker really emits ["GPL-3.0", "UNKNOWN"] lists.
  * bare "AGPL" — _AGPL_PATTERNS required a version digit, and the \\bGPL\\b
    fallback cannot match inside AGPL (no word boundary between A and G).
  * an empty package list — "0 packages, 0 denylist violations", exit 0.

The green cases pin the shapes the gate must NOT flag; they are as
load-bearing as the red ones (the docutils triple-license false-positive is
the reason this scanner exists at all).
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_license_denylist", _ROOT / "scripts" / "lint-license-denylist.py"
)
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_license_denylist"] = lint
_SPEC.loader.exec_module(lint)


def _run_pip(tmp_path, entries, extra_args=()):
    p = tmp_path / "pip-licenses.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return lint.main(["--tool", "pip-licenses", "--input", str(p), "--label", "probe", *extra_args])


def _run_npm(tmp_path, mapping, extra_args=()):
    p = tmp_path / "license-checker.json"
    p.write_text(json.dumps(mapping), encoding="utf-8")
    return lint.main(["--tool", "license-checker", "--input", str(p), "--label", "probe", *extra_args])


def _pkg(name, license_str):
    return {"Name": name, "Version": "1.0", "License": license_str}


class TestRedCases:
    """Each of these must exit 1 — a gate never seen failing is not a gate."""

    def test_gpl_spdx_forms(self, tmp_path):
        assert _run_pip(tmp_path, [_pkg("a", "GPL-3.0-only")]) == 1
        assert _run_pip(tmp_path, [_pkg("b", "GPL-2.0")]) == 1

    def test_gpl_trove_prose(self, tmp_path):
        # The shape pip-licenses actually reports for most of this tree, and
        # the one the tools' own exact-string --fail-on silently never matched.
        assert _run_pip(tmp_path, [_pkg("a", "GNU General Public License v3 (GPLv3)")]) == 1

    def test_bare_gpl(self, tmp_path):
        # The extract-msg regression: self-reported bare "GPL".
        assert _run_pip(tmp_path, [_pkg("a", "GPL")]) == 1

    def test_agpl_forms_including_bare(self, tmp_path):
        assert _run_pip(tmp_path, [_pkg("a", "AGPL-3.0")]) == 1
        assert _run_pip(tmp_path, [_pkg("b", "GNU Affero General Public License v3")]) == 1
        # Bare "AGPL" passed at HEAD 2026-08-07: no version digit for
        # _AGPL_PATTERNS, and \bGPL\b cannot match inside "AGPL".
        assert _run_pip(tmp_path, [_pkg("c", "AGPL")]) == 1

    def test_sspl(self, tmp_path):
        assert _run_pip(tmp_path, [_pkg("a", "SSPL-1.0")]) == 1
        assert _run_pip(tmp_path, [_pkg("b", "Server Side Public License")]) == 1

    def test_compound_and_conjunction(self, tmp_path):
        # AND is a conjunction — the permissive half is not an escape.
        assert _run_pip(tmp_path, [_pkg("a", "MIT AND GPL-3.0")]) == 1

    def test_unknown_alternative_is_not_an_escape(self, tmp_path):
        # Flagship: passed green at HEAD. An UNKNOWN alternative is not a
        # permissive alternative — fail closed.
        assert _run_pip(tmp_path, [_pkg("a", "GPL-3.0; UNKNOWN")]) == 1

    def test_npm_list_with_unknown_alternative(self, tmp_path):
        # Same bug through _load_license_checker's list join.
        assert _run_npm(tmp_path, {"evil@1.0.0": {"licenses": ["GPL-3.0", "UNKNOWN"]}}) == 1

    def test_empty_package_list_is_a_failed_scan(self, tmp_path):
        # "0 packages, 0 violations" is indistinguishable from a scan that
        # never ran — the license-scan job's own history (npm half skipped 15
        # runs straight) is the case for this.
        assert _run_pip(tmp_path, []) == 2
        assert _run_npm(tmp_path, {}) == 2


class TestGreenCases:
    """Shapes the gate must NOT flag — the false-positive half of the pin."""

    def test_permissive(self, tmp_path):
        assert _run_pip(tmp_path, [_pkg("a", "MIT"), _pkg("b", "Apache-2.0"), _pkg("c", "BSD-3-Clause")]) == 0

    def test_disjunction_with_permissive_alternative(self, tmp_path):
        assert _run_pip(tmp_path, [_pkg("a", "GPL-3.0 OR MIT")]) == 0

    def test_docutils_triple_license(self, tmp_path):
        # The false-positive this scanner exists to avoid: GPL is one
        # alternative among three, not a requirement.
        assert _run_pip(
            tmp_path,
            [_pkg("docutils", "BSD License; GNU General Public License (GPL); Public Domain")],
        ) == 0

    def test_lgpl_passes_with_notice(self, tmp_path, capsys):
        assert _run_pip(tmp_path, [_pkg("a", "LGPL-3.0")]) == 0
        assert "NOTICE (LGPL" in capsys.readouterr().out

    def test_unknown_is_loud_but_not_fatal(self, tmp_path, capsys):
        # Standalone UNKNOWN must not hard-fail the tree (PyPI metadata is
        # dirty), but it must never again pass in silence.
        assert _run_pip(tmp_path, [_pkg("a", "UNKNOWN"), _pkg("b", "MIT")]) == 0
        out = capsys.readouterr().out
        assert "UNKNOWN" in out and "a" in out

    def test_allowlisted_exception(self, tmp_path, monkeypatch, capsys):
        al = tmp_path / "allowlist.yaml"
        al.write_text(
            'python:\n  sanctioned-pkg: "probe"\n', encoding="utf-8"
        )
        monkeypatch.setattr(lint, "ALLOWLIST_PATH", al)
        assert _run_pip(tmp_path, [_pkg("sanctioned-pkg", "GPL-3.0")]) == 0
        assert "ALLOWED (reviewed exception)" in capsys.readouterr().out
