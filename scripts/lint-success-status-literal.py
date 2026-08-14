#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Gate 4 — Success-status-on-failure (tasks/2026-08-11-consolidated-audit.md § 3).

Rule (the audit's own words): "a literal `success` may not be passed to
`_log_execution` outside a branch conditioned on the run's own counters."
This closes AF-019 (``webhook_drain`` reports success with ``failed>0``),
AF-021 (``source_poll`` reports success with every fetch silently swallowed
and ``ingested=0``), and AF-037 (``ReembedChunksJob`` aborts a domain at the
first bad page and the job worker still marks the run COMPLETED) as one
class, plus two same-class siblings the audit didn't separately number
(``quarantine_purge``, ``folder_scan`` — both compute a failure count and
print it in the same message they hardcode "success" into).

Three call/assignment shapes are treated as "reporting success", scanned
across all of ``src/mcp``:

  1. A call to a ``*_log_execution`` helper (matched on ``log_execution`` in
     the callee name) passing the string literal ``"success"`` as an
     argument.
  2. An attribute assignment ``x.state = SomeState.COMPLETED`` (or
     ``.SUCCESS`` / ``.SUCCEEDED``) where the enum class name ends in
     ``State`` — the enum equivalent of (1), covering job/task state
     machines (``JobState`` et al). Deliberately does NOT match ``*Status``
     enums assigned inside an exception-based try/except with no internal
     failure-counter concept (e.g. ``RunStatus.COMPLETED`` in
     ``workflows.py`` — that mechanism has no counter to ignore, so it is
     not an instance of this defect class).

A hit is a VIOLATION unless the enclosing function conditions it on the
run's own failure signal, checked three ways:

  * GUARD — the call/assignment is lexically inside (or in the ``else`` of)
    an ``if`` whose test references a fail/error-named local, or a
    parameter whose name suggests a job-result object (covers the
    ``if failed_domains: ...error... else: ...success...`` shape that
    ``sync_export`` already uses correctly).
  * COUNTER-IN-MESSAGE (shape 1 only) — a fail/error-named local computed
    in the function is interpolated into the SAME call's arguments (the
    ``f"...failed={failed}..."`` shape) without a guard — the counter is
    right there in the string and still ignored.
  * SILENT-SWALLOW (shape 1 only) — a loop-nested ``except`` handler that
    precedes the call, does not re-raise, and does not mutate any local
    (no counter at all — the AF-021 shape, where failures are invisible
    rather than merely unguarded).

Neither COUNTER-IN-MESSAGE nor SILENT-SWALLOW fires without something to
guard against, so functions with no per-run failure concept at all (most
of the scheduler's ~30 jobs — they succeed or raise, nothing in between)
are correctly out of scope.

Grandfathered via ``scripts/success_status_literal_allowlist.txt``
(path:line, one-line reason per entry referencing an audit finding id).
Shrink-only — an entry for a line that's fixed or no longer a hit is
"stale" and fails ``--check`` until the allowlist is re-synced.

Usage:
    python scripts/lint-success-status-literal.py            # report (exit 0)
    python scripts/lint-success-status-literal.py --check    # CI gate
    python scripts/lint-success-status-literal.py --update   # reseed allowlist
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "success_status_literal_allowlist.txt"
SCAN_ROOT = REPO_ROOT / "src" / "mcp"

_EXEC_LOG_RE = re.compile(r"log_execution", re.IGNORECASE)
_FAILURE_NAME_RE = re.compile(r"fail|error", re.IGNORECASE)
_RESULT_PARAM_RE = re.compile(r"result", re.IGNORECASE)
_SUCCESS_ENUM_MEMBERS = {"COMPLETED", "SUCCESS", "SUCCEEDED"}
_SUCCESS_STR = "success"
_SKIP_DIR_PARTS = {"__pycache__", ".venv", "venv", "node_modules", "tests"}


class Violation(NamedTuple):
    file: str
    lineno: int
    kind: str  # "literal-call" | "enum-assign" | "silent-swallow"
    detail: str

    def key(self) -> str:
        return f"{self.file}:{self.lineno}"


class _FuncScan:
    """Per-function analysis: parent map, failure-signal names, silent excepts."""

    def __init__(self, func: ast.AST) -> None:
        self.func = func
        self.parents: dict[int, ast.AST] = {}
        self._build_parents(func, None)
        self.signal_names = self._collect_signal_names()
        self.outer_handler_ids = self._collect_outer_handler_ids()
        self.silent_loop_excepts = self._collect_silent_loop_excepts()
        self.local_defs = self._collect_local_defs()

    def _build_parents(self, node: ast.AST, parent: ast.AST | None) -> None:
        if parent is not None:
            self.parents[id(node)] = parent
        for child in ast.iter_child_nodes(node):
            self._build_parents(child, node)

    def _collect_signal_names(self) -> set[str]:
        names: set[str] = set()
        for sub in ast.walk(self.func):
            if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Store):
                if _FAILURE_NAME_RE.search(sub.id):
                    names.add(sub.id)
            elif isinstance(sub, ast.AugAssign) and isinstance(sub.target, ast.Name):
                if _FAILURE_NAME_RE.search(sub.target.id):
                    names.add(sub.target.id)
        args = getattr(self.func, "args", None)
        if args is not None:
            for a in list(args.args) + list(args.kwonlyargs):
                if _RESULT_PARAM_RE.search(a.arg):
                    names.add(a.arg)
        return names

    def _collect_local_defs(self) -> dict[str, ast.expr]:
        """Map ``name -> its assigned expression`` for simple ``name = <expr>``
        statements anywhere in the function. Last write wins; adequate for the
        one-hop indirection this gate resolves (``detail = f"...{failed}..."``
        then ``_log_execution(..., detail)``) without full dataflow analysis."""
        defs: dict[str, ast.expr] = {}
        for sub in ast.walk(self.func):
            if (
                isinstance(sub, ast.Assign)
                and len(sub.targets) == 1
                and isinstance(sub.targets[0], ast.Name)
            ):
                defs[sub.targets[0].id] = sub.value
        return defs

    def _collect_outer_handler_ids(self) -> set[int]:
        """Handlers on the function's own top-level try — the catch-all
        error path, not a per-item swallow. Excluded from SILENT-SWALLOW."""
        ids: set[int] = set()
        body = getattr(self.func, "body", [])
        for stmt in body:
            if isinstance(stmt, ast.Try):
                for h in stmt.handlers:
                    ids.add(id(h))
        return ids

    @staticmethod
    def _body_mutates_state(body: list[ast.stmt]) -> bool:
        for stmt in body:
            for sub in ast.walk(stmt):
                if isinstance(sub, (ast.Assign, ast.AugAssign)):
                    return True
        return False

    def _inside_loop(self, node: ast.AST) -> bool:
        n: ast.AST | None = node
        while n is not None and n is not self.func:
            n = self.parents.get(id(n))
            if isinstance(n, (ast.For, ast.AsyncFor, ast.While)):
                return True
        return False

    def _collect_silent_loop_excepts(self) -> list[int]:
        hits: list[int] = []
        for sub in ast.walk(self.func):
            if not isinstance(sub, ast.ExceptHandler):
                continue
            if id(sub) in self.outer_handler_ids:
                continue
            if any(isinstance(s, ast.Raise) for s in ast.walk(sub)):
                continue
            if self._body_mutates_state(sub.body):
                continue
            if not self._inside_loop(sub):
                continue
            hits.append(sub.lineno)
        return hits

    def guarded(self, node: ast.AST) -> bool:
        """True if `node` sits inside an `if` (body or orelse) whose test
        references one of this function's failure-signal names."""
        n: ast.AST | None = node
        while n is not None and n is not self.func:
            parent = self.parents.get(id(n))
            if isinstance(parent, ast.If):
                for sub in ast.walk(parent.test):
                    if isinstance(sub, ast.Name) and sub.id in self.signal_names:
                        return True
            n = parent
        return False

    def counter_in_call(self, call: ast.Call) -> bool:
        for arg in list(call.args) + [kw.value for kw in call.keywords]:
            exprs = [arg]
            if isinstance(arg, ast.Name) and arg.id in self.local_defs:
                exprs.append(self.local_defs[arg.id])  # resolve one hop
            for expr in exprs:
                for sub in ast.walk(expr):
                    if isinstance(sub, ast.Name) and sub.id in self.signal_names:
                        return True
        return False

    def silent_swallow_before(self, lineno: int) -> bool:
        return any(h < lineno for h in self.silent_loop_excepts)


def _enum_class_name(node: ast.expr) -> str | None:
    """For `Foo.COMPLETED`'s value-part `Foo`, return "Foo" if it's a bare
    Name (covers the direct-import shape every current use follows)."""
    if isinstance(node, ast.Name):
        return node.id
    return None


def _find_enclosing_function(parents: dict[int, ast.AST], node: ast.AST) -> ast.AST | None:
    n: ast.AST | None = parents.get(id(node))
    while n is not None and not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
        n = parents.get(id(n))
    return n


def check_file(path: Path) -> list[Violation]:
    try:
        source = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return []
    rel = str(path.relative_to(REPO_ROOT))
    return check_source(source, rel)


def check_source(source: str, rel: str) -> list[Violation]:
    """Analyze a single module's source. Split out from :func:`check_file` so
    tests can feed source strings directly without a REPO_ROOT-relative path."""
    try:
        tree = ast.parse(source, filename=rel)
    except SyntaxError:
        return []

    violations: list[Violation] = []

    # Whole-module parent map so enclosing-function lookup works for both
    # detector passes without rebuilding it per function.
    module_parents: dict[int, ast.AST] = {}

    def _walk_parents(n: ast.AST, p: ast.AST | None) -> None:
        if p is not None:
            module_parents[id(n)] = p
        for c in ast.iter_child_nodes(n):
            _walk_parents(c, n)

    _walk_parents(tree, None)

    func_scans: dict[int, _FuncScan] = {}

    def _scan_for(func: ast.AST) -> _FuncScan:
        if id(func) not in func_scans:
            func_scans[id(func)] = _FuncScan(func)
        return func_scans[id(func)]

    for node in ast.walk(tree):
        # --- Detector: literal "success" passed to a *_log_execution call ---
        if isinstance(node, ast.Call):
            fname = None
            if isinstance(node.func, ast.Name):
                fname = node.func.id
            elif isinstance(node.func, ast.Attribute):
                fname = node.func.attr
            if fname and _EXEC_LOG_RE.search(fname):
                has_literal_success = any(
                    isinstance(a, ast.Constant) and isinstance(a.value, str)
                    and a.value.lower() == _SUCCESS_STR
                    for a in list(node.args) + [kw.value for kw in node.keywords]
                )
                if has_literal_success:
                    func = _find_enclosing_function(module_parents, node)
                    if func is None:
                        continue
                    scan = _scan_for(func)
                    if scan.guarded(node):
                        continue
                    if scan.counter_in_call(node):
                        violations.append(Violation(
                            rel, node.lineno, "literal-call",
                            f"'{fname}' reports success while a failure counter "
                            "(reported in the same message) goes unchecked",
                        ))
                    elif scan.silent_swallow_before(node.lineno):
                        violations.append(Violation(
                            rel, node.lineno, "silent-swallow",
                            f"'{fname}' reports success after a loop-nested except "
                            "that swallows a failure with no counter at all",
                        ))

        # --- Detector: success-enum assignment to a `.state` attribute ---
        elif isinstance(node, ast.Assign):
            if (
                len(node.targets) == 1
                and isinstance(node.targets[0], ast.Attribute)
                and node.targets[0].attr in ("state", "status")
                and isinstance(node.value, ast.Attribute)
                and node.value.attr in _SUCCESS_ENUM_MEMBERS
            ):
                cls_name = _enum_class_name(node.value.value)
                if cls_name and cls_name.endswith("State"):
                    func = _find_enclosing_function(module_parents, node)
                    if func is None:
                        continue
                    scan = _scan_for(func)
                    if scan.guarded(node):
                        continue
                    violations.append(Violation(
                        rel, node.lineno, "enum-assign",
                        f".{node.targets[0].attr} = {cls_name}.{node.value.attr} "
                        "with no reference to the run's own failure signal",
                    ))

    return violations


def iter_py_files(root: Path) -> list[Path]:
    out = []
    for p in root.rglob("*.py"):
        if set(p.parts) & _SKIP_DIR_PARTS:
            continue
        out.append(p)
    return out


def collect_all() -> list[Violation]:
    violations: list[Violation] = []
    for py in iter_py_files(SCAN_ROOT):
        violations.extend(check_file(py))
    return sorted(violations, key=lambda v: (v.file, v.lineno))


def _load_allowlist() -> dict[str, str]:
    """Return ``{"path:lineno": reason}``. Format: ``path:lineno  # reason``."""
    allow: dict[str, str] = {}
    if not ALLOWLIST_PATH.exists():
        return allow
    for raw in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" not in line:
            continue
        key, _, reason = line.partition("#")
        allow[key.strip()] = reason.strip()
    return allow


def _write_allowlist(violations: list[Violation]) -> None:
    existing = _load_allowlist()
    header = (
        "# success_status_literal_allowlist.txt — grandfathered Gate 4 hits\n"
        "# Enforced by scripts/lint-success-status-literal.py --check\n"
        "# (tasks/2026-08-11-consolidated-audit.md sec 3, gate 4).\n"
        "#\n"
        "# Format: <path>:<lineno>  # <one-line reason citing an audit finding id>\n"
        "# Shrink-only — fixing a site removes its line; the gate fails on any\n"
        "# stale entry (line no longer a hit) or any new, un-allowlisted hit.\n"
        "#\n"
        "# Regenerate (preserves reasons for still-live keys) with:\n"
        "#   python scripts/lint-success-status-literal.py --update\n\n"
    )
    lines = [header.rstrip("\n")]
    for v in violations:
        reason = existing.get(v.key(), "TODO: cite an audit finding id")
        lines.append(f"{v.key()}  # {reason}")
    ALLOWLIST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def format_violation(v: Violation) -> str:
    return f"{v.file}:{v.lineno}: [{v.kind}] {v.detail}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="CI gate: exit 1 on new or stale entries")
    ap.add_argument("--update", action="store_true", help="Reseed the allowlist to the current hit set")
    args = ap.parse_args(argv)

    current = collect_all()

    if args.update:
        _write_allowlist(current)
        print(f"wrote {ALLOWLIST_PATH.relative_to(REPO_ROOT)} ({len(current)} hits)")
        return 0

    allow = _load_allowlist()
    current_keys = {v.key(): v for v in current}
    new_hits = sorted(k for k in current_keys if k not in allow)
    stale = sorted(
        k for k in allow
        if k not in current_keys and (REPO_ROOT / k.split(":", 1)[0]).exists()
    )

    if not args.check:
        print(
            f"[success-status-literal] {len(current)} hit(s); {len(allow)} allowlisted; "
            f"{len(new_hits)} new; {len(stale)} stale.",
        )
        return 0

    if not new_hits and not stale:
        print(
            f"[success-status-literal] OK — {len(current)} hit(s), all grandfathered "
            "(allowlist must only shrink).",
        )
        return 0

    if new_hits:
        print(
            f"\n::error::[success-status-literal] {len(new_hits)} NEW success-status-on-failure "
            "hit(s) — a literal \"success\" (or *State.COMPLETED-shaped equivalent) reached an "
            "execution-logging call site without being conditioned on the run's own failure "
            "signal. Gate it with an if/else on the counter, or (if reviewed as pre-existing "
            "debt) add an allowlist entry citing an audit finding id.",
            file=sys.stderr,
        )
        for k in new_hits:
            print(f"  NEW  {format_violation(current_keys[k])}", file=sys.stderr)
    if stale:
        print(
            f"\n::error::[success-status-literal] {len(stale)} stale allowlist entr(y/ies) — "
            "fixed or no longer a hit. Run "
            "`python scripts/lint-success-status-literal.py --update` to ratchet the allowlist down.",
            file=sys.stderr,
        )
        for k in stale:
            print(f"  STALE {k}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
