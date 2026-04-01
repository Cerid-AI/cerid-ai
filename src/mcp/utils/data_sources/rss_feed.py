# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""RSS/Atom feed poller data source connector.

Polls configured RSS/Atom feeds, deduplicates entries via Redis,
and ingests article content into the KB through ``ingest_content()``.

Feed configs stored in Redis hash ``cerid:rss:feeds``.
Seen entry hashes stored in Redis set ``cerid:rss:seen``.
No external dependencies beyond stdlib for XML parsing.
"""
from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Any

import httpx

from errors import CeridError, IngestionError

from .base import DataSource, DataSourceResult

__all__ = [
    "RSSFeedSource",
    "html_to_text",
    "poll_feed",
    "poll_all_feeds",
    "add_feed",
    "remove_feed",
    "list_feeds",
]

_logger = logging.getLogger("ai-companion.rss_feed")

# Redis keys
_FEEDS_HASH = "cerid:rss:feeds"
_SEEN_SET = "cerid:rss:seen"

# Namespaces for Atom feeds
_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}


# ---------------------------------------------------------------------------
# HTML-to-text helper
# ---------------------------------------------------------------------------

def html_to_text(html: str) -> str:
    """Strip HTML tags, decode entities, collapse whitespace."""
    text = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL)
    text = re.sub(r"<style[^>]*>.*?</style>", "", text, flags=re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"&quot;", '"', text)
    text = re.sub(r"&#39;", "'", text)
    text = re.sub(r"&apos;", "'", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ---------------------------------------------------------------------------
# Feed CRUD (Redis-backed)
# ---------------------------------------------------------------------------

def _get_redis():
    """Lazy import to avoid circular deps at module load."""
    from deps import get_redis
    return get_redis()


def add_feed(
    url: str,
    name: str | None = None,
    domain: str = "general",
) -> dict[str, Any]:
    """Add a new RSS/Atom feed configuration to Redis.

    Returns the feed config dict including generated ``id``.
    """
    feed_id = uuid.uuid4().hex[:12]
    config = {
        "id": feed_id,
        "url": url,
        "name": name or url,
        "domain": domain,
        "enabled": True,
        "last_fetched": None,
        "etag": None,
        "last_modified": None,
    }
    r = _get_redis()
    r.hset(_FEEDS_HASH, feed_id, json.dumps(config))
    _logger.info("Added RSS feed id=%s url=%s domain=%s", feed_id, url, domain)
    return config


def remove_feed(feed_id: str) -> bool:
    """Remove a feed by ID. Returns True if deleted, False if not found."""
    r = _get_redis()
    removed = r.hdel(_FEEDS_HASH, feed_id)
    if removed:
        _logger.info("Removed RSS feed id=%s", feed_id)
    return bool(removed)


def list_feeds() -> list[dict[str, Any]]:
    """Return all configured feeds."""
    r = _get_redis()
    raw = r.hgetall(_FEEDS_HASH)
    feeds = []
    for _fid, data in raw.items():
        try:
            feeds.append(json.loads(data))
        except (json.JSONDecodeError, TypeError):
            _logger.warning("Corrupt feed entry id=%s, skipping", _fid)
    return feeds


def get_feed(feed_id: str) -> dict[str, Any] | None:
    """Fetch a single feed config by ID."""
    r = _get_redis()
    raw = r.hget(_FEEDS_HASH, feed_id)
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def _update_feed(feed_id: str, updates: dict[str, Any]) -> None:
    """Merge updates into a stored feed config."""
    r = _get_redis()
    raw = r.hget(_FEEDS_HASH, feed_id)
    if raw is None:
        return
    try:
        config = json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return
    config.update(updates)
    r.hset(_FEEDS_HASH, feed_id, json.dumps(config))


# ---------------------------------------------------------------------------
# XML parsing helpers
# ---------------------------------------------------------------------------

def _detect_feed_type(root: ET.Element) -> str:
    """Return 'rss' or 'atom' based on root tag."""
    tag = root.tag.lower()
    if tag == "rss" or tag.endswith("}rss"):
        return "rss"
    if "feed" in tag:
        return "atom"
    # Check for channel child (RSS without explicit <rss> wrapper)
    if root.find("channel") is not None:
        return "rss"
    return "atom"


def _parse_rss_items(root: ET.Element) -> list[dict[str, str]]:
    """Extract items from an RSS 2.0 feed."""
    channel = root.find("channel")
    if channel is None:
        return []
    items = []
    for item in channel.findall("item"):
        entry: dict[str, str] = {}
        title_el = item.find("title")
        entry["title"] = (title_el.text or "").strip() if title_el is not None else ""

        link_el = item.find("link")
        entry["link"] = (link_el.text or "").strip() if link_el is not None else ""

        guid_el = item.find("guid")
        entry["guid"] = (guid_el.text or "").strip() if guid_el is not None else entry["link"]

        desc_el = item.find("description")
        entry["summary"] = html_to_text(desc_el.text or "") if desc_el is not None else ""

        pub_el = item.find("pubDate")
        entry["published"] = (pub_el.text or "").strip() if pub_el is not None else ""

        author_el = item.find("author")
        if author_el is None:
            author_el = item.find("{http://purl.org/dc/elements/1.1/}creator")
        entry["author"] = (author_el.text or "").strip() if author_el is not None else ""

        items.append(entry)
    return items


def _parse_atom_entries(root: ET.Element) -> list[dict[str, str]]:
    """Extract entries from an Atom feed."""
    # Handle default namespace
    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    entries = []
    for entry_el in root.findall(f"{ns}entry"):
        entry: dict[str, str] = {}
        title_el = entry_el.find(f"{ns}title")
        entry["title"] = (title_el.text or "").strip() if title_el is not None else ""

        # Atom link is an attribute
        link_el = entry_el.find(f"{ns}link[@rel='alternate']")
        if link_el is None:
            link_el = entry_el.find(f"{ns}link")
        entry["link"] = (link_el.get("href", "") if link_el is not None else "").strip()

        id_el = entry_el.find(f"{ns}id")
        entry["guid"] = (id_el.text or "").strip() if id_el is not None else entry["link"]

        summary_el = entry_el.find(f"{ns}summary")
        if summary_el is None:
            summary_el = entry_el.find(f"{ns}content")
        raw_summary = summary_el.text or "" if summary_el is not None else ""
        entry["summary"] = html_to_text(raw_summary)

        updated_el = entry_el.find(f"{ns}updated")
        if updated_el is None:
            updated_el = entry_el.find(f"{ns}published")
        entry["published"] = (updated_el.text or "").strip() if updated_el is not None else ""

        author_el = entry_el.find(f"{ns}author")
        if author_el is not None:
            name_el = author_el.find(f"{ns}name")
            entry["author"] = (name_el.text or "").strip() if name_el is not None else ""
        else:
            entry["author"] = ""

        entries.append(entry)
    return entries


# ---------------------------------------------------------------------------
# HTTP fetch with conditional headers
# ---------------------------------------------------------------------------

_USER_AGENT = "CeridAI-RSS/1.0"


def _fetch_url(url: str, timeout: float = 10.0, etag: str | None = None,
               last_modified: str | None = None) -> tuple[bytes | None, dict[str, str]]:
    """Fetch a URL using httpx. Returns (body_bytes, response_headers).

    Returns ``(None, {})`` for 304 Not Modified.
    Raises on network/HTTP errors.
    """
    headers: dict[str, str] = {"User-Agent": _USER_AGENT}
    if etag:
        headers["If-None-Match"] = etag
    if last_modified:
        headers["If-Modified-Since"] = last_modified

    resp = httpx.get(url, headers=headers, timeout=timeout, follow_redirects=True)
    if resp.status_code == 304:
        return None, {}
    resp.raise_for_status()
    resp_headers = {k.lower(): v for k, v in resp.headers.items()}
    return resp.content, resp_headers


def _fetch_article_content(url: str, timeout: float = 5.0) -> str | None:
    """Attempt to fetch full article text from a URL.

    Returns stripped text or None on failure.
    """
    try:
        body, _headers = _fetch_url(url, timeout=timeout)
        if body is None:
            return None
        text = html_to_text(body.decode("utf-8", errors="replace"))
        # Crude length gate — very short pages are likely paywalls / JS-only
        if len(text) < 100:
            return None
        # Truncate excessively long articles to avoid blowing up context
        max_chars = 50_000
        if len(text) > max_chars:
            text = text[:max_chars]
        return text
    except (httpx.HTTPStatusError, httpx.RequestError, OSError, UnicodeDecodeError, ValueError) as exc:
        _logger.debug("Article fetch failed for %s: %s", url, exc)
        return None


# ---------------------------------------------------------------------------
# Entry dedup
# ---------------------------------------------------------------------------

def _entry_hash(guid: str) -> str:
    """SHA-256 hash of the entry GUID/link for dedup."""
    return hashlib.sha256(guid.encode("utf-8")).hexdigest()


def _is_seen(entry_hash: str) -> bool:
    r = _get_redis()
    return bool(r.sismember(_SEEN_SET, entry_hash))


def _mark_seen(entry_hash: str) -> None:
    r = _get_redis()
    r.sadd(_SEEN_SET, entry_hash)


# ---------------------------------------------------------------------------
# Core polling logic
# ---------------------------------------------------------------------------

async def poll_feed(feed_config: dict[str, Any]) -> dict[str, Any]:
    """Poll a single RSS/Atom feed and ingest new entries.

    Returns a summary dict: ``{feed_id, new_entries, errors}``.
    """
    from utils.circuit_breaker import CircuitOpenError, get_breaker

    feed_id = feed_config["id"]
    url = feed_config["url"]
    domain = feed_config.get("domain", "general")
    feed_name = feed_config.get("name", url)

    breaker = get_breaker("rss-feed")
    summary: dict[str, Any] = {"feed_id": feed_id, "new_entries": 0, "errors": []}

    try:
        # Fetch with conditional headers
        body, resp_headers = await _run_in_thread(
            breaker,
            _fetch_url,
            url,
            10.0,
            feed_config.get("etag"),
            feed_config.get("last_modified"),
        )
    except CircuitOpenError:
        summary["errors"].append("Circuit breaker open for rss-feed")
        _logger.warning("RSS circuit open, skipping feed id=%s", feed_id)
        return summary
    except (httpx.HTTPStatusError, httpx.RequestError, OSError) as exc:
        summary["errors"].append(str(exc))
        _logger.warning("RSS fetch failed for feed id=%s: %s", feed_id, exc)
        return summary

    if body is None:
        # 304 Not Modified
        _logger.debug("Feed id=%s returned 304, nothing new", feed_id)
        _update_feed(feed_id, {"last_fetched": _now_iso()})
        return summary

    # Parse XML
    try:
        root = ET.fromstring(body)  # nosec B314 — RSS sync, user-configured feeds only
    except ET.ParseError as exc:
        summary["errors"].append(f"XML parse error: {exc}")
        _logger.warning("RSS XML parse error for feed id=%s: %s", feed_id, exc)
        return summary

    feed_type = _detect_feed_type(root)
    if feed_type == "rss":
        items = _parse_rss_items(root)
    else:
        items = _parse_atom_entries(root)

    # Process entries
    from services.ingestion import ingest_content

    for item in items:
        guid = item.get("guid") or item.get("link") or item.get("title", "")
        if not guid:
            continue

        ehash = _entry_hash(guid)
        if _is_seen(ehash):
            continue

        title = item.get("title", "Untitled")
        link = item.get("link", "")
        rss_summary = item.get("summary", "")
        published = item.get("published", "")
        author = item.get("author", "")

        # Try full article content
        content = None
        if link:
            content = _fetch_article_content(link, timeout=5.0)

        if not content:
            content = rss_summary

        if not content:
            _logger.debug("RSS entry has no content, skipping: %s", title)
            continue

        # Build content with title header
        full_text = f"# {title}\n\n{content}"

        metadata: dict[str, Any] = {
            "filename": f"rss-{feed_id}-{ehash[:8]}.txt",
            "source_url": link,
            "author": author,
            "published_date": published,
            "feed_name": feed_name,
            "feed_id": feed_id,
            "content_type": "rss_article",
        }

        try:
            ingest_content(full_text, domain=domain, metadata=metadata)
            _mark_seen(ehash)
            summary["new_entries"] += 1
            _logger.info("Ingested RSS entry: %s (feed=%s)", title[:80], feed_name)
        except (CeridError, IngestionError, ValueError, OSError, RuntimeError) as exc:
            summary["errors"].append(f"Ingest failed for '{title}': {exc}")
            _logger.warning("RSS ingest failed: %s — %s", title[:80], exc)

    # Update feed metadata
    _update_feed(feed_id, {
        "last_fetched": _now_iso(),
        "etag": resp_headers.get("etag"),
        "last_modified": resp_headers.get("last-modified"),
    })

    _logger.info(
        "Feed id=%s polled: %d new entries, %d errors",
        feed_id, summary["new_entries"], len(summary["errors"]),
    )
    return summary


async def poll_all_feeds() -> list[dict[str, Any]]:
    """Poll all enabled feeds and return per-feed summaries."""
    feeds = list_feeds()
    results = []
    for feed in feeds:
        if not feed.get("enabled", True):
            continue
        result = await poll_feed(feed)
        results.append(result)
    return results


# ---------------------------------------------------------------------------
# Threading helper for sync I/O inside async context
# ---------------------------------------------------------------------------

async def _run_in_thread(breaker, fn, *args):
    """Run a sync function through the circuit breaker in a thread pool."""
    import asyncio

    loop = asyncio.get_event_loop()

    async def _wrapped():
        return await loop.run_in_executor(None, fn, *args)

    return await breaker.call(_wrapped)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# DataSource interface (for the registry — read-only query support)
# ---------------------------------------------------------------------------

class RSSFeedSource(DataSource):
    """RSS feed data source for the registry.

    The ``query()`` method returns recently ingested RSS articles
    matching the query. Actual polling is done via ``poll_feed()``
    and ``poll_all_feeds()``.
    """

    name = "rss_feeds"
    description = "RSS/Atom feed poller — ingests articles from configured feeds."
    requires_api_key = False
    domains: list[str] = []  # all domains

    async def query(self, query: str, **kwargs) -> list[DataSourceResult]:
        """Return info about configured feeds (not a search — feeds are polled separately)."""
        feeds = list_feeds()
        if not feeds:
            return []
        lines = [f"- {f['name']} ({f['url']}): last fetched {f.get('last_fetched', 'never')}"
                 for f in feeds if f.get("enabled", True)]
        if not lines:
            return []
        return [DataSourceResult(
            title="Configured RSS Feeds",
            content="\n".join(lines),
            source_name="RSS Feed Poller",
            confidence=0.5,
        )]


# ---------------------------------------------------------------------------
# URL validation helper
# ---------------------------------------------------------------------------

def validate_feed_url(url: str) -> tuple[bool, str]:
    """Check if a URL is reachable and looks like a valid feed.

    Returns ``(ok, message)``.
    """
    try:
        body, _headers = _fetch_url(url, timeout=10.0)
        if body is None:
            return False, "URL returned 304 Not Modified (no body)"
        root = ET.fromstring(body)  # nosec B314 — RSS validation, user-configured feeds only
        ftype = _detect_feed_type(root)
        if ftype == "rss":
            items = _parse_rss_items(root)
        else:
            items = _parse_atom_entries(root)
        return True, f"Valid {ftype.upper()} feed with {len(items)} entries"
    except ET.ParseError:
        return False, "URL did not return valid XML"
    except (httpx.HTTPStatusError, httpx.RequestError, OSError) as exc:
        return False, f"URL unreachable: {exc}"
