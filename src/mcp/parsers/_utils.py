# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared utility functions for file parsers."""

from __future__ import annotations

import re
from typing import List


def _strip_html_tags(html: str) -> str:
    """Strip HTML tags and return plain text. Lightweight, no external deps."""
    from html.parser import HTMLParser

    class _Stripper(HTMLParser):
        def __init__(self):
            super().__init__()
            self._parts: List[str] = []
            self._skip = False

        def handle_starttag(self, tag, attrs):
            self._skip = tag.lower() in ("script", "style", "noscript")
            # Add newline for block-level elements
            if tag.lower() in ("p", "div", "br", "h1", "h2", "h3", "h4", "h5", "h6", "li", "tr"):
                self._parts.append("\n")

        def handle_endtag(self, tag):
            if tag.lower() in ("script", "style", "noscript"):
                self._skip = False

        def handle_data(self, data):
            if not self._skip:
                self._parts.append(data)

    try:
        stripper = _Stripper()
        stripper.feed(html)
        return "".join(stripper._parts).strip()
    except Exception:
        # Last resort: regex strip
        return re.sub(r"<[^>]+>", " ", html).strip()


def _strip_rtf(raw: bytes) -> str:
    """Strip RTF control codes via state-machine approach."""

    text = raw.decode("ascii", errors="replace")

    _skip_destinations = {
        "fonttbl", "colortbl", "stylesheet", "info", "pict",
        "header", "footer", "headerl", "headerr", "footerl", "footerr",
        "headerf", "footerf", "object", "objdata", "datafield",
        "fldinst", "themedata", "colorschememapping", "datastore",
        "latentstyles", "generator",
    }

    output = []
    i = 0
    length = len(text)
    group_depth = 0
    skip_depth = 0  # depth at which we started skipping

    while i < length:
        ch = text[i]

        if ch == "{":
            group_depth += 1
            if i + 1 < length and text[i + 1] == "\\":
                m = re.match(r"\\(\*\\)?([a-z]+)", text[i + 1:i + 40])
                if m:
                    word = m.group(2)
                    if word in _skip_destinations:
                        skip_depth = group_depth
            i += 1
            continue

        if ch == "}":
            if group_depth == skip_depth:
                skip_depth = 0
            group_depth -= 1
            i += 1
            continue

        if skip_depth > 0:
            i += 1
            continue

        if ch == "\\":
            i += 1
            if i >= length:
                break

            next_ch = text[i]

            if next_ch == "'":
                if i + 2 < length:
                    hex_val = text[i + 1:i + 3]
                    try:
                        output.append(chr(int(hex_val, 16)))
                    except ValueError:
                        pass
                    i += 3
                    continue
                i += 1
                continue

            if next_ch == "u":
                m = re.match(r"(-?\d+)", text[i + 1:i + 8])
                if m:
                    code = int(m.group(1))
                    if code < 0:
                        code += 65536
                    try:
                        output.append(chr(code))
                    except ValueError:
                        pass
                    i += 1 + len(m.group(1))
                    if i < length and text[i] == " ":
                        i += 1
                    continue
                i += 1
                continue

            if next_ch.isalpha():
                m = re.match(r"([a-z]+)(-?\d+)?", text[i:i + 30])
                if m:
                    word = m.group(1)
                    i += len(m.group(0))
                    if i < length and text[i] == " ":
                        i += 1
                    if word == "par" or word == "line":
                        output.append("\n")
                    elif word == "tab":
                        output.append("\t")
                    elif word in ("lquote", "rquote"):
                        output.append("'")
                    elif word in ("ldblquote", "rdblquote"):
                        output.append('"')
                    elif word in ("emdash", "endash"):
                        output.append("—" if word == "emdash" else "–")
                    elif word == "bullet":
                        output.append("•")
                    continue
                i += 1
                continue

            if next_ch in ("\\", "{", "}"):
                output.append(next_ch)
                i += 1
                continue

            i += 1
            continue

        if ch in ("\r", "\n"):
            i += 1
            continue
        output.append(ch)
        i += 1

    result = "".join(output)
    result = re.sub(r"\n{3,}", "\n\n", result)
    result = re.sub(r"[ \t]+", " ", result)
    return result.strip()
