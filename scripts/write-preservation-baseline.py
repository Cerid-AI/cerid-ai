#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Parse a preservation JUnit XML file → `tests/eval/baselines/preservation.json`.

Wired into ``preservation`` CI job after the pytest invocation produces
``preservation-results.xml`` (see ``.github/workflows/ci.yml``). The
resulting JSON file is the data source for the ``preservation_health``
component of ``app.services.trust_score`` — without it the component
reports ``not_available`` and the system trust score is computed from
only the available components (still honest, just lower coverage).

JSON shape::

    {
      "passed": int,
      "failed": int,
      "skipped": int,
      "total": int,
      "last_run_at": "<ISO-8601 UTC>",
      "git_sha": "g<short>",
      "source": "ci" | "local"
    }

Trust-score reader (``_read_preservation``) uses ``passed / total``
as the [0, 1] value. ``passed == total`` lights the component as
``status='ok'`` against a target of ``1.0``.

Usage::

    # CI flow — after pytest emits preservation-results.xml
    python3 scripts/write-preservation-baseline.py \
        --junit-xml preservation-results.xml \
        --source ci

    # Local dev — same XML location after `make preservation-check`
    python3 scripts/write-preservation-baseline.py \
        --junit-xml src/mcp/preservation-results.xml \
        --source local
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


# Output target — must match ``trust_score._PRESERVATION_PATH``.
_REPO_ROOT = Path(__file__).resolve().parents[1]
_OUTPUT = _REPO_ROOT / "src" / "mcp" / "tests" / "eval" / "baselines" / "preservation.json"


def _git_sha_short() -> str:
    """Capture the current HEAD's short SHA for traceability.

    Prefixed ``g`` (the git-describe convention) so the stored value is
    never a fully-hex token — a bare 9-char short sha with all-distinct
    characters crosses detect-secrets' hex-entropy threshold and fails
    the security gate as a false positive (sha lottery: some shas pass,
    some don't)."""
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=_REPO_ROOT, stderr=subprocess.DEVNULL,
        )
        return "g" + out.decode().strip()
    except Exception:
        return "unknown"


def _parse_junit(path: Path) -> dict[str, int]:
    """Read a JUnit XML and return ``{passed, failed, skipped, total}``."""
    tree = ET.parse(path)
    root = tree.getroot()

    # JUnit XML can have either a top-level ``<testsuite>`` or a
    # ``<testsuites>`` wrapper containing multiple ``<testsuite>``s.
    # Sum across all testsuite elements found.
    suites = root.findall(".//testsuite")
    if root.tag == "testsuite":
        suites = [root]

    total = errors = failures = skipped = 0
    for s in suites:
        total += int(s.get("tests", 0))
        errors += int(s.get("errors", 0))
        failures += int(s.get("failures", 0))
        skipped += int(s.get("skipped", 0))
    failed = errors + failures
    passed = max(0, total - failed - skipped)
    return {
        "passed": passed,
        "failed": failed,
        "skipped": skipped,
        "total": total,
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="Write preservation.json from JUnit XML")
    ap.add_argument("--junit-xml", required=True, help="Path to preservation-results.xml")
    ap.add_argument(
        "--source", default="ci",
        choices=["ci", "local"],
        help="Marker for who wrote this baseline",
    )
    ap.add_argument(
        "--output", default=str(_OUTPUT),
        help="Output JSON path (default: src/mcp/tests/eval/baselines/preservation.json)",
    )
    args = ap.parse_args()

    junit = Path(args.junit_xml)
    if not junit.exists():
        print(f"ERROR: JUnit XML not found at {junit}", file=sys.stderr)
        return 2

    counts = _parse_junit(junit)
    payload = {
        **counts,
        "last_run_at": datetime.now(timezone.utc).isoformat(),
        "git_sha": _git_sha_short(),
        "source": args.source,
    }

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"wrote {out_path}: passed={counts['passed']} failed={counts['failed']} total={counts['total']}")
    # CI gate hook: fail the writer if any preservation test failed —
    # the regular pytest invocation already fails the job in that case,
    # but a defensive secondary signal is cheap.
    if counts["failed"] > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
