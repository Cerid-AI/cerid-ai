# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for CRDT primitives and presence manager."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

# ---------------------------------------------------------------------------
# LWWRegister
# ---------------------------------------------------------------------------

class TestLWWRegister:
    """Last-Writer-Wins Register tests."""

    def test_lww_register_merge_newer_wins(self):
        from app.sync.crdt import LWWRegister

        old = LWWRegister("old_value", 1.0)
        new = LWWRegister("new_value", 2.0)
        merged = old.merge(new)
        assert merged.value == "new_value"
        assert merged.timestamp == 2.0

    def test_lww_register_merge_older_loses(self):
        from app.sync.crdt import LWWRegister

        current = LWWRegister("current", 5.0)
        stale = LWWRegister("stale", 3.0)
        merged = current.merge(stale)
        assert merged.value == "current"
        assert merged.timestamp == 5.0

    def test_lww_register_set_updates_on_newer(self):
        from app.sync.crdt import LWWRegister

        reg = LWWRegister("a", 1.0)
        reg.set("b", 2.0)
        assert reg.value == "b"

    def test_lww_register_set_ignores_older(self):
        from app.sync.crdt import LWWRegister

        reg = LWWRegister("a", 5.0)
        reg.set("b", 3.0)
        assert reg.value == "a"


# ---------------------------------------------------------------------------
# ORSet
# ---------------------------------------------------------------------------

class TestORSet:
    """Observed-Remove Set tests."""

    def test_or_set_add_and_remove(self):
        from app.sync.crdt import ORSet

        s = ORSet()
        tag = s.add("apple")
        assert "apple" in s.elements

        s.remove("apple", {tag})
        assert "apple" not in s.elements

    def test_or_set_concurrent_add_remove(self):
        """Concurrent add on replica B and remove on replica A.

        After merge, the element should be present because B's add
        introduced a new tag that A's remove did not observe.
        """
        from app.sync.crdt import ORSet

        # Replica A: add then remove
        a = ORSet()
        tag_a = a.add("item")
        a.remove("item", {tag_a})
        assert "item" not in a.elements

        # Replica B: concurrent add (different tag)
        b = ORSet()
        b.add("item", "tag-b")
        assert "item" in b.elements

        # Merge: B's tag survives because A never observed it
        merged = a.merge(b)
        assert "item" in merged.elements

    def test_or_set_empty_remove_noop(self):
        from app.sync.crdt import ORSet

        s = ORSet()
        s.remove("nonexistent")  # should not raise
        assert s.elements == set()

    def test_or_set_local_remove_clears_all_tags(self):
        from app.sync.crdt import ORSet

        s = ORSet()
        s.add("x", "t1")
        s.add("x", "t2")
        assert "x" in s.elements

        # Local remove (observed_tags=None) clears all
        s.remove("x")
        assert "x" not in s.elements


# ---------------------------------------------------------------------------
# LWWElementDict
# ---------------------------------------------------------------------------

class TestLWWElementDict:
    """LWW Element Dictionary tests."""

    def test_lww_element_dict_merge(self):
        from app.sync.crdt import LWWElementDict

        a = LWWElementDict()
        a.set("title", "Draft", 1.0)
        a.set("status", "open", 1.0)

        b = LWWElementDict()
        b.set("title", "Final", 2.0)
        b.set("author", "Alice", 1.5)

        merged = a.merge(b)
        assert merged.get("title") == "Final"  # b's newer timestamp wins
        assert merged.get("status") == "open"  # only in a
        assert merged.get("author") == "Alice"  # only in b

    def test_lww_element_dict_get_missing(self):
        from app.sync.crdt import LWWElementDict

        d = LWWElementDict()
        assert d.get("nonexistent") is None


# ---------------------------------------------------------------------------
# CRDTState encode/decode roundtrip
# ---------------------------------------------------------------------------

class TestCRDTState:
    """CRDTState serialization tests."""

    def test_crdt_state_encode_decode(self):
        from app.sync.crdt import CRDTState, LWWRegister, decode_delta, encode_delta

        state = CRDTState()
        state.metadata.set("title", "Test Artifact", 1.0)
        state.tags.add("python", "tag-1")
        state.tags.add("ai", "tag-2")
        state.content = LWWRegister("Hello world", 1.0)

        encoded = encode_delta(state)
        # Ensure it's JSON-serializable
        json_str = json.dumps(encoded)
        decoded_data = json.loads(json_str)
        restored = decode_delta(decoded_data)

        assert restored.metadata.get("title") == "Test Artifact"
        assert "python" in restored.tags.elements
        assert "ai" in restored.tags.elements
        assert restored.content.value == "Hello world"
        assert restored.content.timestamp == 1.0


# ---------------------------------------------------------------------------
# PresenceManager (mocked Redis)
# ---------------------------------------------------------------------------

class TestPresenceManager:
    """Presence tracking tests with mocked Redis."""

    def _make_manager(self, mock_redis: MagicMock) -> object:
        """Create a PresenceManager with mocked Redis."""
        with patch("app.sync.presence.PresenceManager._get_redis", return_value=mock_redis):
            from app.sync.presence import PresenceManager

            mgr = PresenceManager(timeout_s=90)
            mgr._get_redis = lambda: mock_redis  # type: ignore[assignment]
            return mgr

    def test_presence_manager_update_and_get(self):
        mock_redis = MagicMock()
        # Simulate smembers returning a set of user IDs
        mock_redis.smembers.return_value = {b"user-1"}
        # Simulate get returning stored JSON
        stored = json.dumps({"user_id": "user-1", "last_seen": 1000.0, "display_name": "Alice"})
        mock_redis.get.return_value = stored.encode()

        mgr = self._make_manager(mock_redis)
        mgr.update("user-1", {"display_name": "Alice"})

        # Verify set was called with TTL
        mock_redis.set.assert_called_once()
        call_args = mock_redis.set.call_args
        assert call_args[1]["ex"] == 90

        # Verify sadd was called
        mock_redis.sadd.assert_called_once()

        # get_all should return the user
        users = mgr.get_all()
        assert len(users) == 1
        assert users[0]["user_id"] == "user-1"

    def test_presence_manager_remove(self):
        mock_redis = MagicMock()
        mock_redis.smembers.return_value = set()

        mgr = self._make_manager(mock_redis)
        mgr.remove("user-1")

        mock_redis.delete.assert_called_once()
        mock_redis.srem.assert_called_once()

        # After removal, get_all should be empty
        users = mgr.get_all()
        assert users == []

    def test_presence_heartbeat_refresh(self):
        mock_redis = MagicMock()
        mock_redis.exists.return_value = True

        mgr = self._make_manager(mock_redis)
        mgr.heartbeat("user-1")

        # Should call expire to refresh TTL
        mock_redis.expire.assert_called_once()
        call_args = mock_redis.expire.call_args
        assert call_args[0][1] == 90  # timeout_s

    def test_presence_heartbeat_recreates_if_expired(self):
        mock_redis = MagicMock()
        mock_redis.exists.return_value = False

        mgr = self._make_manager(mock_redis)
        mgr.heartbeat("user-1")

        # Should fall through to update (which calls set + sadd)
        mock_redis.set.assert_called_once()
        mock_redis.sadd.assert_called_once()
