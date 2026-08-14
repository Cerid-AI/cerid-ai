# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Bookmark import must report structurally-unavailable off macOS (WB-50).

The bookmark stores this reads live under ``~/Library`` — a macOS path the
Linux MCP container can never see. Before this fix, ``is_configured()`` was
hardcoded ``True`` and ``detect_browsers()`` silently resolved container-local
paths that could never exist, reporting every browser as simply "not
installed" rather than "this runtime cannot read bookmarks at all".
"""
from __future__ import annotations

from unittest.mock import patch

from app.data_sources.bookmarks import BookmarksSource, detect_browsers


def test_is_configured_false_off_darwin():
    with patch("app.data_sources.bookmarks.platform.system", return_value="Linux"):
        assert BookmarksSource().is_configured() is False


def test_is_configured_true_on_darwin():
    with patch("app.data_sources.bookmarks.platform.system", return_value="Darwin"):
        assert BookmarksSource().is_configured() is True


def test_detect_browsers_reports_structurally_unavailable_off_darwin():
    with patch("app.data_sources.bookmarks.platform.system", return_value="Linux"):
        detected = detect_browsers()
    assert set(detected.keys()) == {"chrome", "firefox", "safari"}
    for entry in detected.values():
        assert entry["installed"] is False
        assert entry["bookmark_count"] == 0
        assert entry["state"] == "structurally_unavailable"


def test_detect_browsers_does_not_touch_filesystem_off_darwin():
    """Off Darwin, detection must short-circuit before any path check —
    never silently resolve container-local macOS paths."""
    with patch("app.data_sources.bookmarks.platform.system", return_value="Linux"), \
         patch("app.data_sources.bookmarks._CHROME_BOOKMARKS_PATH") as mock_path:
        detect_browsers()
    mock_path.exists.assert_not_called()
