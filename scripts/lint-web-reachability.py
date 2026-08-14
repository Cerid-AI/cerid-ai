#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Reachability gate over ``src/web/src`` — Gate 1 of the 2026-08-11 audit.

An exported component/module that no production code imports is a violation.
This closes mechanisms M1 ("tests prove the module works and say nothing about
whether anything reaches it — orphaning code raises coverage and produces no
red signal") and M2 (the union type is the real router) from
``tasks/2026-08-11-consolidated-audit.md`` §3.

How it works
------------
Builds a file-level import graph from static ``import``/``export ... from``
statements, dynamic ``import()`` (React ``lazy()``), ``new URL(...,
import.meta.url)`` worker constructions, and ``require()`` calls, then walks it
from the app roots.

Source is sanitized before extraction: comments are stripped, template-literal
bodies are blanked, regex literals are blanked, and string-literal contents are
lifted into a side table and replaced by placeholders. Import specifiers are
then recovered only from placeholders that sit in genuine import syntax. This
closes the phantom-edge bypass the 2026-08-11 adversarial review demonstrated:
without it, a single comment like ``// import "@/lib/some-orphan"`` in any
reachable file marked a genuinely-unimported module reachable and silenced the
gate — a false green, i.e. exactly mechanism M1 reintroduced via a comment
instead of a test import.

The roots:

* ``src/web/index.html``'s module scripts (what Vite actually builds), and
* ``main.tsx`` / ``App.tsx`` as explicit fallbacks.

The importer set EXCLUDES ``__tests__/**`` and ``*.test.*`` / ``*.spec.*``
files — an import from a test file must NOT count as reachability. Test files
are also excluded from the population (they are not themselves violations).

Any remaining ``.ts``/``.tsx`` file (``.d.ts`` excluded) that the walk cannot
reach is a violation unless listed in
``scripts/web_reachability_allowlist.txt`` with a one-line reason referencing
an audit finding id. The allowlist is seeded with the audit-documented orphans
so the gate is green at HEAD and goes red on any NEW orphan.

A stale allowlist entry (file became reachable or was deleted) is reported as
a warning, not a failure, so remediating an orphan cannot redden the gate;
remove the entry in the same change that wires or deletes the file.

Usage::

    python3 scripts/lint-web-reachability.py --check

sync-manifest: allow-internal-ref — web_reachability_allowlist.txt is internal_only (its entries
name internal-only modules); the Makefile guards this gate on allowlist
presence, so the public mirror skips it until a public allowlist is seeded.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEB_ROOT = REPO_ROOT / "src" / "web" / "src"
DEFAULT_ALLOW_FILE = REPO_ROOT / "scripts" / "web_reachability_allowlist.txt"

# Import-specifier extractors. These run over SANITIZED source (see
# ``_sanitize``): comments/templates/regexes are gone and every string literal
# is a ``\x00N\x00`` placeholder, so a match proves the specifier sits in real
# import syntax — import-shaped text in a comment or string cannot add an edge.
_SPEC_PATTERNS = (
    # import x from "spec" / import { a } from "spec" / export { a } from "spec"
    # / export * from "spec"  (the `from` keyword is the anchor)
    re.compile(r"""\bfrom\s*['"]([^'"]+)['"]"""),
    # side-effect import: import "spec"
    re.compile(r"""\bimport\s*['"]([^'"]+)['"]"""),
    # dynamic import — React lazy(() => import("spec")), route-level splits
    re.compile(r"""\bimport\s*\(\s*['"]([^'"]+)['"]"""),
    # worker construction: new URL("spec", import.meta.url)
    re.compile(r"""new\s+URL\s*\(\s*['"]([^'"]+)['"]\s*,\s*import\.meta\.url"""),
    # CommonJS escape hatch
    re.compile(r"""\brequire\s*\(\s*['"]([^'"]+)['"]\s*\)"""),
)

_TEST_NAME = re.compile(r"\.(test|spec)\.[jt]sx?$")

_PLACEHOLDER = re.compile(r"\x00(\d+)\x00")

# A `/` after one of these characters (or after one of these keywords, or at
# start of input) opens a regex literal, not division. Deliberately EXCLUDES
# `<` and `>` so JSX closing tags (`</div>`) and arrows (`=>`) lex as code.
_REGEX_PREV_CHARS = set("(,=:[!&|?{};")
_REGEX_PREV_KEYWORDS = {
    "return", "case", "typeof", "instanceof", "in", "of", "new",
    "delete", "void", "do", "else", "yield", "await",
}


def _sanitize(text: str) -> tuple[str, list[str]]:
    """Strip comments/templates/regexes; lift string literals to a side table.

    Returns ``(sanitized, strings)``. In the sanitized text each single- or
    double-quoted string literal is replaced by ``<quote>\\x00N\\x00<quote>``
    with its content stored at ``strings[N]``; comments, template-literal
    bodies (interpolation-aware), and regex literals are blanked. Extraction
    then matches import syntax against placeholders only, so import-shaped
    text inside a comment, string, template, or regex can never form an edge.

    Mis-lexing risk is biased safe: a wrongly-consumed span can only DROP an
    edge (over-report, a visible red), never invent one (a silent green).
    """
    out: list[str] = []
    strings: list[str] = []
    interp_depths: list[int] = []  # ${...} nesting; top = brace depth inside
    i, n = 0, len(text)

    def _prev_significant() -> str:
        for k in range(len(out) - 1, -1, -1):
            for c in reversed(out[k]):
                if not c.isspace():
                    return c
        return ""

    def _prev_word_is_keyword() -> bool:
        tail = "".join(out[-16:])[-24:]
        m = re.search(r"([A-Za-z_$][A-Za-z0-9_$]*)\s*$", tail)
        return bool(m and m.group(1) in _REGEX_PREV_KEYWORDS)

    def _consume_template(j: int) -> tuple[int, bool]:
        """From just past a backtick (or a closed interpolation), skip the
        template body. Returns (index, entered_interpolation)."""
        while j < n:
            c = text[j]
            if c == "\\" and j + 1 < n:
                j += 2
                continue
            if c == "`":
                return j + 1, False
            if c == "$" and j + 1 < n and text[j + 1] == "{":
                return j + 2, True
            j += 1
        return j, False

    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if ch == "/" and nxt == "/":
            j = text.find("\n", i)
            i = n if j == -1 else j  # keep the newline
            continue
        if ch == "/" and nxt == "*":
            j = text.find("*/", i + 2)
            out.append(" ")
            i = n if j == -1 else j + 2
            continue
        if ch in "'\"":
            j = i + 1
            buf: list[str] = []
            while j < n and text[j] not in (ch, "\n"):
                if text[j] == "\\" and j + 1 < n:
                    buf.append(text[j + 1])
                    j += 2
                    continue
                buf.append(text[j])
                j += 1
            strings.append("".join(buf))
            out.append(f"{ch}\x00{len(strings) - 1}\x00{ch}")
            i = j + 1 if j < n and text[j] == ch else j
            continue
        if ch == "`":
            out.append(" ")
            i, entered = _consume_template(i + 1)
            if entered:
                interp_depths.append(0)
            continue
        if ch == "/":
            prev = _prev_significant()
            if prev == "" or prev in _REGEX_PREV_CHARS or _prev_word_is_keyword():
                # Regex literal: consume to the closing unescaped `/`
                # (a `/` inside a [...] character class does not close it).
                j = i + 1
                in_class = False
                while j < n and text[j] != "\n":
                    c = text[j]
                    if c == "\\" and j + 1 < n:
                        j += 2
                        continue
                    if c == "[":
                        in_class = True
                    elif c == "]":
                        in_class = False
                    elif c == "/" and not in_class:
                        j += 1
                        break
                    j += 1
                out.append(" ")
                i = j
                continue
            out.append(ch)
            i += 1
            continue
        if interp_depths:
            if ch == "{":
                interp_depths[-1] += 1
            elif ch == "}":
                if interp_depths[-1] == 0:
                    interp_depths.pop()
                    out.append(" ")
                    i, entered = _consume_template(i + 1)
                    if entered:
                        interp_depths.append(0)
                    continue
                interp_depths[-1] -= 1
        out.append(ch)
        i += 1
    return "".join(out), strings


def _is_test(path: Path) -> bool:
    return "__tests__" in path.parts or bool(_TEST_NAME.search(path.name))


def _population(web_root: Path) -> set[Path]:
    """Non-test, non-declaration .ts/.tsx files under web_root."""
    files: set[Path] = set()
    for f in web_root.rglob("*"):
        if f.suffix not in (".ts", ".tsx") or f.name.endswith(".d.ts"):
            continue
        if "node_modules" in f.parts or _is_test(f):
            continue
        files.add(f.resolve())
    return files


def _resolve(spec: str, importer: Path, web_root: Path, population: set[Path]) -> Path | None:
    """Resolve an import specifier to a file in the population, or None."""
    spec = spec.split("?", 1)[0]  # strip Vite suffixes: ?worker, ?url, ?raw
    if spec.startswith("@/"):
        base = web_root / spec[2:]
    elif spec.startswith("."):
        base = importer.parent / spec
    else:
        return None  # bare package import
    for candidate in (
        base,
        base.with_name(base.name + ".ts") if base.suffix == "" else None,
        base.with_name(base.name + ".tsx") if base.suffix == "" else None,
        base / "index.ts",
        base / "index.tsx",
    ):
        if candidate is None:
            continue
        try:
            resolved = candidate.resolve()
        except OSError:
            continue
        if resolved in population:
            return resolved
    return None


def _imports_of(path: Path) -> set[str]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    sanitized, strings = _sanitize(text)
    specs: set[str] = set()
    for pat in _SPEC_PATTERNS:
        for captured in pat.findall(sanitized):
            m = _PLACEHOLDER.fullmatch(captured)
            if m:  # non-placeholder captures cannot occur post-sanitize
                specs.add(strings[int(m.group(1))])
    return specs


def _roots(web_root: Path, population: set[Path]) -> set[Path]:
    roots: set[Path] = set()
    # What Vite builds: module scripts in index.html one level above src/.
    index_html = web_root.parent / "index.html"
    if index_html.exists():
        for m in re.finditer(
            r"""src=["']/?src/([^"']+\.[jt]sx?)["']""", index_html.read_text(encoding="utf-8")
        ):
            candidate = (web_root / m.group(1)).resolve()
            if candidate in population:
                roots.add(candidate)
    # Explicit app roots (fallback + belt-and-braces).
    for name in ("main.tsx", "App.tsx"):
        candidate = (web_root / name).resolve()
        if candidate in population:
            roots.add(candidate)
    return roots


def _reachable(web_root: Path, population: set[Path]) -> set[Path]:
    seen: set[Path] = set()
    stack = list(_roots(web_root, population))
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        for spec in _imports_of(current):
            target = _resolve(spec, current, web_root, population)
            if target is not None and target not in seen:
                stack.append(target)
    return seen


def _load_allowlist(allow_file: Path) -> dict[str, str]:
    """Map of repo-relative path -> reason. Every entry must carry a reason."""
    entries: dict[str, str] = {}
    if not allow_file.exists():
        return entries
    for lineno, raw in enumerate(allow_file.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "#" not in line:
            print(
                f"{allow_file.name}:{lineno}: entry has no reason comment "
                "(format: <path>  # <finding-id>: <reason>)",
                file=sys.stderr,
            )
            sys.exit(2)
        path_part, reason = line.split("#", 1)
        entries[path_part.strip()] = reason.strip()
    return entries


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Web reachability gate (audit Gate 1)")
    ap.add_argument("--check", action="store_true", help="run the gate (default behaviour)")
    ap.add_argument("--web-root", type=Path, default=DEFAULT_WEB_ROOT)
    ap.add_argument("--allow-file", type=Path, default=DEFAULT_ALLOW_FILE)
    ap.add_argument("--list-reachable", action="store_true", help="debug: print the reachable set")
    args = ap.parse_args(argv)

    web_root = args.web_root.resolve()
    if not web_root.exists():
        print(f"web root {web_root} missing — skipping")
        return 0

    population = _population(web_root)
    reachable = _reachable(web_root, population)
    if args.list_reachable:
        for f in sorted(reachable):
            print(f.relative_to(web_root))
        return 0

    try:
        rel_base = web_root.relative_to(REPO_ROOT)
    except ValueError:
        rel_base = Path(".")

    def rel(p: Path) -> str:
        return str(rel_base / p.relative_to(web_root))

    unreachable = {rel(f) for f in population - reachable}
    allowlist = _load_allowlist(args.allow_file)

    violations = sorted(unreachable - set(allowlist))
    stale = sorted(set(allowlist) - unreachable)

    for path in stale:
        print(
            f"[stale-allowlist] {path} is reachable (or gone) — remove its entry "
            f"from {args.allow_file.name} ({allowlist[path]})"
        )

    if violations:
        print(
            "web-reachability: production code imports NOTHING from these "
            "modules (test imports do not count — consolidated-audit M1):\n"
        )
        for path in violations:
            print(f"  {path}: unreachable from app roots")
        print(
            "\nWire the module into the app (nav union, mount point, or caller) "
            "in this same change, or delete it. If it must stay orphaned, add "
            f"it to {args.allow_file.name} with a reason referencing an audit "
            "finding id — see the deliberate-orphan pattern "
            "(knowledge-console.tsx CustomApiDialog note)."
        )
        return 1

    print(
        f"web-reachability: {len(population)} modules, {len(reachable)} reachable, "
        f"{len(allowlist)} allowlisted orphans, 0 new — clean."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
