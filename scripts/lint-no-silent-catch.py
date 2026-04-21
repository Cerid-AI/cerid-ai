#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lint for silent-failure antipatterns in src/mcp/.

Rejects NEW introductions of four patterns:

  1. Bare ``except:``
  2. ``except Exception: pass`` (or ``except Exception as e: pass``)
  3. ``except ...: logger.debug(...)`` (silent-with-DEBUG-log)

Existing violations are grandfathered via ``scripts/silent_catch_allowlist.txt``.
Use ``--bootstrap-allowlist`` to regenerate the allowlist from current state.
The allowlist shrinks as cleanups land — new entries require explicit review.

Usage:
    python scripts/lint-no-silent-catch.py src/mcp/        # lint (CI)
    python scripts/lint-no-silent-catch.py --bootstrap-allowlist src/mcp/
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple


class Violation(NamedTuple):
    file: str
    lineno: int
    kind: str  # "bare-except" | "exception-pass" | "exception-as-pass" | "debug-only"
    detail: str


class SilentCatchVisitor(ast.NodeVisitor):
    """Walk a module AST and collect silent-catch antipatterns."""

    def __init__(self, file: str):
        self.file = file
        self.violations: list[Violation] = []

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:  # noqa: N802
        # Pattern 1: bare `except:` — type is None
        if node.type is None:
            self.violations.append(Violation(
                self.file, node.lineno, "bare-except",
                "`except:` without exception class — catches SystemExit/KeyboardInterrupt",
            ))
        # Pattern 2: `except Exception: pass` or `except Exception as e: pass`
        elif (
            len(node.body) == 1
            and isinstance(node.body[0], ast.Pass)
            and self._is_bare_exception(node.type)
        ):
            kind = "exception-as-pass" if node.name else "exception-pass"
            self.violations.append(Violation(
                self.file, node.lineno, kind,
                "`except Exception: pass` silently swallows all errors",
            ))
        # Pattern 3: except body is only `logger.debug(...)` — silent-at-DEBUG antipattern
        elif self._is_debug_only(node.body):
            self.violations.append(Violation(
                self.file, node.lineno, "debug-only",
                "`except ...: logger.debug(...)` suppresses errors at DEBUG; use logger.exception",
            ))
        self.generic_visit(node)

    @staticmethod
    def _is_bare_exception(type_node: ast.expr) -> bool:
        """True if the caught type is `Exception` (no narrowing)."""
        return isinstance(type_node, ast.Name) and type_node.id == "Exception"

    @staticmethod
    def _is_debug_only(body: list[ast.stmt]) -> bool:
        """True if the except body is a single ``logger.debug(...)`` call."""
        if len(body) != 1 or not isinstance(body[0], ast.Expr):
            return False
        call = body[0].value
        if not isinstance(call, ast.Call):
            return False
        func = call.func
        if not isinstance(func, ast.Attribute) or func.attr != "debug":
            return False
        # Catch the common logger identifier shapes without over-reaching:
        #   - Any name ending in "logger" or "log" (covers logger, app_logger, log, _log, my_log)
        #   - Exactly "LOG" / "_LOG" / "l" (module-level constants / very short aliases)
        # Case-sensitive on purpose — we want "logger" but not "blogger"/"catalogue".
        return isinstance(func.value, ast.Name) and (
            func.value.id.endswith("logger")
            or func.value.id.endswith("log")
            or func.value.id in {"LOG", "_LOG", "l"}
        )


def iter_py_files(root: Path) -> Iterator[Path]:
    for p in root.rglob("*.py"):
        # Skip third-party / generated artifacts
        parts = set(p.parts)
        if parts & {"__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache"}:
            continue
        yield p


def check_file(path: Path) -> list[Violation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return []
    visitor = SilentCatchVisitor(str(path))
    visitor.visit(tree)
    return visitor.violations


def load_allowlist(path: Path) -> dict[str, set[int]]:
    """Parse allowlist plain format: lines of ``path:lineno``.

    We avoid a YAML dep by using the simpler ``path:lineno`` format.
    Comments start with ``#``; blank lines ignored.
    """
    allow: dict[str, set[int]] = {}
    if not path.exists():
        return allow
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        file_path, _, lineno = line.rpartition(":")
        try:
            allow.setdefault(file_path, set()).add(int(lineno))
        except ValueError:
            continue
    return allow


def format_violation(v: Violation) -> str:
    return f"{v.file}:{v.lineno}: [{v.kind}] {v.detail}"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="+", help="File or directory paths to scan")
    parser.add_argument(
        "--allowlist",
        type=Path,
        default=Path(__file__).parent / "silent_catch_allowlist.txt",
        help="Path to allowlist file (default: scripts/silent_catch_allowlist.txt)",
    )
    parser.add_argument(
        "--bootstrap-allowlist",
        action="store_true",
        help="Regenerate the allowlist from current state instead of linting",
    )
    args = parser.parse_args(argv)

    # Gather all violations across all paths
    violations: list[Violation] = []
    for raw_path in args.paths:
        path = Path(raw_path)
        if path.is_file():
            violations.extend(check_file(path))
        elif path.is_dir():
            for py in iter_py_files(path):
                violations.extend(check_file(py))

    if args.bootstrap_allowlist:
        # Write allowlist file — sorted, stable, human-readable
        lines = [
            "# silent_catch_allowlist.txt — auto-generated; shrink-only",
            "# Each line: <relative path>:<lineno>",
            "# Generated by: scripts/lint-no-silent-catch.py --bootstrap-allowlist src/mcp/",
            "",
        ]
        for v in sorted(violations, key=lambda x: (x.file, x.lineno)):
            lines.append(f"{v.file}:{v.lineno}")
        args.allowlist.write_text("\n".join(lines) + "\n")
        print(f"Bootstrapped allowlist with {len(violations)} entries at {args.allowlist}")
        return 0

    # Filter: only report violations NOT in the allowlist
    allowlist = load_allowlist(args.allowlist)
    new_violations = [
        v for v in violations
        if v.lineno not in allowlist.get(v.file, set())
    ]

    if new_violations:
        print(
            f"Found {len(new_violations)} NEW silent-catch violation(s) "
            f"(allowlist has {sum(len(s) for s in allowlist.values())} grandfathered):",
            file=sys.stderr,
        )
        for v in new_violations:
            print(format_violation(v), file=sys.stderr)
        print(
            "\nTo fix: replace with logger.exception + sentry_sdk.capture_exception "
            "(see R1-3 pattern). To grandfather: --bootstrap-allowlist (requires reviewer sign-off).",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
