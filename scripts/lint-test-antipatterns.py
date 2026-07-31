#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Block test shapes that make a test pass without testing anything.

Each rule here graduated from a real incident recorded in ``tasks/lessons.md``.
They are grouped because they share one failure mode: the test goes green, the
suite count goes up, and the thing it claims to cover is unprotected.

    TA001  asyncio.get_event_loop() in a test body
           Passes in isolation, raises "There is no current event loop" once any
           earlier test closes the loop. Bit us twice (trust-state, Tephra
           contradiction tests). Use asyncio.run(). — lessons 2026-06-11

    TA002  importlib.reload() of a re-export bridge package
           Reloading re-snapshots every `import *` source module's CURRENT
           state, laundering in-session global mutation (taxonomy extension,
           runtime domain registration) into package attrs for every later test.
           Reload the leaf module and re-sync the one attr you touched.
           — lessons 2026-07-14

    TA003  patch() of a whole config module
           Replaces the module with a MagicMock, so every attribute the test did
           not set is also a MagicMock. Comparisons against ints raise, the code
           under test swallows the error, and the test asserts an envelope shape
           that was produced without the logic ever running. Use
           monkeypatch.setattr(mod.config, NAME, value) per setting.
           — lessons 2026-07-30

    TA004  importing the symbol you just patched, inside the patch block
           The tautology shape: patch "m.f", then `from m import f`, then call
           f() and assert the fixture you supplied. Exercises unittest.mock and
           nothing else. Three of these sat in test_e2e_pipeline.py's
           TestVerificationPipeline. — lessons 2026-07-30

Suppress a deliberate exception on the offending line:

    # lint-test-antipatterns: allow TA003 — asserting the mock wiring itself

**Shrink-only ratchet.** The rules were written after the fact, so the suite
starts with a residue (151 at seeding: TA003×124, TA004×14, TA001×10, TA002×3).
A per-file baseline makes the gate blocking for NEW code — a new test file has
an implicit baseline of 0 — while letting the existing residue burn down
opportunistically. Counts can never climb. Same convention as
``lint-magic-numbers.py``.

Usage:
    python scripts/lint-test-antipatterns.py           # report vs baseline
    python scripts/lint-test-antipatterns.py --check   # CI gate (fail on regress)
    python scripts/lint-test-antipatterns.py --update  # reseed after burn-down
    python scripts/lint-test-antipatterns.py --list    # print every finding
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
DEFAULT_ROOTS = [REPO / "src" / "mcp" / "tests"]
BASELINE_PATH = REPO / "scripts" / "test_antipattern_baseline.txt"

# Packages whose module objects are re-export bridges (`from x import *`).
# Reloading these is the laundering hazard in TA002.
_BRIDGE_MODULES = {"config", "app.tools", "core.utils"}

_SUPPRESS = "lint-test-antipatterns: allow"


class Finding:
    def __init__(self, path: Path, line: int, code: str, message: str):
        self.path, self.line, self.code, self.message = path, line, code, message

    def __str__(self) -> str:
        rel = self.path.relative_to(REPO)
        return f"{rel}:{self.line}: {self.code} {self.message}"


def _patch_target(node: ast.Call) -> str | None:
    """Return the dotted string target of a patch()/patch.object() call."""
    func = node.func
    name = (
        func.attr if isinstance(func, ast.Attribute)
        else func.id if isinstance(func, ast.Name)
        else None
    )
    if name != "patch":
        return None
    if not node.args:
        return None
    first = node.args[0]
    return first.value if isinstance(first, ast.Constant) and isinstance(first.value, str) else None


class _Visitor(ast.NodeVisitor):
    def __init__(self, path: Path, lines: list[str]):
        self.path, self.lines = path, lines
        self.findings: list[Finding] = []

    # -- helpers ------------------------------------------------------
    def _suppressed(self, lineno: int, code: str) -> bool:
        line = self.lines[lineno - 1] if 0 < lineno <= len(self.lines) else ""
        return _SUPPRESS in line and code in line

    def _add(self, node: ast.AST, code: str, message: str) -> None:
        lineno = getattr(node, "lineno", 0)
        if not self._suppressed(lineno, code):
            self.findings.append(Finding(self.path, lineno, code, message))

    # -- rules --------------------------------------------------------
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func

        # TA001 — asyncio.get_event_loop()
        if isinstance(func, ast.Attribute) and func.attr == "get_event_loop":
            self._add(
                node, "TA001",
                "asyncio.get_event_loop() in a test — passes alone, raises "
                "'no current event loop' after any earlier test closes it. "
                "Use asyncio.run(coro).",
            )

        # TA002 — importlib.reload(<bridge package>)
        if isinstance(func, ast.Attribute) and func.attr == "reload" and node.args:
            target = node.args[0]
            dotted = _dotted(target)
            if dotted in _BRIDGE_MODULES:
                self._add(
                    node, "TA002",
                    f"importlib.reload({dotted}) re-snapshots every `import *` "
                    "source at its CURRENT state, laundering in-session global "
                    "mutation into package attrs for all later tests. Reload the "
                    "leaf module and re-sync the single attr you touched.",
                )

        # TA003 — patch() of a whole config module
        target = _patch_target(node)
        if target and (target == "config" or target.endswith(".config")):
            self._add(
                node, "TA003",
                f'patch("{target}") replaces the whole module with a MagicMock, '
                "so every attribute you did not set is also a MagicMock; "
                "comparisons against numbers raise and the code under test "
                "silently stops running. Use monkeypatch.setattr(mod.config, "
                "NAME, value) per setting.",
            )

        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._check_patch_then_import(node)
        self.generic_visit(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._check_patch_then_import(node)
        self.generic_visit(node)

    def _check_patch_then_import(self, fn: ast.AST) -> None:
        """TA004 — the tautology: import the symbol you just patched."""
        patched: dict[tuple[str, str], int] = {}
        for sub in ast.walk(fn):
            if isinstance(sub, ast.Call):
                target = _patch_target(sub)
                if target and "." in target:
                    module, _, symbol = target.rpartition(".")
                    patched[(module, symbol)] = sub.lineno

        if not patched:
            return

        for sub in ast.walk(fn):
            if not isinstance(sub, ast.ImportFrom) or not sub.module:
                continue
            for alias in sub.names:
                key = (sub.module, alias.name)
                if key in patched:
                    self._add(
                        sub, "TA004",
                        f"`from {sub.module} import {alias.name}` inside a test "
                        f"that patches \"{sub.module}.{alias.name}\" (line "
                        f"{patched[key]}) — the call resolves to the mock, so "
                        "the assertion checks the fixture the test supplied. "
                        "Exercise the real caller instead, or patch the leaf "
                        "dependency rather than the unit under test.",
                    )


def _dotted(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _dotted(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def scan(path: Path) -> list[Finding]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (OSError, SyntaxError):
        return []
    visitor = _Visitor(path, source.splitlines())
    visitor.visit(tree)
    return visitor.findings


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


def write_baseline(counts: Counter[str], by_code: Counter[str]) -> None:
    total = sum(counts.values())
    codes = ", ".join(f"{c}×{n}" for c, n in sorted(by_code.items()))
    lines = [
        "# Per-file test-antipattern baseline — SHRINK-ONLY.",
        "# Regenerate after burn-down: "
        "python scripts/lint-test-antipatterns.py --update",
        "#",
        "# These rules were written after the fact (2026-07-30 graduation sweep),",
        "# so the suite carries a residue. A new test file has an implicit",
        "# baseline of 0, so the gate is blocking for new code while the residue",
        "# burns down opportunistically. Counts can never climb.",
        "#",
        "# SEED THIS IN cerid-ai-internal ONLY. The public mirror strips test",
        "# files, so it reports a LOWER count and --check passes there; running",
        "# --update in public would bake that lower number in and make internal",
        "# fail against its own tree.",
        "#",
        "# Highest-value burn-down targets first: TA004 (the tautology — the test",
        "# asserts the fixture it supplied) then TA003 (blanket config mock —",
        "# silently stops the code under test from running). TA001/TA002 are",
        "# order-dependence hazards, cheap to fix.",
        f"# total={total} files={len(counts)} [{codes}]",
    ]
    lines += [f"{counts[rel]}\t{rel}" for rel in sorted(counts)]
    BASELINE_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Block vacuous test shapes.")
    ap.add_argument("--check", action="store_true", help="CI gate: fail on regression")
    ap.add_argument("--update", action="store_true", help="reseed the baseline")
    ap.add_argument("--list", action="store_true", help="print every finding")
    ap.add_argument("paths", nargs="*", help="files or dirs (default: src/mcp/tests)")
    args = ap.parse_args(argv[1:])

    roots = [Path(p).resolve() for p in args.paths] if args.paths else DEFAULT_ROOTS

    findings: list[Finding] = []
    scanned = 0
    for root in roots:
        paths = sorted(root.rglob("test_*.py")) if root.is_dir() else [root]
        for path in paths:
            scanned += 1
            findings.extend(scan(path))

    counts: Counter[str] = Counter()
    by_code: Counter[str] = Counter()
    for f in findings:
        counts[f.path.relative_to(REPO).as_posix()] += 1
        by_code[f.code] += 1

    if args.list or not (args.check or args.update):
        for f in findings:
            print(str(f))

    if args.update:
        write_baseline(counts, by_code)
        print(f"test-antipattern baseline updated: {len(findings)} finding(s) "
              f"across {len(counts)} file(s)")
        return 0

    baseline = load_baseline()
    regressions = [
        f"  {rel}: {n} finding(s) (baseline {baseline.get(rel, 0)}, "
        f"+{n - baseline.get(rel, 0)})"
        for rel, n in sorted(counts.items())
        if n > baseline.get(rel, 0)
    ]

    total, base_total = sum(counts.values()), sum(baseline.values())
    summary = ", ".join(f"{c}×{n}" for c, n in sorted(by_code.items()))

    if regressions:
        print("lint-test-antipatterns: REGRESSION — these files gained "
              "vacuous-test shapes:", file=sys.stderr)
        print("\n".join(regressions), file=sys.stderr)
        print(
            "\nRun `python scripts/lint-test-antipatterns.py --list` for details. "
            "Each rule's rationale is in the module docstring and "
            "tasks/lessons.md. If the shape is genuinely intended, annotate the "
            "line: `# lint-test-antipatterns: allow <CODE> — reason`.",
            file=sys.stderr,
        )
        return 1

    drift = base_total - total
    note = f" (baseline {base_total}, {drift} burned down)" if drift else ""
    print(f"lint-test-antipatterns: OK — {total} finding(s) [{summary}] "
          f"in {scanned} test file(s){note}")
    if drift > 0:
        print("Baseline is stale in your favour — reseed with --update to lock "
              "the improvement in.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
