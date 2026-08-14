#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Every name declared in config/settings.py or config/features.py must be
read by something before it may appear in .env.example.

Scope and honest limitation: this gate defends against accidental
declaration drift — a knob nobody ever wired up nonetheless reaching
.env.example — not against deliberate laundering. The reader test asks
only "does this identifier appear as a real code reference anywhere
outside the declaring files"; it performs no liveness or reachability
analysis on the Python side, so it never asks whether that referencing
code is itself ever called. A dead, never-invoked function that does
nothing but mention the orphaned name (e.g. `def _unused(): x =
ORPHAN_NAME`, added anywhere in the scanned tree outside tests) satisfies
the reader test and clears the name, even though nothing reaches that
function. Closing that hole needs call-graph reachability analysis for
Python callables, which is out of scope for this pass.

Why this exists (Gate 2 of tasks/2026-08-11-consolidated-audit.md §3,
mechanism M3): `settings.py` is treated as the deliverable rather than the
value it holds. A knob gets declared, `gen_env_example.py` faithfully
promotes it into `.env.example`, and nobody ever notices the getenv call has
zero callers anywhere else in the tree — the generator turns a dead name into
published documentation that tells an operator a lever exists when it does
nothing. AF-047's `CERID_RSS_POLL_INTERVAL` / `SYNC_EXPORT_ON_INGEST`, AF-010's
`CONTEXTUAL_BUDGET_USD_PER_TENANT_PER_MONTH`, and AF-079's dead v1
`QUALITY_WEIGHT_*` trio are all this exact shape.

This is `lint-doc-env-vars.py`'s mirror image. That gate asks "does this name
in the docs exist anywhere in code?" (catches invented names). This gate asks
"does this name that exists in settings.py/features.py get read anywhere
ELSE?" (catches real-but-orphaned names) — a name can cleanly pass one gate
and fail the other.

Two declaration shapes are in scope, both drawn from a single AST pass over
the two canonical config modules:

  1. Any module-level assignment whose value contains an `os.getenv(...)` /
     `os.environ.get(...)` call — the shape `gen_env_example.py` scans to
     produce `.env.example`.
  2. Any module-level assignment to a bare scalar literal (int/float/str/bool
     — not a dict/list/set/tuple, which are internal lookup tables, not
     individually-consumed knobs) — catches config constants that were never
     wired to an env var at all, e.g. AF-079's dead trio.

"Reader" = the attribute name appears as a real code reference anywhere in
src/mcp, the web app, or the desktop app OUTSIDE: the two declaring files,
this gate, the two generators (gen_env_example.py / gen_tier_matrix.py —
counting a generator's own scan as a "reader" would make every declared name
look used by construction), tests, and .env.example itself (the surface
being gated, not a reader of it). One narrow exception: a name whose only
external-file reference is inside a function BODY of settings.py/features.py
itself still counts, but only if that function is itself called from outside
those two files — the wrapper-accessor shape (`embedding_version_for_domain`
reading the module-level `EMBEDDING_MODEL_VERSION` as its fallback default,
called externally from `core/utils/embeddings.py`, `app/routers/kb_admin.py`,
`app/processor/jobs/reembed_chunks.py`) is real production wiring, and
without this carve-out every constant behind an accessor function would look
orphaned purely because the accessor's own body lives in an excluded file.
See `_wrapper_transitive_readers`.

For Python, "real code reference" means the name appears as an `ast.Name`
(any context), the attribute half of an `ast.Attribute` (`config.NAME`), a
function/class name, a parameter name, or a string literal passed as a call
argument (`os.getenv("NAME")`) — extracted via `ast.parse`, not text/regex.
This is deliberately stricter than "the identifier appears as a token
anywhere in the file": an unused `from config.settings import NAME` with no
subsequent reference contributes nothing (import aliases are `ast.alias`
nodes, never `ast.Name`), and a docstring or comment mentioning NAME
contributes nothing (comments aren't tokens in the AST at all; a docstring
is an `ast.Expr(ast.Constant)` statement, not a call argument, so it never
enters the string-literal pool). A plain-grep or bare-identifier-token
reader test would let any future orphan launder itself past this gate with
a single throwaway comment, docstring, or dead import anywhere in the
tree — closing that is the entire value this gate adds over the audits' own
grep methodology. JS/TS/Swift get a comment-stripping regex pass that
preserves string contents; shell/YAML/TOML/Dockerfile/Makefile get a
quote-aware `#`-to-EOL strip — those languages keep the coarser token-scan
treatment (out of scope for the AST-precision pass, since the demonstrated
bypasses were Python-specific and settings.py/features.py are Python).

NOT in scope: `FEATURE_FLAGS` dict keys / `docs/TIER_MATRIX.md`. Those flags
are consumed generically through the tier/capability system (routes iterate
`FEATURE_FLAGS.items()`, `/billing/capabilities` reports all of them), so "is
this exact string token read somewhere" is not a sound reader test for that
population the way it is for a settings.py getenv — a first pass found 18 of
53 flags with no literal-string call site, which is almost certainly a
detection artifact, not 18 real M3 instances, and shipping that as a gate
would be exactly the "gate that cries wolf" lint-doc-env-vars.py's own
docstring warns against. Left for a follow-up with a sound oracle for that
population.

Usage:
    python scripts/lint-env-has-reader.py            # report + exit 1 on new violations
"""
from __future__ import annotations

import ast
import io
import re
import sys
import tokenize
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS_FILE = REPO_ROOT / "src" / "mcp" / "config" / "settings.py"
FEATURES_FILE = REPO_ROOT / "src" / "mcp" / "config" / "features.py"
ENV_EXAMPLE_FILE = REPO_ROOT / ".env.example"

SELF_PATH = Path(__file__).resolve()

CODE_EXTS = {".py", ".ts", ".tsx", ".js", ".sh", ".yml", ".yaml", ".toml", ".json", ".swift"}
NO_SUFFIX_NAMES = {"Dockerfile", "Makefile"}
SKIP_DIR_PARTS = {
    "node_modules", ".git", ".venv", "dist", "build", "__pycache__",
    ".worktrees", ".claude", "archive", "superpowers", "docs", "tasks",
}

IDENT_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")

# A name mentioned only in a comment (or, for Python, only inside a docstring)
# is not a reader — it is the exact laundering vector this gate exists to
# reject ("# TODO: wire X", a log message, a docstring blurb). Comments are
# stripped before identifier extraction; string/docstring *contents* are
# still scanned for non-Python files (matches prior behavior and is out of
# scope for this pass — Python gets the strict treatment below because the
# stdlib tokenizer makes it free and exact).
_JS_LIKE_EXTS = {".ts", ".tsx", ".js", ".swift"}
_HASH_COMMENT_EXTS = {".sh", ".yml", ".yaml", ".toml"}
_HASH_COMMENT_NAMES = {"Dockerfile", "Makefile"}

_JS_COMMENT_OR_STRING_RE = re.compile(
    r"//[^\n]*"                        # line comment
    r"|/\*.*?\*/"                      # block comment
    r'|"(?:[^"\\\n]|\\.)*"'            # double-quoted string (no bare newline)
    r"|'(?:[^'\\\n]|\\.)*'"            # single-quoted string
    r"|`(?:[^`\\]|\\.)*`",             # template string (may span lines)
    re.DOTALL,
)


def _strip_js_like_comments(text: str) -> str:
    def _repl(m: "re.Match[str]") -> str:
        s = m.group(0)
        return " " if s.startswith("//") or s.startswith("/*") else s
    return _JS_COMMENT_OR_STRING_RE.sub(_repl, text)


def _strip_hash_comments(text: str) -> str:
    """Best-effort: drop from an unquoted '#' to end of line, tracking simple
    quote state per line. A '#' inside a quoted string that this heuristic
    misclassifies is an accepted false-negative for this file class (shell/
    YAML/TOML/Dockerfile/Makefile) — Python gets the exact tokenizer-based
    treatment instead since that is where the demonstrated bypass lived."""
    out_lines = []
    for line in text.split("\n"):
        cut = None
        in_s = in_d = False
        for i, ch in enumerate(line):
            if ch == "'" and not in_d:
                in_s = not in_s
            elif ch == '"' and not in_s:
                in_d = not in_d
            elif ch == "#" and not in_s and not in_d:
                cut = i
                break
        out_lines.append(line[:cut] if cut is not None else line)
    return "\n".join(out_lines)


def _strip_python_comments(text: str) -> str:
    """Blank out only `#` COMMENT token spans, located precisely via the
    stdlib tokenizer (Python comments never span multiple lines, so each
    span is a single-line slice). Used only by the regex-fallback path
    below (unparseable files) — the primary path is AST-based and ignores
    comments by construction, since comments are not tokens in the AST at
    all. Falls back to the untouched text on any tokenize failure so a
    parse error never manufactures a false-clean reader index."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError, UnicodeDecodeError):
        return text
    lines = text.split("\n")
    for tok in tokens:
        if tok.type != tokenize.COMMENT:
            continue
        (srow, scol), (erow, ecol) = tok.start, tok.end
        if srow != erow:
            continue  # defensive; COMMENT tokens are always single-line
        line = lines[srow - 1]
        lines[srow - 1] = line[:scol] + " " * (ecol - scol) + line[ecol:]
    return "\n".join(lines)


def _extract_python_identifiers(text: str) -> set[str]:
    """AST-based reader extraction. Deliberately narrower than "every
    identifier-shaped token in the file": a name only counts if it is
    genuinely referenced as code, not merely present as text.

    Included:
      - `ast.Name` (any context) — `NAME`, `if NAME:`, `x = NAME`
      - `ast.Attribute.attr` — `config.NAME`
      - function/class def names and parameter names
      - string literals passed as a CALL ARGUMENT — `os.getenv("NAME")`,
        matches the env-var-string-literal read pattern without also
        matching a docstring (a docstring is an `ast.Expr(ast.Constant)`
        statement, never a call argument, so it is never in this pool)

    Excluded by construction (no special-casing needed — they simply never
    produce any of the node types above):
      - import statement targets (`ast.alias.name` / `.asname` are plain
        strings on the alias node, never an `ast.Name` — an unused
        `from config.settings import NAME` contributes nothing unless NAME
        is referenced again elsewhere in the file)
      - comments (not represented in the AST at all)
      - docstrings / any bare string statement (a `Constant` that is not a
        call argument)

    Falls back to the old regex-over-comment-stripped-text approach when
    the file fails to parse (rare — a handful of non-canonical Python
    fixtures/templates), which is coarser but was the entire behavior
    before this pass and is retained as a safety net rather than silently
    excluding unparseable files from the reader search.
    """
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return set(IDENT_RE.findall(_strip_python_comments(text)))

    idents: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            idents.add(node.id)
        elif isinstance(node, ast.Attribute):
            idents.add(node.attr)
        elif isinstance(node, ast.arg):
            idents.add(node.arg)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            idents.add(node.name)
        elif isinstance(node, ast.Call):
            for value in list(node.args) + [kw.value for kw in node.keywords]:
                if isinstance(value, ast.Constant) and isinstance(value.value, str):
                    idents.add(value.value)
    return idents


def _wrapper_transitive_readers(path: Path, reader_index: set[str]) -> set[str]:
    """Names referenced only inside a function body of settings.py/features.py
    still count as read if that function itself has an external caller —
    the wrapper-accessor shape (`embedding_version_for_domain` reading the
    module-level `EMBEDDING_MODEL_VERSION` as its `.get(domain, ...)`
    fallback, called externally from `core/utils/embeddings.py`,
    `app/routers/kb_admin.py`, `app/processor/jobs/reembed_chunks.py`) is
    real production wiring. Without this, any accessor-function pattern
    defined in the declaring files would make every constant it wraps look
    orphaned, purely because the accessor's own body lives inside a file
    this gate otherwise excludes from the reader search (to keep pure
    self-mentions — a comment, a second unrelated assignment — from
    counting).

    Deliberately does NOT chase multi-level indirection (a function calling
    another function in the same file that reads the name) — one hop is
    enough for every real case found in this codebase, and a name that
    needs two hops of internal indirection to find its own consumer is a
    genuinely different, weaker claim of "used" than the direct forms this
    gate otherwise accepts.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (SyntaxError, ValueError, OSError):
        return set()
    out: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if node.name not in reader_index:
            continue  # this function has no caller outside the declaring files
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name):
                out.add(sub.id)
            elif isinstance(sub, ast.Attribute):
                out.add(sub.attr)
    return out

# Each entry is a REAL, currently-existing violation this gate would
# otherwise fail on. Every entry below was independently re-confirmed against
# HEAD by this script's own reader search (line citations checked, not
# copied from prior text) before being added here. Two provenance classes:
#
#   - Entries citing an AF-id are grounded in one of the four 2026-08-11
#     audit documents or the 2026-07-15 verification — grep the id in
#     tasks/ to find the source paragraph.
#   - Entries citing a `GATE2-SELF-NNN` id were found by this gate's own
#     exhaustive scan of settings.py/features.py and confirmed (by direct
#     grep of all four audit documents for the exact name) to NOT be named
#     by any AF id — the audits are a human sample, not an exhaustive sweep
#     (tasks/2026-08-11-consolidated-audit.md §2, mechanism M3, says this of
#     itself). These are disclosed as self-found rather than misattributed
#     to an audit that never named them; each gets its own id specifically
#     so it stays individually re-checkable instead of hiding behind a
#     shared vague label.
ALLOWLIST: dict[str, str] = {
    # --- AF-047: "5 knobs, each appearing exactly once in src/mcp" ---
    # CERID_RSS_POLL_INTERVAL, SCAN_EXCLUDE_PATTERNS, ENABLE_AI_TRIAGE, and
    # CONTEXTUAL_CHUNKS_MODEL were all deleted from settings.py (P14 / P17
    # remediation) rather than allowlisted — SCHEDULE_SOURCE_POLL, the
    # per-source exclude_patterns field, the inbox_triage FEATURE_FLAGS key,
    # and call_internal_llm(stage="contextual_chunks") are the real levers.
    # Entries removed accordingly.
    # --- AF-010: CONTEXTUAL_BUDGET_USD_PER_TENANT_PER_MONTH deleted
    # (settings.py) rather than allowlisted — P17 remediation. Entry removed
    # accordingly.
    # --- AF-079: dead v1 quality-weight trio (QUALITY_WEIGHT_SUMMARY/_KEYWORDS/
    # _COMPLETENESS) was deleted from settings.py outright rather than
    # allowlisted — the symbols no longer exist, so no entry is needed.
    # NOTE: EMBEDDING_MODEL_VERSION (settings.py:1054) is deliberately NOT
    # listed here. An earlier pass of this allowlist misattributed AF-080
    # (which is about the sibling dict EMBEDDING_MODEL_VERSIONS_PER_DOMAIN,
    # already out of scope via the container-literal exclusion — it has no
    # os.getenv route at all) to this scalar, and fabricated a nonexistent
    # `get_embedding_model_version()` function with a false "zero callers"
    # claim. The real accessor is `embedding_version_for_domain`
    # (settings.py:1068), which has genuine non-test callers at
    # core/utils/embeddings.py:609, app/routers/kb_admin.py:625, and
    # app/processor/jobs/reembed_chunks.py:150, and reads
    # EMBEDDING_MODEL_VERSION as its `.get(domain, ...)` fallback default —
    # a real, wired read. `_wrapper_transitive_readers` resolves this
    # correctly (the accessor function has an external caller, so its body's
    # reference to EMBEDDING_MODEL_VERSION counts), so no allowlist entry is
    # needed or warranted.
    # --- 2026-08-11-reachability-audit.md findings (no numeric ids in that doc; cited by title) ---
    "ALERT_CHECK_INTERVAL_S": "reachability-audit 'the whole alerting config block is dead' — no scheduler/task reads any of the four ALERT_* knobs",
    "ALERT_MAX_PER_METRIC": "reachability-audit 'the whole alerting config block is dead', same finding as ALERT_CHECK_INTERVAL_S",
    "ALERT_WEBHOOK_TIMEOUT_S": "reachability-audit 'the whole alerting config block is dead', same finding as ALERT_CHECK_INTERVAL_S",
    "ALERT_EVENTS_MAX": "reachability-audit 'the whole alerting config block is dead', same finding as ALERT_CHECK_INTERVAL_S",
    # --- GATE2-SELF-*: found by this gate's own scan, NOT individually named
    # by any AF id in the four 2026-08-11 audit documents or the 2026-07-15
    # verification — confirmed by direct grep against all four before filing
    # (see the note below the table). Disclosed as such rather than
    # attributed to an audit that never named them: these are the same M3
    # mechanism (tasks/2026-08-11-consolidated-audit.md §2), surfaced because
    # this gate's scan is exhaustive over settings.py/features.py and the
    # audits were a human sample, not a full sweep (the consolidated audit
    # says this of itself in §2's M3 writeup). Each id below is a distinct,
    # independently re-checkable finding — not a shared label — precisely so
    # a self-found instance can't hide behind a vague shared tag.
    "CLASSIFICATION_ENABLED": "GATE2-SELF-001 — settings.py:1525, zero readers repo-wide of CERID_ENTERPRISE's sibling flag; not named by any AF id",
    "EMPIRICAL_MEMORY_STABILITY_DAYS": "GATE2-SELF-002 — settings.py:813, zero readers repo-wide; not named by any AF id",
    "ENABLE_DEGRADATION_TIERS": "GATE2-SELF-003 — features.py:467, zero readers repo-wide; not named by any AF id",
    "ENABLE_TEMPORAL_PROXIMITY_BOOST": "GATE2-SELF-004 — features.py:432, zero readers repo-wide; not named by any AF id (distinct from the CLI/eval-doc env-var overrides used ad hoc in tasks/*.md soak runs, which are shell exports, not code readers)",
    "TEMPORAL_PROXIMITY_WEIGHT": "GATE2-SELF-005 — features.py:433, zero readers repo-wide, same declaration block as ENABLE_TEMPORAL_PROXIMITY_BOOST (GATE2-SELF-004); not named by any AF id",
    "TEMPORAL_PROXIMITY_HALFLIFE_DAYS": "GATE2-SELF-006 — features.py:434, zero readers repo-wide, same declaration block as ENABLE_TEMPORAL_PROXIMITY_BOOST (GATE2-SELF-004); not named by any AF id",
    "LONGMEMEVAL_INGEST_PARALLEL": "GATE2-SELF-007 — settings.py:1159, zero readers repo-wide; not named by any AF id",
    "LONGMEMEVAL_SCORER": "GATE2-SELF-008 — settings.py:1160, zero readers repo-wide (named as a concept in several tasks/*.md eval handoffs, e.g. as a shell env-var override for ad hoc soak runs, but never as a code call site); not named by any AF id",
    "SYNC_CRDT_ENABLED": "GATE2-SELF-009 — settings.py:1556, zero readers repo-wide, plain literal True with no consumer; not named by any AF id",
    "WS_HEARTBEAT_INTERVAL_S": "GATE2-SELF-010 — settings.py:1553, zero readers repo-wide, plain literal with no consumer; not named by any AF id",
    "ENABLE_SENTRY": "GATE2-SELF-011 — settings.py:17, zero code readers; app/routers/settings.py:1292-1298 carries a comment admitting init_sentry() gates on SENTRY_DSN_MCP/SENTRY_DSN alone and ENABLE_SENTRY 'is NOT read anywhere in the init path' — the exact §1.1 pattern (editing the comment, not the code) applied to itself; not named by any AF id",
}


def _module_level_declared_names(path: Path) -> list[tuple[str, str | None, int]]:
    """Return (attr_name, env_var_string_or_None, lineno) for every module-level
    getenv-backed or plain-scalar-literal assignment. env_var_string is the
    literal passed to getenv/environ.get, when present — it is what actually
    shows up in .env.example, and can differ from the attribute name
    (STORAGE_LIMIT_MB reads CERID_STORAGE_LIMIT_MB)."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out: list[tuple[str, str | None, int]] = []
    for node in tree.body:
        value: ast.expr | None
        if isinstance(node, ast.Assign):
            targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
            value = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            targets = [node.target.id]
            value = node.value
        else:
            continue
        if not targets or value is None:
            continue

        has_call = any(isinstance(n, ast.Call) for n in ast.walk(value))
        is_container = isinstance(value, (ast.Dict, ast.List, ast.Set, ast.Tuple))

        env_str: str | None = None
        is_env = False
        if has_call:
            for sub in ast.walk(value):
                if not isinstance(sub, ast.Call):
                    continue
                f = sub.func
                matches_getenv = (
                    isinstance(f, ast.Attribute) and f.attr == "getenv"
                    and isinstance(f.value, ast.Name) and f.value.id == "os"
                )
                matches_environ_get = (
                    isinstance(f, ast.Attribute) and f.attr == "get"
                    and isinstance(f.value, ast.Attribute) and f.value.attr == "environ"
                )
                if matches_getenv or matches_environ_get:
                    is_env = True
                    if sub.args and isinstance(sub.args[0], ast.Constant) and isinstance(sub.args[0].value, str):
                        env_str = sub.args[0].value

        for name in targets:
            if name.startswith("_"):
                continue
            if is_env:
                out.append((name, env_str, node.lineno))
            elif not has_call and not is_container:
                out.append((name, None, node.lineno))
    return out


def _build_reader_index() -> set[str]:
    """Every identifier token that appears anywhere in source/config, outside
    the declaring files, the generators, tests, and generated docs."""
    generator_paths = {
        (REPO_ROOT / "scripts" / "gen_env_example.py").resolve(),
        (REPO_ROOT / "scripts" / "gen_tier_matrix.py").resolve(),
    }
    exclude_files = {SETTINGS_FILE.resolve(), FEATURES_FILE.resolve(), SELF_PATH} | generator_paths
    idents: set[str] = set()
    for path in REPO_ROOT.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in CODE_EXTS and path.name not in NO_SUFFIX_NAMES:
            continue
        # Skip on parts RELATIVE to the repo root. Absolute parts made the
        # gate skip every file when the checkout itself lives under a
        # skip-named dir — an agent worktree at .claude/worktrees/* scanned
        # nothing and reported every declared name reader-less (2026-08-13).
        rel_parts = set(path.relative_to(REPO_ROOT).parts)
        if SKIP_DIR_PARTS & rel_parts:
            continue
        if path.resolve() in exclude_files:
            continue
        if "tests" in rel_parts or path.name.startswith("test_") or ".test." in path.name:
            continue
        if path.name == ".env.example":
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if path.suffix == ".py":
            idents.update(_extract_python_identifiers(text))
        elif path.suffix in _JS_LIKE_EXTS:
            idents.update(IDENT_RE.findall(_strip_js_like_comments(text)))
        elif path.suffix in _HASH_COMMENT_EXTS or path.name in _HASH_COMMENT_NAMES:
            idents.update(IDENT_RE.findall(_strip_hash_comments(text)))
        else:
            idents.update(IDENT_RE.findall(text))
    return idents


def main() -> int:
    if not SETTINGS_FILE.is_file() or not FEATURES_FILE.is_file():
        print("::error::[env-has-reader] settings.py or features.py missing", file=sys.stderr)
        return 2

    declared: dict[str, tuple[str | None, int]] = {}
    for path in (SETTINGS_FILE, FEATURES_FILE):
        for name, env_str, lineno in _module_level_declared_names(path):
            if name not in declared:
                declared[name] = (env_str, lineno)

    if not declared:
        print("::error::[env-has-reader] found no declared names in settings.py/features.py — "
              "the oracle is empty, something is wrong with the scan.", file=sys.stderr)
        return 2

    reader_index = _build_reader_index()
    transitive_readers = (
        _wrapper_transitive_readers(SETTINGS_FILE, reader_index)
        | _wrapper_transitive_readers(FEATURES_FILE, reader_index)
    )

    violations: list[tuple[str, int]] = []
    for name, (_env_str, lineno) in sorted(declared.items()):
        if name in reader_index or name in transitive_readers:
            continue
        if name in ALLOWLIST:
            continue
        violations.append((name, lineno))

    if violations:
        print("::error::[env-has-reader] declared config names with zero non-declaration "
              "readers, not yet allowlisted — these can still be promoted into .env.example "
              "with no code ever reading them:", file=sys.stderr)
        for name, lineno in violations:
            loc = "settings.py" if name in {n for n, _, _ in _module_level_declared_names(SETTINGS_FILE)} else "features.py"
            print(f"  {name}  ({loc}:{lineno})", file=sys.stderr)
        print(
            "\nEither wire a real reader, delete the declaration, or — only if this is a "
            "confirmed-orphaned name (an AF-id from an existing audit, or a new GATE2-SELF-NNN "
            "id for one this gate itself found) — add it to ALLOWLIST in "
            "scripts/lint-env-has-reader.py with a one-line reason citing the id.",
            file=sys.stderr,
        )
        return 1

    print(
        f"[env-has-reader] OK — {len(declared)} declared names checked, "
        f"{len(declared) - len(ALLOWLIST)} with a live reader, "
        f"{len(ALLOWLIST)} allowlisted orphans (AF-id audit findings + GATE2-SELF ids "
        f"this gate found independently)."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
