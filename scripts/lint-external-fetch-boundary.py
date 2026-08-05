#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Ratchet gate: raw redirect-following external fetches may only decrease.

The audit's Cluster-3 done-signal ("forbid external HTTP outside the hardened
layer") in enforceable form. AST alone can't tell an external httpx call from an
internal one (Chroma/Ollama/self-calls use httpx too), so a blanket "no httpx"
gate would be pure noise. Instead this targets the SHARP, SSRF-amplifying signal:
``follow_redirects=True`` on a raw httpx call.

Why that signal: the ONE sanctioned way to follow redirects on an outbound fetch
is ``core/ingest/sources/safe_fetch.guarded_get`` — it disables auto-redirects
and re-validates every hop against the SSRF blocklist. Any other code passing
``follow_redirects=True`` follows attacker-controllable 3xx targets with no
per-hop check. Internal-infra calls essentially never need it, so the false-
positive rate is low. New code that must follow redirects should route through
``guarded_get``; a genuinely-safe fixed-endpoint exception is added to the
baseline with review.

RATCHET (per-file counts, matching lint-magic-numbers.py): freezes today's count
per file and fails CI when any file EXCEEDS its baseline or a NEW file appears.
Shrink-only — migrate a site onto guarded_get, run --update, the baseline drops
and can never climb. Phase 5 burns it toward zero.

Per-file counts (not a single total, not file:line): a total lets a new bad call
hide behind a removal elsewhere; line-level entries churn on every edit. Counts
are line-stable AND close the swap loophole at file granularity.

Tests are excluded (fixtures/mocks legitimately set the flag).

Usage:
    python scripts/lint-external-fetch-boundary.py          # report vs baseline
    python scripts/lint-external-fetch-boundary.py --check  # CI gate
    python scripts/lint-external-fetch-boundary.py --update # reseed baseline
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BASELINE_PATH = REPO_ROOT / "scripts" / "external_fetch_baseline.txt"
SCAN_ROOT = REPO_ROOT / "src" / "mcp"
# The hardened layer is the sanctioned redirect-follower (uses follow_redirects
# =False + per-hop revalidation); never counted even if it referenced the flag.
_EXCLUDE_FILES = {"core/ingest/sources/safe_fetch.py"}


def _counts_in_file(path: Path) -> int:
    """Number of ast.Call nodes passing ``follow_redirects=True`` (constant)."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, UnicodeDecodeError):
        return 0
    n = 0
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if (
                kw.arg == "follow_redirects"
                and isinstance(kw.value, ast.Constant)
                and kw.value.value is True
            ):
                n += 1
    return n


def current_counts() -> Counter[str]:
    counts: Counter[str] = Counter()
    for path in SCAN_ROOT.rglob("*.py"):
        rel = path.resolve().relative_to(REPO_ROOT).as_posix()
        if "/tests/" in f"/{rel}" or rel in _EXCLUDE_FILES:
            continue
        n = _counts_in_file(path)
        if n:
            counts[rel] = n
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
        "# Per-file raw follow_redirects=True (external-fetch) baseline — SHRINK-ONLY.",
        "# Route new redirect-following fetches through core.ingest.sources.safe_fetch.",
        "# Regenerate after burn-down: python scripts/lint-external-fetch-boundary.py --update",
        "#",
        "# Reviewed residual (Phase 5, 2026-07-02): the two arbitrary/operator-URL",
        "# fetches were migrated to guarded_get(_sync); every entry below is a",
        "# FIXED, TRUSTED endpoint documented inline at its call site with a",
        "# `# follow_redirects:` note — knowledge-pack build-time adapters",
        "# (api.fda.gov, ods.od.nih.gov, gutenberg.org, MediaWiki API,",
        "# docs.python.org, dumps.wikimedia.org, GitHub codeload) plus fixed-host",
        "# release/model downloads (GitHub releases, HuggingFace) and the pooled",
        "# external-API client. Several validate `https://` up front. The streamed",
        "# downloads exceed guarded_get's 8MB in-memory cap, so they stay raw by",
        "# design. Not debt — reviewed exceptions; the ratchet keeps them shrink-only.",
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
        print(
            f"external-fetch baseline updated: {sum(counts.values())} "
            f"follow_redirects=True call(s) across {len(counts)} files"
        )
        return 0

    baseline = load_baseline()
    regressions: list[str] = []
    for rel, n in sorted(counts.items()):
        allowed = baseline.get(rel, 0)
        if n > allowed:
            regressions.append(f"  {rel}: {n} (baseline {allowed}, +{n - allowed})")

    cur_total, base_total = sum(counts.values()), sum(baseline.values())
    if regressions:
        print("External-fetch boundary FAILED — new raw follow_redirects=True above baseline:")
        print("\n".join(regressions))
        print(
            "\nRoute the fetch through core.ingest.sources.safe_fetch.guarded_get "
            "(SSRF-guarded, per-hop redirect revalidation). If it is a genuinely-safe "
            "fixed endpoint, run --update to accept a baseline (reviewed).\n"
            f"Never let the baseline climb. total now={cur_total}, baseline={base_total}."
        )
        return 1

    stale = base_total - cur_total
    msg = f"external-fetch boundary OK: {cur_total} raw follow_redirects=True (baseline {base_total})"
    if stale > 0:
        msg += f" — {stale} migrated since last reseed; run --update to ratchet lower"
    print(msg)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
