# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SSRF guard tests for outbound URL fetches."""
from unittest.mock import patch

import pytest


class TestIsPrivateHost:
    def test_public_url_allowed(self):
        from app.reliability.url_safety import is_private_host
        # Use a domain that resolves reliably to public IPs.
        # Patch getaddrinfo to a known-public address to avoid DNS flakiness.
        fake_public = [(2, 1, 6, "", ("8.8.8.8", 0))]  # Google public DNS
        with patch("socket.getaddrinfo", return_value=fake_public):
            assert is_private_host("https://example.com/feed.xml") is False

    def test_loopback_ipv4_blocked(self):
        from app.reliability.url_safety import is_private_host
        fake_loopback = [(2, 1, 6, "", ("127.0.0.1", 0))]
        with patch("socket.getaddrinfo", return_value=fake_loopback):
            assert is_private_host("http://localhost/") is True

    def test_loopback_ipv6_blocked(self):
        from app.reliability.url_safety import is_private_host
        fake_v6_loopback = [(10, 1, 6, "", ("::1", 0, 0, 0))]
        with patch("socket.getaddrinfo", return_value=fake_v6_loopback):
            assert is_private_host("http://[::1]/") is True

    def test_cloud_metadata_endpoint_blocked(self):
        from app.reliability.url_safety import is_private_host
        fake_metadata = [(2, 1, 6, "", ("169.254.169.254", 0))]
        with patch("socket.getaddrinfo", return_value=fake_metadata):
            assert is_private_host("http://169.254.169.254/latest/") is True

    def test_private_rfc1918_10_blocked(self):
        from app.reliability.url_safety import is_private_host
        fake_private = [(2, 1, 6, "", ("10.0.0.1", 0))]
        with patch("socket.getaddrinfo", return_value=fake_private):
            assert is_private_host("http://10.0.0.1/") is True

    def test_private_rfc1918_192_blocked(self):
        from app.reliability.url_safety import is_private_host
        fake_private = [(2, 1, 6, "", ("192.168.1.1", 0))]
        with patch("socket.getaddrinfo", return_value=fake_private):
            assert is_private_host("http://intranet.local/") is True

    def test_multi_address_with_any_private_blocked(self):
        """DNS rebinding: if ANY resolved IP is private, reject."""
        from app.reliability.url_safety import is_private_host
        fake_mixed = [
            (2, 1, 6, "", ("8.8.8.8", 0)),   # public
            (2, 1, 6, "", ("10.0.0.1", 0)),  # private — triggers block
        ]
        with patch("socket.getaddrinfo", return_value=fake_mixed):
            assert is_private_host("http://rebind.example.com/") is True

    def test_malformed_url_blocked(self):
        from app.reliability.url_safety import is_private_host
        assert is_private_host("not a url") is True

    def test_no_hostname_blocked(self):
        from app.reliability.url_safety import is_private_host
        assert is_private_host("http:///path") is True

    def test_dns_failure_blocked(self):
        from app.reliability.url_safety import is_private_host
        with patch("socket.getaddrinfo", side_effect=OSError("DNS failed")):
            assert is_private_host("http://nonexistent.example/") is True

    def test_reserved_address_blocked(self):
        from app.reliability.url_safety import is_private_host
        fake_reserved = [(2, 1, 6, "", ("240.0.0.1", 0))]
        with patch("socket.getaddrinfo", return_value=fake_reserved):
            assert is_private_host("http://240.0.0.1/") is True

    def test_multicast_address_blocked(self):
        from app.reliability.url_safety import is_private_host
        fake_multicast = [(2, 1, 6, "", ("224.0.0.1", 0))]
        with patch("socket.getaddrinfo", return_value=fake_multicast):
            assert is_private_host("http://224.0.0.1/") is True

    def test_ipv4_mapped_ipv6_private_blocked(self):
        from app.reliability.url_safety import is_private_host
        fake_mapped = [(10, 1, 6, "", ("::ffff:10.0.0.1", 0, 0, 0))]
        with patch("socket.getaddrinfo", return_value=fake_mapped):
            assert is_private_host("http://[::ffff:10.0.0.1]/") is True


class TestGuardOrLog:
    def test_safe_url_returns_true(self):
        from app.reliability.url_safety import guard_or_log
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("8.8.8.8", 0))]):
            assert guard_or_log("https://example.com/", source_name="rss_feed") is True

    def test_private_url_returns_false_and_captures(self, monkeypatch):
        from app.reliability.url_safety import guard_or_log
        captures = []
        monkeypatch.setattr(
            "sentry_sdk.capture_message",
            lambda *a, **k: captures.append((a, k)),
        )
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("10.0.0.1", 0))]):
            result = guard_or_log("http://10.0.0.1/", source_name="rss_feed")
        assert result is False
        assert len(captures) == 1
        assert "rss_feed.ssrf_blocked" in captures[0][0][0]


class TestRssFeedIntegration:
    @pytest.mark.asyncio
    async def test_rss_feed_skips_private_url(self, monkeypatch):
        """A feed config with a private-host URL must produce an empty result
        and must never call _fetch_url (the underlying httpx.get wrapper).

        The SSRF guard fires before the circuit breaker is even initialised,
        so no circuit-breaker mock is needed.
        """
        private_feed = {
            "id": "test001",
            "url": "http://192.168.1.1/feed.xml",
            "name": "Evil Feed",
            "domain": "general",
            "enabled": True,
            "last_fetched": None,
            "etag": None,
            "last_modified": None,
        }

        # Patch sentry to avoid real calls
        monkeypatch.setattr("sentry_sdk.capture_message", lambda *a, **k: None)

        # Track whether _fetch_url is called
        fetch_called = []
        import utils.data_sources.rss_feed as rss_module
        original_fetch = rss_module._fetch_url

        def tracking_fetch(*args, **kwargs):
            fetch_called.append(args)
            return original_fetch(*args, **kwargs)

        monkeypatch.setattr(rss_module, "_fetch_url", tracking_fetch)

        # Patch socket.getaddrinfo to return a private address
        with patch("socket.getaddrinfo", return_value=[(2, 1, 6, "", ("192.168.1.1", 0))]):
            result = await rss_module.poll_feed(private_feed)

        # The feed should be blocked — no new entries, ssrf_blocked error recorded
        assert result["new_entries"] == 0
        assert len(fetch_called) == 0  # proved: _fetch_url never called
        assert any("ssrf" in str(e).lower() or "blocked" in str(e).lower()
                   for e in result["errors"])
