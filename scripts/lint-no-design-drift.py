#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels / Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
"""Drift gate: flag design-system violations in the frontend source tree.

Checks (each independently controllable via flags):

  --check-hex             Raw hex colour literals in .ts/.tsx files.
                          Pattern: ``#[0-9a-fA-F]{3,8}\\b`` outside comments
                          and CSS-var contexts.

  --check-inline-style    ``style={{...}}`` blocks in .tsx files.

  --check-arbitrary-tailwind
                          Tailwind arbitrary values: ``p-[13px]``,
                          ``text-[11px]``, ``max-w-[240px]`` etc.
                          Pattern: ``\\b[a-z-]+-\\[(#|\\d+(px|rem|em)?)``

  --check-icons           Imports from non-lucide icon libraries
                          (heroicons, react-icons, material-ui icons, etc.)

  --check-motion          Imports from non-shadcn motion libraries
                          (framer-motion, gsap, react-spring).

  --check-settings-controls
                          Settings drift (J-11): under components/settings/,
                          native ``<select>`` / raw ``<input>`` JSX and
                          ``scale-[0.6]`` are banned — control semantics must
                          come from the registry-driven primitives
                          (``SettingRow`` / ``ToggleRow`` / ``SliderRow`` /
                          shadcn ``Select`` / ``Input``). settings-primitives.tsx
                          itself is exempt (it owns the raw elements).

Additional controls:

  --root PATH             Directory to scan (default: ``src/web/src``).
  --allow-file PATH       Path to allow-file (``path:lineno`` format, one per
                          line). Repeatable. Lines starting with ``#`` and
                          blank lines are ignored.
  --report-only           Exit 0 even when violations are found; print the
                          punch list to stdout. Use during the promotion
                          window before flipping the gate to blocking.
  --exclude-dir NAME      Subdirectory name to skip (repeatable). Defaults
                          exclude the ``ui`` shadcn component directory.

Exit codes
----------
  0   All checks pass (or --report-only was set).
  1   One or more violations found (and --report-only not set).
  2   Script error (bad arguments, missing root directory, etc.)

Suppress individual lines with a trailing comment:
  ``// drift-allowed: <reason>``

Style matches scripts/lint-no-silent-catch.py.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import NamedTuple

# ---------------------------------------------------------------------------
# Patterns
# ---------------------------------------------------------------------------

# Raw hex colour literal: #RGB, #RRGGBB, #RRGGBBAA, #RGBA
# Regex does NOT look inside comments; we do that in text-level pre-filtering.
_RE_HEX = re.compile(r"#[0-9a-fA-F]{3,8}\b")

# Inline style prop: style={{ or style = {{
_RE_INLINE_STYLE = re.compile(r"style\s*=\s*\{\{")

# Tailwind arbitrary value:  something-[#...], something-[Npx], something-[Nrem],
# something-[Nem], something-[N] (bare number in brackets — only meaningful when
# used as a size/length).
_RE_ARBITRARY_TAILWIND = re.compile(
    r"""\b[a-z][a-z0-9-]*-\[(?:#[0-9a-fA-F]{3,8}|[0-9]+(?:px|rem|em)?)\]"""
)

# Non-lucide icon library imports
_RE_NON_LUCIDE_ICONS = re.compile(
    r"""from\s+['"](?:@heroicons|heroicons|react-icons|@material-ui/icons"""
    r"""|@material-ui/core/(?:icons|Icon)|@phosphor-icons|phosphor-react"""
    r"""|@ant-design/icons|ionicons)[^'"]*['"]"""
)

# Non-shadcn motion library imports
_RE_NON_SHADCN_MOTION = re.compile(
    r"""from\s+['"](?:framer-motion|gsap|react-spring|@react-spring/[a-z-]+)['"]"""
)

# Settings-control drift (J-11): native form elements and the shrunken-switch
# hack under components/settings/ (settings-primitives.tsx exempt).
_RE_SETTINGS_NATIVE_CONTROL = re.compile(r"<(?:select|input|textarea)\b")
_SETTINGS_SCALE_HACK = "scale-[0.6]"
_SETTINGS_DIR_MARKER = "components/settings/"
_SETTINGS_PRIMITIVES_FILE = "settings-primitives.tsx"

# Suppression token — trailing comment on the same source line
_DRIFT_ALLOWED_TOKEN = "drift-allowed:"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class Violation(NamedTuple):
    file: str
    lineno: int
    check: str   # hex | inline-style | arbitrary-tailwind | icons | motion
    line: str    # raw source line (stripped)


# ---------------------------------------------------------------------------
# File-level helpers
# ---------------------------------------------------------------------------


def _is_comment_line(line: str) -> bool:
    """True if the stripped line starts with ``//`` or ``*``."""
    s = line.lstrip()
    return s.startswith("//") or s.startswith("*")


def _has_suppression(line: str) -> bool:
    return _DRIFT_ALLOWED_TOKEN in line


def _in_css_var_context(line: str, match_start: int) -> bool:
    """True when the hex literal appears inside ``var(--...)`` or ``#`` in
    an HTML entity reference like ``&#9888;``.

    We only exclude two narrow cases:
    - ``var(--...-foreground: #aabbcc`` — the hex is a fallback to a CSS var.
    - ``&#NNNN;`` HTML entities (decimal OR hex starting with x).

    Full CSS-in-JS is not a concern here — hex in JSX is almost always
    a colour value that should be replaced with a design token.
    """
    # Check preceding chars for ``var(--`` (CSS variable fallback syntax)
    before = line[:match_start]
    if "var(--" in before:
        return True
    # HTML entity &#...; — decimal or &#x...; hex
    if re.search(r"&#(?:[0-9]+|x[0-9a-fA-F]+);", line[max(0, match_start - 3):match_start + 12]):
        return True
    return False


# ---------------------------------------------------------------------------
# Per-check scanners
# ---------------------------------------------------------------------------


def _scan_hex(path: Path, lines: list[str]) -> list[Violation]:
    violations = []
    for i, raw in enumerate(lines, 1):
        if _is_comment_line(raw) or _has_suppression(raw):
            continue
        for m in _RE_HEX.finditer(raw):
            if _in_css_var_context(raw, m.start()):
                continue
            violations.append(Violation(str(path), i, "hex", raw.strip()))
            break  # one violation per line is enough for hex
    return violations


def _scan_inline_style(path: Path, lines: list[str]) -> list[Violation]:
    violations = []
    for i, raw in enumerate(lines, 1):
        if _is_comment_line(raw) or _has_suppression(raw):
            continue
        if _RE_INLINE_STYLE.search(raw):
            violations.append(Violation(str(path), i, "inline-style", raw.strip()))
    return violations


def _scan_arbitrary_tailwind(path: Path, lines: list[str]) -> list[Violation]:
    violations = []
    for i, raw in enumerate(lines, 1):
        if _is_comment_line(raw) or _has_suppression(raw):
            continue
        if _RE_ARBITRARY_TAILWIND.search(raw):
            violations.append(Violation(str(path), i, "arbitrary-tailwind", raw.strip()))
    return violations


def _scan_icons(path: Path, lines: list[str]) -> list[Violation]:
    violations = []
    for i, raw in enumerate(lines, 1):
        if _is_comment_line(raw) or _has_suppression(raw):
            continue
        if _RE_NON_LUCIDE_ICONS.search(raw):
            violations.append(Violation(str(path), i, "icons", raw.strip()))
    return violations


def _scan_motion(path: Path, lines: list[str]) -> list[Violation]:
    violations = []
    for i, raw in enumerate(lines, 1):
        if _is_comment_line(raw) or _has_suppression(raw):
            continue
        if _RE_NON_SHADCN_MOTION.search(raw):
            violations.append(Violation(str(path), i, "motion", raw.strip()))
    return violations


def _scan_settings_controls(path: Path, lines: list[str]) -> list[Violation]:
    posix = path.as_posix()
    if _SETTINGS_DIR_MARKER not in posix or posix.endswith(_SETTINGS_PRIMITIVES_FILE):
        return []
    violations = []
    for i, raw in enumerate(lines, 1):
        if _is_comment_line(raw) or _has_suppression(raw):
            continue
        if _RE_SETTINGS_NATIVE_CONTROL.search(raw) or _SETTINGS_SCALE_HACK in raw:
            violations.append(Violation(str(path), i, "settings-control", raw.strip()))
    return violations


# ---------------------------------------------------------------------------
# File iteration
# ---------------------------------------------------------------------------


def _should_skip_dir(dirname: str, exclude_dirs: frozenset[str]) -> bool:
    return dirname in exclude_dirs or dirname.startswith(".") or dirname in {
        "node_modules", "__pycache__", ".venv", "dist", ".cache",
    }


def _iter_ts_tsx(root: Path, exclude_dirs: frozenset[str]) -> list[Path]:
    results: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        # Skip excluded directory names at any level
        if any(_should_skip_dir(part, exclude_dirs) for part in path.parts):
            continue
        if path.suffix in {".ts", ".tsx"}:
            results.append(path)
    return results


def check_file(
    path: Path,
    *,
    check_hex: bool,
    check_inline_style: bool,
    check_arbitrary_tailwind: bool,
    check_icons: bool,
    check_motion: bool,
    check_settings_controls: bool,
) -> list[Violation]:
    """Scan a single file and return all violations found."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []

    lines = text.splitlines()
    violations: list[Violation] = []

    if check_hex:
        violations.extend(_scan_hex(path, lines))
    if check_inline_style and path.suffix == ".tsx":
        violations.extend(_scan_inline_style(path, lines))
    if check_arbitrary_tailwind and path.suffix == ".tsx":
        violations.extend(_scan_arbitrary_tailwind(path, lines))
    if check_icons:
        violations.extend(_scan_icons(path, lines))
    if check_motion:
        violations.extend(_scan_motion(path, lines))
    if check_settings_controls and path.suffix == ".tsx":
        violations.extend(_scan_settings_controls(path, lines))

    return violations


# ---------------------------------------------------------------------------
# Allowlist loading
# ---------------------------------------------------------------------------


def load_allow_files(paths: list[Path]) -> dict[str, set[int]]:
    """Parse one or more allow-files in ``path:lineno`` format.

    Lines starting with ``#`` and blank lines are skipped.
    Duplicate entries are silently merged.
    """
    allow: dict[str, set[int]] = {}
    for allow_path in paths:
        if not allow_path.exists():
            print(f"WARN: allow-file not found: {allow_path}", file=sys.stderr)
            continue
        for raw in allow_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            file_part, _, lineno_part = line.rpartition(":")
            if not file_part or not lineno_part:
                continue
            try:
                allow.setdefault(file_part, set()).add(int(lineno_part))
            except ValueError:
                continue
    return allow


def filter_allowlisted(
    violations: list[Violation], allow: dict[str, set[int]]
) -> list[Violation]:
    return [v for v in violations if v.lineno not in allow.get(v.file, set())]


# ---------------------------------------------------------------------------
# Output formatting
# ---------------------------------------------------------------------------


def format_violation(v: Violation) -> str:
    excerpt = v.line[:120] + ("…" if len(v.line) > 120 else "")
    return f"{v.file}:{v.lineno}: [{v.check}] {excerpt}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:  # noqa: C901 — single-entry CLI, acceptable length
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--root",
        default="src/web/src",
        metavar="PATH",
        help="Root directory to scan (default: src/web/src)",
    )
    parser.add_argument(
        "--allow-file",
        dest="allow_files",
        action="append",
        default=[],
        metavar="PATH",
        help="Allow-file in path:lineno format (repeatable)",
    )
    parser.add_argument(
        "--exclude-dir",
        dest="exclude_dirs",
        action="append",
        default=["ui"],
        metavar="NAME",
        help=(
            "Directory name to skip at any depth (repeatable). "
            "Default: ``ui`` (shadcn component tree — arbitrary values expected)."
        ),
    )
    parser.add_argument(
        "--report-only",
        action="store_true",
        help="Print violations but exit 0 (soft-warn mode for pre-clean-up CI)",
    )

    # Per-check flags — all on by default
    parser.add_argument("--check-hex", action="store_true", default=True, help=argparse.SUPPRESS)
    parser.add_argument("--no-check-hex", dest="check_hex", action="store_false")
    parser.add_argument("--check-inline-style", action="store_true", default=True, help=argparse.SUPPRESS)
    parser.add_argument("--no-check-inline-style", dest="check_inline_style", action="store_false")
    parser.add_argument("--check-arbitrary-tailwind", action="store_true", default=True, help=argparse.SUPPRESS)
    parser.add_argument("--no-check-arbitrary-tailwind", dest="check_arbitrary_tailwind", action="store_false")
    parser.add_argument("--check-icons", action="store_true", default=True, help=argparse.SUPPRESS)
    parser.add_argument("--no-check-icons", dest="check_icons", action="store_false")
    parser.add_argument("--check-motion", action="store_true", default=True, help=argparse.SUPPRESS)
    parser.add_argument("--no-check-motion", dest="check_motion", action="store_false")
    parser.add_argument("--check-settings-controls", action="store_true", default=True, help=argparse.SUPPRESS)
    parser.add_argument("--no-check-settings-controls", dest="check_settings_controls", action="store_false")

    args = parser.parse_args(argv)

    root = Path(args.root)
    if not root.exists():
        print(f"ERROR: --root path does not exist: {root}", file=sys.stderr)
        return 2
    if not root.is_dir():
        print(f"ERROR: --root path is not a directory: {root}", file=sys.stderr)
        return 2

    exclude_dirs = frozenset(args.exclude_dirs)

    allow: dict[str, set[int]] = {}
    for raw_path in args.allow_files:
        for k, v in load_allow_files([Path(raw_path)]).items():
            allow.setdefault(k, set()).update(v)

    files = _iter_ts_tsx(root, exclude_dirs)

    all_violations: list[Violation] = []
    for f in files:
        raw = check_file(
            f,
            check_hex=args.check_hex,
            check_inline_style=args.check_inline_style,
            check_arbitrary_tailwind=args.check_arbitrary_tailwind,
            check_icons=args.check_icons,
            check_motion=args.check_motion,
            check_settings_controls=args.check_settings_controls,
        )
        all_violations.extend(filter_allowlisted(raw, allow))

    if not all_violations:
        print(
            "lint-no-design-drift: OK — "
            f"0 violations in {len(files)} files scanned."
        )
        return 0

    # Tally per check
    counts: dict[str, int] = {}
    for v in all_violations:
        counts[v.check] = counts.get(v.check, 0) + 1

    summary_parts = [f"{c} {k}" for k, c in sorted(counts.items())]
    stream = sys.stdout if args.report_only else sys.stderr

    mode = "REPORT-ONLY" if args.report_only else "FAIL"
    print(
        f"[{mode}] lint-no-design-drift: "
        f"{len(all_violations)} violation(s) in {len(files)} files "
        f"({', '.join(summary_parts)}):",
        file=stream,
    )

    for v in all_violations:
        print(format_violation(v), file=stream)

    print("", file=stream)
    print(
        "To suppress a line: add ``// drift-allowed: <reason>`` at end of the line.\n"
        "To allowlist a file range: add ``path:lineno`` lines to an allow-file "
        "passed via ``--allow-file``.\n"
        "See docs/CONVENTIONS.md#design-tokens-d1 for remediation guidance.",
        file=stream,
    )

    return 0 if args.report_only else 1


if __name__ == "__main__":
    sys.exit(main())
