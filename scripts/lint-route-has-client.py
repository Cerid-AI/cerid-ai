#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Contract gate: every FastAPI route must have a client somewhere.

Scope and honest limitation: a route path that appears as a text literal
inside an orphaned client file — one that is itself never imported or
built into any bundle — still counts as "has a client," because this
gate's corpus is every file under the six client dirs below, not the
subset of those files that anything actually reaches. A route can be
laundered past this gate by mentioning its path in a client file nobody
calls. For src/web specifically this hole is closed by a companion gate:
scripts/lint-web-reachability.py flags the orphaned client file itself
(no production code imports it), so a route laundered through a dead
src/web client file goes red there even though it passes here. The other
five client dirs (packages/desktop/src, packages/sdk,
packages/extension/src, packages/widget/src, packages/cli/src) have no
equivalent reachability gate today, so the same laundering vector remains
open for them.

Graduates Gate 6 ("Route-has-client") from
tasks/2026-08-11-consolidated-audit.md §3. The reachability audit found
full CRUD routers (alerts, webhook subscriptions, migration, several
kb_admin diagnostics) that are registered, tested, and dead — no component,
hook, settings-registry entry, SDK client, or desktop IPC bridge ever
requests them. A route with a passing backend test looks referenced to
any coverage-based or import-based check; nothing but a cross-tier scan
catches "server implements it, nobody calls it."

Mechanism (the ratchet, same shape as lint-route-response-model.py):

* AST-scan every ``@router.<method>("/path")`` under the full src/mcp/app/
  tree (not just src/mcp/app/routers/ — a real mounted router,
  app/processor/router.py, lives outside that directory; walking all of
  app/ catches it and any future router file wherever it lands) plus
  src/mcp/routers/ (no app.* import needed — runs in a slim container),
  same extraction approach as gen_router_registry.py /
  lint-route-response-model.py. Resolve each route's APIRouter(prefix=...)
  to get the full server-side path.
* A route "has a client" when its full path — path-param segments
  ``{like_this}`` treated as a wildcard, since callers interpolate a
  variable there instead of the literal name — appears as a text literal,
  in LIVE code, anywhere under the client surfaces: src/web/src,
  packages/desktop/src, packages/sdk, packages/extension/src,
  packages/widget/src, packages/cli/src. Mirrors the reachability audit's
  own "six client dirs" methodology.
* Only source/code files count, and only their live code — never prose,
  never dead code, never test-only fixtures:
    - ``.md`` is excluded from the scan entirely: a route path written into
      a README, TODO, or design note is not a caller, and the whole point
      of this gate is to distinguish "someone wrote it down" from
      "something fetches it" (the exact laundering pattern the
      consolidated audit names as its dominant failure mode).
    - Whole-line and block comments are stripped before matching, so a
      `// TODO: wire /admin/kb/reindex` note in a .ts file can't flip the
      gate green either — only an actual literal in executable code counts.
    - Test/mock/fixture/story directories (``__tests__``, ``__mocks__``,
      ``mocks``, ``fixtures``, ``stories``, ``tests``) and ``*.test.*`` /
      ``*.spec.*`` / ``*.stories.*`` files are excluded from the client
      corpus — a route referenced only from test scaffolding has no real
      caller, same reasoning as excluding backend test files.
    - ``if (false) { ... }`` / ``if (0) { ... }`` dead branches are
      stripped (brace-balanced) before matching — an unreachable branch is
      not a caller.
    - A bare ``const``/``let``/``var`` literal-string declaration whose
      name is never referenced again anywhere else in the file is masked
      out before matching — a route path assigned to a dead, unused local
      constant is not a caller either. (A declaration whose name IS used
      elsewhere — including inside a `` `${NAME}` `` template used by a
      real call — is left untouched; this is what
      ``_resolve_local_const_templates`` depends on.)
* The grandfather allowlist (``route_has_client_allowlist.txt``) holds
  two kinds of entries, both requiring a one-line reason:
    - ``VIOLATION`` — a route the reachability audit found genuinely
      unreachable (alerts, webhook subscriptions, migration, kb_admin
      diagnostics). Debt, not scope. Shrinks as routes get wired.
    - ``API-ONLY`` — a route that is intentionally server-only: SDK/curl
      surfaces, inbound webhook receivers, health/infra probes. Not
      debt; stays allowlisted by design.
  The gate fails when a route with NO client and NO allowlist entry
  appears (new unwired route), or when a VIOLATION entry now has a
  client (stale — must be removed via --update).

Usage:
    python scripts/lint-route-has-client.py            # report current state (exit 0)
    python scripts/lint-route-has-client.py --check    # CI gate (exit 1 on new debt or stale entries)
    python scripts/lint-route-has-client.py --update   # reseed VIOLATION entries to current unwired set

sync-manifest: allow-internal-ref — route_has_client_allowlist.txt is internal_only (its entries
name internal-only modules); the Makefile guards this gate on allowlist
presence, so the public mirror skips it until a public allowlist is seeded.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ALLOWLIST_PATH = REPO_ROOT / "scripts" / "route_has_client_allowlist.txt"

# Walking the whole app/ tree (not just app/routers/) so a real mounted
# router living elsewhere — e.g. app/processor/router.py — isn't invisible
# to this gate. Confirmed by grep (2026-08-11) that app/routers/ + app/
# + src/mcp/routers/ is the complete set of files with an actual
# ``@router.<method>(...)`` decorator anywhere in src/mcp/ (config/features.py
# has one lexical match but it's inside a docstring example, not real code —
# AST-based extraction never sees it).
ROUTER_SCAN_DIRS = [
    REPO_ROOT / "src" / "mcp" / "app",
    REPO_ROOT / "src" / "mcp" / "routers",
]

# Mirrors the reachability audit's own "six client dirs" (2026-08-11).
CLIENT_SCAN_DIRS = [
    REPO_ROOT / "src" / "web" / "src",
    REPO_ROOT / "packages" / "desktop" / "src",
    REPO_ROOT / "packages" / "sdk",
    REPO_ROOT / "packages" / "extension" / "src",
    REPO_ROOT / "packages" / "widget" / "src",
    REPO_ROOT / "packages" / "cli" / "src",
]
# .md is deliberately NOT scanned: a route path documented in a README,
# TODO, or scratch note is not a caller. Only source/code files count.
CLIENT_FILE_EXTS = {".ts", ".tsx", ".py", ".swift", ".sh"}
CLIENT_EXCLUDE_PARTS = {
    "node_modules", "dist", "build", "out", "release", "__pycache__", ".mypy_cache", ".vite-temp",
    # Test/mock/fixture directories are not a real caller — a route path that
    # only appears in test scaffolding is exactly as unreachable as one that
    # only appears in a backend pytest file (which this gate already treats
    # as unwired by only scanning the client dirs at all).
    "__tests__", "__mocks__", "mocks", "fixtures", "stories", "tests",
}
# Filename substrings that mark a file as test/mock scaffolding rather than
# production client code, even when it isn't inside an excluded directory.
_CLIENT_EXCLUDE_FILENAME_MARKERS = (".test.", ".spec.", ".stories.", ".mock.")

# Line-comment markers by extension, for whole-line-comment stripping.
_LINE_COMMENT_PREFIXES: dict[str, str] = {
    ".ts": "//",
    ".tsx": "//",
    ".swift": "//",
    ".py": "#",
    ".sh": "#",
}
# Extensions with C-style block comments to also strip.
_BLOCK_COMMENT_EXTS = {".ts", ".tsx", ".swift"}
_BLOCK_COMMENT_RE = re.compile(r"/\*.*?\*/", re.DOTALL)


def _strip_comments(source: str, suffix: str) -> str:
    """Strip whole-line and (where applicable) block comments.

    A bare comment mention of a route path (e.g. ``// TODO: wire
    /admin/kb/reindex``) is not a caller — only code counts. This is a
    line-level heuristic (an inline trailing comment after real code on
    the same line is not stripped, to avoid mis-parsing string literals
    like "https://..."), which is deliberately conservative: it removes
    comment-only lines and multi-line block comments, not every
    occurrence of a comment token.
    """
    if suffix in _BLOCK_COMMENT_EXTS:
        source = _BLOCK_COMMENT_RE.sub("", source)
    prefix = _LINE_COMMENT_PREFIXES.get(suffix)
    if not prefix:
        return source
    kept_lines = []
    for line in source.splitlines():
        if line.strip().startswith(prefix):
            continue
        kept_lines.append(line)
    return "\n".join(kept_lines)

_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}

# Per-line suppression token (rare — prefer the allowlist).
_SUPPRESS_TOKEN = "route-has-client-allowed"


def _name_of(node: ast.expr) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _extract_prefix(tree: ast.AST) -> str:
    """Pull prefix= out of the module's APIRouter(...) call, if literal."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        is_router_ctor = (isinstance(func, ast.Name) and func.id == "APIRouter") or (
            isinstance(func, ast.Attribute) and func.attr == "APIRouter"
        )
        if not is_router_ctor:
            continue
        for kw in node.keywords:
            if kw.arg == "prefix" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
                return kw.value.value
        return ""  # APIRouter() found with no prefix kwarg
    return ""


def _route_decorator(decorator: ast.expr) -> tuple[str, str] | None:
    """Return (METHOD, path) for ``@router.<method>("/path")``-shaped decorators."""
    if not isinstance(decorator, ast.Call):
        return None
    func = decorator.func
    if not isinstance(func, ast.Attribute):
        return None
    method = func.attr.lower()
    if method not in _HTTP_METHODS:
        return None
    if not isinstance(func.value, ast.Name):
        return None
    if not decorator.args:
        return None
    arg0 = decorator.args[0]
    if not (isinstance(arg0, ast.Constant) and isinstance(arg0.value, str)):
        return None
    return method.upper(), arg0.value


def _join_path(prefix: str, path: str) -> str:
    full = f"{prefix.rstrip('/')}/{path.lstrip('/')}" if prefix else path
    full = re.sub(r"/+", "/", full)
    if len(full) > 1 and full.endswith("/"):
        full = full[:-1]
    return full or "/"


def _line_suppressed(source_lines: list[str], lineno: int) -> bool:
    return 0 < lineno <= len(source_lines) and _SUPPRESS_TOKEN in source_lines[lineno - 1]


class Route:
    __slots__ = ("module", "handler", "method", "path")

    def __init__(self, module: str, handler: str, method: str, path: str) -> None:
        self.module = module
        self.handler = handler
        self.method = method
        self.path = path

    @property
    def key(self) -> str:
        return f"{self.module}::{self.handler}::{self.method} {self.path}"


def collect_routes(router_dirs: list[Path] | None = None, rel_root: Path | None = None) -> list[Route]:
    router_dirs = router_dirs if router_dirs is not None else ROUTER_SCAN_DIRS
    rel_root = rel_root if rel_root is not None else REPO_ROOT
    routes: list[Route] = []
    for base in router_dirs:
        if not base.exists():
            continue
        for p in sorted(base.rglob("*.py")):
            if "__pycache__" in p.parts:
                continue
            try:
                source = p.read_text(encoding="utf-8")
                tree = ast.parse(source, filename=str(p))
            except (SyntaxError, UnicodeDecodeError):
                continue
            source_lines = source.splitlines()
            prefix = _extract_prefix(tree)
            try:
                rel = str(p.relative_to(rel_root)).replace("\\", "/")
            except ValueError:
                rel = str(p).replace("\\", "/")
            for node in ast.walk(tree):
                if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                for dec in node.decorator_list:
                    parsed = _route_decorator(dec)
                    if parsed is None:
                        continue
                    if _line_suppressed(source_lines, dec.lineno):
                        continue
                    method, raw_path = parsed
                    routes.append(Route(rel, node.name, method, _join_path(prefix, raw_path)))
    return routes


def _path_pattern(path: str) -> re.Pattern[str]:
    """Build a regex matching *path* as a literal, path-param segments wildcarded."""
    segments = [s for s in path.split("/") if s != ""]
    parts = []
    for seg in segments:
        if seg.startswith("{") and seg.endswith("}"):
            parts.append(r"[^/'\"`\s]+")
        else:
            parts.append(re.escape(seg))
    body = "/".join(parts) if parts else ""
    pattern = r"(?<![\w-])/" + body + r"(?![\w-])"
    return re.compile(pattern)


def _iter_client_files(client_dirs: list[Path] | None = None):
    client_dirs = client_dirs if client_dirs is not None else CLIENT_SCAN_DIRS
    for base in client_dirs:
        if not base.exists():
            continue
        for p in base.rglob("*"):
            if not p.is_file():
                continue
            if p.suffix not in CLIENT_FILE_EXTS:
                continue
            if CLIENT_EXCLUDE_PARTS & set(p.parts):
                continue
            if any(marker in p.name for marker in _CLIENT_EXCLUDE_FILENAME_MARKERS):
                continue
            yield p


# Matches a simple local string-constant assignment, e.g.
# ``const BASE = "/graph/tour"`` — deliberately conservative (no
# interpolation inside the assigned literal itself).
_CONST_ASSIGN_RE = re.compile(
    r"""\b(?:const|let|var)\s+(\w+)\s*(?::\s*string)?\s*=\s*(['"`])([^'"`\n${}]*)\2"""
)


def _resolve_local_const_templates(text: str) -> str:
    """Substitute ``${NAME}`` with NAME's locally-assigned string literal.

    Route clients commonly build a path from a local base constant
    (``const BASE = "/graph/tour"`` then `` `${BASE}/generate` ``) rather
    than writing the full path as one literal. Without this, the full
    path never appears as contiguous text and a genuinely wired route
    reads as unwired. Best-effort and file-local by design — it only
    resolves names assigned to a plain string literal earlier in the
    same file, never anything computed.
    """
    matches = _CONST_ASSIGN_RE.findall(text)
    if not matches:
        return text
    consts = {name: value for name, _quote, value in matches}
    for name, value in consts.items():
        text = text.replace("${" + name + "}", value)
    return text


# Matches a whole-line const/let/var literal-string declaration — anchored
# to the full line (unlike _CONST_ASSIGN_RE, which matches mid-line) so a
# masking replacement only ever touches an actual declaration statement,
# never a call argument that happens to look similar.
_DEAD_CONST_LINE_RE = re.compile(
    r"""^[ \t]*(?:export\s+)?(?:const|let|var)\s+(\w+)\s*(?::\s*string)?\s*=\s*(['"`])([^'"`\n]*)\2\s*;?\s*$""",
    re.MULTILINE,
)
_IDENT_RE = re.compile(r"[A-Za-z_$][\w$]*")


def _mask_unreferenced_literal_consts(text: str) -> str:
    """Blank the value of a const/let/var literal string declaration whose
    name is never referenced again anywhere else in the file.

    Closes the "dead constant" laundering vector: ``const DEAD_ROUTE =
    "/some/route"`` with DEAD_ROUTE never imported or used anywhere still
    puts the literal route-path text into the file, which would otherwise
    satisfy the gate for a route with zero real callers. A declaration
    whose name IS referenced elsewhere — including inside a ``${NAME}``
    template consumed by ``_resolve_local_const_templates`` below — is
    left completely untouched; the replacement is same-length so it never
    shifts any other match's offset.
    """
    matches = list(_DEAD_CONST_LINE_RE.finditer(text))
    if not matches:
        return text
    ident_counts: dict[str, int] = {}
    for ident in _IDENT_RE.findall(text):
        ident_counts[ident] = ident_counts.get(ident, 0) + 1
    out = text
    for m in matches:
        name, _quote, value = m.group(1), m.group(2), m.group(3)
        if not value or ident_counts.get(name, 0) > 1:
            continue
        start, end = m.span(3)
        out = out[:start] + ("\0" * len(value)) + out[end:]
    return out


_DEAD_IF_RE = re.compile(r"if\s*\(\s*(?:false|0)\s*\)\s*\{")


def _strip_dead_branches(source: str, suffix: str) -> str:
    """Strip ``if (false) { ... }`` / ``if (0) { ... }`` dead branches.

    A route path fetched only inside an unreachable branch is not a real
    caller. Brace-balanced scan, best-effort (a stray ``{``/``}`` inside a
    string literal within the block could throw off the match) — accepted
    as a lint-heuristic limitation, not a load-bearing security boundary.
    """
    if suffix not in (".ts", ".tsx"):
        return source
    out = []
    i = 0
    n = len(source)
    while True:
        m = _DEAD_IF_RE.search(source, i)
        if not m:
            out.append(source[i:])
            break
        out.append(source[i:m.start()])
        depth = 1
        j = m.end()
        while j < n and depth > 0:
            if source[j] == "{":
                depth += 1
            elif source[j] == "}":
                depth -= 1
            j += 1
        i = j
    return "".join(out)


def _load_client_corpus(client_dirs: list[Path] | None = None) -> str:
    chunks = []
    for p in _iter_client_files(client_dirs):
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        text = _strip_comments(text, p.suffix)
        text = _strip_dead_branches(text, p.suffix)
        text = _mask_unreferenced_literal_consts(text)
        text = _resolve_local_const_templates(text)
        chunks.append(text)
    return "\n".join(chunks)


def collect_unwired(routes: list[Route], corpus: str) -> list[Route]:
    unwired = []
    for r in routes:
        if not _path_pattern(r.path).search(corpus):
            unwired.append(r)
    return unwired


# ---------------------------------------------------------------------------
# Allowlist I/O
# ---------------------------------------------------------------------------

_HEADER = (
    "# Grandfather allowlist: FastAPI routes with no client reference TODAY.\n"
    "# Enforced by scripts/lint-route-has-client.py --check.\n"
    "#\n"
    "# Format per line: <CATEGORY>|<key>|<reason>\n"
    "#   CATEGORY = VIOLATION   -- real reachability debt (audit-documented). Shrinks\n"
    "#                             only; remove the line once a client is wired.\n"
    "#              API-ONLY    -- intentionally server-only (SDK/curl surface,\n"
    "#                             inbound webhook receiver, health/infra probe).\n"
    "#                             Not debt; stays by design.\n"
    "#   key      = <module>::<handler>::<METHOD> <path>\n"
    "#\n"
    "# --update reseeds VIOLATION entries to the current unwired set (does not\n"
    "# touch API-ONLY entries — those are a design classification, not a debt count).\n"
    "# tasks/2026-08-11-reachability-audit.md is the source for every VIOLATION reason.\n"
)


def _load_allowlist(allowlist_path: Path) -> dict[str, tuple[str, str]]:
    """Return {key: (category, reason)}."""
    if not allowlist_path.exists():
        return {}
    out: dict[str, tuple[str, str]] = {}
    for line in allowlist_path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        parts = s.split("|", 2)
        if len(parts) != 3:
            continue
        category, key, reason = parts
        out[key] = (category, reason)
    return out


def _write_allowlist(entries: dict[str, tuple[str, str]], allowlist_path: Path) -> None:
    lines = [_HEADER.rstrip("\n")]
    for key in sorted(entries):
        category, reason = entries[key]
        lines.append(f"{category}|{key}|{reason}")
    allowlist_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="CI gate: exit 1 on new unwired routes or stale VIOLATION entries")
    ap.add_argument("--update", action="store_true", help="Reseed VIOLATION entries to the current unwired set")
    ap.add_argument("--router-dir", action="append", type=Path, help="Override router scan dir (repeatable). Default: src/mcp/app/routers, src/mcp/routers.")
    ap.add_argument("--client-dir", action="append", type=Path, help="Override client scan dir (repeatable). Default: the six client dirs.")
    ap.add_argument("--allowlist", type=Path, help="Override allowlist file path.")
    ap.add_argument("--rel-root", type=Path, help="Root used to compute route module rel-paths (default: repo root).")
    args = ap.parse_args(argv)

    router_dirs = args.router_dir if args.router_dir else ROUTER_SCAN_DIRS
    client_dirs = args.client_dir if args.client_dir else CLIENT_SCAN_DIRS
    allowlist_path = args.allowlist if args.allowlist else ALLOWLIST_PATH
    rel_root = args.rel_root if args.rel_root else REPO_ROOT

    routes = collect_routes(router_dirs, rel_root)
    corpus = _load_client_corpus(client_dirs)
    unwired = collect_unwired(routes, corpus)
    unwired_keys = {r.key: r for r in unwired}
    allow = _load_allowlist(allowlist_path)

    if args.update:
        api_only = {k: v for k, v in allow.items() if v[0] == "API-ONLY"}
        stale_violation_reasons = {k: v[1] for k, v in allow.items() if v[0] == "VIOLATION"}
        new_entries = dict(api_only)
        for key in unwired_keys:
            if key in api_only:
                continue
            reason = stale_violation_reasons.get(
                key, "reachability audit 2026-08-11 (tasks/2026-08-11-reachability-audit.md) — needs a written reason"
            )
            new_entries[key] = ("VIOLATION", reason)
        _write_allowlist(new_entries, allowlist_path)
        print(f"wrote {allowlist_path} ({len(new_entries)} entries, {len(unwired_keys)} currently unwired)")
        return 0

    allowed_keys = set(allow)
    new_debt = sorted(k for k in unwired_keys if k not in allowed_keys)
    # Stale: allowlisted VIOLATION whose route either now has a client, or no
    # longer exists. API-ONLY entries never go stale on client-appearance —
    # they're a design choice, not a debt tracker — but do go stale if the
    # route itself was removed.
    all_route_keys = {r.key for r in routes}
    stale = []
    for key, (category, _reason) in allow.items():
        if key not in all_route_keys:
            stale.append((key, "route no longer exists"))
        elif category == "VIOLATION" and key not in unwired_keys:
            stale.append((key, "now has a client"))

    if not args.check:
        print(
            f"[route-has-client] {len(routes)} routes scanned; {len(unwired_keys)} unwired; "
            f"{len(allow)} allowlisted; {len(new_debt)} new; {len(stale)} stale."
        )
        return 0

    if not new_debt and not stale:
        print(f"[route-has-client] OK — {len(unwired_keys)} unwired routes, all allowlisted.")
        return 0

    if new_debt:
        print(
            "\n::error::[route-has-client] "
            f"{len(new_debt)} NEW route(s) with no client anywhere under "
            "src/web/src, packages/desktop/src, packages/sdk, packages/extension/src, "
            "packages/widget/src, packages/cli/src — wire a caller, or if this is "
            "intentionally API-only, add an API-ONLY line to "
            f"{allowlist_path}.",
            file=sys.stderr,
        )
        for k in new_debt:
            print(f"  NEW  {k}", file=sys.stderr)
    if stale:
        print(
            f"\n::error::[route-has-client] {len(stale)} stale allowlist entr(y/ies) — "
            "run `python scripts/lint-route-has-client.py --update` to ratchet VIOLATION "
            "entries down (route no longer exists / route removed entries need manual "
            "cleanup for API-ONLY lines).",
            file=sys.stderr,
        )
        for k, why in stale:
            print(f"  STALE {k}  ({why})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
