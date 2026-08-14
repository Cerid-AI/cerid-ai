# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Detect and reverse UTF-8-as-latin-1 mojibake in short strings.

The Apple Mail connector's quoted-printable decode turned UTF-8 bytes
into Unicode code points one byte at a time, so every curly apostrophe
became ``â€™`` ("Guardian Tigerâ€™s Eye…") and those names were stored
as graph entities. The decode is fixed at the root
(``packages/desktop/src/main/connectors/apple_mail.ts``); this module
exists to repair what was already written —
``scripts/repair_mojibake_entities.py`` is the consumer.

The reversal is exact, not fuzzy: re-encode the string with the 8-bit
codec that produced it (cp1252 first — its € ™ „ … mappings cover text
that went through a Windows-1252 display — then latin-1 for the raw
byte-per-code-point shape) and decode the bytes as the UTF-8 they
always were. A string that was never mojibake fails one of the two
steps and comes back unchanged.
"""
from __future__ import annotations

import re

# The lead bytes of multi-byte UTF-8 sequences, as latin-1/cp1252 renders
# them: â (0xE2, punctuation family), Ã (0xC3, Latin letters), Â (0xC2,
# NBSP family), plus the literal replacement-char sequence ``ï¿½``.
# The second character of a mangled pair lands in U+0080–U+00BF when the
# text was decoded as latin-1, or in cp1252's remapping of that range
# (€ ‚ „ … ‘ ’ “ ” – — ™ œ …) when it went through a Windows-1252
# display — the marker is a lead character followed by either.
_TRAIL = (
    "\u0080-\u00bf"  # raw latin-1 trail bytes (C1 controls + supplement)
    "\u20ac\u201a\u0192\u201e\u2026\u2020\u2021\u02c6\u2030\u0160\u2039"  # cp1252 remaps
    "\u0152\u017d\u2018\u2019\u201c\u201d\u2022\u2013\u2014\u02dc\u2122"
    "\u0161\u203a\u0153\u017e\u0178"
)
_MOJIBAKE_RE = re.compile(f"[âÃÂ][{_TRAIL}]|ï¿½")

_MAX_PASSES = 3  # double/triple-encoded text unwinds one layer per pass


def looks_like_mojibake(text: str) -> bool:
    """True when ``text`` carries the UTF-8-as-latin-1 signature."""
    return bool(_MOJIBAKE_RE.search(text or ""))


def _one_pass(text: str) -> str | None:
    for codec in ("cp1252", "latin-1"):
        try:
            raw = text.encode(codec)
        except UnicodeEncodeError:
            continue
        try:
            out = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if out != text:
            return out
    return None


def fix_mojibake(text: str) -> str:
    """Reverse the mis-decode; returns the input unchanged if not mojibake.

    Only attempts repair when the signature is present — a legitimate
    string that happens to survive an encode/decode round-trip is left
    alone.
    """
    if not text or not looks_like_mojibake(text):
        return text
    fixed = text
    for _ in range(_MAX_PASSES):
        repaired = _one_pass(fixed)
        if repaired is None:
            break
        fixed = repaired
        if not looks_like_mojibake(fixed):
            break
    return fixed
