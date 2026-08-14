#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
"""One-time audit: find useQuery-data sites that touch nested fields without a
null-fallback. Catches three common crash classes surfaced by the
PaneErrorBoundary (Cluster A) once it stopped swallowing errors silently:

1. ``data.map(...)`` / ``.filter(...)`` / ``.reduce(...)`` — original cluster
   from rc2 (``t.map is not a function``).
2. ``data.field.count`` / ``.length`` / ``.size`` — nested numeric reads
   where the inner object can be absent (Knowledge Digest, rc2.1).
3. ``Object.keys(data.field)`` / ``Object.entries(...)`` / ``Object.values(...)``
   — Object reflection on a possibly-undefined member (Health Cards, rc2.1).

To keep the false-positive rate workable, the .count/.length/.size and
Object.keys/entries/values patterns are scoped to **identifiers that the
file destructures out of a useQuery call** (``const { data: digest } =
useQuery(...)`` or the default-name ``const { data } = ...``). The
map/filter/reduce pattern stays broad — it matches the original audit's
behavior and catches the t.map crash class even on derived names.

A line is considered safe when it has one of the SAFE markers (e.g.
``?? []``, ``Array.isArray(``, ``safeArray(``, ``audit-allowed``), or
when the access is fully optional-chained (``digest?.artifacts?.count``).
"""
from __future__ import annotations
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent / "src" / "web" / "src" / "components"

# ---------------------------------------------------------------------------
# Pattern catalog
# ---------------------------------------------------------------------------
# Broad: any identifier followed by .map/.filter/.reduce — original cluster.
UNSAFE_MAP = re.compile(r'\b(\w+)(?:\?\.[\w.]+)?\.(?:map|filter|reduce)\(')

# Nested numeric read on a *named* data identifier (filled in below).
def _nested_numeric(name: str) -> re.Pattern[str]:
    return re.compile(rf'\b{re.escape(name)}\.(\w+)\.(?:count|length|size)\b')

# Object reflection on a nested member of a *named* data identifier.
def _object_reflection(name: str) -> re.Pattern[str]:
    return re.compile(rf'\bObject\.(?:keys|entries|values)\(\s*{re.escape(name)}\.(\w+)\b')

# ``const { data: NAME } = useQuery`` and ``const { data, … } = useQuery``
# destructure capture. The second variant captures the literal ``data``.
DATA_RENAME = re.compile(r'\bdata\s*:\s*(\w+)\b')
DATA_DEFAULT = re.compile(r'\{\s*[^}]*\bdata\b[^}]*\}\s*=\s*useQuery')

SAFE: tuple[str, ...] = (
    "?? []",
    "?? {}",
    "?? 0",
    "Array.isArray(",
    "safeArray(",
    "audit-allowed",
)


def _data_names_in(text: str) -> set[str]:
    """Return the set of identifiers this file destructures out of useQuery."""
    names: set[str] = set()
    # The classic ``{ data: foo } = useQuery`` form.
    for block_start in re.finditer(r'useQuery\b', text):
        # Scan back ~200 chars from the useQuery call to find the destructure
        # that feeds it.
        chunk = text[max(0, block_start.start() - 300) : block_start.start()]
        for m in DATA_RENAME.finditer(chunk):
            names.add(m.group(1))
        if DATA_DEFAULT.search(chunk + "useQuery"):
            names.add("data")
    return names


# ---------------------------------------------------------------------------
# Walk
# ---------------------------------------------------------------------------
def _find_violations() -> list[str]:
    violations: list[str] = []
    for tsx in sorted(ROOT.rglob("*.tsx")):
        text = tsx.read_text(encoding="utf-8")
        if "useQuery" not in text:
            continue
        data_names = _data_names_in(text)
        nested_patterns = [(name, _nested_numeric(name)) for name in data_names]
        object_patterns = [(name, _object_reflection(name)) for name in data_names]

        for n, line in enumerate(text.splitlines(), 1):
            if "useQuery" in line or any(s in line for s in SAFE):
                continue

            # (1) Broad map/filter/reduce.
            if UNSAFE_MAP.search(line):
                violations.append(
                    f"{tsx.relative_to(ROOT.parent.parent)}:{n}: [map/filter/reduce] {line.strip()}"
                )
                continue

            # (2) Nested .count/.length/.size on a known data identifier.
            flagged = False
            for name, pattern in nested_patterns:
                m = pattern.search(line)
                if m and f"{name}?.{m.group(1)}" not in line:
                    violations.append(
                        f"{tsx.relative_to(ROOT.parent.parent)}:{n}: "
                        f"[nested .count/.length/.size on {name}] {line.strip()}"
                    )
                    flagged = True
                    break
            if flagged:
                continue

            # (3) Object.keys/entries/values on a known data identifier.
            for name, pattern in object_patterns:
                m = pattern.search(line)
                if m and f"{name}?.{m.group(1)}" not in line:
                    violations.append(
                        f"{tsx.relative_to(ROOT.parent.parent)}:{n}: "
                        f"[Object.keys/entries/values on {name}] {line.strip()}"
                    )
                    break
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="CI gate mode — same scan either way; matches the sibling "
        "lint-*.py scripts' calling convention (warn-only in gates.yaml "
        "until the existing map/filter/reduce backlog is triaged).",
    )
    parser.parse_args()

    violations = _find_violations()
    if violations:
        print(f"\n[audit-usequery-map] {len(violations)} candidate sites:\n")
        for v in violations:
            print(f"  {v}")
        return 1
    print("[audit-usequery-map] clean.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
