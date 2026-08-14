# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Deterministic tests for the RSS/Atom feed parser (_parse_feed).

Parsing is the risky part of the RSS connector, so it's a pure function tested
against crafted samples — no network. Cursor semantics: feeds are newest-first;
_parse_feed returns NEW entries oldest-first and the advanced cursor.
"""

from __future__ import annotations

from core.ingest.sources.connectors.rss import _html_to_text, _parse_feed

_RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<title>Test Feed</title>
<item><title>Third</title><link>http://x/3</link><guid>g3</guid>
<pubDate>Wed, 03 Jun 2026 10:00:00 GMT</pubDate><description>third body</description></item>
<item><title>Second</title><link>http://x/2</link><guid>g2</guid>
<pubDate>Tue, 02 Jun 2026 10:00:00 GMT</pubDate><description>second body</description></item>
<item><title>First</title><link>http://x/1</link><guid>g1</guid>
<pubDate>Mon, 01 Jun 2026 10:00:00 GMT</pubDate><description>first body</description></item>
</channel></rss>"""

_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<title>Atom Test</title>
<entry><title>A2</title><id>a2</id><link href="http://x/a2"/>
<published>2026-06-02T10:00:00Z</published><content>a2 body</content></entry>
<entry><title>A1</title><id>a1</id><link href="http://x/a1"/>
<published>2026-06-01T10:00:00Z</published><summary>a1 summary</summary></entry>
</feed>"""


def test_rss_empty_cursor_returns_all_oldest_first() -> None:
    entries, cursor = _parse_feed(_RSS, {})
    assert [e["guid"] for e in entries] == ["g1", "g2", "g3"]  # oldest-first
    assert entries[0]["title"] == "First"
    assert "first body" in entries[0]["content"]
    assert entries[0]["url"] == "http://x/1"
    assert cursor["last_guid"] == "g3"  # advanced to newest


def test_rss_cursor_returns_only_new() -> None:
    entries, cursor = _parse_feed(_RSS, {"last_guid": "g2"})
    assert [e["guid"] for e in entries] == ["g3"]  # only newer than g2
    assert cursor["last_guid"] == "g3"


def test_rss_cursor_at_head_returns_nothing() -> None:
    entries, cursor = _parse_feed(_RSS, {"last_guid": "g3"})
    assert entries == []
    assert cursor["last_guid"] == "g3"  # unchanged


def test_atom_empty_cursor_parses_entries() -> None:
    entries, cursor = _parse_feed(_ATOM, {})
    assert [e["guid"] for e in entries] == ["a1", "a2"]  # oldest-first
    assert "a2 body" in entries[1]["content"]
    assert entries[1]["url"] == "http://x/a2"
    assert cursor["last_guid"] == "a2"


def test_malformed_xml_is_safe() -> None:
    entries, cursor = _parse_feed("<rss><channel><item>broken", {"last_guid": "x"})
    assert entries == []
    assert cursor == {"last_guid": "x"}  # cursor preserved


def test_entry_without_guid_falls_back_to_link() -> None:
    xml = (
        '<rss version="2.0"><channel><item>'
        "<title>No GUID</title><link>http://x/noguid</link>"
        "<description>body</description></item></channel></rss>"
    )
    entries, _ = _parse_feed(xml, {})
    assert len(entries) == 1
    assert entries[0]["guid"] == "http://x/noguid"  # link is the fallback id


def test_doctype_entity_feed_is_refused() -> None:
    # XXE / billion-laughs guard: a feed declaring a DTD/ENTITY is refused.
    malicious = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE rss [<!ENTITY lol "lololol">]>'
        '<rss version="2.0"><channel><item>'
        "<guid>g1</guid><title>&lol;</title><description>x</description>"
        "</item></channel></rss>"
    )
    entries, cursor = _parse_feed(malicious, {})
    assert entries == []  # refused, not parsed


# --- _html_to_text (WB-06) — feed bodies are frequently HTML/CDATA, not
#     plain text; fetch_since must sanitize before handing content to
#     ingest_fn. ---


def test_html_to_text_strips_tags_and_decodes_entities() -> None:
    html = "<p>Hello &amp; welcome</p><div>to &lt;RSS&gt;</div>"
    assert _html_to_text(html) == "Hello & welcome to <RSS>"


def test_html_to_text_drops_script_and_style_blocks() -> None:
    html = "<style>.x{color:red}</style><script>alert(1)</script><p>real text</p>"
    assert _html_to_text(html) == "real text"


def test_html_to_text_collapses_whitespace() -> None:
    html = "<p>line one</p>\n\n<p>line   two</p>"
    assert _html_to_text(html) == "line one line two"


# --- SSRF guard (_assert_fetchable / _is_blocked_ip) — IP literals + localhost
#     are resolved offline, so these are deterministic without real DNS. ---
import pytest  # noqa: E402

from core.ingest.sources.safe_fetch import assert_fetchable, is_blocked_ip  # noqa: E402


def test_ssrf_blocks_internal_targets() -> None:
    for bad in (
        "http://127.0.0.1/feed",
        "http://localhost/feed",
        "http://169.254.169.254/latest/meta-data",  # cloud metadata
        "http://10.0.0.5/x",
        "http://192.168.1.1/x",
        "http://[::1]/x",
    ):
        with pytest.raises(ValueError):
            assert_fetchable(bad)


def test_ssrf_blocks_non_http_schemes() -> None:
    for bad in ("file:///etc/passwd", "ftp://host/x", "gopher://host/x"):
        with pytest.raises(ValueError):
            assert_fetchable(bad)


def test_ssrf_allows_public_ip_literals() -> None:
    assert_fetchable("http://8.8.8.8/feed.xml")   # public → must not raise
    assert_fetchable("https://1.1.1.1/feed")


def test_is_blocked_ip_ranges() -> None:
    assert is_blocked_ip("127.0.0.1")
    assert is_blocked_ip("169.254.169.254")
    assert is_blocked_ip("10.1.2.3")
    assert is_blocked_ip("::1")
    assert is_blocked_ip("not-an-ip")  # unparseable → blocked
    assert not is_blocked_ip("8.8.8.8")
