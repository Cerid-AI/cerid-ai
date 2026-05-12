# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Wikilink extraction (Workstream RAG Cycle C2.1).

Pure-Python parser for Obsidian-style ``[[wikilinks]]`` embedded in
Markdown bodies.  Lives in ``core/`` so the markdown chunker strategy
can call it without crossing the import-linter ``core ↛ app`` boundary.

Supported forms (all return one :class:`WikilinkRef` per occurrence):

* ``[[Target]]``                — plain link, alias defaults to target
* ``[[Target|Alias]]``           — display alias
* ``[[Target#Heading]]``         — heading anchor
* ``[[Target#Heading|Alias]]``   — combined
* ``![[Target]]``                — embed (transclusion); ``is_embed=True``

Anything inside an inline backtick code span or a fenced code block
(triple-backtick or triple-tilde) is ignored — those forms are
prose-level documentation of wikilink syntax, not real links.

Robustness contract:

* 50 KB hard input cap — adversarial inputs (millions of brackets) can
  push regex engines into pathological backtracking; the cap is a
  cheap belt to keep parse latency O(input).
* Deduplicates ``(target, heading, alias, is_embed)`` tuples preserving
  first occurrence — a body that links to the same target three times
  emits one edge, not three.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# Hard input cap to bound regex work and guard against adversarial
# pathological inputs.  Real markdown notes are rarely above 50 KB;
# anything larger is almost certainly machine-generated content where
# wikilink density is not the signal we care about.
_MAX_INPUT_BYTES = 50 * 1024


@dataclass(frozen=True, slots=True)
class WikilinkRef:
    """One resolved wikilink occurrence.

    Attributes:
        target: The link target text (filename stem or alias to resolve).
        alias: Display alias if specified, else equals ``target``.
        heading: Heading anchor (``[[Foo#Heading]]``), else empty string.
        is_embed: True for ``![[Foo]]`` (transclusion / image embed),
            False for plain ``[[Foo]]``.
    """

    target: str
    alias: str
    heading: str
    is_embed: bool


# Match a wikilink that is NOT preceded by a backtick.  The lookbehind
# rules out inline-code spans like `` `[[example]]` ``; fenced code
# blocks are stripped separately so the regex never sees them.
#
# Groups:
#   1: optional "!" prefix marking an embed
#   2: target — required, no [, ], |, # or newline allowed
#   3: heading — optional, after "#"
#   4: alias — optional, after "|"
_WIKILINK_RE = re.compile(
    r"(?<!`)"
    r"(!?)"                       # group 1: "!" for embeds
    r"\[\["
    r"([^\[\]|#\n]+?)"            # group 2: target
    r"(?:#([^\[\]|\n]+?))?"       # group 3: heading
    r"(?:\|([^\[\]\n]+?))?"       # group 4: alias
    r"\]\]",
    re.UNICODE,
)

# Match fenced code blocks (``` or ~~~), DOTALL semantics via (?s).
# The fence delimiter is captured so the closing fence must match the
# opener (a ``` block can't be closed by ~~~ and vice versa).
_FENCE_RE = re.compile(r"(?s)(```|~~~).*?\1")


def _strip_code_blocks(text: str) -> str:
    """Replace fenced code-block contents with same-length whitespace.

    Preserving offsets (rather than ``re.sub`` to ``""``) keeps any
    future caller that wants to map back to source positions honest
    — and means the regex pass over the stripped text sees the same
    indices as the original, which simplifies reasoning.
    """
    def _blank(match: re.Match[str]) -> str:
        return " " * (match.end() - match.start())

    return _FENCE_RE.sub(_blank, text)


def extract_wikilinks(text: str) -> list[WikilinkRef]:
    """Return wikilinks found in ``text``.

    Behaviour:

    * Fenced code blocks (triple-backtick or triple-tilde) are blanked
      before scanning, so links inside them are ignored.
    * Inline backtick code spans are excluded via a lookbehind that
      rejects ``[[`` preceded by a backtick.
    * Empty input → ``[]``.
    * Inputs ≥ 50 KB → ``[]`` (defensive bound; see module docstring).
    * Duplicates (same ``target/heading/alias/is_embed``) collapse to
      the first occurrence.

    The returned ``alias`` defaults to ``target`` when no ``|alias``
    was supplied, so callers always have a display string.
    """
    if not text:
        return []
    # Bound by byte length, not character length: UTF-8 expansion can
    # surprise on emoji-heavy content but the 50 KB ceiling is well
    # above realistic note size.
    if len(text.encode("utf-8", errors="ignore")) >= _MAX_INPUT_BYTES:
        return []

    stripped = _strip_code_blocks(text)
    seen: set[tuple[str, str, str, bool]] = set()
    out: list[WikilinkRef] = []
    for match in _WIKILINK_RE.finditer(stripped):
        bang, target, heading, alias = match.groups()
        target = (target or "").strip()
        if not target:
            continue
        heading = (heading or "").strip()
        alias = (alias or "").strip() or target
        is_embed = bang == "!"
        key = (target, heading, alias, is_embed)
        if key in seen:
            continue
        seen.add(key)
        out.append(
            WikilinkRef(
                target=target,
                alias=alias,
                heading=heading,
                is_embed=is_embed,
            ),
        )
    return out
