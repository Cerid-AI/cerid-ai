#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Lint for silent-failure antipatterns in src/mcp/.

Strict patterns (fail CI when new instances appear, allowlist grandfathers
existing ones):

  1. Bare ``except:``
  2. ``except Exception: pass`` (or ``except Exception as e: pass``)
  3. ``except ...: logger.debug(...)`` (silent-with-DEBUG-log)

Warn-only pattern (Phase 2.4 of the 2026-04-22 ship audit):

  4. ``except Exception:`` whose body has no observable trace —
     no ``log_swallowed_error()``, no ``logger.exception(...)``,
     no ``sentry_sdk.capture_exception(...)``, and no ``raise``.
     Reported under ``[broad-no-helper]``. Suppress on a single line
     with a trailing ``# noqa: BLE001`` or
     ``# silent-catch-allowed: <reason>`` comment.

Promote ``broad-no-helper`` to a hard failure with ``--strict-broad``
once the existing 102 call sites are remediated or annotated.

Existing strict violations are grandfathered via
``scripts/silent_catch_allowlist.txt``. Use ``--bootstrap-allowlist`` to
regenerate it. The allowlist shrinks as cleanups land — new entries
require explicit review.

Usage:
    python scripts/lint-no-silent-catch.py src/mcp/                 # lint (CI)
    python scripts/lint-no-silent-catch.py --strict-broad src/mcp/  # promote
    python scripts/lint-no-silent-catch.py --bootstrap-allowlist src/mcp/
"""
from __future__ import annotations

import argparse
import ast
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import NamedTuple

_OBSERVABLE_ATTR_CALLS = {"exception", "capture_exception"}
_OBSERVABLE_NAME_CALLS = {"log_swallowed_error"}
_NOQA_BROAD_TOKENS = ("noqa: BLE001", "silent-catch-allowed")


class Violation(NamedTuple):
    file: str
    lineno: int
    kind: str  # "bare-except" | "exception-pass" | "exception-as-pass" | "debug-only" | "broad-no-helper"
    detail: str


class SilentCatchVisitor(ast.NodeVisitor):
    """Walk a module AST and collect silent-catch antipatterns.

    Strict violations land in ``self.violations``; the warn-only
    ``broad-no-helper`` kind lands in ``self.warnings``.
    """

    def __init__(self, file: str, source_lines: list[str] | None = None):
        self.file = file
        self.violations: list[Violation] = []
        self.warnings: list[Violation] = []
        self._source_lines = source_lines or []

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
        # Pattern 4 (warn-only): broad `except Exception` without an observability call
        elif (
            self._is_broad_exception(node.type)
            and not self._body_is_observable(node.body)
            and not self._line_has_opt_out(node.lineno)
        ):
            self.warnings.append(Violation(
                self.file, node.lineno, "broad-no-helper",
                "`except Exception:` body lacks log_swallowed_error / logger.exception / re-raise",
            ))
        self.generic_visit(node)

    @staticmethod
    def _is_bare_exception(type_node: ast.expr) -> bool:
        """True if the caught type is exactly ``Exception`` (no narrowing)."""
        return isinstance(type_node, ast.Name) and type_node.id == "Exception"

    @classmethod
    def _is_broad_exception(cls, type_node: ast.expr | None) -> bool:
        """True for ``except Exception`` and ``except (Exception, ...)``."""
        if type_node is None:
            return False
        if cls._is_bare_exception(type_node):
            return True
        if isinstance(type_node, ast.Tuple):
            return any(cls._is_bare_exception(t) for t in type_node.elts)
        return False

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

    @staticmethod
    def _body_is_observable(body: list[ast.stmt]) -> bool:
        """True if the body re-raises or calls a canonical observability helper.

        Recognized observability shapes:

        * ``raise`` / ``raise SomethingElse(...)`` — error is propagated.
        * ``log_swallowed_error(module, exc, ...)`` — the cerid helper.
        * ``<anything>.exception(...)`` — covers ``logger.exception``,
          ``app_logger.exception``, etc. Stack trace lands in logs.
        * ``<anything>.capture_exception(...)`` — covers
          ``sentry_sdk.capture_exception`` regardless of import alias.
        """
        for stmt in body:
            for sub in ast.walk(stmt):
                if isinstance(sub, ast.Raise):
                    return True
                if isinstance(sub, ast.Call):
                    func = sub.func
                    if (
                        isinstance(func, ast.Name)
                        and func.id in _OBSERVABLE_NAME_CALLS
                    ):
                        return True
                    if (
                        isinstance(func, ast.Attribute)
                        and func.attr in _OBSERVABLE_ATTR_CALLS
                    ):
                        return True
        return False

    def _line_has_opt_out(self, lineno: int) -> bool:
        """True if the source line carries ``# noqa: BLE001`` or
        ``# silent-catch-allowed: ...`` — already explicitly waived."""
        if not self._source_lines or lineno <= 0 or lineno > len(self._source_lines):
            return False
        line = self._source_lines[lineno - 1]
        return any(token in line for token in _NOQA_BROAD_TOKENS)


def iter_py_files(root: Path) -> Iterator[Path]:
    for p in root.rglob("*.py"):
        # Skip third-party / generated artifacts
        parts = set(p.parts)
        if parts & {"__pycache__", ".venv", "venv", "node_modules", ".mypy_cache", ".pytest_cache", ".ruff_cache"}:
            continue
        yield p


def check_file(path: Path) -> list[Violation]:
    """Strict-pattern violations only.

    Preserved for backward-compatible callers (existing
    ``tests/test_lint_no_silent_catch.py`` and the bootstrap path).
    For the warn-only ``broad-no-helper`` set, use
    :func:`check_file_with_warnings`.
    """
    strict, _ = check_file_with_warnings(path)
    return strict


def check_file_with_warnings(path: Path) -> tuple[list[Violation], list[Violation]]:
    """Return ``(strict_violations, broad_no_helper_warnings)`` for one file."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, UnicodeDecodeError):
        return [], []
    visitor = SilentCatchVisitor(str(path), source.splitlines())
    visitor.visit(tree)
    return visitor.violations, visitor.warnings


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
    parser.add_argument(
        "--strict-broad",
        action="store_true",
        help="Promote broad-no-helper warnings to hard failures (Phase 2.4 follow-up)",
    )
    args = parser.parse_args(argv)

    # Gather all violations + warnings across all paths
    violations: list[Violation] = []
    warnings: list[Violation] = []
    for raw_path in args.paths:
        path = Path(raw_path)
        if path.is_file():
            v, w = check_file_with_warnings(path)
            violations.extend(v)
            warnings.extend(w)
        elif path.is_dir():
            for py in iter_py_files(path):
                v, w = check_file_with_warnings(py)
                violations.extend(v)
                warnings.extend(w)

    if args.bootstrap_allowlist:
        # Write allowlist file — sorted, stable, human-readable.
        # Only strict kinds are grandfathered; broad-no-helper is warn-only
        # and uses inline opt-out tokens (see _NOQA_BROAD_TOKENS) instead.
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

    # Filter strict violations: only report ones NOT in the allowlist
    allowlist = load_allowlist(args.allowlist)
    new_violations = [
        v for v in violations
        if v.lineno not in allowlist.get(v.file, set())
    ]

    exit_code = 0

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
        exit_code = 1

    if warnings:
        stream = sys.stderr if args.strict_broad else sys.stdout
        prefix = "FAIL" if args.strict_broad else "WARN"
        print(
            f"\n[{prefix}] {len(warnings)} broad-except handler(s) without an observability "
            "helper (log_swallowed_error / logger.exception / capture_exception / re-raise):",
            file=stream,
        )
        for v in warnings:
            print(format_violation(v), file=stream)
        print(
            "\nTo silence: add `# noqa: BLE001` or `# silent-catch-allowed: <reason>` "
            "to the `except` line. To fix: call `log_swallowed_error(<module>, exc)`.",
            file=stream,
        )
        if args.strict_broad:
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
