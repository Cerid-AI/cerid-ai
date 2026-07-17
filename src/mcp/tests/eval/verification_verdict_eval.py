# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Verdict-accuracy eval harness (Quality-Maximization Phase 0.2).

Drives the live ``POST /agent/verify-stream`` endpoint (same SSE contract as
``tests/beta/eval/verification_efficacy.py``) over the ~100-case labeled
dataset in ``datasets/verification_cases_v2.jsonl`` and scores the pipeline's
final per-claim *verdicts* against hand-labeled ground truth — the "42-case
labeled verdict harness exists but is not CI-wired" gap called out in the
2026-07-13 quality-maximization audit. This script is intentionally
report-only by default (env-gated floors, unset = exit 0) so an operator can
baseline current accuracy before turning it into a hard nightly gate.

Not a pytest module — script-style, mirroring ``tests/eval/augmented_eval.py``
and ``scripts/nli_faithfulness_ablation.py``. Requires a live MCP stack.

Usage::

    PYTHONPATH=src/mcp .venv/bin/python -m tests.eval.verification_verdict_eval \\
        [--limit N] [--concurrency N] [--mcp-base URL] [--output PATH]

    # Hard-gate mode (integrator, post-baseline):
    VERDICT_EVAL_MIN_ACCURACY=0.75 VERDICT_EVAL_MAX_TIMEOUT_RATE=0.05 \\
        PYTHONPATH=src/mcp .venv/bin/python -m tests.eval.verification_verdict_eval

Env:
    MCP_BASE                     Live-stack base URL (default http://localhost:8888).
    CERID_API_KEY                Sent as X-API-Key when set.
    VERDICT_EVAL_MIN_ACCURACY    Floor on overall accuracy. Unset = report-only.
    VERDICT_EVAL_MAX_TIMEOUT_RATE  Ceiling on case timeout rate. Unset = report-only.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from tests.eval._live_eval_common import gate_floor

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SCRIPT_DIR = Path(__file__).parent
_DEFAULT_CASES = _SCRIPT_DIR / "datasets" / "verification_cases_v2.jsonl"
_DEFAULT_OUTPUT = _SCRIPT_DIR / "out" / "verification_verdict_results.json"

_ALLOWED_CLAIM_TYPES = {"factual", "recency", "ignorance", "citation", "evasion"}
_ALLOWED_VERDICTS = {"verified", "unverified", "uncertain"}


# ---------------------------------------------------------------------------
# Dataset loading
# ---------------------------------------------------------------------------


def load_cases(path: Path, limit: int = 0) -> list[dict[str, Any]]:
    """Load the JSONL case set, optionally capped to the first ``limit`` rows."""
    cases: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                cases.append(json.loads(line))
    if limit:
        cases = cases[:limit]
    return cases


def _load_dotenv() -> None:
    """Best-effort ``.env`` loader, mirroring ``scripts/nli_faithfulness_ablation.py``.

    Only fills env vars not already set — an explicitly exported CERID_API_KEY
    (or one injected by CI) always wins over the file.
    """
    env_path = _REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding="utf-8").splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


# ---------------------------------------------------------------------------
# Live-stack helpers (mirrors tests/beta/eval/conftest.py's stream_verify /
# seed_content / cleanup_artifact / wait_for_indexed — kept self-contained
# here rather than imported since tests/beta/eval is a separate pytest root
# with its own fixtures and a docker-hostname MCP_BASE default).
# ---------------------------------------------------------------------------


async def seed_content(client: httpx.AsyncClient, content: str, domain: str = "general") -> str:
    """Ingest content via POST /ingest, return artifact_id. Retries on 429."""
    resp: httpx.Response | None = None
    for attempt in range(5):
        resp = await client.post("/ingest", json={"content": content, "domain": domain})
        if resp.status_code == 429:
            await asyncio.sleep(3 * (attempt + 1))
            continue
        resp.raise_for_status()
        return str(resp.json()["artifact_id"])
    assert resp is not None
    resp.raise_for_status()
    raise RuntimeError("unreachable")  # pragma: no cover


async def cleanup_artifact(client: httpx.AsyncClient, artifact_id: str) -> None:
    try:
        await client.delete(f"/admin/artifacts/{artifact_id}")
    except httpx.HTTPError:
        pass  # best-effort cleanup


async def wait_for_indexed(client: httpx.AsyncClient, artifact_id: str, timeout: float = 10) -> None:
    deadline = time.monotonic() + timeout
    delay = 0.5
    while time.monotonic() < deadline:
        resp = await client.get(f"/artifacts/{artifact_id}")
        if resp.status_code == 200:
            return
        await asyncio.sleep(delay)
        delay = min(delay * 1.5, 3.0)
    raise TimeoutError(f"Artifact {artifact_id} not indexed within {timeout}s")


async def stream_verify(
    client: httpx.AsyncClient, response_text: str, user_query: str, *, timeout_s: float
) -> dict[str, Any]:
    """Call POST /agent/verify-stream and parse SSE events into a structured result."""
    body: dict[str, Any] = {
        "response_text": response_text,
        "conversation_id": f"verdict-eval-{uuid.uuid4().hex[:8]}",
        "user_query": user_query,
    }
    claims: list[dict[str, Any]] = []
    summary: dict[str, Any] = {}
    errors: list[str] = []

    async with client.stream("POST", "/agent/verify-stream", json=body, timeout=timeout_s) as resp:
        resp.raise_for_status()
        buffer = ""
        async for chunk in resp.aiter_text():
            buffer += chunk
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line or line.startswith(":") or not line.startswith("data: "):
                    continue
                data_str = line[6:]
                try:
                    event = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                etype = event.get("event", event.get("type", ""))
                if etype == "claim_extracted":
                    claims.append({
                        "index": event.get("index"),
                        "claim": event.get("claim", ""),
                        "claim_type": event.get("claim_type", ""),
                        "status": "pending",
                    })
                elif etype == "claim_verified":
                    idx = event.get("index")
                    for c in claims:
                        if c["index"] == idx:
                            c["status"] = event.get("status", "")
                            c["confidence"] = event.get("confidence", 0)
                            c["reason"] = event.get("reason", "")
                            c["verification_method"] = event.get("verification_method", "")
                            break
                elif etype == "summary":
                    summary.update(event)
                elif etype == "error":
                    errors.append(event.get("detail", str(event)))
    return {"claims": claims, "summary": summary, "errors": errors}


def find_matching_claim(claims: list[dict[str, Any]], text_fragment: str) -> dict[str, Any] | None:
    """Find an extracted claim whose text contains the fragment (case-insensitive).

    Falls back to word-overlap matching. Mirrors
    ``tests/beta/eval/verification_efficacy.py::find_matching_claim``.
    """
    fragment_lower = text_fragment.lower()
    for c in claims:
        if fragment_lower in c.get("claim", "").lower():
            return c
    fragment_words = set(fragment_lower.split())
    for c in claims:
        claim_words = set(c.get("claim", "").lower().split())
        overlap = fragment_words & claim_words
        if len(overlap) >= max(2, len(fragment_words) * 0.6):
            return c
    return None


# ---------------------------------------------------------------------------
# Per-case execution + grading
# ---------------------------------------------------------------------------


@dataclass
class ClaimGrade:
    case_id: str
    fragment: str
    bucket_type: str
    freshness: str
    expected: str
    matched: bool
    actual: str | None = None
    correct: bool = False
    # Raw per-claim confidence score as reported by the ``claim_verified`` SSE
    # event (Quality-Maximization Phase 3.5 — confidence calibration needs the
    # score distribution behind each verdict, not just the verdict itself).
    # None when the claim was never matched (nothing to read a score from).
    confidence: float | None = None
    # Pipeline path that produced the verdict (``kb``, ``kb_nli``, ``kb_batch``,
    # ``cross_model``, ``web_search``, ...). Load-bearing for calibration: the
    # cross-model path CLAMPS confidence per-verdict (refuted <= 0.35,
    # uncertain in [0.36, 0.64]) so its scores are band-separated by
    # construction — only KB-path scores are organic similarity values.
    verification_method: str | None = None


@dataclass
class CaseResult:
    case_id: str
    claim_type: str
    freshness: str
    latency_ms: float
    timed_out: bool = False
    transport_error: str | None = None
    n_claims_returned: int = 0
    n_claims_uncertain: int = 0
    grades: list[ClaimGrade] = field(default_factory=list)


def _bucket_for(case: dict[str, Any], expected_claim: dict[str, Any]) -> str:
    t = expected_claim.get("type")
    return t if t in _ALLOWED_CLAIM_TYPES else str(case["claim_type"])


async def run_case(client: httpx.AsyncClient, case: dict[str, Any], *, timeout_s: float) -> CaseResult:
    seeded_id: str | None = None
    t0 = time.monotonic()
    result: dict[str, Any] = {"claims": [], "summary": {}, "errors": []}
    timed_out = False
    transport_error: str | None = None

    try:
        if case.get("seed_content"):
            seeded_id = await seed_content(client, case["seed_content"], case.get("seed_domain", "general"))
            await wait_for_indexed(client, seeded_id)
        result = await stream_verify(client, case["response_text"], case["user_query"], timeout_s=timeout_s)
    except httpx.TimeoutException:
        timed_out = True
    except httpx.HTTPError as exc:
        transport_error = str(exc)
    finally:
        if seeded_id:
            await cleanup_artifact(client, seeded_id)

    latency_ms = (time.monotonic() - t0) * 1000.0
    claims = result["claims"]
    n_uncertain = sum(1 for c in claims if c.get("status") == "uncertain")

    case_result = CaseResult(
        case_id=case["id"],
        claim_type=case["claim_type"],
        freshness=case["freshness"],
        latency_ms=latency_ms,
        timed_out=timed_out,
        transport_error=transport_error,
        n_claims_returned=len(claims),
        n_claims_uncertain=n_uncertain,
    )

    for ec in case.get("expected_claims", []):
        expected = ec.get("expected_verdict")
        if not expected:
            continue  # intentionally ungraded (e.g. seasonally-flaky fragments)
        fragment = ec["text_fragment"]
        bucket = _bucket_for(case, ec)
        matched_claim = find_matching_claim(claims, fragment)
        grade = ClaimGrade(
            case_id=case["id"],
            fragment=fragment,
            bucket_type=bucket,
            freshness=case["freshness"],
            expected=expected,
            matched=matched_claim is not None,
        )
        if matched_claim is not None:
            grade.actual = matched_claim.get("status")
            grade.correct = grade.actual == expected
            raw_confidence = matched_claim.get("confidence")
            if isinstance(raw_confidence, (int, float)):
                grade.confidence = float(raw_confidence)
            grade.verification_method = matched_claim.get("verification_method") or None
        case_result.grades.append(grade)

    return case_result


async def run_eval(
    cases: list[dict[str, Any]], *, mcp_base: str, api_key: str | None, concurrency: int, timeout_s: float
) -> list[CaseResult]:
    headers = {"X-Client-ID": "verdict-eval", "Content-Type": "application/json"}
    if api_key:
        headers["X-API-Key"] = api_key

    sem = asyncio.Semaphore(max(1, concurrency))

    async with httpx.AsyncClient(base_url=mcp_base, headers=headers, timeout=timeout_s + 30) as client:

        async def _bounded(case: dict[str, Any]) -> CaseResult:
            async with sem:
                return await run_case(client, case, timeout_s=timeout_s)

        return list(await asyncio.gather(*[_bounded(c) for c in cases]))


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------


def _accuracy(grades: list[ClaimGrade]) -> float:
    if not grades:
        return 0.0
    return round(sum(1 for g in grades if g.correct) / len(grades), 4)


def summarize(case_results: list[CaseResult], *, n_cases_total: int) -> dict[str, Any]:
    all_grades = [g for cr in case_results for g in cr.grades]
    n_cases_run = len(case_results)
    n_timeouts = sum(1 for cr in case_results if cr.timed_out)
    n_transport_errors = sum(1 for cr in case_results if cr.transport_error)

    by_claim_type: dict[str, list[ClaimGrade]] = {}
    by_freshness: dict[str, list[ClaimGrade]] = {}
    for g in all_grades:
        by_claim_type.setdefault(g.bucket_type, []).append(g)
        by_freshness.setdefault(g.freshness, []).append(g)

    total_claims_returned = sum(cr.n_claims_returned for cr in case_results)
    total_uncertain = sum(cr.n_claims_uncertain for cr in case_results)

    latency_by_type: dict[str, list[float]] = {}
    for cr in case_results:
        latency_by_type.setdefault(cr.claim_type, []).append(cr.latency_ms)

    unmatched = [g for g in all_grades if not g.matched]
    failures = [
        {
            "case_id": g.case_id,
            "fragment": g.fragment,
            "claim_type": g.bucket_type,
            "expected": g.expected,
            "actual": g.actual,
            "matched": g.matched,
            "confidence": g.confidence,
        }
        for g in all_grades
        if not g.correct
    ]
    # Raw (confidence, expected, actual, correct) tuples for every matched
    # claim — the score distribution a band-boundary calibration pass needs.
    # Kept separate from `failures` so a downstream analysis doesn't have to
    # reconstruct correct-and-matched rows that `failures` deliberately omits.
    calibration_records = [
        {
            "case_id": g.case_id,
            "claim_type": g.bucket_type,
            "expected": g.expected,
            "actual": g.actual,
            "confidence": g.confidence,
            "verification_method": g.verification_method,
            "correct": g.correct,
        }
        for g in all_grades
        if g.matched and g.confidence is not None
    ]

    return {
        "n_cases_total": n_cases_total,
        "n_cases_run": n_cases_run,
        "n_claims_graded": len(all_grades),
        "n_claims_matched": len(all_grades) - len(unmatched),
        "extraction_match_rate": round((len(all_grades) - len(unmatched)) / len(all_grades), 4) if all_grades else 0.0,
        "overall_accuracy": _accuracy(all_grades),
        "per_claim_type_accuracy": {
            k: {"n": len(v), "accuracy": _accuracy(v)} for k, v in sorted(by_claim_type.items())
        },
        "per_freshness_accuracy": {
            k: {"n": len(v), "accuracy": _accuracy(v)} for k, v in sorted(by_freshness.items())
        },
        "timeout_rate": round(n_timeouts / n_cases_run, 4) if n_cases_run else 0.0,
        "transport_error_rate": round(n_transport_errors / n_cases_run, 4) if n_cases_run else 0.0,
        "uncertain_rate": round(total_uncertain / total_claims_returned, 4) if total_claims_returned else 0.0,
        "mean_latency_ms_overall": round(sum(cr.latency_ms for cr in case_results) / n_cases_run, 1) if n_cases_run else 0.0,
        "mean_latency_ms_by_claim_type": {
            k: round(sum(v) / len(v), 1) for k, v in sorted(latency_by_type.items())
        },
        "failures": failures,
        "calibration_records": calibration_records,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _apply_floors(summary: dict[str, Any]) -> tuple[bool, list[str]]:
    """Check env-gated floors. Returns (gated, violations). ``gated`` is False
    (report-only) unless at least one floor env var is set."""
    floor = gate_floor("VERDICT_EVAL_MIN_ACCURACY")
    ceiling = gate_floor("VERDICT_EVAL_MAX_TIMEOUT_RATE")
    gated = floor is not None or ceiling is not None
    violations: list[str] = []

    if floor is not None and summary["overall_accuracy"] < floor:
        violations.append(
            f"overall_accuracy {summary['overall_accuracy']} < VERDICT_EVAL_MIN_ACCURACY {floor}"
        )
    if ceiling is not None and summary["timeout_rate"] > ceiling:
        violations.append(
            f"timeout_rate {summary['timeout_rate']} > VERDICT_EVAL_MAX_TIMEOUT_RATE {ceiling}"
        )
    return gated, violations


async def _main_async(args: argparse.Namespace) -> int:
    _load_dotenv()
    cases = load_cases(args.cases, limit=args.limit)
    if not cases:
        print(f"No cases loaded from {args.cases}", file=sys.stderr)
        return 1

    api_key = os.getenv("CERID_API_KEY")
    case_results = await run_eval(
        cases,
        mcp_base=args.mcp_base,
        api_key=api_key,
        concurrency=args.concurrency,
        timeout_s=args.timeout,
    )

    total_cases_in_dataset = len(load_cases(args.cases))
    summary = summarize(case_results, n_cases_total=total_cases_in_dataset)
    summary["mcp_base"] = args.mcp_base
    summary["cases_path"] = str(args.cases)

    gated, violations = _apply_floors(summary)
    summary["floors"] = {
        "min_accuracy": os.getenv("VERDICT_EVAL_MIN_ACCURACY"),
        "max_timeout_rate": os.getenv("VERDICT_EVAL_MAX_TIMEOUT_RATE"),
        "gated": gated,
        "violations": violations,
    }

    print(json.dumps(summary, indent=2))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

    if gated and violations:
        for v in violations:
            print(f"FLOOR VIOLATION: {v}", file=sys.stderr)
        return 1
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Verification verdict-accuracy eval (Phase 0.2)")
    parser.add_argument("--cases", type=Path, default=_DEFAULT_CASES)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    parser.add_argument("--mcp-base", type=str, default=os.getenv("MCP_BASE", "http://localhost:8888"))
    parser.add_argument("--limit", type=int, default=0, help="cap dataset size (0 = full)")
    parser.add_argument("--concurrency", type=int, default=3)
    parser.add_argument("--timeout", type=float, default=120.0, help="per-case verify-stream timeout (s)")
    args = parser.parse_args()
    return asyncio.run(_main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
