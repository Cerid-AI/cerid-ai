# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A suppression with no expiry is a decision nobody revisits.

Every gate in this repo can be silenced: pip-audit takes ``--ignore-vuln``,
Trivy takes a .trivyignore, dependabot.yml takes ``ignore:``. Each silence was
correct on the day it was written. None of them expire on their own.

This gate enforces the convention the repo already uses — ``Re-eval
YYYY-MM-DD`` in the comment above a suppression — and fails when that date has
passed. It exists because the convention was being written and then not
honoured: on 2026-08-31 four Trivy suppressions were already past their
re-eval date, two of them by two months, and nothing had said so.

Testing the expired ones rather than extending them found SEVEN that were
dead — three Trivy CVEs Debian had since fixed, four pip-audit findings that
no longer fire at all. They had been suppressing nothing, which is the
expensive kind of stale: it looks like diligence and reads like coverage.

Related but distinct from lint-dependency-currency.py. That gate asks whether
a dependency is WATCHED. This one asks whether a decision to look away is
still justified. `src/mcp` was watched the entire time its chromadb `ignore`
rule sat there with a reason that had stopped being true.

TWO CHECKS

  EXPIRED   any ``Re-eval <date>`` in a suppression file whose date has passed
  UNDATED   any suppression entry with no re-eval date at all, unless it is
            in the baseline below — which may only SHRINK

Usage:
    python scripts/lint-suppression-expiry.py
    python scripts/lint-suppression-expiry.py --list
    python scripts/lint-suppression-expiry.py --update-baseline
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
BASELINE = REPO / "scripts" / "suppression_undated_baseline.txt"

#: Files whose suppression entries this gate governs.
SOURCES = [
    Path("scripts/ci/docker-gate.sh"),
    Path("scripts/audit-python-deps.sh"),
    Path(".github/dependabot.yml"),
]

_ENTRY_RE = re.compile(
    # a CVE/GHSA/PYSEC id alone on a line (trivy list, pip-audit IGNORES), or a
    # dependabot `ignore:` entry. The second shape is the one the chromadb
    # muzzle lived in: `src/mcp` was watched the whole time, but a rule whose
    # stated reason had stopped being true kept a core dependency out of every
    # update cycle. Coverage gates cannot see that; only expiry can.
    r"^\s*(?:-\s*dependency-name:\s*[\"']([^\"']+)[\"']"
    r"|(CVE-\d{4}-\d+|GHSA-[\w-]+|PYSEC-\d{4}-\d+)\s*$)"
)
_DATE_RE = re.compile(r"[Rr]e-eval:?\s*(\d{4})-(\d{2})-(\d{2})")


def _entries() -> list[tuple[Path, int, str, dt.date | None]]:
    """Every suppression entry with the re-eval date from its comment block."""
    out: list[tuple[Path, int, str, dt.date | None]] = []
    for rel in SOURCES:
        path = REPO / rel
        if not path.exists():
            continue
        lines = path.read_text().split("\n")
        for i, line in enumerate(lines):
            m = _ENTRY_RE.match(line)
            if not m:
                continue
            ident_txt = m.group(1) or m.group(2)
            # Two shapes in this repo. docker-gate.sh documents each entry in
            # the comment block directly above it. audit-python-deps.sh keeps a
            # keyed table far above the IGNORES=( array, so walking up finds
            # nothing — the entry must be located by its ID anywhere in a
            # comment. Getting this wrong silently reclassifies every dated pip
            # suppression as undated, which is how the first version of this
            # gate reported OK on a date set to 2026-01-15.
            block = []
            j = i - 1
            # Walk up to the documenting comment block. In dependabot.yml the
            # comment sits above the whole `ignore:` group, with structural
            # lines between it and the second/third entry, so those are
            # stepped over rather than treated as a boundary — otherwise only
            # the first entry in a group is ever seen as documented.
            while j >= 0:
                s = lines[j].strip()
                if s.startswith("#"):
                    block.append(lines[j])
                elif block:
                    break
                elif s.startswith(("- dependency-name:", "update-types:", "ignore:", "-")) or not s:
                    pass
                else:
                    break
                j -= 1
            if not any(_DATE_RE.search(b) for b in block):
                ident = ident_txt
                for k, other in enumerate(lines):
                    if other.lstrip().startswith("#") and ident in other:
                        block = [other]
                        k += 1
                        while k < len(lines) and re.match(r"^#\s{12,}\S", lines[k]):
                            block.append(lines[k])
                            k += 1
                        break
            # A date only counts if the comment block plausibly covers THIS
            # entry: either it sits immediately above it, or it names it.
            # Without that, an entry slipped into an already-dated `ignore:`
            # group silently inherits a justification written for something
            # else — which is the same rot as a stale reason, just acquired by
            # proximity instead of by age. Verified: adding an undated
            # `dependency-name` to the python group is now caught.
            adjacent = i > 0 and lines[i - 1].strip().startswith("#")
            names_it = any(ident_txt.lower() in b.lower() for b in block)
            if not (adjacent or names_it):
                block = []
            date = None
            for b in block:
                d = _DATE_RE.search(b)
                if d:
                    try:
                        date = dt.date(int(d.group(1)), int(d.group(2)), int(d.group(3)))
                    except ValueError:
                        pass
                    break
            out.append((rel, i + 1, ident_txt, date))
    return out


def _baseline() -> set[str]:
    if not BASELINE.exists():
        return set()
    return {
        l.strip()
        for l in BASELINE.read_text().split("\n")
        if l.strip() and not l.startswith("#")
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--update-baseline", action="store_true")
    args = ap.parse_args()

    entries, today, base = _entries(), dt.date.today(), _baseline()

    if args.list:
        for rel, ln, ident, date in entries:
            state = "undated" if date is None else ("EXPIRED" if date < today else str(date))
            print(f"  {state:>10}  {ident:<22} {rel}:{ln}")
        return 0

    undated = sorted({e[2] for e in entries if e[3] is None})
    if args.update_baseline:
        BASELINE.write_text(
            "# Suppressions that carry no `Re-eval <date>`, grandfathered by\n"
            "# scripts/lint-suppression-expiry.py. This list may only SHRINK: give an\n"
            "# entry a re-eval date and delete it from here. Regenerating it to absorb\n"
            "# a NEW undated suppression defeats the gate.\n"
            + "".join(f"{u}\n" for u in undated)
        )
        print(f"[suppression-expiry] baseline written with {len(undated)} entr(ies)")
        return 0

    errors: list[str] = []
    for rel, ln, ident, date in entries:
        if date is not None and date < today:
            errors.append(
                f"{ident} ({rel}:{ln}) expired {date} — re-evaluate it. If it no longer "
                f"fires, DELETE it; seven were dead on 2026-08-31."
            )
    for ident in undated:
        if ident not in base:
            errors.append(
                f"{ident} has no `Re-eval <date>` in its comment block — a suppression "
                f"with no expiry is never revisited"
            )
    stale_base = sorted(base - set(undated))
    for ident in stale_base:
        errors.append(f"{ident} is in the undated baseline but is now dated or gone — remove it from {BASELINE.name}")

    if errors:
        for e in errors:
            print(f"::error::[suppression-expiry] {e}")
        print(f"[suppression-expiry] {len(errors)} problem(s)")
        return 1

    dated = sum(1 for e in entries if e[3] is not None)
    print(
        f"[suppression-expiry] OK — {len(entries)} suppression(s): {dated} dated and current, "
        f"{len(base)} grandfathered undated (may only shrink)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
