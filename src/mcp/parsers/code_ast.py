# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""AST-based code parsers — extract structured summaries from source files.

Registers parsers for Python (.py) and JavaScript/TypeScript (.js, .ts, .jsx, .tsx)
that extract function signatures, class names, docstrings, and imports alongside
the full source text. Overrides the plain-text fallback in structured.py.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from parsers.registry import _MAX_TEXT_CHARS, register_parser  # type: ignore[attr-defined]

# ---------------------------------------------------------------------------
# Python AST parser
# ---------------------------------------------------------------------------

@register_parser([".py"])
def parse_python_ast(file_path: str) -> dict[str, Any]:
    """Parse Python files using stdlib ast for structured extraction."""
    path = Path(file_path)
    source = path.read_text(encoding="utf-8", errors="replace")

    summary_parts: list[str] = []

    try:
        tree = ast.parse(source, filename=path.name)
    except SyntaxError:
        # Fallback to plain text if AST parsing fails
        return {
            "text": source[:_MAX_TEXT_CHARS],
            "file_type": "py",
            "page_count": None,
        }

    # Module docstring
    docstring = ast.get_docstring(tree)
    if docstring:
        summary_parts.append(f"Module: {docstring.split(chr(10))[0]}")

    # Imports
    imports: list[str] = []
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append(alias.name)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                imports.append(f"{module}.{alias.name}")
    if imports:
        summary_parts.append(f"Imports: {', '.join(imports[:20])}")
        if len(imports) > 20:
            summary_parts.append(f"  ... and {len(imports) - 20} more imports")

    # Classes and functions
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.ClassDef):
            class_doc = ast.get_docstring(node)
            doc_line = f" — {class_doc.split(chr(10))[0]}" if class_doc else ""
            bases = [_name_of(b) for b in node.bases]
            bases_str = f"({', '.join(bases)})" if bases else ""
            summary_parts.append(f"class {node.name}{bases_str}{doc_line}")

            # Methods
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    prefix = "async " if isinstance(item, ast.AsyncFunctionDef) else ""
                    args = _format_args(item.args)
                    method_doc = ast.get_docstring(item)
                    doc_suffix = f" — {method_doc.split(chr(10))[0]}" if method_doc else ""
                    summary_parts.append(f"  {prefix}def {item.name}({args}){doc_suffix}")

        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            prefix = "async " if isinstance(node, ast.AsyncFunctionDef) else ""
            args = _format_args(node.args)
            func_doc = ast.get_docstring(node)
            doc_suffix = f" — {func_doc.split(chr(10))[0]}" if func_doc else ""
            summary_parts.append(f"{prefix}def {node.name}({args}){doc_suffix}")

    summary = "\n".join(summary_parts)
    text = f"--- AST Summary ---\n{summary}\n\n--- Source ---\n{source}" if summary else source

    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "py",
        "page_count": None,
    }


def _name_of(node: ast.expr) -> str:
    """Extract a readable name from an AST node."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name_of(node.value)}.{node.attr}"
    return "?"


def _format_args(args: ast.arguments) -> str:
    """Format function arguments into a compact signature string."""
    parts: list[str] = []
    for arg in args.args:
        if arg.arg == "self" or arg.arg == "cls":
            continue
        parts.append(arg.arg)
    if len(parts) > 5:
        return f"{', '.join(parts[:5])}, ... ({len(parts)} total)"
    return ", ".join(parts)


# ---------------------------------------------------------------------------
# JavaScript / TypeScript regex-based parser
# ---------------------------------------------------------------------------

# Patterns for extracting declarations from JS/TS
_JS_EXPORT_RE = re.compile(
    r"^export\s+(?:default\s+)?(?:async\s+)?(?:function|class|const|let|type|interface|enum)\s+(\w+)",
    re.MULTILINE,
)
_JS_FUNCTION_RE = re.compile(
    r"^(?:export\s+)?(?:async\s+)?function\s+(\w+)\s*\(([^)]*)\)",
    re.MULTILINE,
)
_JS_CLASS_RE = re.compile(
    r"^(?:export\s+)?(?:abstract\s+)?class\s+(\w+)(?:\s+extends\s+(\w+))?",
    re.MULTILINE,
)
_JS_ARROW_RE = re.compile(
    r"^(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(?:async\s+)?\([^)]*\)\s*(?::\s*\w+)?\s*=>",
    re.MULTILINE,
)
_JSDOC_RE = re.compile(r"/\*\*\s*(.*?)\s*\*/", re.DOTALL)
_JS_IMPORT_RE = re.compile(
    r"^import\s+.*?\s+from\s+['\"]([^'\"]+)['\"]",
    re.MULTILINE,
)


@register_parser([".js", ".ts", ".jsx", ".tsx"])
def parse_js_ts(file_path: str) -> dict[str, Any]:
    """Parse JavaScript/TypeScript files with regex-based structure extraction."""
    path = Path(file_path)
    source = path.read_text(encoding="utf-8", errors="replace")

    summary_parts: list[str] = []

    # Imports
    imports = _JS_IMPORT_RE.findall(source)
    if imports:
        summary_parts.append(f"Imports: {', '.join(imports[:15])}")
        if len(imports) > 15:
            summary_parts.append(f"  ... and {len(imports) - 15} more")

    # Exports
    exports = _JS_EXPORT_RE.findall(source)
    if exports:
        summary_parts.append(f"Exports: {', '.join(exports[:20])}")

    # Classes
    for match in _JS_CLASS_RE.finditer(source):
        name = match.group(1)
        extends = match.group(2)
        ext_str = f" extends {extends}" if extends else ""
        summary_parts.append(f"class {name}{ext_str}")

    # Functions
    for match in _JS_FUNCTION_RE.finditer(source):
        name = match.group(1)
        args = match.group(2).strip()
        if len(args) > 60:
            args = args[:57] + "..."
        summary_parts.append(f"function {name}({args})")

    # Arrow functions (exported)
    for match in _JS_ARROW_RE.finditer(source):
        name = match.group(1)
        summary_parts.append(f"const {name} = (...) =>")

    summary = "\n".join(summary_parts)
    text = f"--- Structure Summary ---\n{summary}\n\n--- Source ---\n{source}" if summary else source

    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": path.suffix.lstrip("."),
        "page_count": None,
    }
