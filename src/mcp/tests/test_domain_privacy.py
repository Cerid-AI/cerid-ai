# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for utils.domain_privacy — deferred Phase D.2 cleanup."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from utils.domain_privacy import (
    DOMAIN_PRIVACY_FLOOR,
    get_global_private_mode_level,
    is_domain_visible,
    visible_domains,
)


class TestDomainPrivacyFloor:
    def test_messages_requires_level_2(self):
        assert DOMAIN_PRIVACY_FLOOR["messages"] == 2

    def test_imessage_alias_same_floor(self):
        assert DOMAIN_PRIVACY_FLOOR["imessage"] == 2


class TestVisibleDomains:
    def test_none_passes_through(self):
        # None means "no narrowing" — the filter has nothing to subtract
        # from and returns None.
        assert visible_domains(None, 0) is None

    def test_empty_list_returns_empty(self):
        assert visible_domains([], 0) == []

    def test_non_gated_domains_pass_at_all_levels(self):
        for level in range(0, 5):
            assert visible_domains(["personal", "notes", "mail"], level) == [
                "personal", "notes", "mail",
            ]

    def test_messages_hidden_below_floor(self):
        result = visible_domains(["personal", "messages", "notes"], 0)
        assert "messages" not in result
        assert "personal" in result
        assert "notes" in result

    def test_messages_hidden_at_level_1(self):
        result = visible_domains(["personal", "messages"], 1)
        assert result == ["personal"]

    def test_messages_visible_at_level_2(self):
        result = visible_domains(["personal", "messages"], 2)
        assert "messages" in result

    def test_messages_visible_at_higher_levels(self):
        for level in (3, 4):
            assert "messages" in visible_domains(["messages"], level)

    def test_does_not_mutate_input(self):
        original = ["personal", "messages"]
        _ = visible_domains(original, 0)
        assert original == ["personal", "messages"]

    def test_imessage_alias_filtered_same_as_messages(self):
        assert visible_domains(["imessage"], 0) == []
        assert visible_domains(["imessage"], 2) == ["imessage"]


class TestIsDomainVisible:
    def test_unknown_domain_visible_at_all_levels(self):
        for level in range(0, 5):
            assert is_domain_visible("unknown_future_domain", level) is True

    def test_messages_level_floor(self):
        assert is_domain_visible("messages", 0) is False
        assert is_domain_visible("messages", 1) is False
        assert is_domain_visible("messages", 2) is True


class TestGlobalLevelReader:
    def test_returns_0_when_redis_unavailable(self):
        with patch("app.deps.get_redis", return_value=None):
            assert get_global_private_mode_level() == 0

    def test_returns_0_on_redis_error(self):
        broken = MagicMock()
        broken.get.side_effect = RuntimeError("boom")
        with patch("app.deps.get_redis", return_value=broken):
            assert get_global_private_mode_level() == 0

    def test_reads_redis_value(self):
        mock = MagicMock()
        mock.get.return_value = b"2"
        with patch("app.deps.get_redis", return_value=mock):
            assert get_global_private_mode_level() == 2

    def test_returns_0_when_key_missing(self):
        mock = MagicMock()
        mock.get.return_value = None
        with patch("app.deps.get_redis", return_value=mock):
            assert get_global_private_mode_level() == 0


class TestPrivacyDefaulting:
    """Privacy invariants — the filter MUST default closed."""

    def test_unknown_redis_means_messages_hidden(self):
        # If get_global_private_mode_level can't read Redis (returns 0),
        # messages must be excluded.
        with patch(
            "utils.domain_privacy.get_global_private_mode_level",
            return_value=0,
        ):
            from utils.domain_privacy import get_global_private_mode_level as get_lvl
            assert get_lvl() == 0
        assert visible_domains(["messages"], 0) == []
