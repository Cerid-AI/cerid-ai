# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Red/green probes for scripts/detect-secrets-report.py.

The load-bearing case: xargs splits the tracked-file list once it outgrows
its command buffer, so the scan output is several JSON documents back to
back. The verdict must be the same as for one document, and a finding in
any chunk must still fail the gate.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SCRIPT = _ROOT / "scripts" / "detect-secrets-report.py"
_SPEC = importlib.util.spec_from_file_location("detect_secrets_report", _SCRIPT)
report = importlib.util.module_from_spec(_SPEC)
sys.modules["detect_secrets_report"] = report
_SPEC.loader.exec_module(report)

_HIT = {"type": "Secret Keyword", "line_number": 7, "is_verified": False}


def _doc(results: dict) -> str:
    return json.dumps({"version": "1.5.0", "results": results}, indent=2) + "\n"


def _run(text: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(_SCRIPT)], input=text, capture_output=True, text=True
    )


def test_single_clean_report_passes():
    proc = _run(_doc({}))
    assert proc.returncode == 0, proc.stdout
    assert "No secrets detected (1 scan chunk)" in proc.stdout


def test_concatenated_clean_reports_pass():
    text = _doc({}) + _doc({})
    assert len(report.parse_reports(text)) == 2
    proc = _run(text)
    assert proc.returncode == 0, proc.stdout
    assert "2 scan chunks" in proc.stdout


def test_finding_in_a_later_chunk_fails():
    text = _doc({}) + _doc({"src/mcp/app/x.py": [_HIT]})
    assert report.findings(text) == {"src/mcp/app/x.py": [_HIT]}
    proc = _run(text)
    assert proc.returncode == 1
    assert "src/mcp/app/x.py:7 - Secret Keyword" in proc.stdout


def test_findings_for_one_file_merge_across_chunks():
    other = {**_HIT, "line_number": 12}
    text = _doc({"a.py": [_HIT]}) + _doc({"a.py": [other]})
    assert report.findings(text) == {"a.py": [_HIT, other]}


def test_empty_output_fails():
    proc = _run("")
    assert proc.returncode == 1
    assert "no report" in proc.stdout
