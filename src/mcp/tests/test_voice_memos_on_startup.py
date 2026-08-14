# Copyright 2026 Cerid AI. Apache-2.0 license.
"""RA-64: VoiceMemosPlugin.on_startup() must actually start the opt-in
watcher — before this fix, watch_voice_memos_dir had zero callers and
VOICE_MEMOS_OPT_IN was read by nothing."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from plugins.voice_memos.plugin import VoiceMemosPlugin, _ingest_voice_memo  # noqa: E402


class TestVoiceMemosOnStartup:
    def test_does_nothing_off_darwin_even_when_opted_in(self):
        with (
            patch("plugins.voice_memos.plugin.platform.system", return_value="Linux"),
            patch.dict("os.environ", {"VOICE_MEMOS_OPT_IN": "true"}),
            patch("plugins.voice_memos.plugin.threading.Thread") as mock_thread,
        ):
            VoiceMemosPlugin().on_startup()
        mock_thread.assert_not_called()

    def test_does_nothing_when_not_opted_in_on_darwin(self):
        with (
            patch("plugins.voice_memos.plugin.platform.system", return_value="Darwin"),
            patch.dict("os.environ", {"VOICE_MEMOS_OPT_IN": "false"}),
            patch("plugins.voice_memos.plugin.threading.Thread") as mock_thread,
        ):
            VoiceMemosPlugin().on_startup()
        mock_thread.assert_not_called()

    def test_does_nothing_when_opt_in_var_absent_on_darwin(self):
        with (
            patch("plugins.voice_memos.plugin.platform.system", return_value="Darwin"),
            patch.dict("os.environ", {}, clear=False),
            patch("plugins.voice_memos.plugin.threading.Thread") as mock_thread,
        ):
            import os
            os.environ.pop("VOICE_MEMOS_OPT_IN", None)
            VoiceMemosPlugin().on_startup()
        mock_thread.assert_not_called()

    def test_starts_watcher_thread_when_opted_in_on_darwin(self):
        from plugins.voice_memos.plugin import watch_voice_memos_dir

        with (
            patch("plugins.voice_memos.plugin.platform.system", return_value="Darwin"),
            patch.dict("os.environ", {"VOICE_MEMOS_OPT_IN": "true"}),
            patch("plugins.voice_memos.plugin.threading.Thread") as mock_thread,
        ):
            VoiceMemosPlugin().on_startup()

        mock_thread.assert_called_once()
        _, kwargs = mock_thread.call_args
        assert kwargs["target"] is watch_voice_memos_dir
        assert kwargs["args"] == (_ingest_voice_memo,)
        assert kwargs["daemon"] is True
        mock_thread.return_value.start.assert_called_once()
