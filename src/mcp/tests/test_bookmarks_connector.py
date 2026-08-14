# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for BookmarksConnector.connect — NETSCAPE HTML bookmark parsing.

WB-03: the HREF attribute is HTML-escaped the same as any other attribute
value (e.g. a query string's ``&`` becomes ``&amp;``); the parser already
unescaped the title text but stored the URL raw.
"""
from __future__ import annotations

import pytest

from core.ingest.sources.connectors.bookmarks import BookmarksConnector

_NETSCAPE_HTML = """<!DOCTYPE NETSCAPE-Bookmark-file-1>
<DL><p>
    <DT><A HREF="https://example.com/search?a=1&amp;b=2" ADD_DATE="1">Search &amp; Filter</A>
</DL><p>
"""


@pytest.mark.asyncio
async def test_connect_unescapes_href_entities() -> None:
    result = await BookmarksConnector().connect({"html": _NETSCAPE_HTML})
    bookmarks = result.config["parsed_bookmarks"]
    assert len(bookmarks) == 1
    assert bookmarks[0]["url"] == "https://example.com/search?a=1&b=2"
    assert bookmarks[0]["title"] == "Search & Filter"


@pytest.mark.asyncio
async def test_connect_leaves_unescaped_urls_unchanged() -> None:
    html = '<DT><A HREF="https://example.com/plain">Plain</A>'
    result = await BookmarksConnector().connect({"html": html})
    assert result.config["parsed_bookmarks"][0]["url"] == "https://example.com/plain"
