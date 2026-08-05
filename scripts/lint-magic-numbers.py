#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Ratchet gate: production magic numbers (ruff PLR2004) may only decrease.

Graduates the ``PLR2004`` sprint from tasks/2026-06-29-rag-api-systemic-audit.md
(§5a). The disease: 2,359 magic-value comparisons, of which ~2,035 are in tests
(legitimate — fixtures, expected values, thresholds under test) and 324 are in
production. Bolting ``PLR2004`` straight into ``ruff --select`` would fail CI on
all 324 at once; a bulk ``--add-noqa`` would pollute 324 source lines without
removing a single magic number. Neither is acceptable.

So this is a RATCHET, matching the program's other gates
(``lint-retrieval-import-boundary.py`` et al.): it freezes a PER-FILE count of
today's production PLR2004 violations and fails CI when any file EXCEEDS its
baseline — or when a NEW file introduces a magic number (baseline 0). The
number can only go down: fix magic numbers in a file, run ``--update``, the
baseline ratchets lower and can never climb back. Phase 5 burns the baseline to
empty for 1.0.

Why per-file counts (not a single total, not a line allowlist): a single total
lets a new magic number in file A hide behind a removal in file B. A line-level
allowlist (file:line) churns on every unrelated edit as line numbers shift.
Per-file counts are line-stable AND close the swap loophole at file granularity.

Tests are excluded by design — magic numbers in ``tests/`` are the point.

Usage:
    python scripts/lint-magic-numbers.py            # report current vs baseline
    python scripts/lint-magic-numbers.py --check    # CI gate (non-zero on regress)
    python scripts/lint-magic-numbers.py --update    # reseed baseline (after burn-down)
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "scripts" / "magic_number_baseline.txt"
SCAN_ROOT = REPO_ROOT / "src" / "mcp"
_RUFF = Path(sys.executable).parent / "ruff"


def current_counts() -> Counter[str]:
    """Per-file production PLR2004 counts (paths relative to repo root)."""
    ruff = str(_RUFF) if _RUFF.exists() else "ruff"
    proc = subprocess.run(
        [
            ruff, "check", str(SCAN_ROOT),
            "--exclude", "tests",
            "--select", "PLR2004",
            "--output-format", "json",
            "--no-cache",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    # ruff exits non-zero when violations exist; JSON is still on stdout.
    try:
        rows = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        sys.stderr.write(proc.stderr or "ruff produced no parseable JSON\n")
        raise SystemExit(2) from None
    counts: Counter[str] = Counter()
    for row in rows:
        rel = Path(row["filename"]).resolve().relative_to(REPO_ROOT).as_posix()
        counts[rel] += 1
    return counts


def load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    baseline: dict[str, int] = {}
    for line in BASELINE_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        count, _, rel = line.partition("\t")
        baseline[rel.strip()] = int(count)
    return baseline


def write_baseline(counts: Counter[str]) -> None:
    total = sum(counts.values())
    lines = [
        "# Per-file production PLR2004 (magic-number) baseline — SHRINK-ONLY.",
        "# Regenerate after burn-down: python scripts/lint-magic-numbers.py --update",
        "#",
        "# Reviewed residual (Phase 5, 2026-07-02): HTTP status codes were extracted",
        "# to http.HTTPStatus and threshold DUPLICATES were pointed at their existing",
        "# config constants (NLI_*_THRESHOLD, QUALITY_TIER_*). What remains is",
        "# deliberately-inline SELF-EVIDENT literals — tiny ints (0/1/2/3…), epsilons",
        "# (1e-9, 1e-12), simple ratios (0.5), local input-length/word caps",
        "# (len(x) > N), sanity bounds (month > 12, uint16 0xFFFF), and assertion",
        "# counts — where a named constant would add indirection, not clarity",
        "# (Phase-0 assessment, tasks/2026-06-30-sprint-plr-magic-numbers.md). The",
        "# ratchet keeps them shrink-only; extract opportunistically, never regress.",
        f"# total={total} files={len(counts)}",
    ]
    lines += [f"{counts[rel]}\t{rel}" for rel in sorted(counts)]
    BASELINE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="CI gate: fail on regression")
    ap.add_argument("--update", action="store_true", help="reseed the baseline")
    args = ap.parse_args()

    counts = current_counts()

    if args.update:
        write_baseline(counts)
        print(f"magic-number baseline updated: {sum(counts.values())} violations across {len(counts)} files")
        return 0

    baseline = load_baseline()
    regressions: list[str] = []
    for rel, n in sorted(counts.items()):
        allowed = baseline.get(rel, 0)
        if n > allowed:
            regressions.append(f"  {rel}: {n} magic numbers (baseline {allowed}, +{n - allowed})")

    cur_total, base_total = sum(counts.values()), sum(baseline.values())
    if regressions:
        print("Magic-number ratchet FAILED — new PLR2004 comparisons above baseline:")
        print("\n".join(regressions))
        print(
            "\nExtract the value to a named constant (config/constants.py) or, if it is "
            "genuinely self-evident, keep it and run --update to accept a LOWER baseline.\n"
            f"Never let the baseline climb. total now={cur_total}, baseline={base_total}."
        )
        return 1

    stale = base_total - cur_total
    msg = f"magic-number ratchet OK: {cur_total} production violations (baseline {base_total})"
    if stale > 0:
        msg += f" — {stale} burned down since last reseed; run --update to ratchet the baseline lower"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
