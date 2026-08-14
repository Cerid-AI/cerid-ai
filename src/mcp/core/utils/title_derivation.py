# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Derive a human-scannable title from raw ingested text.

Every artifact ingested without a filename used to be stored as the
literal name ``text_input`` — general/digests/conversations artifacts all
carried the same label, so provenance was unrecoverable in the Library
and citations were useless (todo item 6). The ingest chokepoint calls
:func:`derive_title` for those artifacts so each one gets a name a human
can recognise in a list.

Pure text heuristics only — no LLM. The title is a label, not a summary;
the first heading or opening line is what a person scanning a list would
use to recognise the content anyway.
"""
from __future__ import annotations

import re

# Titles are display labels: long enough to recognise, short enough to scan.
MAX_TITLE_CHARS = 80

_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*#*\s*$")
_EMPHASIS_RE = re.compile(r"[*_`]+")
_FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
# Lines that carry no naming signal: rules and bare blockquote markers;
# list bullets are stripped to their text instead.
_NOISE_LINE_RE = re.compile(r"^\s*(?:---+|===+|\*\*\*+|>+\s*$)")
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+")

_MAX_SCAN_LINES = 20
# A one-character line ("-", "#") is markup residue, not a name.
_MIN_TITLE_CHARS = 2


def _clean(line: str) -> str:
    text = _EMPHASIS_RE.sub("", _BULLET_RE.sub("", line)).strip()
    return re.sub(r"\s+", " ", text)


def _truncate(text: str) -> str:
    if len(text) <= MAX_TITLE_CHARS:
        return text
    cut = text[:MAX_TITLE_CHARS]
    head, _, _ = cut.rpartition(" ")
    return (head or cut).rstrip(" ,;:.-") + "…"


def derive_title(content: str) -> str:
    """A display title from the content's first heading or opening line.

    Returns ``""`` when the content offers nothing usable (empty or pure
    markup) — the caller keeps its existing default in that case.
    """
    raw_lines = (content or "").split("\n", _MAX_SCAN_LINES * 2)[:_MAX_SCAN_LINES]

    # Drop fenced code blocks — their contents are code, not names.
    lines: list[str] = []
    in_fence = False
    for line in raw_lines:
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            lines.append(line)

    # A markdown heading anywhere in the opening lines is the best label.
    for line in lines:
        m = _HEADING_RE.match(line)
        if m:
            cleaned = _clean(m.group(1))
            if cleaned:
                return _truncate(cleaned)

    # Otherwise the first line with prose in it.
    for line in lines:
        if _NOISE_LINE_RE.match(line):
            continue
        cleaned = _clean(line)
        if len(cleaned) >= _MIN_TITLE_CHARS:
            return _truncate(cleaned)
    return ""
