#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
"""One-time audit: find useQuery + .map/.filter/.reduce sites without null-fallback."""
from __future__ import annotations
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent / "src" / "web" / "src" / "components"
# Find a bare identifier (or optional-chained member access) followed by .map / .filter / .reduce.
# Python `re` does not allow variable-width lookbehind, so the safe-context filter
# is applied separately on the surrounding line text.
UNSAFE = re.compile(r'\b(\w+)(?:\?\.[\w.]+)?\.(?:map|filter|reduce)\(')
SAFE = ("?? []", "Array.isArray(", "safeArray(", "audit-allowed")

violations: list[str] = []
for tsx in sorted(ROOT.rglob("*.tsx")):
    text = tsx.read_text(encoding="utf-8")
    if "useQuery" not in text:
        continue
    for n, line in enumerate(text.splitlines(), 1):
        if "useQuery" in line or any(s in line for s in SAFE):
            continue
        if UNSAFE.search(line):
            violations.append(f"{tsx.relative_to(ROOT.parent.parent)}:{n}: {line.strip()}")

if violations:
    print(f"\n[audit-usequery-map] {len(violations)} candidate sites:\n")
    for v in violations:
        print(f"  {v}")
    sys.exit(1)
print("[audit-usequery-map] clean.")
sys.exit(0)
