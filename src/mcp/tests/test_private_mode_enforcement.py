# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Server-side Private Mode L1 enforcement (Task 1.1).

Private Mode has historically been a client-side-only convenience — the
level is stored in Redis, but no server code path consulted it. These
tests verify that a direct API caller hitting the write endpoints below
gets the same "skip saves & sync" guarantee the web client applies
locally, once the global private-mode level is >= 1:

  * ``POST /user-state/conversations``       (save_conversation)
  * ``POST /user-state/conversations/bulk``  (save_conversations_bulk)
  * ``POST /agent/memory/extract``           (memory_extract_endpoint)
  * ``POST /sdk/v1/feedback``                (submit_claim_feedback)

Also covers ``app.services.private_mode.get_private_mode_level``'s behaviour
when Redis is unreachable. That used to be fail-open-to-0; as of 2026-07-30 it
holds the last successfully-read level instead, because failing open silently
deactivated every server-side guarantee for API/SDK/MCP callers, which have no
client-side skip to fall back on. See tests/test_private_mode_redis_failure.py
for the full contract.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.services.private_mode import PRIVATE_MODE_KEY, get_private_mode_level


class _FakeRedis:
    """Minimal in-memory stand-in — matches the pattern in test_private_mode_l4.py."""

    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def get(self, key):
        return self.store.get(key)

    def set(self, key, value):
        self.store[key] = value


@pytest.fixture()
def fake_redis():
    return _FakeRedis()


# ---------------------------------------------------------------------------
# get_private_mode_level — fail-open behavior
# ---------------------------------------------------------------------------


class TestGetPrivateModeLevelRedisFailure:
    """Redis unreachable → hold the last known level, not 0.

    ``_last_known_level`` is a module global, so these tests pin it explicitly
    rather than inheriting whatever an earlier test in the session read.
    """

    @pytest.fixture(autouse=True)
    def _cold_cache(self):
        import app.services.private_mode as pm

        pm._last_known_level = 0
        yield
        pm._last_known_level = 0

    def test_returns_0_when_redis_raises_and_no_level_was_ever_read(self, monkeypatch):
        """Cold start with Redis already down — 0 is the only honest answer."""
        def _boom():
            raise ConnectionError("redis unreachable")

        monkeypatch.setattr("app.services.private_mode.get_redis", _boom)
        assert get_private_mode_level() == 0

    def test_holds_the_last_known_level_when_redis_raises(self, fake_redis, monkeypatch):
        """The regression guard: a blip must not silently drop the user to 0."""
        fake_redis.set(PRIVATE_MODE_KEY, "3")
        monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)
        assert get_private_mode_level() == 3

        def _boom():
            raise ConnectionError("redis unreachable")

        monkeypatch.setattr("app.services.private_mode.get_redis", _boom)
        assert get_private_mode_level() == 3, (
            "private mode failed open to 0 on a Redis error — every server-side "
            "guarantee silently deactivated for callers with no client-side skip"
        )

    def test_returns_level_from_redis(self, fake_redis, monkeypatch):
        fake_redis.set(PRIVATE_MODE_KEY, "1")
        monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)
        assert get_private_mode_level() == 1

    def test_returns_0_when_unset(self, fake_redis, monkeypatch):
        monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)
        assert get_private_mode_level() == 0


# ---------------------------------------------------------------------------
# user_state.py — save_conversation / save_conversations_bulk
# ---------------------------------------------------------------------------


@pytest.fixture()
def user_state_client(fake_redis, monkeypatch, tmp_path):
    from app.routers import user_state

    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)
    monkeypatch.setattr(user_state, "_sync_dir", lambda: str(tmp_path))

    app = FastAPI()
    app.include_router(user_state.router)
    return TestClient(app)


class TestSaveConversationL1:
    def test_l1_blocks_save_and_skips_store(self, user_state_client, fake_redis):
        fake_redis.set(PRIVATE_MODE_KEY, "1")
        with patch("app.routers.user_state.write_conversation") as mock_write:
            res = user_state_client.post(
                "/user-state/conversations",
                json={"id": "c1", "title": "hello"},
            )
        assert res.status_code == 200
        assert res.json() == {"saved": None}
        mock_write.assert_not_called()

    def test_l0_saves_normally(self, user_state_client, fake_redis):
        with patch("app.routers.user_state.write_conversation") as mock_write:
            res = user_state_client.post(
                "/user-state/conversations",
                json={"id": "c1", "title": "hello"},
            )
        assert res.status_code == 200
        assert res.json() == {"saved": "c1"}
        mock_write.assert_called_once()


class TestSaveConversationsBulkL1:
    def test_l1_blocks_save_and_skips_store(self, user_state_client, fake_redis):
        fake_redis.set(PRIVATE_MODE_KEY, "1")
        with patch("app.routers.user_state.write_conversation") as mock_write:
            res = user_state_client.post(
                "/user-state/conversations/bulk",
                json=[{"id": "b1"}, {"id": "b2"}],
            )
        assert res.status_code == 200
        assert res.json() == {"saved": []}
        mock_write.assert_not_called()

    def test_l0_saves_normally(self, user_state_client, fake_redis):
        with patch("app.routers.user_state.write_conversation") as mock_write:
            res = user_state_client.post(
                "/user-state/conversations/bulk",
                json=[{"id": "b1"}, {"id": "b2"}],
            )
        assert res.status_code == 200
        assert res.json() == {"saved": 2}
        assert mock_write.call_count == 2


# ---------------------------------------------------------------------------
# agents.py — memory_extract_endpoint
# ---------------------------------------------------------------------------


@pytest.fixture()
def agents_client(fake_redis, monkeypatch):
    from app.routers import agents

    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)
    # extract_and_store_memories is patched per-test, but its kwargs
    # (chroma_client=get_chroma(), ...) are evaluated eagerly by the
    # handler even when the function itself is mocked — stub these so
    # the L0 pass-through path doesn't attempt a real connection.
    monkeypatch.setattr(agents, "get_chroma", lambda: object())
    monkeypatch.setattr(agents, "get_neo4j", lambda: object())
    monkeypatch.setattr(agents, "get_redis", lambda: fake_redis)

    app = FastAPI()
    app.include_router(agents.router)
    return TestClient(app)


class TestMemoryExtractL1:
    def test_l1_blocks_extract_and_skips_store(self, agents_client, fake_redis):
        fake_redis.set(PRIVATE_MODE_KEY, "1")
        with patch(
            "app.agents.memory.extract_and_store_memories",
            new_callable=AsyncMock,
        ) as mock_extract:
            res = agents_client.post(
                "/agent/memory/extract",
                json={"response_text": "some response", "conversation_id": "conv-1"},
            )
        assert res.status_code == 200
        assert res.json() == {"stored": False, "skipped": "private_mode"}
        mock_extract.assert_not_called()

    def test_l0_extracts_normally(self, agents_client, fake_redis):
        with patch(
            "app.agents.memory.extract_and_store_memories",
            new_callable=AsyncMock,
            return_value={
                "conversation_id": "conv-1",
                "timestamp": "2026-07-05T00:00:00Z",
                "memories_extracted": 0,
                "memories_stored": 0,
                "skipped_duplicates": 0,
                "results": [],
            },
        ) as mock_extract:
            res = agents_client.post(
                "/agent/memory/extract",
                json={"response_text": "some response", "conversation_id": "conv-1"},
            )
        assert res.status_code == 200, res.text
        mock_extract.assert_called_once()


# ---------------------------------------------------------------------------
# agents.py — hallucination_check_endpoint / verify_stream_endpoint
# verified-memory-promotion create_memory_fn wiring (task 1.2b)
#
# promote_verified_facts (core.agents.verified_memory) writes a permanent,
# no-decay :Memory node with the raw claim text via an injected
# create_memory_fn, then unconditionally ingests that same text into Chroma
# regardless of what create_memory_fn returned. Neither write was gated by
# private_blocks. These tests verify the fix: at L1+, both endpoints inject
# bare None — not a no-op callable — as create_memory_fn. None matters
# specifically because it also trips the `create_memory_fn is not None`
# dispatch guard already present at both call sites in
# core.agents.hallucination.streaming, which skips promote_verified_facts
# (and therefore the Chroma ingest) entirely; a callable that merely
# returns None would still be dispatched and would still leak the claim
# text into Chroma. See app.routers.agents._verified_memory_fn.
# ---------------------------------------------------------------------------


class TestHallucinationEndpointVerifiedMemoryGating:
    @staticmethod
    def _fake_check_hallucinations(captured):
        async def _fake(**kwargs):
            captured.update(kwargs)
            return {
                "conversation_id": kwargs.get("conversation_id"),
                "timestamp": "2026-07-05T00:00:00Z",
                "skipped": False,
                "claims": [],
                "summary": {"total": 0, "verified": 0, "unverified": 0, "uncertain": 0},
            }
        return _fake

    def test_l1_injects_none_not_a_noop_callable(self, agents_client, fake_redis):
        fake_redis.set(PRIVATE_MODE_KEY, "1")
        captured: dict = {}
        with patch(
            "core.agents.hallucination.check_hallucinations",
            new=self._fake_check_hallucinations(captured),
        ):
            res = agents_client.post(
                "/agent/hallucination",
                json={
                    "response_text": "x" * 300,
                    "conversation_id": "conv-l1",
                    "persist": False,
                },
            )
        assert res.status_code == 200, res.text
        assert captured["create_memory_fn"] is None

    def test_l0_injects_create_memory_node(self, agents_client, fake_redis):
        captured: dict = {}
        with (
            patch(
                "core.agents.hallucination.check_hallucinations",
                new=self._fake_check_hallucinations(captured),
            ),
            patch("app.db.neo4j.memory.create_memory_node") as mock_create,
        ):
            res = agents_client.post(
                "/agent/hallucination",
                json={
                    "response_text": "x" * 300,
                    "conversation_id": "conv-l0",
                    "persist": False,
                },
            )
        assert res.status_code == 200, res.text
        assert captured["create_memory_fn"] is mock_create


class TestVerifyStreamEndpointVerifiedMemoryGating:
    """SSE twin of the above. The endpoint function itself is a plain
    ``async def`` returning a ``StreamingResponse`` — calling it directly
    and draining ``resp.body_iterator`` drives the same wiring without
    needing a real SSE-over-HTTP round trip."""

    @staticmethod
    def _fake_verify_stream(captured):
        async def _fake(**kwargs):
            captured.update(kwargs)
            return
            yield  # pragma: no cover — makes this an async generator function
        return _fake

    @pytest.mark.asyncio
    async def test_l1_injects_none_not_a_noop_callable(self, agents_client, fake_redis):
        from app.routers import agents

        fake_redis.set(PRIVATE_MODE_KEY, "1")
        captured: dict = {}
        with patch(
            "core.agents.hallucination.verify_response_streaming",
            new=self._fake_verify_stream(captured),
        ):
            req = agents.VerifyStreamRequest(
                response_text="x" * 300, conversation_id="conv-l1",
            )
            resp = await agents.verify_stream_endpoint(req)
            async for _ in resp.body_iterator:
                pass

        assert captured["create_memory_fn"] is None

    @pytest.mark.asyncio
    async def test_l0_injects_create_memory_node(self, agents_client, fake_redis):
        from app.routers import agents

        captured: dict = {}
        with (
            patch(
                "core.agents.hallucination.verify_response_streaming",
                new=self._fake_verify_stream(captured),
            ),
            patch("app.db.neo4j.memory.create_memory_node") as mock_create,
        ):
            req = agents.VerifyStreamRequest(
                response_text="x" * 300, conversation_id="conv-l0",
            )
            resp = await agents.verify_stream_endpoint(req)
            async for _ in resp.body_iterator:
                pass

        assert captured["create_memory_fn"] is mock_create


# ---------------------------------------------------------------------------
# feedback.py — submit_claim_feedback
# ---------------------------------------------------------------------------


@pytest.fixture()
def feedback_client(fake_redis, monkeypatch):
    from app.routers import feedback

    monkeypatch.setattr("app.services.private_mode.get_redis", lambda: fake_redis)

    app = FastAPI()
    app.include_router(feedback.router)
    return TestClient(app)


class TestSubmitClaimFeedbackL1:
    def test_l1_blocks_feedback_and_skips_store(self, feedback_client, fake_redis):
        fake_redis.set(PRIVATE_MODE_KEY, "1")
        with patch(
            "app.routers.feedback.submit_feedback",
            new_callable=AsyncMock,
        ) as mock_submit:
            res = feedback_client.post(
                "/sdk/v1/feedback",
                json={"claim_id": "claim-001", "sentiment": 1},
            )
        assert res.status_code == 201
        assert res.json() == {"ok": True, "rating_id": None}
        mock_submit.assert_not_called()

    def test_l0_submits_normally(self, feedback_client, fake_redis):
        with patch(
            "app.routers.feedback.submit_feedback",
            new_callable=AsyncMock,
            return_value="rating-abc123",
        ) as mock_submit:
            res = feedback_client.post(
                "/sdk/v1/feedback",
                json={"claim_id": "claim-001", "sentiment": 1},
            )
        assert res.status_code == 201
        assert res.json() == {"ok": True, "rating_id": "rating-abc123"}
        mock_submit.assert_called_once()
