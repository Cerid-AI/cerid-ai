#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Lint for module-level ``os.getenv(...)`` captures (Phase 2.5 — lessons.md).

The 2026-04-22 beta-test incident: ``app/routers/chat.py:32`` carried

    OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")

at module scope. The setup wizard rotated the key at runtime and patched
``os.environ``, but the module-level constant was already frozen at
import time, so every chat request used the stale boot-time key — making
the bug look like "chats are failing" when the model-router was correct.

Rule of thumb (from ``tasks/lessons.md``): if a value can change at
runtime (env var written by wizard, config reload, etc.), reading it
once at import time is a bug. Module-level capture is fine for TRUE
constants (URLs, enum values, hard-coded defaults) — never for anything
a user can edit.

Detection
---------

Two shapes of the same freeze, both top-level statements only.

``module-env-capture`` — the original:

    NAME = os.getenv("X", "default")
    NAME: str = os.getenv("X")

``config-value-import`` — the same freeze one import statement away
(2026-09-02 audit, gate G-B):

    from config.features import FEATURE_TIER

``config``/``config.features``/``config.settings`` are the RUNTIME
settings surface: ``utils.features.set_toggle`` does
``setattr(config.features, "ENABLE_X", v)`` and ``setattr(config,
"ENABLE_X", v)``, and ``PATCH /settings`` assigns ``config.X`` directly.
A ``from`` import binds the *value* into the reader's own namespace at
import time, so those setattrs never reach it — ``core.utils.diversity``
serves its boot-time ``MMR_LAMBDA`` for the life of the process no
matter what the operator sets. The fix is ``import config.features as
features_mod`` plus ``features_mod.MMR_LAMBDA`` at the use site, which
re-reads the attribute on every call.

Only SCREAMING_CASE names are flagged: functions, classes and enums
imported from the same modules are definitions, not values that drift.

Skipped:
* ``src/mcp/config/`` — the canonical settings module pattern;
  defaults are documented constants, not user-editable.
* Lines carrying ``# env-capture-allowed: <reason>``.
* Imports inside a function body — those re-execute per call.

Default mode is warn-only (exit 0). Promote to a hard failure with
``--strict`` once the existing call sites are remediated or annotated.

Usage:
    python scripts/lint-no-module-env-captures.py src/mcp/             # CI (warn)
    python scripts/lint-no-module-env-captures.py --strict src/mcp/    # promote
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

_OPT_OUT_TOKEN = "env-capture-allowed"
_SKIP_DIR_PARTS = {"config"}  # src/mcp/config/* is the canonical settings module

#: Modules whose module-level names the running process rebinds via setattr
#: (utils.features.set_toggle, PATCH /settings). A value-import from any of
#: them is a boot-time copy that no runtime write can reach.
#: ``config.taxonomy`` belongs here for the same reason: ``TAXONOMY`` is
#: mutated in place by domain rehydration and POST /taxonomy/domains, but
#: ``DOMAINS`` is *rebound* from its keys — so a value-import of DOMAINS is a
#: boot-time snapshot, which published 12 of 23 live domains on
#: /sdk/v1/taxonomy (F340).
_MUTABLE_CONFIG_MODULES = {
    "config",
    "config.features",
    "config.settings",
    "config.taxonomy",
}


class Capture(NamedTuple):
    file: str
    lineno: int
    name: str
    kind: str = "module-env-capture"


def _is_os_getenv(call: ast.expr) -> bool:
    if not isinstance(call, ast.Call):
        return False
    f = call.func
    return (
        isinstance(f, ast.Attribute)
        and f.attr == "getenv"
        and isinstance(f.value, ast.Name)
        and f.value.id == "os"
    )


def _is_config_value_name(name: str) -> bool:
    """SCREAMING_CASE — a value, as opposed to a function/class/enum."""
    stripped = name.lstrip("_")
    return bool(stripped) and stripped.isupper() and stripped[0].isalpha()


def _target_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    if isinstance(node, ast.AnnAssign):
        return [node.target.id] if isinstance(node.target, ast.Name) else []
    out: list[str] = []
    for tgt in node.targets:
        if isinstance(tgt, ast.Name):
            out.append(tgt.id)
    return out


def check_file(path: Path) -> list[Capture]:
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    lines = source.splitlines()
    captures: list[Capture] = []

    def _opted_out(lineno: int) -> bool:
        return 0 < lineno <= len(lines) and _OPT_OUT_TOKEN in lines[lineno - 1]

    for node in tree.body:  # top-level only
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            if node.value is None or not _is_os_getenv(node.value):
                continue
            if _opted_out(node.lineno):
                continue
            for name in _target_names(node):
                captures.append(Capture(str(path), node.lineno, name))
        elif isinstance(node, ast.ImportFrom):
            if node.level != 0 or node.module not in _MUTABLE_CONFIG_MODULES:
                continue
            if _opted_out(node.lineno):
                continue
            for alias in node.names:
                if _is_config_value_name(alias.name):
                    captures.append(
                        Capture(str(path), node.lineno, alias.name, "config-value-import")
                    )
    return captures


def iter_py_files(root: Path) -> Iterator[Path]:
    for p in root.rglob("*.py"):
        parts = set(p.parts)
        if parts & {"__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache"}:
            continue
        if parts & _SKIP_DIR_PARTS:
            continue
        yield p


def format_capture(c: Capture) -> str:
    if c.kind == "config-value-import":
        return (
            f"{c.file}:{c.lineno}: [config-value-import] from <config> import {c.name} "
            "binds a boot-time copy; runtime writes to the config module never reach it"
        )
    return f"{c.file}:{c.lineno}: [module-env-capture] {c.name} = os.getenv(...) at module scope"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="File or directory paths to scan")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero on findings (default: warn-only).",
    )
    args = parser.parse_args(argv)

    captures: list[Capture] = []
    for raw_path in args.paths:
        path = Path(raw_path)
        if path.is_file():
            captures.extend(check_file(path))
        elif path.is_dir():
            for py in iter_py_files(path):
                captures.extend(check_file(py))

    if not captures:
        return 0

    stream = sys.stderr if args.strict else sys.stdout
    label = "FAIL" if args.strict else "WARN"
    n_env = sum(1 for c in captures if c.kind == "module-env-capture")
    n_import = len(captures) - n_env
    print(
        f"\n[{label}] {n_env} module-level os.getenv capture(s) and {n_import} value-import(s) "
        "of mutable config outside src/mcp/config/. Values that can change at runtime (API keys, "
        "secrets, settings the PATCH handler rewrites) must be read at the use site, not bound "
        "once at import time.",
        file=stream,
    )
    for c in captures:
        print(format_capture(c), file=stream)
    print(
        f"\nTo silence: add `# {_OPT_OUT_TOKEN}: <reason>` to the line. "
        "To fix an os.getenv capture: read it inline at each use site, or wrap it in a small "
        "accessor. To fix a value-import: `import config.features as features_mod` and read "
        "`features_mod.NAME` at the use site so the attribute is re-read per call.",
        file=stream,
    )
    return 1 if args.strict else 0


if __name__ == "__main__":
    sys.exit(main())
