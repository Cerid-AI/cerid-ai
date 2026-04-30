# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Code parser — tree-sitter AST splitter (Workstream E Phase 2b.3).

Closes the audit gap "Code: char-based chunking; functions/classes
broken mid-body" by parsing source files via the tree-sitter AST and
emitting one element per top-level definition. Each element carries
its file path, language, qualified name, and source line range so a
retrieval query for "the auth function" can match a chunk that
preserves the function's full body intact (rather than half-of-it
plus the start of the next function).

Phase 2b.3 ships Python support — by far the most-ingested code
language in this codebase. The dispatch table at module-level lets
:func:`parse_code` route ``.py`` to the Python parser; future
sub-phases register `tree_sitter_javascript`, `tree_sitter_typescript`,
`tree_sitter_go`, etc by appending to ``_LANGUAGE_PARSERS``.

Library choice: official ``tree-sitter`` Python bindings + per-language
grammar packages (``tree-sitter-python``). The older
``tree-sitter-languages`` mega-package is deprecated upstream; the
per-language packages are the 2026 path.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from core.ingest.parsers import ParsedElement

logger = logging.getLogger("ai-companion.ingest.parsers.code_ast")

# Lazy import — keep the module loadable even when tree-sitter isn't
# installed. The actual parser-creation lives in _python_parser() so
# the cost is paid only on the first parse.
_tree_sitter_available = True
try:
    import tree_sitter
except ImportError:
    _tree_sitter_available = False


# ---------------------------------------------------------------------------
# Per-language parsers
# ---------------------------------------------------------------------------


def _python_parser() -> Any:
    """Return a memoised tree-sitter Parser bound to the Python grammar."""
    if not _tree_sitter_available:
        raise ImportError(
            "tree-sitter is not installed — install tree-sitter and "
            "tree-sitter-python to enable code AST chunking.",
        )
    cache = _python_parser.__dict__
    if "_parser" in cache:
        return cache["_parser"]
    import tree_sitter_python  # type: ignore[import-not-found]

    lang = tree_sitter.Language(tree_sitter_python.language())
    parser = tree_sitter.Parser(lang)
    cache["_parser"] = parser
    return parser


# Map file-extension → (language_name, parser_factory). Future
# Phase 2b.3+ commits append to this table.
_LANGUAGE_PARSERS: dict[str, tuple[str, Callable[[], Any]]] = {
    ".py": ("python", _python_parser),
}


def _supported_extensions() -> list[str]:
    """Public helper for the dispatcher: which extensions are wired up."""
    return list(_LANGUAGE_PARSERS)


# ---------------------------------------------------------------------------
# Python AST → ParsedElement
# ---------------------------------------------------------------------------


# Python tree-sitter node types we care about at module level.
_PY_FUNCTION_NODES = {"function_definition"}
_PY_CLASS_NODES = {"class_definition"}
_PY_IMPORT_NODES = {"import_statement", "import_from_statement"}
_PY_DEF_NODES = _PY_FUNCTION_NODES | _PY_CLASS_NODES


def _node_text(source: bytes, node: Any) -> str:
    """Decode a tree-sitter node's source span as UTF-8."""
    return source[node.start_byte : node.end_byte].decode("utf-8", errors="replace")


def _node_name(node: Any, source: bytes) -> str:
    """Extract the identifier name from a function_definition or class_definition.

    Python function_definition shape:
        function_definition
          ├─ "def"
          ├─ identifier   <- name we want
          ├─ parameters
          └─ block (body)

    class_definition is the same shape with "class" instead of "def".
    """
    for child in node.children:
        if child.type == "identifier":
            return _node_text(source, child)
    return "<anonymous>"


def _walk_python(source: bytes, root: Any, file_path: str) -> list[ParsedElement]:
    """Walk a Python module's AST, emit one element per top-level def/class/import.

    Top-level only — nested functions/classes stay inside their
    parent's chunk so the parent's body remains coherent.
    """
    elements: list[ParsedElement] = []
    for child in root.children:
        if child.type in _PY_DEF_NODES:
            kind: Literal["CodeFunction", "CodeClass"] = (
                "CodeFunction" if child.type in _PY_FUNCTION_NODES else "CodeClass"
            )
            name = _node_name(child, source)
            text = _node_text(source, child)
            elements.append(
                {
                    "text": text,
                    "element_type": kind,
                    "metadata": {
                        "file": file_path,
                        "language": "python",
                        "name": name,
                        "qualified_name": name,
                        "start_line": child.start_point[0] + 1,
                        "end_line": child.end_point[0] + 1,
                    },
                },
            )
        elif child.type in _PY_IMPORT_NODES:
            text = _node_text(source, child)
            elements.append(
                {
                    "text": text,
                    "element_type": "CodeImport",
                    "metadata": {
                        "file": file_path,
                        "language": "python",
                        "start_line": child.start_point[0] + 1,
                        "end_line": child.end_point[0] + 1,
                    },
                },
            )
    return elements


# ---------------------------------------------------------------------------
# Public entrypoints
# ---------------------------------------------------------------------------


def parse_code(path: str | Path) -> list[ParsedElement]:
    """Parse a source file via tree-sitter, emit AST-bounded elements.

    Args:
        path: Filesystem path to the source file. Extension drives
            language selection via :data:`_LANGUAGE_PARSERS`.

    Returns:
        A list of :class:`ParsedElement` dicts with element_type ∈
        {CodeFunction, CodeClass, CodeImport}. Each carries
        ``{file, language, name, start_line, end_line}`` in metadata.

        Returns ``[]`` for empty files. For files in unsupported
        languages, returns ``[]`` and logs at info level — the
        upstream registry shim falls back to the legacy token chunker.

    Raises:
        FileNotFoundError: when ``path`` doesn't exist.
        ImportError: when tree-sitter or the language grammar isn't
            installed (callable code paths only — module loads fine).
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Source file not found: {p}")

    ext = p.suffix.lower()
    if ext not in _LANGUAGE_PARSERS:
        logger.info(
            "code_ast_skip ext=%s file=%s — language not yet supported",
            ext, p.name,
        )
        return []

    language_name, parser_factory = _LANGUAGE_PARSERS[ext]
    source = p.read_bytes()
    if not source.strip():
        return []

    parser = parser_factory()
    tree = parser.parse(source)
    if language_name == "python":
        elements = _walk_python(source, tree.root_node, str(p))
    else:  # pragma: no cover — dispatch enforces python-only today
        elements = []

    logger.info(
        "code_ast_parsed file=%s language=%s elements=%d",
        p.name, language_name, len(elements),
    )
    return elements
