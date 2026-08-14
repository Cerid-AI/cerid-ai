"""RSS 1.0 (RDF) parsing in the connector path.

AF-020 retired the legacy ``app.data_sources.rss_feed`` poller, whose
``_parse_rdf_items`` was the only code that handled RSS 1.0 / RDF feeds
(namespaced ``<item>``, ``rdf:about`` guid, ``dc:date``). The RSS-2.0 branch
in ``connectors.rss._parse_feed`` matches only unqualified ``<item>``, so an
RDF feed would have parsed to zero entries. The capability was ported into the
connector; this test is the guard that it stays ported.
"""
from __future__ import annotations

from core.ingest.sources.connectors.rss import _parse_feed

_RDF_FEED = """<?xml version="1.0"?>
<rdf:RDF xmlns="http://purl.org/rss/1.0/"
         xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#"
         xmlns:dc="http://purl.org/dc/elements/1.1/">
  <channel rdf:about="http://example.com/feed">
    <title>RDF Test Feed</title>
  </channel>
  <item rdf:about="http://example.com/1">
    <title>First</title>
    <link>http://example.com/1</link>
    <description>first body</description>
    <dc:date>2026-06-01T10:00:00Z</dc:date>
  </item>
  <item rdf:about="http://example.com/2">
    <title>Second</title>
    <link>http://example.com/2</link>
    <description>second body</description>
  </item>
</rdf:RDF>"""


def test_rdf_feed_parses_namespaced_items():
    entries, cursor = _parse_feed(_RDF_FEED, {})
    # Two items parsed (not zero — the RSS-2.0-only branch would have missed them).
    assert len(entries) == 2
    # Oldest-first after the reversal the parser does for cursor monotonicity.
    guids = [e["guid"] for e in entries]
    assert "http://example.com/1" in guids and "http://example.com/2" in guids
    first = next(e for e in entries if e["guid"] == "http://example.com/1")
    assert first["title"] == "First"
    assert first["url"] == "http://example.com/1"
    assert "first body" in first["content"]
    assert first["published_at"]  # dc:date normalized, not dropped
    # Cursor advanced to the newest item so a re-poll dedups.
    assert cursor["last_guid"] == "http://example.com/1"


def test_rdf_feed_dedups_on_cursor():
    _, cursor = _parse_feed(_RDF_FEED, {})
    again, _ = _parse_feed(_RDF_FEED, cursor)
    assert again == []
