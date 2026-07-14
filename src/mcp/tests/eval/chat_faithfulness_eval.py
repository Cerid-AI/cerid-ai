# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Chat-path faithfulness harness (Quality-Maximization Phase 0.3).

The real chat surface assembles RAG context CLIENT-SIDE (``use-chat-send.ts``)
and streams through ``/chat/stream`` — a path with **no faithfulness eval at
all** today (the RAGAS gate only scores pre-baked golden contexts). This
harness measures it end to end:

  live ``/query`` (top_k=5)  ->  FE-parity prompt assembly  ->  ``/chat/stream``
  ->  LLM-judge faithfulness of the streamed answer vs the provided contexts.

FE parity
---------
The context block is reconstructed to match ``src/web/src/hooks/use-chat-send.ts``
+ ``src/web/src/lib/kb-utils.ts``: the verbatim ``RAG_SYSTEM_PREAMBLE`` (kept in
sync below), Jaccard chunk dedup, and ``<document …>`` blocks ordered by
descending relevance. ``/query`` only returns a 200-char content *preview* per
source, so for the controlled ``eval-fixture-*`` docs the harness substitutes
the full local fixture text — higher fidelity than the preview and exactly what
was ingested. Non-fixture (operator-KB) hits fall back to the preview.

Judge
-----
Faithfulness is scored with ``app.eval.ragas_metrics.faithfulness_llm`` — the
LLM-as-judge sibling of the NLI ``faithfulness`` that ``ragas_eval.py`` uses,
chosen here for host-side determinism and to avoid a local NLI-model download.
If that import is unavailable, a minimal inline judge (``call_llm``, strict
rubric returning ``{faithful_claims,total_claims,score}``) is used instead.

Report-only by default. Set ``CHAT_FAITHFULNESS_MIN`` to gate. Cost-bounded:
default cheap model, and ``--max-items`` caps live calls.

Run::

    cd src/mcp && ../../.venv/bin/python -m tests.eval.chat_faithfulness_eval --max-items 3
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Any

import httpx
import pytest

from core.utils.swallowed import log_swallowed_error
from tests.eval import _live_eval_common as common

# --- tunables (named; no bare literals in comparisons) ---
CHAT_TOP_K = 5
JACCARD_DEDUP_THRESHOLD = 0.7
DEFAULT_CHAT_MODEL = "openai/gpt-4o-mini"
DEFAULT_JUDGE_MODEL = "openai/gpt-4o-mini"
CHAT_TEMPERATURE = 0.0
CHAT_STREAM_TIMEOUT_S = 120.0
ANSWER_PREVIEW_CHARS = 240
_ROUND = 4
_GATE_ENV = "CHAT_FAITHFULNESS_MIN"
_MODULE = "tests.eval.chat_faithfulness_eval"
_RESULTS_PATH = common.OUT_DIR / "chat_faithfulness_results.json"

# Verbatim copy of src/web/src/lib/rag-prompt.ts::RAG_SYSTEM_PREAMBLE.
# KEEP IN SYNC WITH rag-prompt.ts — the FE injects this exact string ahead of
# the retrieved <document> blocks; drift here silently makes the eval measure a
# prompt the product never sends.
RAG_SYSTEM_PREAMBLE = (
    "The user has a personal knowledge base. Below are documents retrieved for "
    "this conversation; each is tagged with its source. Rules: (1) When the "
    "documents answer the question, ground your answer in them and cite "
    "specifics. (2) Distinguish clearly between facts from these documents and "
    "your general knowledge. (3) For time-sensitive values (prices, versions, "
    "schedules), treat the documents as point-in-time records — qualify with "
    "the document's date if shown, otherwise present the value as a recorded "
    "(possibly outdated) value, never as current; suggest checking a live "
    "source when currency matters. When documents conflict about the same fact, "
    "trust the most recently dated one. (4) If the documents don't cover the "
    "question, say so plainly, then answer from general knowledge if you can, "
    "labeled as such. A clear \"your knowledge base doesn't cover this\" is "
    "better than a guess. (5) For analytical questions — counting or combining "
    "facts across documents, date arithmetic (how long between events, which "
    "came first), or applying the user's stated preferences — reason step by "
    "step across the documents and DERIVE the answer; don't refuse just because "
    "no single document states it outright. Only say you can't answer when the "
    "underlying facts are genuinely absent."
)


def load_dataset() -> dict[str, Any]:
    path = common.DATASETS_DIR / "chat_faithfulness_queries.json"
    with path.open() as f:
        data: dict[str, Any] = json.load(f)
    return data


def _retrieval_corpus() -> list[dict[str, str]]:
    """The 0.3 harness reuses the 0.1 fixture corpus + seed manifest."""
    path = common.DATASETS_DIR / "retrieval_golden_queries.json"
    with path.open() as f:
        return json.load(f)["corpus"]


# ---------------------------------------------------------------------------
# FE-parity context assembly (mirror of kb-utils.ts + use-chat-send.ts)
# ---------------------------------------------------------------------------
def _jaccard(a: str, b: str) -> float:
    wa, wb = set(a.lower().split()), set(b.lower().split())
    if not wa or not wb:
        return 0.0
    inter = len(wa & wb)
    union = len(wa) + len(wb) - inter
    return inter / union if union else 0.0


def _dedup_chunks(
    docs: list[tuple[dict[str, Any], str]],
) -> list[tuple[dict[str, Any], str]]:
    """Mirror of kb-utils.ts::deduplicateChunks (keep first / higher relevance).

    Each doc is ``(source, content)``; overlap is measured on ``content``.
    """
    kept: list[tuple[dict[str, Any], str]] = []
    for source, content in docs:
        if any(
            _jaccard(kept_content, content) >= JACCARD_DEDUP_THRESHOLD
            for _, kept_content in kept
        ):
            continue
        kept.append((source, content))
    return kept


def _extract_date(value: str | None) -> str | None:
    """Mirror of kb-utils.ts::extractDate — YYYY-MM-DD prefix of an ISO string."""
    if not value or len(value) < len("YYYY-MM-DD"):
        return None
    head = value[:10]
    parts = head.split("-")
    ymd_parts = 3
    if len(parts) == ymd_parts and parts[0].isdigit() and parts[1].isdigit() and parts[2].isdigit():
        return head
    return None


def _format_document(source: dict[str, Any], content: str) -> str:
    """Mirror of kb-utils.ts::formatChunkWithHeader — attribute order matters."""
    attrs: list[str] = []
    if source.get("artifact_id"):
        attrs.append(f'id="{source["artifact_id"]}"')
    if source.get("domain"):
        attrs.append(f'domain="{source["domain"]}"')
    if source.get("sub_category"):
        attrs.append(f'category="{source["sub_category"]}"')
    if source.get("filename"):
        attrs.append(f'source="{source["filename"]}"')
    if source.get("chunk_index") is not None:
        attrs.append(f'chunk="{source["chunk_index"]}"')
    if source.get("relevance") is not None:
        attrs.append(f'relevance="{source["relevance"]:.2f}"')
    if source.get("source_type"):
        attrs.append(f'type="{source["source_type"]}"')
    date_str = _extract_date(source.get("created_at"))
    if date_str:
        attrs.append(f'date="{date_str}"')
    attr_str = (" " + " ".join(attrs)) if attrs else ""
    return f"<document{attr_str}>\n{content}\n</document>"


def _resolve_content(source: dict[str, Any]) -> str:
    """Full fixture text for eval-fixture docs (``/query`` returns only a
    200-char preview); the preview for any operator-KB hit."""
    filename = source.get("filename", "")
    if filename.startswith(common.FIXTURE_PREFIX):
        try:
            return common.read_fixture(filename)
        except OSError as exc:
            log_swallowed_error(_MODULE, exc)
    return source.get("content", "")


def assemble_context(
    sources: list[dict[str, Any]],
) -> tuple[str, list[str]]:
    """Return (context_block, contexts). ``context_block`` is the FE-parity
    ``<document>`` join; ``contexts`` is the parallel list of full doc texts the
    judge scores the answer against."""
    docs: list[tuple[dict[str, Any], str]] = [
        (s, _resolve_content(s)) for s in sources
    ]
    docs = [(s, c) for s, c in docs if c.strip()]
    docs = _dedup_chunks(docs)[:CHAT_TOP_K]
    block = "\n\n".join(_format_document(s, c) for s, c in docs)
    return block, [c for _, c in docs]


# ---------------------------------------------------------------------------
# /chat/stream
# ---------------------------------------------------------------------------
def stream_chat(
    client: httpx.Client, model: str, messages: list[dict[str, str]]
) -> tuple[str, str | None]:
    """POST /chat/stream and accumulate the streamed answer.

    Returns ``(answer, error)``. ``error`` is non-None when the stream carried
    an error event or the endpoint returned a non-200 status.
    """
    payload = {
        "model": model,
        "messages": messages,
        "temperature": CHAT_TEMPERATURE,
        "stream": True,
    }
    parts: list[str] = []
    error: str | None = None
    with client.stream(
        "POST", "/chat/stream", json=payload, timeout=CHAT_STREAM_TIMEOUT_S
    ) as resp:
        if resp.status_code != httpx.codes.OK:
            body = resp.read().decode(errors="replace")[:200]
            return "", f"http_{resp.status_code}: {body}"
        for line in resp.iter_lines():
            if not line or not line.startswith("data:"):
                continue
            data = line[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                obj = json.loads(data)
            except json.JSONDecodeError:
                continue
            if "cerid_meta" in obj or "cerid_meta_update" in obj:
                continue
            if obj.get("error"):
                error = str(obj["error"].get("message", obj["error"]))[:200]
                continue
            choices = obj.get("choices")
            if choices:
                piece = choices[0].get("delta", {}).get("content")
                if piece:
                    parts.append(piece)
    return "".join(parts), error


# ---------------------------------------------------------------------------
# Faithfulness judge
# ---------------------------------------------------------------------------
_MINIMAL_JUDGE_SYSTEM = (
    "You are a strict faithfulness judge for a RAG system. Given CONTEXTS and "
    "an ANSWER, decompose the ANSWER into atomic factual claims and decide how "
    "many are directly supported by the CONTEXTS. A claim that the contexts do "
    "not cover is NOT faithful. An honest refusal (\"the documents don't cover "
    "this\") contains no factual claims and scores 1.0. Return ONLY JSON: "
    '{"faithful_claims": int, "total_claims": int, "score": float} where score '
    "= faithful_claims/total_claims (1.0 when total_claims is 0)."
)


async def _minimal_faithfulness_judge(
    answer: str, contexts: list[str], model: str
) -> tuple[float, str]:
    """Inline LLM judge (fallback when ragas_metrics is unavailable).

    Uses ``core.utils.llm_client.call_llm`` — whose observability breadcrumb is
    ``breaker_name`` (``call_internal_llm`` is the one that takes ``stage=``).
    """
    from core.utils.llm_client import call_llm

    ctx_block = "\n---\n".join(contexts) if contexts else "(no contexts retrieved)"
    messages = [
        {"role": "system", "content": _MINIMAL_JUDGE_SYSTEM},
        {"role": "user", "content": f"CONTEXTS:\n{ctx_block}\n\nANSWER:\n{answer}"},
    ]
    raw = await call_llm(
        messages,
        model=model,
        temperature=CHAT_TEMPERATURE,
        max_tokens=500,
        response_format={"type": "json_object"},
        breaker_name="chat_faithfulness_eval",
    )
    try:
        data = json.loads(raw)
        score = max(0.0, min(1.0, float(data.get("score", 0.0))))
        reason = (
            f"{data.get('faithful_claims')}/{data.get('total_claims')} claims "
            "faithful (inline judge)"
        )
        return score, reason
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        log_swallowed_error(_MODULE, exc)
        return 0.0, f"unparseable judge output: {raw[:120]}"


async def judge_faithfulness(
    answer: str, contexts: list[str], model: str
) -> tuple[float, str]:
    """Score answer-vs-contexts. Prefers the importable ragas judge utility."""
    try:
        from app.eval.ragas_metrics import faithfulness_llm
    except ImportError as exc:
        log_swallowed_error(_MODULE, exc)
        return await _minimal_faithfulness_judge(answer, contexts, model)
    result = await faithfulness_llm(answer, contexts, model=model)
    return round(result.score, _ROUND), result.reasoning


# ---------------------------------------------------------------------------
# Eval driver
# ---------------------------------------------------------------------------
def _mean(values: list[float]) -> float | None:
    return round(sum(values) / len(values), _ROUND) if values else None


def run_eval(
    client: httpx.Client,
    dataset: dict[str, Any],
    *,
    chat_model: str,
    judge_model: str,
    max_items: int | None,
) -> dict[str, Any]:
    corpus = _retrieval_corpus()
    common.seed_corpus(client, corpus)
    common.wait_until_retrievable(client, corpus)

    items: list[dict[str, Any]] = dataset["items"]
    if max_items is not None:
        items = items[:max_items]

    per_item: list[dict[str, Any]] = []
    for item in items:
        sources = common.query_ranked(
            client, item["query"], item["search_domains"], top_k=CHAT_TOP_K
        )
        context_block, contexts = assemble_context(sources)
        messages: list[dict[str, str]] = []
        if context_block:
            messages.append(
                {"role": "system", "content": f"{RAG_SYSTEM_PREAMBLE}\n\n{context_block}"}
            )
        messages.append({"role": "user", "content": item["query"]})

        answer, chat_error = stream_chat(client, chat_model, messages)
        if chat_error and not answer:
            per_item.append({
                "id": item["id"], "kind": item["kind"], "query": item["query"],
                "n_contexts": len(contexts), "faithfulness": None,
                "error": chat_error, "answer_preview": "", "reasoning": "",
            })
            continue

        score, reasoning = asyncio.run(
            judge_faithfulness(answer, contexts, judge_model)
        )
        per_item.append({
            "id": item["id"], "kind": item["kind"], "query": item["query"],
            "n_contexts": len(contexts), "faithfulness": score,
            "answer_preview": answer[:ANSWER_PREVIEW_CHARS], "reasoning": reasoning,
            "error": chat_error,
        })

    scored = [r["faithfulness"] for r in per_item if r["faithfulness"] is not None]
    by_kind: dict[str, float | None] = {}
    for kind in sorted({r["kind"] for r in per_item}):
        by_kind[kind] = _mean(
            [r["faithfulness"] for r in per_item
             if r["kind"] == kind and r["faithfulness"] is not None]
        )

    overall = _mean(scored)
    floor = None
    gate_passed: bool | None = None
    floor_env = os.getenv(_GATE_ENV)
    if floor_env is not None and overall is not None:
        floor = floor_env
        gate_passed = overall >= float(floor_env)

    summary: dict[str, Any] = {
        "harness": "chat_faithfulness_eval",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "mcp_base": common.mcp_base(),
        "chat_model": chat_model,
        "judge_model": judge_model,
        "n_items": len(per_item),
        "overall_faithfulness": overall,
        "by_kind": by_kind,
        "gate": {"min": floor, "passed": gate_passed},
        "per_item": per_item,
    }
    common.OUT_DIR.mkdir(parents=True, exist_ok=True)
    with _RESULTS_PATH.open("w") as f:
        json.dump(summary, f, indent=2)
    return summary


def _print_summary(summary: dict[str, Any]) -> None:
    print(
        f"\nChat-path faithfulness — {summary['n_items']} items "
        f"(chat={summary['chat_model']}, judge={summary['judge_model']}) "
        f"@ {summary['mcp_base']}",
        file=sys.stderr,
    )
    print("=" * 72, file=sys.stderr)
    print(f"OVERALL faithfulness = {summary['overall_faithfulness']}", file=sys.stderr)
    for kind, val in summary["by_kind"].items():
        print(f"  {kind:10} faithfulness = {val}", file=sys.stderr)
    for row in summary["per_item"]:
        flag = "" if not row.get("error") else f"  [err: {row['error']}]"
        print(
            f"  {row['id']:11} kind={row['kind']:8} ctx={row['n_contexts']} "
            f"faithful={row['faithfulness']}{flag}",
            file=sys.stderr,
        )
    gate = summary["gate"]
    if gate["min"] is not None:
        verdict = "PASS" if gate["passed"] else "FAIL"
        print(f"\nGATE {_GATE_ENV}={gate['min']} -> {verdict}", file=sys.stderr)
    print("=" * 72, file=sys.stderr)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Chat-path faithfulness eval")
    parser.add_argument("--cleanup", action="store_true",
                        help="Delete seeded fixtures and exit (no evaluation).")
    parser.add_argument("--keep", action="store_true",
                        help="Leave fixtures seeded after the run.")
    parser.add_argument("--max-items", type=int, default=None,
                        help="Cap live items (cost discipline; e.g. 3 to prove plumbing).")
    parser.add_argument("--chat-model", default=DEFAULT_CHAT_MODEL)
    parser.add_argument("--judge-model", default=DEFAULT_JUDGE_MODEL)
    args = parser.parse_args(argv)

    dataset = load_dataset()
    client = common.make_client()
    try:
        if not common.health_ok(client):
            print(f"Stack not reachable at {common.mcp_base()}", file=sys.stderr)
            return 2
        if args.cleanup:
            n = common.cleanup_by_content(client, _retrieval_corpus())
            print(f"Cleaned up {n} fixture artifacts.", file=sys.stderr)
            return 0
        if not common.ensure_openrouter_key():
            print("OPENROUTER_API_KEY not available — judge cannot run.", file=sys.stderr)
            return 2

        summary = run_eval(
            client, dataset, chat_model=args.chat_model,
            judge_model=args.judge_model, max_items=args.max_items,
        )
        _print_summary(summary)
        print(json.dumps(summary, indent=2))
        return 1 if summary["gate"]["passed"] is False else 0
    finally:
        if not args.keep and not args.cleanup:
            common.cleanup_by_content(client, _retrieval_corpus())
        client.close()


@pytest.mark.eval
def test_chat_faithfulness_reports_or_gates() -> None:
    """End-to-end chat faithfulness eval; report-only unless
    ``CHAT_FAITHFULNESS_MIN`` is set. Skips without a live stack / keys.
    Caps at 3 items under pytest to stay cost-bounded."""
    if not common.resolve_api_key():
        pytest.skip("CERID_API_KEY not set — chat faithfulness eval requires auth")
    if not common.ensure_openrouter_key():
        pytest.skip("OPENROUTER_API_KEY not available — judge cannot run")
    dataset = load_dataset()
    client = common.make_client()
    try:
        if not common.health_ok(client):
            pytest.skip(f"Cerid stack not reachable at {common.mcp_base()}")
        cost_bounded_items = 3
        summary = run_eval(
            client, dataset, chat_model=DEFAULT_CHAT_MODEL,
            judge_model=DEFAULT_JUDGE_MODEL, max_items=cost_bounded_items,
        )
        _print_summary(summary)
        assert summary["n_items"] > 0
        gate = summary["gate"]
        if gate["passed"] is not None:
            assert gate["passed"], (
                f"overall faithfulness {summary['overall_faithfulness']} "
                f"below floor {gate['min']}"
            )
    finally:
        common.cleanup_by_content(client, _retrieval_corpus())
        client.close()


if __name__ == "__main__":
    raise SystemExit(main())
