# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tier 2: RAG retrieval quality + RAGAS LLM-judge generation quality.

Retrieval floors are derived offline from the seed alone (``seed_reference.py``,
pinned by ``test_retrieval_scoring.py``). Each query is seeded with the one
document that answers it and three same-topic documents that do not, so a
ranker that finds the topic but cannot discriminate inside it scores below 1.0.
Mean NDCG@5 / MRR over the ten cases:

    perfect ranking (answer first)                  1.000 / 1.000
    random permutation of all 40 seeded docs        0.075 / 0.107
    random order inside the answer's own cluster    0.642 / 0.523
    lexical term-overlap ranking over all 40 docs   0.963 / 0.950

The floors (NDCG@5 0.80, MRR 0.70) sit between the in-cluster random and the
lexical ranker: a retriever must beat "found the topic, guessed the document"
and need not beat plain term overlap. Until 2026-10-05 the seed had no
distractors and the floors were 0.05; both metrics read 1.000 on every run and
gated nothing. These are floors — ratchet up as the retriever improves, never
down to make a run pass.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

import httpx
import pytest
from conftest import (
    cleanup_artifact,
    generate_chat_answer,
    load_jsonl,
    seed_content,
    wait_for_indexed,
)
from metrics import retrieval_summary

SEED_CASES = load_jsonl("benchmark_seed.jsonl")

# Offline reference numbers (see the module docstring); pinned by test_retrieval_scoring.py.
REFERENCE = {
    "perfect": {"ndcg": 1.0, "mrr": 1.0},
    "random_pool": {"ndcg": 0.075, "mrr": 0.107},
    "random_cluster": {"ndcg": 0.642, "mrr": 0.523},
    "lexical": {"ndcg": 0.963, "mrr": 0.950},
}
NDCG_5_THRESHOLD = 0.80
MRR_THRESHOLD = 0.70
# D28-A (2026-10-06, live mean 0.52/0.53, min 0.20): mean floor raised from 0.15; revisit after the grounding fix.
FAITHFULNESS_THRESHOLD = 0.4
# D28-A: any one scored answer below this fails the gate (the 0.20 answer used 2023 401(k) figures over the KB's 2025).
FAITHFULNESS_ANSWER_THRESHOLD = 0.3
RELEVANCY_THRESHOLD = 0.6


def assert_retrieval_floors(summary: dict) -> None:
    """The gate: mean NDCG@5 and MRR at or above the floors."""
    assert summary["ndcg"]["mean"] >= NDCG_5_THRESHOLD, (
        f"avg NDCG@5 {summary['ndcg']['mean']:.3f} < {NDCG_5_THRESHOLD}"
    )
    assert summary["mrr"]["mean"] >= MRR_THRESHOLD, (
        f"avg MRR {summary['mrr']['mean']:.3f} < {MRR_THRESHOLD}"
    )


@pytest.fixture(scope="module")
async def seeded_benchmark(aclient: httpx.AsyncClient) -> list[dict]:
    """Seed every case's answer and its distractors; return entries with artifact ids."""
    seeded: list[dict] = []
    seeded_count = 0

    async def _seed(content: str, domain: str) -> str:
        nonlocal seeded_count
        if seeded_count:
            await asyncio.sleep(1)  # Avoid rate limiting on rapid ingests
        seeded_count += 1
        aid = await seed_content(aclient, content, domain)
        await wait_for_indexed(aclient, aid, timeout=15)
        return aid

    for case in SEED_CASES:
        answer_id = await _seed(case["answer"], case["domain"])
        distractor_ids = [await _seed(d, case["domain"]) for d in case["distractors"]]
        seeded.append({
            **case,
            "relevant_ids": [answer_id],
            "distractor_ids": distractor_ids,
            "artifact_ids": [answer_id, *distractor_ids],
        })

    # Extra wait for vector embeddings to settle
    await asyncio.sleep(5)

    yield seeded

    # Cleanup
    for entry in seeded:
        for aid in entry["artifact_ids"]:
            await cleanup_artifact(aclient, aid)


async def _query_full_rag(
    aclient: httpx.AsyncClient, payload: dict, attempts: int = 3
) -> dict:
    """POST /agent/query, retrying budget-degraded envelopes.

    Under CPU load the query agent hits its wall-clock budget and returns an
    HTTP-200 *degraded* envelope (``budget_exceeded: true``, kb/memory
    ``timeout``, external-fallback-only sources with empty artifact_ids).
    Scoring those as rankings produced the 2026-07-08 flat NDCG@5=0.000 —
    a measurement artifact, not retrieval quality. Retry, then fail LOUDLY:
    an unmeasurable metric must never score as zero.
    """
    data: dict = {}
    for attempt in range(attempts):
        if attempt:
            await asyncio.sleep(5 * attempt)
        # The benchmark measures RANKING quality, not latency (benchmark-slo
        # owns latency) — opt into a patient budget so ambient load on the
        # shared local-inference backend can't make the metric unmeasurable.
        # The client read timeout must exceed the server budget, else the
        # transport gives up on queries the server would have completed.
        resp = await aclient.post(
            "/agent/query",
            json={**payload, "budget_seconds": 60},
            timeout=90.0,
        )
        assert resp.status_code == 200, f"Query failed: {resp.text}"
        data = resp.json()
        kb_status = (data.get("source_status") or {}).get("kb", "ok")
        if not data.get("budget_exceeded") and kb_status == "ok":
            return data
    pytest.fail(
        f"KB retrieval degraded on all {attempts} attempts for "
        f"{payload['query']!r} (budget_exceeded="
        f"{data.get('budget_exceeded')}, source_status="
        f"{data.get('source_status')}) — retrieval quality is UNMEASURABLE "
        "under this load; re-run the eval tier when the stack is quiet."
    )


@pytest.mark.asyncio
async def test_retrieval_metrics(aclient: httpx.AsyncClient, seeded_benchmark: list[dict]) -> None:
    """Phase B: Retrieval quality — NDCG@5 and MRR across the seeded benchmark.

    Each query must rank its answering document above the same-topic
    distractors seeded beside it; ``test_retrieval_scoring.py`` proves offline
    that this gate goes red when it does not.
    """
    rankings: list[tuple[list[str], set[str]]] = []
    distractors_above: list[int] = []
    for q in seeded_benchmark:
        data = await _query_full_rag(aclient, {"query": q["query"], "top_k": 20})
        # /agent/query returns both "sources" and "results" — use sources for artifact IDs
        sources = data.get("sources", data.get("results", []))
        ranked_ids = [r["artifact_id"] for r in sources]
        relevant = set(q["relevant_ids"])
        rankings.append((ranked_ids, relevant))
        answer_pos = next((i for i, rid in enumerate(ranked_ids) if rid in relevant), len(ranked_ids))
        distractors_above.append(sum(1 for rid in ranked_ids[:answer_pos] if rid in q["distractor_ids"]))

    summary = retrieval_summary(rankings, k=5)
    for metric in ("ndcg", "mrr"):
        d = summary[metric]
        print(
            f"\n  Retrieval {metric}: mean={d['mean']:.3f} min={d['min']:.3f} max={d['max']:.3f} "
            f"spread={d['spread']:.3f} non_discriminating={d['non_discriminating']}"
        )
    for q, row, above in zip(seeded_benchmark, summary["per_query"], distractors_above):
        print(
            f"    {q['query'][:50]:50s}  NDCG@5={row['ndcg']:.3f}  MRR={row['mrr']:.3f}  "
            f"answer_rank={row['answer_rank']}  distractors_above={above}"
        )

    assert_retrieval_floors(summary)


REPORTS_DIR = Path(__file__).parent / "reports"
PER_ANSWER_REPORT = REPORTS_DIR / "ragas-per-answer.json"
WORST_N = 5


@pytest.mark.asyncio
async def test_ragas_quality(aclient: httpx.AsyncClient, seeded_benchmark: list[dict]) -> None:
    """Phase C: RAGAS LLM-judge — faithfulness + answer relevancy.

    Every judgement is recorded per answer as ``(score, reasoning,
    parse_failed, abstained)``; the worst five land in
    ``reports/ragas-per-answer.json`` and on stdout so a low mean can be read.
    Parse failures and abstentions are excluded from the mean — the first is
    the instrument breaking, the second is the grounding rules working — and
    a run in which the judge scored nothing fails as UNMEASURABLE rather than
    as a quality miss. Otherwise faithfulness fails on a mean below
    ``FAITHFULNESS_THRESHOLD`` or on any one scored answer below
    ``FAITHFULNESS_ANSWER_THRESHOLD`` (``assert_faithfulness_floors``, D28-A).
    """
    records: list[dict] = []
    faithfulness: list[JudgeResult] = []
    relevancy: list[JudgeResult] = []

    # Test first 5 queries for cost control
    for q in seeded_benchmark[:5]:
        # Get RAG context (budget-degraded envelopes retried — see _query_full_rag)
        rag_data = await _query_full_rag(aclient, {"query": q["query"], "top_k": 5})
        contexts = [r["content"] for r in rag_data.get("sources", rag_data.get("results", []))]

        # Generate answer via chat
        answer = await generate_chat_answer(aclient, q["query"])
        assert answer, f"Empty answer for: {q['query']}"

        if is_abstention(answer):
            faith = JudgeResult(0.0, "abstained: the answer reports no grounding", abstained=True)
        else:
            faith = await _judge_metric(aclient, "faithfulness", answer=answer, contexts=contexts)
        rel = await _judge_metric(aclient, "relevancy", question=q["query"], answer=answer)
        faithfulness.append(faith)
        relevancy.append(rel)
        for metric, result in (("faithfulness", faith), ("relevancy", rel)):
            records.append({"query": q["query"], "metric": metric, "answer": answer[:300], **asdict(result)})

    faith_summary = judge_mean(faithfulness)
    rel_summary = judge_mean(relevancy)
    worst = worst_n([r for r in records if r["metric"] == "faithfulness"], WORST_N)
    REPORTS_DIR.mkdir(exist_ok=True)
    PER_ANSWER_REPORT.write_text(json.dumps(
        {"faithfulness": faith_summary, "relevancy": rel_summary, "worst_faithfulness": worst, "records": records},
        indent=2,
    ))
    print(f"\n  RAGAS faithfulness: {faith_summary}")
    print(f"  RAGAS relevancy:    {rel_summary}")
    print(f"  Worst {WORST_N} faithfulness answers (full report: {PER_ANSWER_REPORT}):")
    for r in worst:
        print(f"    {format_judgement(r)}")

    # Instrument before quality: a judge that answered nothing measured nothing.
    for name, summary in (("faithfulness", faith_summary), ("relevancy", rel_summary)):
        if summary["n_parse_failed"] * 2 > summary["n"]:
            pytest.fail(
                f"{name} UNMEASURABLE: judge output unparseable for "
                f"{summary['n_parse_failed']}/{summary['n']} answers — see {PER_ANSWER_REPORT}"
            )
        if summary["mean"] is None:
            pytest.fail(f"{name} UNMEASURABLE: no scored answers ({summary})")

    assert_faithfulness_floors(faith_summary, [r for r in records if r["metric"] == "faithfulness"])
    assert rel_summary["mean"] >= RELEVANCY_THRESHOLD, (
        f"avg relevancy {rel_summary['mean']:.3f} < {RELEVANCY_THRESHOLD}"
    )


# Judge model isolation: use a different model family than the answer generator.
# Smart routing (model="auto") may pick the same family for both — defeating
# the purpose of an independent judge. We pin the judge to a specific model
# from a different family. The answer generator uses "auto" (typically GPT/Gemini),
# so we pin the judge to Anthropic (Claude Sonnet 4.6).
JUDGE_MODEL = "anthropic/claude-sonnet-4.6"


@dataclass(frozen=True)
class JudgeResult:
    """Mirrors ``app/eval/ragas_metrics.MetricResult`` (that package is not
    importable from the slim eval container). ``parse_failed`` means the
    instrument broke; ``abstained`` means the answer declined for lack of
    grounding. Both carry ``score=0.0`` and neither belongs in a mean."""

    score: float
    reasoning: str
    parse_failed: bool = False
    abstained: bool = False


_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)
_SCORE_KEY_RE = re.compile(r'"score"\s*:\s*(-?\d+(?:\.\d+)?)')
# Copied from core/agents/summary_quality.ABSENCE_PATTERNS — one vocabulary,
# not importable here. Every sentence must match for an answer to count as an
# abstention; a fact followed by a stated limit is scored normally.
_ABSENCE_RE = re.compile("|".join((
    r"i\s+don'?t\s+know",
    r"i\s+don'?t\s+have\s+any\s+sources",
    r"there\s+(?:is|are)\s+no\s+(?:\w+\s+){0,2}?information",
    r"no\s+information\s+(?:about|on|regarding|is\s+available)",
    r"(?:is|are)\s+not\s+mentioned\s+in\s+the\s+(?:provided|supplied|available)",
    r"(?:memories|memory|context|excerpts?|sources?)(?:\s+\w+){0,2}?\s+"
    r"(?:do|does)\s+not\s+(?:contain|include|mention|provide|record)",
    r"not\s+(?:mentioned|found|present|recorded|described)\s+in\s+the\s+"
    r"(?:provided\s+|supplied\s+)?(?:memories|context|excerpts?)",
    r"(?:is|are)\s+not\s+a\s+named\s+entity",
    r"no\s+relevant\s+information",
)), re.I)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def is_abstention(answer: str) -> bool:
    """True when every sentence of the answer reports absence of grounding."""
    sentences = [x.strip() for x in _SENTENCE_SPLIT_RE.split((answer or "").strip()) if x.strip()]
    return bool(sentences) and all(_ABSENCE_RE.search(x) for x in sentences)


def parse_judge(raw: str) -> JudgeResult:
    """JSON (fence-stripped) → explicit ``"score":`` key → ``parse_failed``.

    Deliberately no "first number in the string" fallback: that is how
    ``{"reasoning": "only [1] of 4 relevant", "score": 0.25}`` inside a fence
    once became 1.0 — a parse bug turned into a green gate. Failing to 0.0 with
    the flag set is loud and excluded from the mean.
    """
    text = _FENCE_RE.sub("", raw or "").strip()
    try:
        data = json.loads(text)
        score = max(0.0, min(1.0, float(data.get("score", 0.0))))
        return JudgeResult(score, str(data.get("reasoning", "")))
    except (json.JSONDecodeError, ValueError, TypeError, AttributeError):
        pass
    match = _SCORE_KEY_RE.search(text)
    if match:
        return JudgeResult(max(0.0, min(1.0, float(match.group(1)))), text[:200])
    return JudgeResult(0.0, f"Failed to parse: {text[:200]}", parse_failed=True)


def judge_mean(results: list[JudgeResult]) -> dict:
    """Mean over the scored results only, with the excluded counts beside it.
    ``mean`` is ``None`` — never 0.0 — when nothing was scored."""
    scored = [r.score for r in results if not (r.parse_failed or r.abstained)]
    summary = {
        "mean": sum(scored) / len(scored) if scored else None,
        "n": len(results),
        "n_scored": len(scored),
        "n_parse_failed": sum(r.parse_failed for r in results),
        "n_abstained": sum(r.abstained for r in results),
    }
    if scored:
        summary.update(
            min=min(scored), max=max(scored), spread=max(scored) - min(scored),
            non_discriminating=len(scored) > 1 and min(scored) == max(scored),
        )
    return summary


def worst_n(records: list[dict], n: int) -> list[dict]:
    """Lowest-scoring scored records first; failures and abstentions after them
    so they stay visible without masquerading as the worst scores."""
    scored = sorted((r for r in records if not (r["parse_failed"] or r["abstained"])), key=lambda r: r["score"])
    excluded = [r for r in records if r["parse_failed"] or r["abstained"]]
    return (scored + excluded)[:n]


def format_judgement(r: dict) -> str:
    """One per-answer line: score (or why it was not scored), query, judge's reasoning."""
    flag = "PARSE_FAILED" if r["parse_failed"] else "ABSTAINED" if r["abstained"] else f"{r['score']:.2f}"
    return f"[{flag}] {r['query'][:50]:50s} {r['reasoning'][:120]}"


def assert_faithfulness_floors(summary: dict, records: list[dict]) -> None:
    """The gate: the mean at or above FAITHFULNESS_THRESHOLD and no scored answer
    below FAITHFULNESS_ANSWER_THRESHOLD. ``summary`` is ``judge_mean`` over
    ``records`` and must have a mean (the UNMEASURABLE checks run first). Parse
    failures and abstentions are not scores, so neither trips the answer floor."""
    failures = []
    if summary["mean"] < FAITHFULNESS_THRESHOLD:
        failures.append(
            f"avg faithfulness {summary['mean']:.3f} < {FAITHFULNESS_THRESHOLD} "
            f"(n_scored={summary['n_scored']}, abstained={summary['n_abstained']}); worst:\n"
            + "\n".join(f"  {format_judgement(r)}" for r in worst_n(records, WORST_N))
        )
    below = [
        r for r in worst_n(records, len(records))
        if not (r["parse_failed"] or r["abstained"]) and r["score"] < FAITHFULNESS_ANSWER_THRESHOLD
    ]
    if below:
        failures.append(
            f"{len(below)} answer(s) below the per-answer floor {FAITHFULNESS_ANSWER_THRESHOLD}:\n"
            + "\n".join(f"  {format_judgement(r)}" for r in below)
        )
    assert not failures, "\n".join(failures)


async def _judge_metric(
    client: httpx.AsyncClient,
    metric: str,
    *,
    answer: str = "",
    question: str = "",
    contexts: list[str] | None = None,
) -> JudgeResult:
    """Use chat proxy as LLM-as-judge with model isolation.

    Gap 4 fix: The judge uses a pinned model (JUDGE_MODEL) from a different
    provider family than the answer generator to avoid self-confirmation bias.
    """
    shape = '{"reasoning": "<one or two sentences>", "score": <float>}'
    if metric == "faithfulness":
        ctx_text = "\n---\n".join(contexts or [])
        prompt = (
            f"You are an evaluation judge. Rate how faithful this answer is to the "
            f"provided context. Score 0.0 (completely unfaithful) to 1.0 (perfectly faithful).\n\n"
            f"Context:\n{ctx_text}\n\nAnswer:\n{answer}\n\n"
            f"Do not answer the question yourself. Respond with ONLY a JSON object: {shape}"
        )
    else:  # relevancy
        prompt = (
            f"You are an evaluation judge. Rate how relevant this answer is to the "
            f"question. Score 0.0 (completely irrelevant) to 1.0 (perfectly relevant).\n\n"
            f"Question:\n{question}\n\nAnswer:\n{answer}\n\n"
            f"Do not answer the question yourself. Respond with ONLY a JSON object: {shape}"
        )

    # Use pinned judge model — not "auto" — to guarantee model family isolation
    response = await generate_chat_answer(client, prompt, model=JUDGE_MODEL)
    return parse_judge(response)
