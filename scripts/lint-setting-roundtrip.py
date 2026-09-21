#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Gate G-C — every settings field must survive the round trip.

The mechanism (2026-09-02 audit): ``PATCH /settings`` accepts a field,
validates it, writes it to ``config.X`` / ``config.features.X`` /
``os.environ["X"]``, echoes it back in ``updated``, and returns 200. The GUI
shows the new value. Nothing in the pipeline ever reads it again, so the knob
is a placebo — and it is a *convincing* placebo, because every observable
signal (200, echo, re-render, and the router's own unit test asserting
``config.X == 5``) confirms the write.

The write half is easy to test and always tested. The read half is what
decides whether the setting does anything, and no test asserts it, because
there is nothing local to assert. Only a whole-tree check can answer it.

Two failure kinds, both reported per model field:

  not-written
      The field is on the update model but the PATCH handler has no branch
      that assigns anything for it — accepted, validated, echoed, dropped.

  no-readback
      The handler writes a symbol, but no runtime reader in ``src/mcp`` ever
      reads it back.

What counts as a runtime reader
-------------------------------
A read that happens *per request*, i.e. inside a function body:

  * ``<mod>.SYMBOL`` attribute load, or ``getattr(mod, "SYMBOL", ...)``;
  * a string literal ``"SYMBOL"`` / ``"env_key"`` (covers ``os.getenv("X")``,
    ``FEATURE_TOGGLES["enable_x"]``, and the getattr shape);
  * a bare ``SYMBOL`` name load — but ONLY when the reading module did not
    bind that name with a module-level ``from ... import SYMBOL``.

That last exclusion is the point of the gate rather than an edge case.
``from config.features import MMR_LAMBDA`` copies the value into the reading
module's namespace at import time; ``features_mod.MMR_LAMBDA = 0.4`` in the
PATCH handler rebinds a different name, and ``core.utils.diversity`` keeps
serving 0.7 forever. Crediting that as a readback would make the gate green
on precisely the shape it exists to catch. (The value-import itself is gate
G-B's finding; here it just does not count as a read.)

A module-scope read does not count either: it runs once at import, so it
cannot observe a value the PATCH handler writes later.

Fields are enumerated from the pydantic model's own class body, so adding a
field to ``SettingsUpdateRequest`` puts it in scope automatically — the gate
cannot go stale the way a hand-kept list of knobs does.

Allowlist
---------
``scripts/setting_roundtrip_allowlist.txt``, keyed ``<field> <kind>`` with a
one-line reason. Shrink-only; a field that starts round-tripping makes its
entry stale and fails ``--check``.

Usage:
    python scripts/lint-setting-roundtrip.py            # report (exit 0)
    python scripts/lint-setting-roundtrip.py --check    # CI gate
    python scripts/lint-setting-roundtrip.py --update   # reseed allowlist
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "setting_roundtrip_allowlist.txt"
SCAN_ROOT = REPO_ROOT / "src" / "mcp"
SETTINGS_ROUTER = REPO_ROOT / "src" / "mcp" / "app" / "routers" / "settings.py"

MODEL_CLASS = "SettingsUpdateRequest"
HANDLER_FUNC = "update_settings_endpoint"

#: Calls whose first string argument names the thing being written.
#: ``set_toggle("enable_x", v)`` rebinds ``config.features.ENABLE_X``,
#: ``config.ENABLE_X`` and ``FEATURE_TOGGLES["enable_x"]`` (utils/features.py).
_KEYED_WRITERS = {"set_toggle", "setattr", "set_setting"}

_SKIP_DIR_PARTS = {"__pycache__", ".venv", "venv", "node_modules", "tests"}


class Violation(NamedTuple):
    field: str
    kind: str  # "not-written" | "no-readback"
    detail: str

    def key(self) -> str:
        return f"{self.field} {self.kind}"


class Writes(NamedTuple):
    """What the PATCH branch for one field mutates."""

    symbols: frozenset[str]  # attribute names: config.X, features_mod.X, X.upper()
    keys: frozenset[str]  # string keys: os.environ["K"], set_toggle("k", ...)

    def any(self) -> bool:
        return bool(self.symbols or self.keys)

    def describe(self) -> str:
        parts = [f"attr {s}" for s in sorted(self.symbols)]
        parts += [f"key '{k}'" for k in sorted(self.keys)]
        return ", ".join(parts) or "nothing"


class Readers(NamedTuple):
    """Everything read at request time anywhere in the scanned tree."""

    names: frozenset[str]
    literals: frozenset[str]

    def reads(self, w: Writes) -> bool:
        return bool(
            (w.symbols & (self.names | self.literals)) or (w.keys & self.literals)
        )


# ── the update model ─────────────────────────────────────────────────────────

def model_fields(tree: ast.Module, class_name: str = MODEL_CLASS) -> list[str]:
    """Field names off the pydantic model's class body.

    Equivalent to ``Model.model_fields`` without importing ``app.*`` — the
    gate has to run in a bare interpreter, same constraint as
    ``gen_router_registry.py``. Either way the list is derived, never typed
    out, which is the property that keeps it from going stale.
    """
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return [
                stmt.target.id
                for stmt in node.body
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name)
            ]
    return []


# ── the PATCH handler ────────────────────────────────────────────────────────

def _field_branches(handler: ast.AST, fields: set[str]) -> dict[str, list[ast.If]]:
    """``{field: [if-block, ...]}`` for each ``if req.<field> is not None:``."""
    branches: dict[str, list[ast.If]] = {}
    for node in ast.walk(handler):
        if not isinstance(node, ast.If):
            continue
        tested = {
            sub.attr
            for sub in ast.walk(node.test)
            if isinstance(sub, ast.Attribute)
            and isinstance(sub.value, ast.Name)
            and sub.value.id == "req"
        }
        for field in tested & fields:
            branches.setdefault(field, []).append(node)
    return branches


def handler_writes(tree: ast.Module, fields: list[str]) -> dict[str, Writes]:
    handler = next(
        (
            n
            for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
            and n.name == HANDLER_FUNC
        ),
        None,
    )
    if handler is None:
        return {f: Writes(frozenset(), frozenset()) for f in fields}

    branches = _field_branches(handler, set(fields))
    out: dict[str, Writes] = {}
    for field in fields:
        symbols: set[str] = set()
        keys: set[str] = set()
        for block in branches.get(field, []):
            for node in ast.walk(block):
                if isinstance(node, ast.Assign):
                    for target in node.targets:
                        if isinstance(target, ast.Attribute):
                            symbols.add(target.attr)
                        elif (
                            isinstance(target, ast.Subscript)
                            and isinstance(target.value, ast.Attribute)
                            and target.value.attr == "environ"
                            and isinstance(target.slice, ast.Constant)
                            and isinstance(target.slice.value, str)
                        ):
                            keys.add(target.slice.value)
                elif isinstance(node, ast.Call):
                    fn = (
                        node.func.attr
                        if isinstance(node.func, ast.Attribute)
                        else node.func.id
                        if isinstance(node.func, ast.Name)
                        else ""
                    )
                    if (
                        fn in _KEYED_WRITERS
                        and node.args
                        and isinstance(node.args[0], ast.Constant)
                        and isinstance(node.args[0].value, str)
                    ):
                        keys.add(node.args[0].value)
                        symbols.add(node.args[0].value.upper())
        out[field] = Writes(frozenset(symbols), frozenset(keys))
    return out


# ── the runtime read side ────────────────────────────────────────────────────

def _value_imported_names(tree: ast.Module) -> set[str]:
    """Names bound by a module-level ``from x import NAME`` — frozen copies."""
    frozen: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                frozen.add(alias.asname or alias.name)
    return frozen


def readers_in(source: str) -> Readers:
    """Runtime reads in one module: function bodies only, value-imports excluded."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return Readers(frozenset(), frozenset())
    frozen = _value_imported_names(tree)
    names: set[str] = set()
    literals: set[str] = set()
    for func in [
        n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]:
        for node in ast.walk(func):
            if isinstance(node, ast.Attribute) and isinstance(node.ctx, ast.Load):
                names.add(node.attr)
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                if node.id not in frozen:
                    names.add(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                literals.add(node.value)
    return Readers(frozenset(names), frozenset(literals))


def merge_readers(sources: Iterable[str]) -> Readers:
    names: set[str] = set()
    literals: set[str] = set()
    for src in sources:
        r = readers_in(src)
        names |= r.names
        literals |= r.literals
    return Readers(frozenset(names), frozenset(literals))


# ── analysis ─────────────────────────────────────────────────────────────────

def analyse(settings_source: str, reader_sources: Iterable[str]) -> list[Violation]:
    """Pure core: the update model's source plus every other module's source."""
    try:
        tree = ast.parse(settings_source)
    except SyntaxError:
        return []
    fields = model_fields(tree)
    writes = handler_writes(tree, fields)
    readers = merge_readers(reader_sources)

    violations: list[Violation] = []
    for field in fields:
        w = writes[field]
        if not w.any():
            violations.append(
                Violation(
                    field,
                    "not-written",
                    "accepted and echoed by the PATCH handler but no branch assigns anything",
                )
            )
        elif not readers.reads(w):
            violations.append(
                Violation(
                    field,
                    "no-readback",
                    f"PATCH writes {w.describe()}; nothing in src/mcp reads it at request time",
                )
            )
    return violations


def _iter_reader_sources() -> Iterator[str]:
    for path in sorted(SCAN_ROOT.rglob("*.py")):
        if set(path.parts) & _SKIP_DIR_PARTS or path == SETTINGS_ROUTER:
            continue
        try:
            yield path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def collect_all() -> list[Violation]:
    if not SETTINGS_ROUTER.exists():
        return []
    return sorted(
        analyse(SETTINGS_ROUTER.read_text(encoding="utf-8"), _iter_reader_sources()),
        key=lambda v: v.key(),
    )


# ── allowlist ────────────────────────────────────────────────────────────────

def _load_allowlist() -> dict[str, str]:
    allow: dict[str, str] = {}
    if not ALLOWLIST_PATH.exists():
        return allow
    for raw in ALLOWLIST_PATH.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "#" not in line:
            continue
        key, _, reason = line.partition("#")
        allow[key.strip()] = reason.strip()
    return allow


_HEADER = """\
# setting_roundtrip_allowlist.txt — settings fields that do not round-trip
# Enforced by scripts/lint-setting-roundtrip.py --check (gate G-C,
# tasks/2026-09-02-audit-findings.json).
#
# Format: <field> <kind>  # <one-line reason>
#   not-written  PATCH accepts the field and assigns nothing
#   no-readback  PATCH writes a symbol nothing reads at request time
#
# Every line here is a knob the GUI presents as working and the pipeline
# ignores. Shrink-only — wiring a reader (or deleting the field) makes the
# entry stale and fails the gate.
#
# scripts/tests/test_lint_setting_roundtrip.py pins the count as
# non-increasing, so this file cannot grow by accident.
#
# Regenerate (preserves reasons for still-live keys) with:
#   python scripts/lint-setting-roundtrip.py --update
"""


def _write_allowlist(violations: list[Violation]) -> None:
    existing = _load_allowlist()
    lines = [_HEADER.rstrip("\n"), ""]
    for v in violations:
        lines.append(f"{v.key()}  # {existing.get(v.key(), 'TODO: cite an audit finding id')}")
    ALLOWLIST_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--check", action="store_true", help="CI gate: exit 1 on new or stale entries")
    ap.add_argument("--update", action="store_true", help="Reseed the allowlist to the current hit set")
    args = ap.parse_args(argv)

    current = collect_all()

    if args.update:
        _write_allowlist(current)
        print(f"wrote {ALLOWLIST_PATH.relative_to(REPO_ROOT)} ({len(current)} fields)")
        return 0

    allow = _load_allowlist()
    by_key = {v.key(): v for v in current}
    new_hits = sorted(k for k in by_key if k not in allow)
    stale = sorted(k for k in allow if k not in by_key)

    if not args.check:
        print(
            f"[setting-roundtrip] {len(by_key)} non-round-tripping field(s); "
            f"{len(allow)} allowlisted; {len(new_hits)} new; {len(stale)} stale."
        )
        return 0

    if not new_hits and not stale:
        print(
            f"[setting-roundtrip] OK — {len(by_key)} field(s) do not round-trip, all "
            "grandfathered (allowlist must only shrink)."
        )
        return 0

    if new_hits:
        print(
            f"\n::error::[setting-roundtrip] {len(new_hits)} settings field(s) newly fail the "
            "round trip. PATCH /settings accepts the field, echoes it in `updated` and returns "
            "200, but nothing reads the written value at request time — the knob is a placebo. "
            "Wire a runtime reader, or (if reviewed as pre-existing debt) add an allowlist entry.",
            file=sys.stderr,
        )
        for k in new_hits:
            v = by_key[k]
            print(f"  NEW  {v.field}: [{v.kind}] {v.detail}", file=sys.stderr)
    if stale:
        print(
            f"\n::error::[setting-roundtrip] {len(stale)} stale allowlist entr(y/ies) — the field "
            "now round-trips (or is gone). Run `python scripts/lint-setting-roundtrip.py --update` "
            "to ratchet the allowlist down.",
            file=sys.stderr,
        )
        for k in stale:
            print(f"  STALE {k}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
