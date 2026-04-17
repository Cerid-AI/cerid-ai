#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
Bidirectional sync between cerid-ai-internal and cerid-ai (public).

Usage:
    python scripts/sync-repos.py to-public  [--dry-run]
    python scripts/sync-repos.py from-public [--dry-run]
    python scripts/sync-repos.py validate
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import shutil
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# ANSI helpers
# ---------------------------------------------------------------------------
GREEN = "\033[32m"
YELLOW = "\033[33m"
RED = "\033[31m"
BOLD = "\033[1m"
RESET = "\033[0m"


def ok(msg: str) -> None:
    print(f"  {GREEN}OK{RESET}  {msg}")


def warn(msg: str) -> None:
    print(f"  {YELLOW}WARN{RESET}  {msg}")


def fail(msg: str) -> None:
    print(f"  {RED}FAIL{RESET}  {msg}")


# ---------------------------------------------------------------------------
# YAML loader (pyyaml with fallback)
# ---------------------------------------------------------------------------
try:
    import yaml  # type: ignore[import-untyped]

    def load_yaml(path: Path) -> dict:
        with open(path) as f:
            return yaml.safe_load(f)

except ImportError:
    # Minimal fallback — good enough for this manifest structure
    def load_yaml(path: Path) -> dict:  # type: ignore[misc]
        warn("pyyaml not installed; using basic text parser (install pyyaml for robustness)")
        text = path.read_text()
        import json, re, subprocess  # noqa: E401
        # Shell out to python -c with a trivial parser
        # Actually, just parse the simple structure ourselves.
        result: dict = {"internal_only": [], "mixed_files": [], "forbidden_in_public": []}
        current_key: str | None = None
        current_item: dict | None = None
        for line in text.splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                continue
            if not line.startswith(" ") and stripped.endswith(":"):
                current_key = stripped[:-1]
                current_item = None
                continue
            if current_key and stripped.startswith("- "):
                val = stripped[2:].strip()
                if current_key == "mixed_files" and val.startswith("path:"):
                    current_item = {"path": val.split(":", 1)[1].strip(), "hook_marker": None}
                    result.setdefault(current_key, []).append(current_item)
                else:
                    result.setdefault(current_key, []).append(val)
                    current_item = None
                continue
            if current_item and stripped.startswith("hook_marker:"):
                raw = stripped.split(":", 1)[1].strip()
                if raw == "null":
                    current_item["hook_marker"] = None
                else:
                    current_item["hook_marker"] = raw.strip("\"'")
        return result


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
INTERNAL_ROOT = Path(__file__).resolve().parent.parent
PUBLIC_ROOT = INTERNAL_ROOT.parent / "cerid-ai-public"
MANIFEST_PATH = INTERNAL_ROOT / ".sync-manifest.yaml"

SCANNABLE_EXTS = {".py", ".ts", ".tsx", ".yaml", ".yml", ".json", ".toml", ".cfg", ".md"}

# Deps to strip from requirements when syncing to public
INTERNAL_DEPS = {"structlog", "stripe"}


# ---------------------------------------------------------------------------
# Manifest helpers
# ---------------------------------------------------------------------------
def load_manifest() -> dict:
    if not MANIFEST_PATH.exists():
        print(f"{RED}Manifest not found:{RESET} {MANIFEST_PATH}")
        sys.exit(1)
    return load_yaml(MANIFEST_PATH)


# Directories + files that are NEVER synced to/from the public repo. These
# are local-developer state (git, caches, venvs) or secrets that must never
# leak across the boundary. Keep this list conservative: if in doubt, add.
_SYNC_SKIP_PREFIXES: tuple[str, ...] = (
    ".git/",
    ".worktrees/",      # git worktrees live outside main tree but rglob sees them
    "node_modules/",
    "__pycache__/",
    ".venv/",
    ".mypy_cache/",
    ".ruff_cache/",
    ".pytest_cache/",
    ".tox/",
    ".cache/",
    ".vite/",
    "dist/",
    "build/",
    ".next/",
    ".turbo/",
    ".DS_Store",
    "coverage/",
    ".coverage",
    "htmlcov/",
)

# Secrets — never leave the internal repo. .env.example is fine (template).
_SYNC_SKIP_BASENAMES: frozenset[str] = frozenset({
    ".env",
    ".env.age",
    ".env.local",
    ".env.production",
    ".env.development",
})


def _is_sync_skip(relpath: str) -> bool:
    """True when *relpath* must be skipped by the file-walk — caches, venvs,
    .git metadata, secrets. Checked before the internal_only manifest so
    this list is authoritative for local-developer / secret files regardless
    of manifest contents.
    """
    if any(relpath.startswith(p) for p in _SYNC_SKIP_PREFIXES):
        return True
    # Match on any component (e.g. packages/x/node_modules/...)
    for part in relpath.split("/"):
        if part in {"__pycache__", "node_modules", ".venv", ".mypy_cache",
                    ".ruff_cache", ".pytest_cache", ".DS_Store", ".next",
                    ".turbo"}:
            return True
    basename = relpath.rsplit("/", 1)[-1]
    if basename in _SYNC_SKIP_BASENAMES:
        return True
    # Wildcard .env.* (but NOT .env.example which is template)
    if basename.startswith(".env.") and basename != ".env.example":
        return True
    return False


def _match_double_star(relpath: str, pat: str) -> bool:
    """Match a pattern containing ``**`` against *relpath* using the four
    shapes actually used in .sync-manifest.yaml:

      * ``a/b/**``           — anything under directory ``a/b``
      * ``**/b/**``          — any path containing a ``b`` component
      * ``**/x.ext``         — any file named ``x.ext`` at any depth
      * ``a/**/x.ext``       — ``x.ext`` at any depth under ``a``

    Returns False for anything that doesn't match one of those shapes; the
    caller's ``fnmatch.fnmatch`` will still handle literal globs.

    Previously this function used ``relpath.startswith(pat.split('**')[0])``
    which, for patterns starting with ``**`` (prefix = ''), matched every
    file in the tree — causing every sync pass to report 0 copies.
    """
    # Shape: dir/**  →  anything at or below dir/
    if pat.endswith("/**"):
        dir_prefix = pat[:-3]
        if dir_prefix and (relpath == dir_prefix or relpath.startswith(dir_prefix + "/")):
            return True

    # Shape: **/name/**  →  any component equals name
    if pat.startswith("**/") and pat.endswith("/**"):
        component = pat[3:-3]
        if component and f"/{component}/" in f"/{relpath}/":
            return True

    # Shape: **/name  (or **/name.ext)  →  basename match at any depth
    if pat.startswith("**/") and not pat.endswith("/**"):
        tail = pat[3:]
        parts = relpath.split("/")
        # Match basename as literal OR as a glob (e.g. **/test_trading_*.py)
        if parts and (parts[-1] == tail or fnmatch.fnmatch(parts[-1], tail)):
            return True
        # Also match if any sub-suffix equals the pattern tail (e.g. **/a/b.py)
        if "/" in tail:
            if relpath == tail or relpath.endswith("/" + tail):
                return True

    # Shape: dir/**/name.ext  →  name under dir at any depth
    if "/**/" in pat and not pat.endswith("/**") and not pat.startswith("**/"):
        dir_prefix, tail = pat.split("/**/", 1)
        if relpath.startswith(dir_prefix + "/"):
            remainder = relpath[len(dir_prefix) + 1:]
            parts = remainder.split("/")
            if parts and (parts[-1] == tail or fnmatch.fnmatch(parts[-1], tail)):
                return True

    return False


def matches_internal_only(relpath: str, patterns: list[str]) -> bool:
    """Return True if *relpath* matches any internal_only pattern."""
    for pat in patterns:
        if fnmatch.fnmatch(relpath, pat):
            return True
        if "**" in pat and _match_double_star(relpath, pat):
            return True
    return False


def get_mixed_entry(relpath: str, mixed_files: list[dict]) -> dict | None:
    for entry in mixed_files:
        if entry["path"] == relpath:
            return entry
    return None


# ---------------------------------------------------------------------------
# Truncate / append at hook marker
# ---------------------------------------------------------------------------
def truncate_at_marker(content: str, marker: str) -> str:
    """Return content up to (but not including) the hook_marker line."""
    lines = content.splitlines(keepends=True)
    out: list[str] = []
    for line in lines:
        if line.rstrip().startswith(marker):
            # Remove any trailing blank lines before the marker
            while out and out[-1].strip() == "":
                out.pop()
            out.append("")  # single trailing newline
            break
        out.append(line)
    return "".join(out).rstrip("\n") + "\n"


def extract_hook_block(content: str, marker: str) -> str:
    """Return everything from the hook_marker line onward."""
    lines = content.splitlines(keepends=True)
    for i, line in enumerate(lines):
        if line.rstrip().startswith(marker):
            return "".join(lines[i:])
    return ""


# ---------------------------------------------------------------------------
# Requirements special handling
# ---------------------------------------------------------------------------
def strip_internal_deps(content: str) -> str:
    """Remove lines containing internal-only dependencies and their comment blocks."""
    lines = content.splitlines()
    out: list[str] = []
    skip_block = False
    for line in lines:
        lower = line.lower().strip()
        # Check if this is a comment preceding an internal dep
        if lower.startswith("#") and any(d in lower for d in INTERNAL_DEPS):
            skip_block = True
            continue
        if skip_block and (lower.startswith("#") or not lower):
            continue
        skip_block = False
        if any(lower.startswith(d) for d in INTERNAL_DEPS):
            continue
        out.append(line)
    return "\n".join(out).rstrip("\n") + "\n"


# ---------------------------------------------------------------------------
# Leak scanner
# ---------------------------------------------------------------------------
def scan_for_leaks(root: Path, forbidden: list[str], skip_patterns: list[str] | None = None) -> list[str]:
    """Scan all scannable files under *root* for forbidden strings.

    *skip_patterns* — fnmatch patterns (relative to root) to exclude from the
    scan. Used to ignore files not managed by the sync (e.g., repo-specific
    CLAUDE.md, README.md that each repo maintains independently).
    """
    issues: list[str] = []
    for fpath in sorted(root.rglob("*")):
        if not fpath.is_file():
            continue
        if fpath.suffix not in SCANNABLE_EXTS:
            continue
        rel = str(fpath.relative_to(root))
        if _is_sync_skip(rel):
            continue
        # Skip files excluded by internal_only patterns
        if skip_patterns and any(fnmatch.fnmatch(rel, p) for p in skip_patterns):
            continue
        try:
            text = fpath.read_text(errors="replace")
        except Exception:
            continue
        for term in forbidden:
            if term in text:
                issues.append(f"{rel}: contains '{term}'")
    return issues


# ---------------------------------------------------------------------------
# to-public
# ---------------------------------------------------------------------------
def cmd_to_public(manifest: dict, dry_run: bool) -> int:
    internal_only = manifest.get("internal_only", [])
    mixed_files = manifest.get("mixed_files", [])
    forbidden = manifest.get("forbidden_in_public", [])

    if not PUBLIC_ROOT.exists():
        fail(f"Public repo not found at {PUBLIC_ROOT}")
        return 1

    print(f"{BOLD}to-public{RESET}  {INTERNAL_ROOT} -> {PUBLIC_ROOT}")
    if dry_run:
        print(f"  {YELLOW}(dry run){RESET}")

    copied, skipped, truncated = 0, 0, 0

    for fpath in sorted(INTERNAL_ROOT.rglob("*")):
        if not fpath.is_file():
            continue
        rel = str(fpath.relative_to(INTERNAL_ROOT))
        if _is_sync_skip(rel):
            continue

        # Skip internal-only files
        if matches_internal_only(rel, internal_only):
            skipped += 1
            continue

        # Check if it's a mixed file
        mixed = get_mixed_entry(rel, mixed_files)
        if mixed:
            marker = mixed.get("hook_marker")
            if marker is None:
                # Null marker = handled manually, skip
                skipped += 1
                if dry_run:
                    warn(f"skip (manual): {rel}")
                continue
            # Truncate at marker
            content = fpath.read_text()
            if marker and marker in content:
                new_content = truncate_at_marker(content, marker)
                truncated += 1
                if dry_run:
                    print(f"  {YELLOW}TRUNC{RESET}  {rel}")
                else:
                    dest = PUBLIC_ROOT / rel
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    dest.write_text(new_content)
                copied += 1
                continue
            else:
                warn(f"hook marker '{marker}' not found in {rel} — copying as-is")

        # Regular file — copy
        if dry_run:
            print(f"  {GREEN}COPY{RESET}   {rel}")
        else:
            dest = PUBLIC_ROOT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Special handling for requirements.txt
            if rel == "src/mcp/requirements.txt":
                content = fpath.read_text()
                dest.write_text(strip_internal_deps(content))
            elif rel == "src/mcp/requirements.lock":
                content = fpath.read_text()
                dest.write_text(strip_internal_deps(content))
            else:
                shutil.copy2(fpath, dest)
        copied += 1

    print(f"\n  Copied: {copied}  Skipped: {skipped}  Truncated: {truncated}")

    # Leak scan
    if not dry_run:
        print(f"\n{BOLD}Scanning public repo for forbidden strings...{RESET}")
        leaks = scan_for_leaks(PUBLIC_ROOT, forbidden, skip_patterns=internal_only)
        if leaks:
            fail(f"Found {len(leaks)} leak(s):")
            for issue in leaks[:20]:
                print(f"    {RED}{issue}{RESET}")
            return 1
        ok("No forbidden strings detected in public repo")

    return 0


# ---------------------------------------------------------------------------
# from-public
# ---------------------------------------------------------------------------
def cmd_from_public(manifest: dict, dry_run: bool) -> int:
    internal_only = manifest.get("internal_only", [])
    mixed_files = manifest.get("mixed_files", [])

    if not PUBLIC_ROOT.exists():
        fail(f"Public repo not found at {PUBLIC_ROOT}")
        return 1

    print(f"{BOLD}from-public{RESET}  {PUBLIC_ROOT} -> {INTERNAL_ROOT}")
    if dry_run:
        print(f"  {YELLOW}(dry run){RESET}")

    copied, reattached = 0, 0

    for fpath in sorted(PUBLIC_ROOT.rglob("*")):
        if not fpath.is_file():
            continue
        rel = str(fpath.relative_to(PUBLIC_ROOT))
        if _is_sync_skip(rel):
            continue

        # Skip internal-only patterns (shouldn't exist in public, but guard)
        if matches_internal_only(rel, internal_only):
            continue

        mixed = get_mixed_entry(rel, mixed_files)
        if mixed:
            marker = mixed.get("hook_marker")
            if marker is None:
                if dry_run:
                    warn(f"skip (manual): {rel}")
                continue
            # Re-attach the internal hook block
            internal_file = INTERNAL_ROOT / rel
            if internal_file.exists():
                internal_content = internal_file.read_text()
                hook_block = extract_hook_block(internal_content, marker)
                if hook_block:
                    public_content = fpath.read_text()
                    new_content = public_content.rstrip("\n") + "\n\n" + hook_block
                    reattached += 1
                    if dry_run:
                        print(f"  {YELLOW}MERGE{RESET}  {rel}")
                    else:
                        internal_file.write_text(new_content)
                    copied += 1
                    continue
                else:
                    warn(f"No hook block found in internal {rel} for marker '{marker}'")

        # Regular copy
        if dry_run:
            print(f"  {GREEN}COPY{RESET}   {rel}")
        else:
            dest = INTERNAL_ROOT / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            # Special handling: re-append internal deps to requirements
            if rel == "src/mcp/requirements.txt":
                internal_req = INTERNAL_ROOT / rel
                if internal_req.exists():
                    old = internal_req.read_text()
                    # Extract internal-only lines
                    internal_lines = []
                    for line in old.splitlines():
                        lower = line.strip().lower()
                        if any(lower.startswith(d) or (d in lower and lower.startswith("#")) for d in INTERNAL_DEPS):
                            internal_lines.append(line)
                    public_content = fpath.read_text().rstrip("\n")
                    if internal_lines:
                        public_content += "\n\n" + "\n".join(internal_lines) + "\n"
                    dest.write_text(public_content)
                else:
                    shutil.copy2(fpath, dest)
            elif rel == "src/mcp/requirements.lock":
                # For lock files, copy public then append internal entries
                internal_lock = INTERNAL_ROOT / rel
                if internal_lock.exists():
                    old = internal_lock.read_text()
                    internal_lines = [
                        l for l in old.splitlines()
                        if any(d in l.lower() for d in INTERNAL_DEPS)
                    ]
                    public_content = fpath.read_text().rstrip("\n")
                    if internal_lines:
                        public_content += "\n" + "\n".join(internal_lines) + "\n"
                    dest.write_text(public_content)
                else:
                    shutil.copy2(fpath, dest)
            else:
                shutil.copy2(fpath, dest)
        copied += 1

    print(f"\n  Copied: {copied}  Re-attached hook blocks: {reattached}")
    return 0


# ---------------------------------------------------------------------------
# validate
# ---------------------------------------------------------------------------
def cmd_validate(manifest: dict) -> int:
    internal_only = manifest.get("internal_only", [])
    mixed_files = manifest.get("mixed_files", [])
    forbidden = manifest.get("forbidden_in_public", [])

    errors = 0

    # 1. Scan public repo for forbidden strings
    print(f"{BOLD}[1/3] Scanning public repo for forbidden strings...{RESET}")
    if PUBLIC_ROOT.exists():
        leaks = scan_for_leaks(PUBLIC_ROOT, forbidden, skip_patterns=internal_only)
        if leaks:
            errors += len(leaks)
            for issue in leaks[:30]:
                fail(issue)
        else:
            ok("No forbidden strings in public repo")
    else:
        warn(f"Public repo not found at {PUBLIC_ROOT} — skipping leak scan")

    # 2. Check internal-only files exist
    print(f"\n{BOLD}[2/3] Checking internal-only files exist...{RESET}")
    for pattern in internal_only:
        if "**" in pattern or "*" in pattern:
            # Glob pattern — check at least one match
            matches = list(INTERNAL_ROOT.glob(pattern))
            if not matches:
                warn(f"No files match pattern: {pattern}")
        else:
            if not (INTERNAL_ROOT / pattern).exists():
                warn(f"Missing internal-only file: {pattern}")

    # 3. Verify hook markers in mixed files
    print(f"\n{BOLD}[3/3] Verifying hook markers in mixed files...{RESET}")
    for entry in mixed_files:
        path = entry["path"]
        marker = entry.get("hook_marker")
        fpath = INTERNAL_ROOT / path
        if not fpath.exists():
            warn(f"Mixed file not found: {path}")
            continue
        if marker is None:
            ok(f"{path} (manual — no marker)")
            continue
        content = fpath.read_text()
        if marker in content:
            ok(f"{path} — marker found")
        else:
            fail(f"{path} — marker '{marker}' NOT FOUND")
            errors += 1

    # Summary
    print()
    if errors:
        fail(f"Validation failed with {errors} error(s)")
        return 1
    ok("All checks passed")
    return 0


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main() -> None:
    parser = argparse.ArgumentParser(
        description="Bidirectional sync between cerid-ai-internal and cerid-ai"
    )
    parser.add_argument(
        "command",
        choices=["to-public", "from-public", "validate"],
        help="Sync direction or validation",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show changes without writing")
    args = parser.parse_args()

    manifest = load_manifest()

    if args.command == "to-public":
        rc = cmd_to_public(manifest, args.dry_run)
    elif args.command == "from-public":
        rc = cmd_from_public(manifest, args.dry_run)
    elif args.command == "validate":
        rc = cmd_validate(manifest)
    else:
        rc = 1

    sys.exit(rc)


if __name__ == "__main__":
    main()
