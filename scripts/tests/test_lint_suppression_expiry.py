# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Red/green probes for scripts/lint-suppression-expiry.py's Trivy source.

The load-bearing case: the gate must read its Trivy entries from the
`cat > .ci-artifacts/trivyignore <<'EOF' ... EOF` heredoc inside
scripts/ci/docker-gate.sh — the file Trivy is actually pointed at — not
from the standalone root `.trivyignore`, which the scan never reads.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_suppression_expiry", _ROOT / "scripts" / "lint-suppression-expiry.py"
)
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_suppression_expiry"] = lint
_SPEC.loader.exec_module(lint)

_GATE_PREFIX = (
    "#!/usr/bin/env bash\n"
    "set -euo pipefail\n"
    "mkdir -p .ci-artifacts\n"
    "cat > .ci-artifacts/trivyignore <<'EOF'\n"
)
_GATE_SUFFIX = (
    "EOF\n"
    "trivy image --ignorefile .ci-artifacts/trivyignore \"$1\"\n"
)


def _gate_script(tmp_path: Path, heredoc_body: str) -> Path:
    ci_dir = tmp_path / "scripts" / "ci"
    ci_dir.mkdir(parents=True)
    gate = ci_dir / "docker-gate.sh"
    gate.write_text(_GATE_PREFIX + heredoc_body + "\n" + _GATE_SUFFIX, encoding="utf-8")
    return tmp_path


def _patched(repo_root: Path, baseline_lines: list[str] | None = None):
    orig_repo = lint.REPO
    orig_baseline = lint.BASELINE
    orig_sources = lint.SOURCES

    class _Patch:
        def __enter__(self):
            lint.REPO = repo_root
            lint.BASELINE = repo_root / "scripts" / "suppression_undated_baseline.txt"
            lint.SOURCES = [Path("scripts/ci/docker-gate.sh")]
            if baseline_lines is not None:
                lint.BASELINE.write_text("\n".join(baseline_lines) + "\n", encoding="utf-8")
            return self

        def __exit__(self, *exc):
            lint.REPO = orig_repo
            lint.BASELINE = orig_baseline
            lint.SOURCES = orig_sources
            return False

    return _Patch()


def _run(repo_root: Path, baseline_lines: list[str] | None = None) -> int:
    with _patched(repo_root, baseline_lines):
        return lint.main([])


class TestRedCases:
    """Each must exit 1 — a gate never seen failing is not a gate."""

    def test_undated_entry_not_in_baseline_fails(self, tmp_path):
        root = _gate_script(
            tmp_path,
            "# dated entry, safely in the future\n"
            "# Re-eval 2099-01-01\n"
            "CVE-2026-11111\n"
            "# undated entry, no baseline coverage\n"
            "CVE-2026-22222\n",
        )
        assert _run(root, baseline_lines=[]) == 1

    def test_expired_entry_fails(self, tmp_path):
        root = _gate_script(
            tmp_path,
            "# expired long ago\n"
            "# Re-eval 2020-01-01\n"
            "CVE-2026-33333\n",
        )
        assert _run(root, baseline_lines=[]) == 1


class TestGreenCases:
    def test_dated_future_and_baselined_undated_pass(self, tmp_path):
        root = _gate_script(
            tmp_path,
            "# dated entry, safely in the future\n"
            "# Re-eval 2099-01-01\n"
            "CVE-2026-11111\n"
            "# undated entry, grandfathered\n"
            "CVE-2026-22222\n",
        )
        assert _run(root, baseline_lines=["CVE-2026-22222"]) == 0

    def test_entries_read_from_heredoc_not_whole_file(self, tmp_path):
        # A bare CVE id sitting outside the heredoc (e.g. in a shell comment
        # explaining why a build is cache-free) must never be picked up as a
        # suppression entry.
        root = _gate_script(
            tmp_path,
            "# dated entry, safely in the future\n"
            "# Re-eval 2099-01-01\n"
            "CVE-2026-11111\n",
        )
        gate = root / "scripts" / "ci" / "docker-gate.sh"
        gate.write_text(
            "#!/usr/bin/env bash\n"
            "# a fresh build already fixes\n"
            "CVE-2026-99999\n"
            + gate.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        with _patched(root, baseline_lines=[]):
            assert lint.main([]) == 0  # CVE-2026-99999 sits outside the heredoc
            idents = {e[2] for e in lint._entries()}
        assert "CVE-2026-99999" not in idents
        assert "CVE-2026-11111" in idents


class TestMissingHeredoc:
    def test_missing_heredoc_raises(self, tmp_path):
        ci_dir = tmp_path / "scripts" / "ci"
        ci_dir.mkdir(parents=True)
        (ci_dir / "docker-gate.sh").write_text("#!/usr/bin/env bash\necho hi\n", encoding="utf-8")
        try:
            _run(tmp_path, baseline_lines=[])
        except SystemExit as exc:
            assert "heredoc" in str(exc)
        else:
            raise AssertionError("expected SystemExit for a missing heredoc")
