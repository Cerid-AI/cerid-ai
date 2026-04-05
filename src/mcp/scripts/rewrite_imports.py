#!/usr/bin/env python3
# scripts/rewrite_imports.py — One-time libcst import rewriter for Phase C
"""Rewrites imports from old top-level modules to app.* prefixed paths.

Usage: cd src/mcp && python3 scripts/rewrite_imports.py [--dry-run]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import libcst as cst

# Modules that moved under app/
MOVED_MODULES = {
    "routers", "middleware", "services", "parsers", "db", "sync",
    "models", "stores", "eval", "deps", "tools", "main", "scheduler",
}

# Directories to skip (core/ must not be rewritten)
SKIP_DIRS = {"core", "__pycache__", ".git", "node_modules"}


class ImportRewriter(cst.CSTTransformer):
    """Rewrites `from X.Y import Z` -> `from app.X.Y import Z` for moved modules."""

    def __init__(self) -> None:
        self.changes: list[str] = []

    def _should_rewrite(self, module_parts: list[str]) -> bool:
        return bool(module_parts) and module_parts[0] in MOVED_MODULES

    def leave_ImportFrom(
        self, original: cst.ImportFrom, updated: cst.ImportFrom
    ) -> cst.ImportFrom:
        if updated.module is None:
            return updated
        parts = []
        node = updated.module
        while isinstance(node, cst.Attribute):
            parts.insert(0, node.attr.value)
            node = node.value
        if isinstance(node, cst.Name):
            parts.insert(0, node.value)

        if not self._should_rewrite(parts):
            return updated

        new_parts = ["app"] + parts
        new_module: cst.BaseExpression = cst.Name(new_parts[0])
        for part in new_parts[1:]:
            new_module = cst.Attribute(value=new_module, attr=cst.Name(part))

        self.changes.append(f"  from {'.'.join(parts)} -> from {'.'.join(new_parts)}")
        return updated.with_changes(module=new_module)

    def leave_Import(
        self, original: cst.Import, updated: cst.Import
    ) -> cst.Import:
        if not isinstance(updated.names, (list, tuple)):
            return updated
        new_names = []
        changed = False
        for alias in updated.names:
            if not isinstance(alias, cst.ImportAlias):
                new_names.append(alias)
                continue
            parts = []
            node = alias.name
            while isinstance(node, cst.Attribute):
                parts.insert(0, node.attr.value)
                node = node.value
            if isinstance(node, cst.Name):
                parts.insert(0, node.value)

            if self._should_rewrite(parts):
                new_parts = ["app"] + parts
                new_name: cst.BaseExpression = cst.Name(new_parts[0])
                for part in new_parts[1:]:
                    new_name = cst.Attribute(value=new_name, attr=cst.Name(part))
                new_names.append(alias.with_changes(name=new_name))
                self.changes.append(f"  import {'.'.join(parts)} -> import {'.'.join(new_parts)}")
                changed = True
            else:
                new_names.append(alias)
        return updated.with_changes(names=new_names) if changed else updated


def rewrite_file(path: Path, dry_run: bool = False) -> list[str]:
    source = path.read_text()
    try:
        tree = cst.parse_module(source)
    except cst.ParserSyntaxError:
        return []
    rewriter = ImportRewriter()
    new_tree = tree.visit(rewriter)
    if rewriter.changes and not dry_run:
        path.write_text(new_tree.code)
    return rewriter.changes


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--scope",
        choices=["app", "tests", "all"],
        default="app",
        help="Which directories to rewrite (default: app only)",
    )
    args = parser.parse_args()

    root = Path(".")

    # Determine which directories to scan
    if args.scope == "app":
        scan_roots = [root / "app"]
    elif args.scope == "tests":
        scan_roots = [root / "tests"]
    else:
        scan_roots = [root]

    total_changes = 0
    for scan_root in scan_roots:
        for py_file in sorted(scan_root.rglob("*.py")):
            if any(part in SKIP_DIRS for part in py_file.parts):
                continue
            changes = rewrite_file(py_file, dry_run=args.dry_run)
            if changes:
                prefix = "[DRY RUN] " if args.dry_run else ""
                print(f"{prefix}{py_file}:")
                for c in changes:
                    print(c)
                total_changes += len(changes)

    print(f"\n{'Would rewrite' if args.dry_run else 'Rewrote'} {total_changes} imports.")
    if args.dry_run and total_changes:
        print("Run without --dry-run to apply changes.")


if __name__ == "__main__":
    main()
