# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""`safe_fromstring` must parse benign feeds but reject entity-expansion
(billion-laughs) and external-entity (XXE) payloads, surfacing both as
`xml.etree.ElementTree.ParseError` so existing callers need no new handling."""
from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from core.utils.safe_xml import safe_fromstring

_BILLION_LAUGHS = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [
  <!ENTITY lol "lol">
  <!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">
  <!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">
]>
<rss><channel><title>&lol3;</title></channel></rss>"""

_XXE_EXTERNAL = b"""<?xml version="1.0"?>
<!DOCTYPE foo [ <!ENTITY xxe SYSTEM "file:///etc/passwd"> ]>
<rss><channel><title>&xxe;</title></channel></rss>"""


def test_parses_benign_feed() -> None:
    root = safe_fromstring(b"<rss><channel><title>Hello</title></channel></rss>")
    assert root.tag == "rss"
    assert root.find("./channel/title").text == "Hello"


def test_accepts_str_input() -> None:
    root = safe_fromstring("<rss><channel><title>x</title></channel></rss>")
    assert root.tag == "rss"


def test_rejects_billion_laughs() -> None:
    with pytest.raises(ET.ParseError):
        safe_fromstring(_BILLION_LAUGHS)


def test_rejects_external_entity() -> None:
    with pytest.raises(ET.ParseError):
        safe_fromstring(_XXE_EXTERNAL)


def test_malformed_xml_is_parse_error() -> None:
    with pytest.raises(ET.ParseError):
        safe_fromstring(b"<rss><channel><title>unclosed")
