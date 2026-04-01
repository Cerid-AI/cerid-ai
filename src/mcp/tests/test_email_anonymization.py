# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for email header anonymization during ingestion."""
from __future__ import annotations

from unittest.mock import patch


def test_anonymize_strips_email_addresses():
    """When ANONYMIZE_EMAIL_HEADERS is true, From/To/Cc should be redacted."""
    from parsers.email import _anonymize_header

    with patch("parsers.email._config.ANONYMIZE_EMAIL_HEADERS", True):
        result = _anonymize_header("John Doe <john.doe@example.com>")
        assert "john.doe@example.com" not in result
        assert "john.doe" not in result
        assert "example.com" in result
        assert "[redacted]@example.com" in result


def test_anonymize_handles_multiple_addresses():
    """Multiple recipients should all be anonymized."""
    from parsers.email import _anonymize_header

    with patch("parsers.email._config.ANONYMIZE_EMAIL_HEADERS", True):
        result = _anonymize_header("alice@foo.com, Bob <bob@bar.org>")
        assert "alice@foo.com" not in result
        assert "bob@bar.org" not in result
        assert "[redacted]@foo.com" in result
        assert "[redacted]@bar.org" in result


def test_anonymize_preserves_non_email_text():
    """Subject lines and dates should pass through unchanged."""
    from parsers.email import _anonymize_header

    with patch("parsers.email._config.ANONYMIZE_EMAIL_HEADERS", True):
        result = _anonymize_header("Weekly team standup notes")
        assert result == "Weekly team standup notes"


def test_anonymize_disabled_preserves_original():
    """When ANONYMIZE_EMAIL_HEADERS is false, original headers are kept."""
    from parsers.email import _anonymize_header

    with patch("parsers.email._config.ANONYMIZE_EMAIL_HEADERS", False):
        result = _anonymize_header("John Doe <john.doe@example.com>")
        assert "john.doe@example.com" in result
