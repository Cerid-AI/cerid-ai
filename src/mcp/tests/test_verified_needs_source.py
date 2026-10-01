# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""A claim is counted as verified only when a source backs it.

A second model agreeing from its own training (``cross_model`` with no
``source_urls``) keeps ``status == "verified"`` on the wire, and is left out of
every ``verified`` count the server computes: the streaming summary, the
non-streaming report, the persisted report, the analytics entry, and the band a
brief claim is given. Contradictions from a second model count as before.
"""
from __future__ import annotations

import contextlib
import json
from unittest.mock import AsyncMock, patch

import pytest

_MOD = "core.agents.hallucination.streaming"

_AGREED = {
    "status": "verified", "similarity": 1.0,
    "verification_method": "cross_model", "source_urls": [],
}
_AGREED_COMPLEX = {**_AGREED, "verification_method": "cross_model_complex"}
_KB = {
    "status": "verified", "similarity": 0.9,
    "verification_method": "kb_nli", "source_artifact_id": "art-1",
}
_WEB = {
    "status": "verified", "similarity": 0.8,
    "verification_method": "web_search", "source_urls": ["https://example.org/a"],
}
_WEB_NO_URL = {**_WEB, "source_urls": []}
_REFUTED = {
    "status": "unverified", "similarity": 0.3,
    "verification_method": "cross_model", "source_urls": [],
}


class TestPredicate:
    @pytest.mark.parametrize(
        "claim",
        [
            _KB,
            _WEB,
            {"status": "verified", "verification_method": "kb"},
            {"status": "verified", "verification_method": "kb_batch"},
            {"status": "verified", "verification_method": "kb_only_timeout"},
            {"status": "verified", "verification_method": "cited_url",
             "source_urls": ["https://example.org/cited"]},
            {"status": "verified", "verification_method": "cross_model",
             "source_urls": ["https://example.org/annotated"]},
        ],
    )
    def test_source_backed(self, claim):
        from core.agents.hallucination.source_backing import (
            counts_as_verified,
            is_agreement_only,
            is_source_backed,
        )

        assert is_source_backed(claim)
        assert counts_as_verified(claim)
        assert not is_agreement_only(claim)

    @pytest.mark.parametrize(
        "claim",
        [
            _AGREED,
            _AGREED_COMPLEX,
            _WEB_NO_URL,
            {"status": "verified", "verification_method": "web_search", "source_urls": [""]},
            {"status": "verified", "verification_method": "cross_model", "source_urls": None},
            {"status": "verified"},
        ],
    )
    def test_agreement_without_a_source(self, claim):
        from core.agents.hallucination.source_backing import (
            counts_as_verified,
            is_agreement_only,
            is_source_backed,
        )

        assert not is_source_backed(claim)
        assert not counts_as_verified(claim)
        assert is_agreement_only(claim)

    def test_a_contradiction_is_neither(self):
        from core.agents.hallucination.source_backing import (
            counts_as_verified,
            is_agreement_only,
        )

        assert not counts_as_verified(_REFUTED)
        assert not is_agreement_only(_REFUTED)
        backed_refutation = {**_REFUTED, "source_urls": ["https://example.org/x"]}
        assert not counts_as_verified(backed_refutation)
        assert not is_agreement_only(backed_refutation)


class TestSummarizeClaims:
    def test_agreement_is_not_counted_verified(self):
        from core.agents.hallucination.streaming import _summarize_claims

        counts, overall = _summarize_claims([_AGREED, _AGREED_COMPLEX, _KB, _WEB, _REFUTED])

        assert counts["verified"] == 2
        assert counts["unverified"] == 1
        assert counts["uncertain"] == 0
        assert counts["total"] == 5

    def test_only_agreement_counts_no_verified(self):
        from core.agents.hallucination.streaming import _summarize_claims

        counts, _ = _summarize_claims([_AGREED, _AGREED])

        assert counts["verified"] == 0
        assert counts["uncertain"] == 0
        assert counts["total"] == 2

    def test_assessed_and_overall_still_cover_every_verdict(self):
        """The confidence mean is over the claims that got a verdict, agreed
        ones included; only the verified count changes."""
        from core.agents.hallucination.streaming import _summarize_claims

        counts, overall = _summarize_claims([_AGREED, _KB, _REFUTED])

        assert counts["assessed"] == 3
        assert overall == round((1.0 + 0.9 + 0.3) / 3, 3)

    def test_contradictions_count_as_before(self):
        from core.agents.hallucination.streaming import _summarize_claims

        counts, _ = _summarize_claims([_REFUTED, {**_REFUTED, "verification_method": "kb"}])

        assert counts["unverified"] == 2
        assert counts["verified"] == 0


class _FakeRedis:
    def __init__(self) -> None:
        self.saved: dict[str, str] = {}
        self.pushed: list[tuple[str, str]] = []

    def setex(self, key, _ttl, val):
        self.saved[key] = val

    def rpush(self, key, val):
        self.pushed.append((key, val))

    def get(self, _key):
        return None

    def __getattr__(self, _name):
        return lambda *a, **k: None


@contextlib.contextmanager
def _extraction(claims: list[str]):
    with (
        patch(f"{_MOD}._extract_claims_heuristic", return_value=claims),
        patch(f"{_MOD}._detect_evasion", return_value=[]),
        patch(f"{_MOD}._extract_citation_claims", return_value=[]),
        patch(f"{_MOD}._extract_ignorance_claims", return_value=[]),
        patch(f"{_MOD}._resolve_pronouns_heuristic", side_effect=lambda c, *a, **kw: c),
        patch(f"{_MOD}._extract_claims_llm", new_callable=AsyncMock, return_value=None),
    ):
        yield


_BY_CLAIM = {
    "Agreed fact one.": _AGREED,
    "Agreed fact two.": _AGREED_COMPLEX,
    "Document fact.": _KB,
    "Web fact.": _WEB,
    "Wrong fact.": _REFUTED,
}


async def _verify_claim(claim_text, *a, **k):
    return {"claim": claim_text, **_BY_CLAIM[claim_text]}


async def _stream(claims, redis, save=None):
    from core.agents.hallucination import verify_response_streaming

    events = []
    with (
        _extraction(claims),
        patch(f"{_MOD}.verify_claim", side_effect=_verify_claim),
        patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 5),
    ):
        async for ev in verify_response_streaming(
            "A response long enough to be checked.", "cid-src", None, None, redis,
            save_report_fn=save,
        ):
            events.append(ev)
    return events


def _metrics(redis: _FakeRedis) -> dict:
    from core.utils.cache import REDIS_VERIFICATION_METRICS_KEY

    entries = [json.loads(v) for k, v in redis.pushed if k == REDIS_VERIFICATION_METRICS_KEY]
    assert len(entries) == 1
    return entries[0]


class TestStreamingSummary:
    @pytest.mark.asyncio
    async def test_free_essay_with_no_sources_has_no_verified_claims(self):
        """The reported case: two claims a second model agreed with, nothing
        in the user's documents. It was summarised as 2 verified."""
        redis = _FakeRedis()
        saved: dict = {}
        events = await _stream(
            ["Agreed fact one.", "Agreed fact two."], redis, save=lambda **kw: saved.update(kw),
        )

        summary = next(e for e in events if e["type"] == "summary")
        assert summary["verified"] == 0
        assert summary["unverified"] == 0
        assert summary["uncertain"] == 0
        assert summary["total"] == 2
        assert saved["verified"] == 0
        assert saved["total"] == 2

        from core.agents.hallucination.streaming import REDIS_HALLUCINATION_PREFIX

        report = json.loads(redis.saved[f"{REDIS_HALLUCINATION_PREFIX}cid-src"])
        assert report["summary"]["verified"] == 0
        assert report["summary"]["total"] == 2
        entry = _metrics(redis)
        assert entry["verified"] == 0
        assert entry["accuracy"] == 0.0

    @pytest.mark.asyncio
    async def test_the_verdict_itself_is_unchanged_on_the_wire(self):
        redis = _FakeRedis()
        events = await _stream(["Agreed fact one."], redis)

        verdict = next(e for e in events if e["type"] == "claim_verified")
        assert verdict["status"] == "verified"
        assert verdict["verification_method"] == "cross_model"
        assert verdict["source_urls"] == []

    @pytest.mark.asyncio
    async def test_mixed_response_counts_only_the_backed_claims(self):
        redis = _FakeRedis()
        saved: dict = {}
        events = await _stream(
            ["Agreed fact one.", "Document fact.", "Web fact.", "Wrong fact."],
            redis, save=lambda **kw: saved.update(kw),
        )

        summary = next(e for e in events if e["type"] == "summary")
        assert summary["verified"] == 2
        assert summary["unverified"] == 1
        assert summary["total"] == 4
        assert summary["assessed"] == 4
        assert saved["verified"] == 2
        assert saved["unverified"] == 1
        entry = _metrics(redis)
        assert entry["verified"] == 2
        assert entry["accuracy"] == 0.5

    @pytest.mark.asyncio
    async def test_all_backed_response_counts_every_claim(self):
        redis = _FakeRedis()
        events = await _stream(["Document fact.", "Web fact."], redis)

        summary = next(e for e in events if e["type"] == "summary")
        assert summary["verified"] == 2
        assert summary["total"] == 2


class TestNonStreamingSummary:
    @pytest.mark.asyncio
    async def test_report_counts_only_the_backed_claims(self):
        from core.agents.hallucination import check_hallucinations

        redis = _FakeRedis()
        claims = ["Agreed fact one.", "Agreed fact two.", "Document fact.", "Wrong fact."]
        with (
            patch(f"{_MOD}.extract_claims", new_callable=AsyncMock, return_value=(claims, "heuristic")),
            patch(f"{_MOD}.verify_claim", side_effect=_verify_claim),
            patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 5),
            patch("config.ENABLE_VERIFIED_MEMORY_PROMOTION", False),
        ):
            report = await check_hallucinations(
                response_text="A response long enough to be checked.",
                conversation_id="cid-ns",
                chroma_client=None, neo4j_driver=None, redis_client=redis,
            )

        assert report["summary"]["verified"] == 1
        assert report["summary"]["unverified"] == 1
        assert report["summary"]["total"] == 4
        assert report["summary"]["assessed"] == 4
        assert [c["status"] for c in report["claims"]] == [
            "verified", "verified", "verified", "unverified",
        ]
        assert _metrics(redis)["verified"] == 1


class TestBriefBand:
    def test_agreement_is_a_partial_band(self):
        from app.services.briefs.verification import verdict_to_band

        assert verdict_to_band(_AGREED) == "partial"
        assert verdict_to_band(_WEB_NO_URL) == "partial"

    def test_backed_claims_keep_the_verified_band(self):
        from app.services.briefs.verification import verdict_to_band

        assert verdict_to_band(_KB) == "verified"
        assert verdict_to_band(_WEB) == "verified"

    def test_other_bands_are_unchanged(self):
        from app.services.briefs.verification import verdict_to_band

        assert verdict_to_band(_REFUTED) == "unverified"
        assert verdict_to_band({"status": "uncertain"}) == "partial"
        assert verdict_to_band({"status": "error"}) == "unverified"
        assert verdict_to_band({}) == "unverified"


# ---------------------------------------------------------------------------
# The agreed count is a field of the summary, and the buckets sum to the total
# ---------------------------------------------------------------------------

_UNCERTAIN = {
    "status": "uncertain", "similarity": 0.5,
    "verification_method": "cross_model", "source_urls": [],
}
_SKIPPED = {"status": "skipped", "similarity": 0.0, "verification_method": "cross_model"}
_ERROR = {"status": "error", "similarity": 0.0, "verification_method": "cross_model"}

_BY_CLAIM["Unclear fact."] = _UNCERTAIN
_BY_CLAIM["Skipped fact."] = _SKIPPED

_BUCKETS = ("verified", "agreed", "unverified", "uncertain", "skipped", "error")
_MIXED = [
    "Agreed fact one.", "Agreed fact two.", "Document fact.",
    "Web fact.", "Wrong fact.", "Unclear fact.",
]


def _assert_buckets_sum_to_total(summary: dict) -> None:
    assert type(summary["agreed"]) is int
    assert sum(summary.get(k, 0) for k in _BUCKETS) == summary["total"]


def _stored_report(redis: _FakeRedis, cid: str = "cid-src") -> dict:
    from core.agents.hallucination.streaming import REDIS_HALLUCINATION_PREFIX

    return json.loads(redis.saved[f"{REDIS_HALLUCINATION_PREFIX}{cid}"])


class TestAgreedCountInSummarizeClaims:
    def test_mixed_claims(self):
        from core.agents.hallucination.streaming import _summarize_claims

        counts, _ = _summarize_claims([
            _AGREED, _AGREED_COMPLEX, _WEB_NO_URL, _KB, _WEB,
            _REFUTED, _UNCERTAIN, _SKIPPED, _ERROR,
        ])

        assert counts["agreed"] == 3
        assert counts["verified"] == 2
        assert counts["unverified"] == 1
        assert counts["uncertain"] == 2
        assert counts["skipped"] == 1
        _assert_buckets_sum_to_total(counts)

    def test_no_agreement_is_zero_not_missing(self):
        from core.agents.hallucination.streaming import _summarize_claims

        counts, _ = _summarize_claims([_KB, _REFUTED])

        assert counts["agreed"] == 0
        _assert_buckets_sum_to_total(counts)

    def test_a_contradiction_is_never_agreed(self):
        from core.agents.hallucination.streaming import _summarize_claims

        counts, _ = _summarize_claims([_REFUTED, _REFUTED])

        assert counts["agreed"] == 0
        assert counts["unverified"] == 2


class TestAgreedCountNonStreaming:
    @pytest.mark.asyncio
    async def test_report_summary_stored_report_and_metrics(self):
        from core.agents.hallucination import check_hallucinations

        redis = _FakeRedis()
        with (
            patch(f"{_MOD}.extract_claims", new_callable=AsyncMock, return_value=(_MIXED, "heuristic")),
            patch(f"{_MOD}.verify_claim", side_effect=_verify_claim),
            patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 5),
            patch("config.ENABLE_VERIFIED_MEMORY_PROMOTION", False),
        ):
            report = await check_hallucinations(
                response_text="A response long enough to be checked.",
                conversation_id="cid-ns",
                chroma_client=None, neo4j_driver=None, redis_client=redis,
            )

        assert report["summary"]["agreed"] == 2
        assert report["summary"]["verified"] == 2
        _assert_buckets_sum_to_total(report["summary"])
        stored = _stored_report(redis, "cid-ns")
        assert stored["summary"]["agreed"] == 2
        _assert_buckets_sum_to_total(stored["summary"])
        assert _metrics(redis)["agreed"] == 2

    @pytest.mark.asyncio
    async def test_skipped_run_has_a_zero_agreed_count(self):
        from core.agents.hallucination import check_hallucinations

        report = await check_hallucinations(
            response_text="Hi.", conversation_id="cid-short",
            chroma_client=None, neo4j_driver=None, redis_client=_FakeRedis(),
        )

        assert report["skipped"] is True
        _assert_buckets_sum_to_total(report["summary"])


class TestAgreedCountStreaming:
    @pytest.mark.asyncio
    async def test_summary_stored_report_neo4j_counts_and_metrics(self):
        redis = _FakeRedis()
        saved: dict = {}
        events = await _stream(_MIXED, redis, save=lambda **kw: saved.update(kw))

        summary = next(e for e in events if e["type"] == "summary")
        assert summary["agreed"] == 2
        assert summary["verified"] == 2
        assert summary["unverified"] == 1
        assert summary["uncertain"] == 1
        _assert_buckets_sum_to_total(summary)

        stored = _stored_report(redis)
        assert stored["summary"]["agreed"] == 2
        _assert_buckets_sum_to_total(stored["summary"])
        assert saved["agreed"] == 2
        assert saved["verified"] + saved["agreed"] + saved["unverified"] + saved["uncertain"] == saved["total"]
        assert _metrics(redis)["agreed"] == 2

    @pytest.mark.asyncio
    async def test_skipped_claims_are_still_their_own_bucket(self):
        events = await _stream(["Agreed fact one.", "Skipped fact.", "Document fact."], _FakeRedis())

        summary = next(e for e in events if e["type"] == "summary")
        assert summary["agreed"] == 1
        assert summary["skipped"] == 1
        _assert_buckets_sum_to_total(summary)

    @pytest.mark.asyncio
    async def test_too_short_response_has_a_zero_agreed_count(self):
        from core.agents.hallucination import verify_response_streaming

        events = [
            ev async for ev in verify_response_streaming("Hi.", "cid-short", None, None, _FakeRedis())
        ]

        # "skipped" on this event is the flag for a run that checked nothing.
        assert events[-1]["type"] == "summary"
        assert events[-1]["skipped"] is True
        assert events[-1]["agreed"] == 0
        assert events[-1]["total"] == 0

    @pytest.mark.asyncio
    async def test_run_cut_short_by_the_deadline(self):
        """Claims settled before the deadline keep their bucket; the one the
        deadline cut off settles as uncertain."""
        import asyncio

        from core.agents.hallucination import verify_response_streaming

        async def _slow_for_one(claim_text, *a, **k):
            if claim_text == "Wrong fact.":
                await asyncio.sleep(5)
            return {"claim": claim_text, **_BY_CLAIM[claim_text]}

        redis = _FakeRedis()
        events = []
        with (
            _extraction(["Agreed fact one.", "Document fact.", "Wrong fact."]),
            patch(f"{_MOD}.verify_claim", side_effect=_slow_for_one),
            patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 5),
            patch("config.STREAMING_TOTAL_TIMEOUT", 0.3),
        ):
            async for ev in verify_response_streaming(
                "A response long enough to be checked.", "cid-src", None, None, redis,
            ):
                events.append(ev)

        summary = next(e for e in events if e["type"] == "summary")
        assert summary["interrupted"] is True
        assert summary["agreed"] == 1
        assert summary["verified"] == 1
        assert summary["uncertain"] == 1
        _assert_buckets_sum_to_total(summary)
        _assert_buckets_sum_to_total(_stored_report(redis)["summary"])

    @pytest.mark.asyncio
    async def test_retry_sweep_recount(self):
        """A claim that timed out and then settled as a second model's
        agreement moves from uncertain to agreed in the summary_update."""
        from core.agents.hallucination import verify_response_streaming

        async def _times_out_first(claim_text, *a, streaming=True, **k):
            if claim_text == "Agreed fact two." and streaming:
                return {
                    "claim": claim_text, "status": "uncertain", "similarity": 0.0,
                    "verification_method": "timeout",
                }
            return {"claim": claim_text, **_BY_CLAIM[claim_text]}

        redis = _FakeRedis()
        saved: dict = {}
        events = []
        with (
            _extraction(_MIXED),
            patch(f"{_MOD}.verify_claim", side_effect=_times_out_first),
            patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 5),
        ):
            async for ev in verify_response_streaming(
                "A response long enough to be checked.", "cid-src", None, None, redis,
                save_report_fn=lambda **kw: saved.update(kw),
            ):
                events.append(ev)

        summary = next(e for e in events if e["type"] == "summary")
        assert summary["agreed"] == 1
        assert summary["uncertain"] == 2
        _assert_buckets_sum_to_total(summary)

        update = next(e for e in events if e["type"] == "summary_update")
        assert update["agreed"] == 2
        assert update["uncertain"] == 1
        _assert_buckets_sum_to_total(update)

        assert _stored_report(redis)["summary"]["agreed"] == 2
        assert saved["agreed"] == 2
        assert _metrics(redis)["agreed"] == 2

    @pytest.mark.asyncio
    async def test_single_claim_retry_merge(self):
        """The merged report's summary is rebuilt from its claims, the agreed
        count with it."""
        from core.agents.hallucination import verify_response_streaming

        existing = {
            "conversation_id": "cid-src",
            "claims": [
                {"claim": "Document fact.", **_KB},
                {"claim": "Wrong fact.", **_REFUTED},
                {"claim": "Agreed fact one.", **_UNCERTAIN},
                {"claim": "Agreed fact two.", **_AGREED_COMPLEX},
            ],
            # Stored before the count existed.
            "summary": {"total": 4, "verified": 2, "unverified": 1, "uncertain": 1, "skipped": 0},
        }

        class _RedisWithReport(_FakeRedis):
            def get(self, _key):
                return json.dumps(existing)

        redis = _RedisWithReport()
        saved: dict = {}
        with (
            _extraction(["Agreed fact one."]),
            patch(f"{_MOD}.verify_claim", side_effect=_verify_claim),
            patch("config.HALLUCINATION_MIN_RESPONSE_LENGTH", 5),
        ):
            async for _ in verify_response_streaming(
                "A response long enough to be checked.", "cid-src", None, None, redis,
                save_report_fn=lambda **kw: saved.update(kw),
                merge_claim_index=2,
            ):
                pass

        stored = _stored_report(redis)
        assert len(stored["claims"]) == 4
        assert stored["summary"]["agreed"] == 2
        assert stored["summary"]["verified"] == 1
        assert stored["summary"]["uncertain"] == 0
        _assert_buckets_sum_to_total(stored["summary"])
        assert saved["agreed"] == 2
        assert saved["total"] == 4


class TestAgreedCountInNeo4j:
    @pytest.fixture(autouse=True)
    def _no_redis(self, monkeypatch):
        from unittest.mock import MagicMock

        monkeypatch.setattr("app.deps.get_redis", lambda: MagicMock(), raising=False)

    @staticmethod
    def _driver():
        from unittest.mock import MagicMock

        driver = MagicMock()
        session = MagicMock()
        driver.session.return_value.__enter__ = MagicMock(return_value=session)
        driver.session.return_value.__exit__ = MagicMock(return_value=False)
        return driver, session

    def test_saved_beside_the_other_counts(self):
        from app.db.neo4j.artifacts import save_verification_report

        driver, session = self._driver()
        save_verification_report(
            driver, conversation_id="c1", claims=[_AGREED, _KB], overall_score=0.9,
            verified=1, agreed=1, total=2,
        )

        call = session.run.call_args_list[0]
        assert "r.agreed = $agreed" in call.args[0]
        assert call.kwargs["agreed"] == 1

    def test_a_caller_that_sends_no_count_stores_none(self):
        """A null property is no property in Neo4j: the report reads as one
        with no agreed count, not as one with none agreed."""
        from app.db.neo4j.artifacts import save_verification_report

        driver, session = self._driver()
        save_verification_report(
            driver, conversation_id="c1", claims=[_AGREED], overall_score=0.9,
            verified=1, total=1,
        )

        assert session.run.call_args_list[0].kwargs["agreed"] is None

    def test_read_back(self):
        from app.db.neo4j.artifacts import get_verification_report

        driver, session = self._driver()
        row = {
            "id": "r1", "conversation_id": "c1", "claims": json.dumps([_AGREED, _KB]),
            "overall_score": 0.9, "verified": 1, "agreed": 1, "unverified": 0,
            "uncertain": 0, "total": 2, "created_at": "2026-09-29T00:00:00Z",
        }
        session.run.return_value.single.return_value = row

        assert get_verification_report(driver, "c1")["agreed"] == 1
        assert "r.agreed AS agreed" in session.run.call_args.args[0]

    def test_report_stored_before_the_count_reads_as_none(self):
        from app.db.neo4j.artifacts import get_verification_report

        driver, session = self._driver()
        session.run.return_value.single.return_value = {
            "id": "r1", "conversation_id": "c1", "claims": json.dumps([_AGREED]),
            "overall_score": 0.9, "verified": 1, "agreed": None, "unverified": 0,
            "uncertain": 0, "total": 1, "created_at": "2026-08-01T00:00:00Z",
        }

        assert get_verification_report(driver, "c1")["agreed"] is None


class TestAgreedCountThroughTheRouter:
    @pytest.fixture
    def client(self):
        from unittest.mock import MagicMock

        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from app.routers import agents

        app = FastAPI()
        app.include_router(agents.router)
        with (
            patch.object(agents, "get_chroma", return_value=MagicMock()),
            patch.object(agents, "get_neo4j", return_value=MagicMock()),
            patch.object(agents, "get_redis", return_value=MagicMock()),
        ):
            yield TestClient(app, raise_server_exceptions=False)

    def test_auto_persist_passes_the_count(self, client):
        from unittest.mock import MagicMock

        async def _check(**_kw):
            return {
                "conversation_id": "c1", "timestamp": "2026-09-29T00:00:00Z", "skipped": False,
                "claims": [_AGREED, _KB],
                "summary": {
                    "total": 2, "assessed": 2, "overall_confidence": 0.95,
                    "verified": 1, "agreed": 1, "unverified": 0, "uncertain": 0, "error": 0,
                },
            }

        save = MagicMock(return_value="r1")
        with (
            patch("core.agents.hallucination.check_hallucinations", new=_check),
            patch("app.db.neo4j.memory.create_memory_node", new=MagicMock()),
            patch("app.db.neo4j.artifacts.save_verification_report", new=save),
        ):
            res = client.post(
                "/agent/hallucination",
                json={"response_text": "x" * 300, "conversation_id": "c1"},
            )

        assert res.status_code == 200, res.text
        assert res.json()["summary"]["agreed"] == 1
        assert res.json()["persisted"] is True
        assert save.call_args.kwargs["agreed"] == 1
        assert save.call_args.kwargs["verified"] == 1

    def test_fast_mode_summary(self, client):
        async def _extract(text, user_query=None):
            return ["One claim.", "Another claim."], "heuristic"

        with patch(
            "core.agents.hallucination.extraction.extract_claims",
            new=AsyncMock(side_effect=_extract),
        ):
            res = client.post(
                "/agent/hallucination",
                json={"response_text": "x" * 300, "conversation_id": "c1", "mode": "fast"},
            )

        assert res.status_code == 200, res.text
        assert res.json()["summary"]["agreed"] == 0
        _assert_buckets_sum_to_total(res.json()["summary"])

    @pytest.mark.parametrize(
        ("sent", "stored"),
        [({"agreed": 1}, 1), ({"agreed": 0}, 0), ({}, None)],
    )
    def test_verification_save(self, client, sent, stored):
        from unittest.mock import MagicMock

        save = MagicMock(return_value="r1")
        with patch("app.db.neo4j.artifacts.save_verification_report", new=save):
            res = client.post(
                "/verification/save",
                json={
                    "conversation_id": "c1", "claims": [_AGREED, _KB], "overall_score": 0.9,
                    "verified": 1, "total": 2, **sent,
                },
            )

        assert res.status_code == 200, res.text
        assert save.call_args.kwargs["agreed"] == stored
        assert save.call_args.kwargs["verified"] == 1
