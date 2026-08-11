#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Every CERID_* variable named in the docs must actually exist.

Why this exists: `CERID_FEATURE_TIER` appeared **nine times across six Pro
docs** and **zero times in the codebase**. Two of those instances told
operators to "set `CERID_FEATURE_TIER=pro` in .env" to unlock a paid feature —
advice that silently does nothing, given to exactly the customer who has just
paid and is wondering why nothing happened. The real variable is `CERID_TIER`.

Nothing could have caught it: env drift was checked in one direction only
(does .env.example match the code?), never "does the prose match either?".

The oracle is "does this name appear ANYWHERE in code or config" — not just
`.env.example`. The first cut used .env.example alone and produced 18 findings,
almost all legitimate: `CERID_DAILY_DIGEST_ENABLED` is read via
``os.getenv(spec["env_enabled"])``, a variable rather than a literal, so the
AST-based generator cannot see it; and the LAN/gateway names live in scripts
and Caddy config, outside src/mcp entirely. A gate that cries wolf gets
ignored, which is worse than no gate — so this checks the one thing that is
unambiguously wrong: a name that exists in NO source file at all.

Usage:
    python scripts/lint-doc-env-vars.py            # report + exit 1 on unknown names
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = REPO_ROOT / ".env.example"

# Docs an operator actually follows. Archived material is excluded: it records
# what was true when written, and rewriting history to satisfy a linter would
# destroy the reason it is kept.
DOC_ROOTS = [REPO_ROOT / "docs", REPO_ROOT / "README.md", REPO_ROOT / "CLAUDE.md"]
SKIP_DIR_PARTS = {"archive", "superpowers", "node_modules"}

_VAR_RE = re.compile(r"\bCERID_[A-Z0-9_]{2,}\b")

# Names that are legitimately absent from .env.example because nothing reads
# them via os.getenv — they are compose service names, doc placeholders, or
# prefixes. Keep this SHORT and justified; it is the escape hatch that turns a
# gate into a rubber stamp.
KNOWN_NON_ENV = {
    "CERID_PORT_",      # documented as a prefix family, not a variable
    "CERID_ENABLE_",    # same — a naming convention in ENV_CONVENTIONS.md
    # NOTE: CERID_FEATURE_TIER was briefly listed here so PRO_OUTLOOK.md could
    # name the mistake it documents. That was wrong: a blanket exemption for
    # the one name this gate exists to catch meant a REAL occurrence (in
    # PRO_GMAIL.md) came back and the gate stayed green. Historical mentions
    # now use the ~~strikethrough~~ convention instead, which is local and
    # visible in the prose rather than hidden in this list.
}


CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".sh", ".yml", ".yaml", ".toml", ".json", ".swift"}
CODE_SKIP = {"node_modules", ".git", ".venv", "dist", "build", "__pycache__",
             ".worktrees", ".claude", "docs"}


def known_vars() -> set[str]:
    """Every CERID_* name that appears in a source or config file."""
    out: set[str] = set()
    if ENV_EXAMPLE.is_file():
        for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
            line = line.strip().lstrip("#").strip()
            if "=" in line:
                out.add(line.split("=", 1)[0].strip())
    self_path = Path(__file__).resolve()
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file() or path.suffix not in CODE_EXTS:
            continue
        if CODE_SKIP & set(path.parts):
            continue
        # Skip THIS file. Its docstring names CERID_FEATURE_TIER to explain the
        # defect, and counting that made the oracle vouch for the very name the
        # gate exists to reject — it passed a planted fault. Same shape as
        # lint-pro-gating counting gates that lived only in test files.
        if path.resolve() == self_path:
            continue
        try:
            out.update(_VAR_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
        except OSError:
            continue
    # Dockerfiles and compose overlays carry names too, and have no suffix.
    for extra in ("Dockerfile", "docker-compose.yml", "Makefile"):
        for path in REPO_ROOT.rglob(extra):
            if CODE_SKIP & set(path.parts):
                continue
            try:
                out.update(_VAR_RE.findall(path.read_text(encoding="utf-8", errors="replace")))
            except OSError:
                continue
    return out


def iter_docs():
    for root in DOC_ROOTS:
        if root.is_file():
            yield root
        elif root.is_dir():
            for p in sorted(root.rglob("*.md")):
                if SKIP_DIR_PARTS & set(p.parts):
                    continue
                yield p


def main() -> int:
    known = known_vars()
    if not known:
        # Fail closed: with no oracle every name would look valid, and a gate
        # that passes because it has nothing to compare against is the exact
        # failure class this file was written for.
        print("::error::[doc-env-vars] found no CERID_* names in any source file — "
              "the oracle is empty, so every doc name would look valid. "
              "Something is wrong with the scan, not with the docs.")
        return 2

    unknown: dict[str, list[str]] = {}
    for doc in iter_docs():
        text = doc.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            # ENV_CONVENTIONS.md's "Known Inconsistencies" table names the
            # variable each bare name SHOULD have had. Those are deliberate
            # hypotheticals, not claims that the name exists — flagging them
            # would train readers to ignore this gate.
            if "Should be" in line or "Could be" in line:
                continue
            # A struck-through name (~~CERID_X~~) is the convention for "this
            # was real once and is not any more". Documenting a retired
            # variable so nobody reintroduces it is the opposite of the defect
            # this gate exists to catch.
            if "~~" in line:
                continue
            for name in _VAR_RE.findall(line):
                if name in known or any(name.startswith(p) for p in KNOWN_NON_ENV):
                    continue
                rel = str(doc.relative_to(REPO_ROOT))
                unknown.setdefault(name, []).append(f"{rel}:{lineno}")

    if unknown:
        print("::error::[doc-env-vars] docs name environment variables that do not "
              "exist — an operator following them changes nothing:")
        for name, places in sorted(unknown.items()):
            print(f"  {name}")
            for place in places[:6]:
                print(f"      {place}")
            if len(places) > 6:
                print(f"      … and {len(places) - 6} more")
        print("\nFix the doc, or add the variable. If a name is deliberately not an "
              "env var, justify it in KNOWN_NON_ENV.")
        return 1

    print(f"[doc-env-vars] OK — every CERID_* name in the docs exists somewhere "
          f"in code or config ({len(known)} known names).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
