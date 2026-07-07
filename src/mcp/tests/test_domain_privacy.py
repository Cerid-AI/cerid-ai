# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for utils.domain_privacy (Task 1.2e: dedicated sensitive-domain opt-in,
decoupled from the private-mode isolation level)."""
from __future__ import annotations

from unittest.mock import patch

from utils.domain_privacy import (
    SENSITIVE_DOMAINS,
    is_domain_visible,
    sensitive_domains_opted_in,
    visible_domains,
)


class TestSensitiveDomains:
    def test_messages_is_sensitive(self):
        assert "messages" in SENSITIVE_DOMAINS

    def test_imessage_alias_is_sensitive(self):
        assert "imessage" in SENSITIVE_DOMAINS


class TestVisibleDomains:
    def test_none_passes_through(self):
        # None means "no narrowing" — the filter has nothing to subtract
        # from and returns None.
        assert visible_domains(None, include_sensitive=False) is None
        assert visible_domains(None, include_sensitive=True) is None

    def test_empty_list_returns_empty(self):
        assert visible_domains([], include_sensitive=False) == []

    def test_non_gated_domains_pass_regardless_of_opt_in(self):
        for include_sensitive in (False, True):
            assert visible_domains(
                ["personal", "notes", "mail"], include_sensitive=include_sensitive
            ) == ["personal", "notes", "mail"]

    def test_messages_hidden_when_opted_out(self):
        result = visible_domains(
            ["personal", "messages", "notes"], include_sensitive=False
        )
        assert "messages" not in result
        assert "personal" in result
        assert "notes" in result

    def test_messages_visible_when_opted_in(self):
        result = visible_domains(["personal", "messages"], include_sensitive=True)
        assert "messages" in result

    def test_imessage_alias_filtered_same_as_messages(self):
        assert visible_domains(["imessage"], include_sensitive=False) == []
        assert visible_domains(["imessage"], include_sensitive=True) == ["imessage"]

    def test_does_not_mutate_input(self):
        original = ["personal", "messages"]
        _ = visible_domains(original, include_sensitive=False)
        assert original == ["personal", "messages"]

    def test_independent_of_any_private_mode_level(self):
        """The old level-based coupling is gone entirely — there is no level
        parameter, so opt-in state is the ONLY thing that can change the
        result. Simulate a private-mode level swing and confirm it has no
        bearing on visibility (no level argument even exists to pass)."""
        for _simulated_private_mode_level in (0, 1, 2, 3, 4):
            # No matter what a hypothetical private-mode level might be,
            # the outcome only depends on include_sensitive.
            assert visible_domains(["messages"], include_sensitive=False) == []
            assert visible_domains(["messages"], include_sensitive=True) == ["messages"]


class TestIsDomainVisible:
    def test_unknown_domain_visible_regardless_of_opt_in(self):
        for include_sensitive in (False, True):
            assert is_domain_visible(
                "unknown_future_domain", include_sensitive=include_sensitive
            ) is True

    def test_messages_gated_by_opt_in(self):
        assert is_domain_visible("messages", include_sensitive=False) is False
        assert is_domain_visible("messages", include_sensitive=True) is True


class TestSensitiveDomainsOptedIn:
    def test_reflects_config_false(self):
        with patch("config.settings.SENSITIVE_DOMAIN_RETRIEVAL_ENABLED", False):
            assert sensitive_domains_opted_in() is False

    def test_reflects_config_true(self):
        with patch("config.settings.SENSITIVE_DOMAIN_RETRIEVAL_ENABLED", True):
            assert sensitive_domains_opted_in() is True


class TestPrivacyDefaulting:
    """Privacy invariant — the filter MUST default closed.

    With the opt-in OFF (its documented env default), sensitive domains must
    stay hidden — identical to today's behavior at private_mode level < 2.
    This is the core safety invariant of Task 1.2e: removing the
    "raise-privacy-to-reveal" coupling must NOT make sensitive data visible
    by default.
    """

    def test_default_off_hides_messages(self):
        with patch("config.settings.SENSITIVE_DOMAIN_RETRIEVAL_ENABLED", False):
            assert sensitive_domains_opted_in() is False
            assert visible_domains(["messages"], include_sensitive=sensitive_domains_opted_in()) == []

    def test_default_off_hides_imessage_alias(self):
        with patch("config.settings.SENSITIVE_DOMAIN_RETRIEVAL_ENABLED", False):
            opted_in = sensitive_domains_opted_in()
            assert visible_domains(["imessage"], include_sensitive=opted_in) == []
