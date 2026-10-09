# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Every place the server writes conversation-keyed data is owned by a forget
adapter or named as out of reach. A new store that keys on a conversation id
without either fails here, before it can outlive a forget.

Three store shapes are enumerated from the source under ``app/`` and ``core/``:
Redis keys (f-strings embedding a conversation id), Neo4j labels (Cypher that
matches a node on ``conversation_id``, and ``:Conversation`` itself) and Chroma
filename prefixes (f-strings embedding the 8-character conversation prefix).
"""
from __future__ import annotations

import ast
import re
import sys
from collections.abc import Iterator
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_MCP = _ROOT / "src" / "mcp"
sys.path.insert(0, str(_MCP))

from app.services.forget.adapters import (  # noqa: E402
    ADAPTERS,
    CLAIMS,
    OUT_OF_REACH,
)

_SCAN_DIRS = ("app", "core")
_CONV_NAMES = frozenset({"conversation_id", "conv_id", "cid", "convo_id"})
_CONV_PREFIX_NAMES = frozenset({"convo_prefix"})
_REDIS_KEY = re.compile(r"[\w:{}.\-]+")
_NEO4J_CONV_PROP = re.compile(r":(\w+)\s*\{[^}]*\bconversation_id\s*:")
_NEO4J_CONVERSATION = re.compile(r"\(\w*:Conversation\b")
_CHROMA_HEAD = re.compile(r"[a-z][a-z_]*_")

# Patterns the scan reports whose variable is named like a conversation id but
# holds something else. Each needs the file and what the id really is.
_NOT_CONVERSATION_KEYED: dict[str, str] = {
    "redis:concept:{cid}": "app/routers/wiki.py: a wiki page slug; cid is a graph community id",
}


def _modules() -> Iterator[tuple[Path, ast.Module]]:
    for top in _SCAN_DIRS:
        for path in sorted((_MCP / top).rglob("*.py")):
            if "tests" in path.relative_to(_MCP).parts:
                continue
            yield path, ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _constants(modules: list[tuple[Path, ast.Module]]) -> tuple[dict[Path, dict[str, str]], dict[str, set[str]]]:
    """Module-level ``NAME = "literal"`` assignments, per file and across the tree."""
    per_file: dict[Path, dict[str, str]] = {}
    anywhere: dict[str, set[str]] = {}
    for path, tree in modules:
        local: dict[str, str] = {}
        for node in tree.body:
            target, value = None, None
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target, value = node.targets[0], node.value
            elif isinstance(node, ast.AnnAssign):
                target, value = node.target, node.value
            if isinstance(target, ast.Name) and isinstance(value, ast.Constant) and isinstance(value.value, str):
                local[target.id] = value.value
                anywhere.setdefault(target.id, set()).add(value.value)
        per_file[path] = local
    return per_file, anywhere


def _terminal_name(expr: ast.expr) -> str | None:
    if isinstance(expr, ast.Subscript):
        expr = expr.value
    if isinstance(expr, ast.Name):
        return expr.id
    if isinstance(expr, ast.Attribute):
        return expr.attr
    return None


class _Scan:
    def __init__(self) -> None:
        self.modules = list(_modules())
        self.per_file, self.anywhere = _constants(self.modules)

    def _resolve(self, path: Path, name: str) -> str | None:
        if name in self.per_file[path]:
            return self.per_file[path][name]
        values = self.anywhere.get(name, set())
        return next(iter(values)) if len(values) == 1 else None

    def _render(self, path: Path, parts: list[ast.expr]) -> str:
        out = []
        for part in parts:
            if isinstance(part, ast.Constant):
                out.append(str(part.value))
            elif isinstance(part, ast.FormattedValue):
                name = part.value.id if isinstance(part.value, ast.Name) else None
                literal = self._resolve(path, name) if name else None
                out.append(literal if literal is not None else "{" + ast.unparse(part.value) + "}")
        return "".join(out)

    def patterns(self) -> set[str]:
        found: set[str] = set()
        for path, tree in self.modules:
            for node in ast.walk(tree):
                if isinstance(node, ast.JoinedStr):
                    found |= self._redis(path, node) | self._chroma(node)
                elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                    found |= _neo4j(node.value)
        return found

    def _redis(self, path: Path, node: ast.JoinedStr) -> set[str]:
        found = set()
        for i, part in enumerate(node.values):
            if isinstance(part, ast.FormattedValue) and _terminal_name(part.value) in _CONV_NAMES:
                prefix = self._render(path, node.values[:i])
                key = prefix + "{cid}" + self._render(path, node.values[i + 1:])
                if ":" in prefix and _REDIS_KEY.fullmatch(key):
                    found.add(f"redis:{key}")
        return found

    @staticmethod
    def _chroma(node: ast.JoinedStr) -> set[str]:
        keyed = any(
            isinstance(p, ast.FormattedValue)
            and (
                _terminal_name(p.value) in _CONV_PREFIX_NAMES
                or (isinstance(p.value, ast.Subscript) and _terminal_name(p.value) in _CONV_NAMES)
            )
            for p in node.values
        )
        head = node.values[0] if node.values else None
        text = head.value if isinstance(head, ast.Constant) else None
        if keyed and isinstance(text, str) and _CHROMA_HEAD.fullmatch(text):
            return {f"chroma:conversations:{text}*"}
        return set()


def _neo4j(text: str) -> set[str]:
    found = {f"neo4j:{label}.conversation_id" for label in _NEO4J_CONV_PROP.findall(text)}
    if _NEO4J_CONVERSATION.search(text):
        found.add("neo4j:Conversation.id")
    return found


def _unclaimed() -> list[str]:
    known = set(CLAIMS) | set(OUT_OF_REACH) | set(_NOT_CONVERSATION_KEYED)
    return sorted(p for p in _Scan().patterns() if p not in known)


def test_every_conversation_keyed_store_is_claimed():
    unclaimed = _unclaimed()
    assert not unclaimed, (
        "conversation-keyed stores with no forget adapter; claim each in "
        f"app/services/forget/adapters.py CLAIMS or name it in OUT_OF_REACH: {unclaimed}"
    )


def test_every_claim_names_a_real_adapter():
    adapters = {a.name for a in ADAPTERS}
    assert not {owner for owner in CLAIMS.values() if owner not in adapters}


def test_every_out_of_reach_entry_gives_a_reason():
    assert all(reason.strip() for reason in OUT_OF_REACH.values())
    assert not set(CLAIMS) & set(OUT_OF_REACH)


def test_the_gate_sees_a_planted_store():
    planted = _MCP / "app" / "_planted_forget_probe.py"
    planted.write_text(
        '_PLANTED_PREFIX = "cerid:planted_const:"\n'
        'def keys(conversation_id, req, convo_prefix):\n'
        '    a = f"cerid:planted:{conversation_id}:x"\n'
        "    b = f'{_PLANTED_PREFIX}{req.conversation_id}'\n"
        '    c = "MATCH (p:PlantedNode {conversation_id: $cid}) RETURN p"\n'
        '    d = f"planted_chunk_{convo_prefix}_1"\n'
        "    return a, b, c, d\n",
        encoding="utf-8",
    )
    try:
        unclaimed = _unclaimed()
    finally:
        planted.unlink()
    for pattern in (
        "redis:cerid:planted:{cid}:x",
        "redis:cerid:planted_const:{cid}",
        "neo4j:PlantedNode.conversation_id",
        "chroma:conversations:planted_chunk_*",
    ):
        assert pattern in unclaimed
