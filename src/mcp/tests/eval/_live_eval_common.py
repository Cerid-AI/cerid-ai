# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Shared plumbing for the LIVE eval harnesses (Phase 0.1 + 0.3).

Both ``live_retrieval_eval.py`` (0.1) and ``chat_faithfulness_eval.py`` (0.3)
run against a live Cerid stack (``MCP_BASE`` default ``http://localhost:8888``)
authenticated with ``X-API-Key`` from ``CERID_API_KEY``. They self-seed the
same deterministic fixture corpus (``fixtures/*.md``) via the ingestion API so
retrieval is never measured against an empty/degenerate corpus — the repo's #1
historical eval failure.

This module owns everything the two harnesses share: config resolution, the
authenticated HTTP client, corpus seeding + readiness polling, the live
``/query`` call, and idempotent teardown. It is prefixed with ``_`` so pytest
never collects it as a test module.

Not a pytest module — imported by the harnesses.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger(__name__)

_EVAL_DIR = Path(__file__).resolve().parent
FIXTURES_DIR = _EVAL_DIR / "fixtures"
DATASETS_DIR = _EVAL_DIR / "datasets"
OUT_DIR = _EVAL_DIR / "out"

#: Fixture filename namespace — makes the seeded docs trivially identifiable
#: and removable on the operator's live personal instance.
FIXTURE_PREFIX = "eval-fixture-"

DEFAULT_MCP_BASE = "http://localhost:8888"

# --- tunables (named so no bare numeric literal appears at a comparison) ---
HTTP_TIMEOUT_S = 90.0
DELETE_TIMEOUT_S = 15.0
SEED_READY_TIMEOUT_S = 45.0
SEED_POLL_INTERVAL_S = 2.0
READY_PROBE_TOP_K = 10
SEED_MAX_ATTEMPTS = 4
SEED_RETRY_BASE_S = 5.0
SEED_PACE_S = 0.5
#: Per-request /query wall-clock budget (API-validated 1-120s). The default
#: 20s budget degrades to an EMPTY envelope under ambient host load, which a
#: harness would mis-score as retrieval misses.
EVAL_QUERY_BUDGET_S = 60.0
_MODULE = "tests.eval._live_eval_common"


# ---------------------------------------------------------------------------
# Config resolution
# ---------------------------------------------------------------------------
def mcp_base() -> str:
    """Base URL of the live stack (``MCP_BASE`` env, default localhost:8888)."""
    return os.getenv("MCP_BASE", DEFAULT_MCP_BASE)


def _key_from_dotenv(var_name: str) -> str:
    """Best-effort read of ``var_name`` from the nearest repo ``.env``.

    Convenience for local runs so an operator need not export the key by hand;
    CI injects the value as a real env var and never reaches this path. The
    value is returned to the caller but never logged/printed.
    """
    for parent in _EVAL_DIR.parents:
        env_path = parent / ".env"
        if not env_path.exists():
            continue
        try:
            for line in env_path.read_text().splitlines():
                stripped = line.strip()
                if stripped.startswith(f"{var_name}="):
                    return stripped.split("=", 1)[1].strip().strip('"').strip("'")
        except OSError as exc:
            log_swallowed_error(_MODULE, exc)
    return ""


def resolve_api_key() -> str:
    """``CERID_API_KEY`` from env, falling back to the repo ``.env``."""
    return os.getenv("CERID_API_KEY", "") or _key_from_dotenv("CERID_API_KEY")


def ensure_openrouter_key() -> bool:
    """Make sure ``OPENROUTER_API_KEY`` is in ``os.environ`` for in-process
    LLM-judge calls (0.3). Loads it from the repo ``.env`` when absent.

    Returns True when a key is available. Never prints the value.
    """
    if os.getenv("OPENROUTER_API_KEY"):
        return True
    key = _key_from_dotenv("OPENROUTER_API_KEY")
    if key:
        os.environ["OPENROUTER_API_KEY"] = key
        return True
    return False


def make_client() -> httpx.Client:
    """Authenticated HTTP client for the live stack. Every request carries
    ``X-API-Key`` (when configured) and a unique ``X-Client-ID`` so the eval's
    traffic lands in its own rate-limit bucket."""
    headers: dict[str, str] = {
        "Content-Type": "application/json",
        "X-Client-ID": f"eval-live-{uuid4().hex[:8]}",
    }
    key = resolve_api_key()
    if key:
        headers["X-API-Key"] = key
    return httpx.Client(base_url=mcp_base(), headers=headers, timeout=HTTP_TIMEOUT_S)


def health_ok(client: httpx.Client) -> bool:
    """True when the stack answers /health with 200."""
    try:
        return client.get("/health", timeout=SEED_POLL_INTERVAL_S).status_code == 200
    except httpx.HTTPError as exc:
        log_swallowed_error(_MODULE, exc)
        return False


# ---------------------------------------------------------------------------
# Fixture corpus
# ---------------------------------------------------------------------------
def read_fixture(filename: str) -> str:
    """Read a fixture doc's full text from ``fixtures/``."""
    return (FIXTURES_DIR / filename).read_text()


def fixture_artifact_id(content: str) -> str:
    """Reproduce the server's content-addressed artifact id.

    ``app.services.ingestion.ingest_content`` sets ``artifact_id =
    sha256(content)``; recomputing it locally lets ``--cleanup`` delete the
    fixtures without first querying for their ids.
    """
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def seed_corpus(client: httpx.Client, corpus: list[dict[str, str]]) -> dict[str, str]:
    """Ingest every corpus doc via ``POST /ingest/structured``.

    Idempotent: ingestion is content-addressed, so re-seeding identical content
    returns ``status="duplicate"`` and reuses the existing artifact. Returns a
    ``{filename: artifact_id}`` map for teardown.

    ``sub_category`` + ``tags_json`` are pre-supplied so the ingestion
    enrichment seam (``app.services.ingestion.ingest_content``, ``enrich=True``)
    skips its per-doc ``ai_categorize`` internal-LLM call — measured at
    ~40s/doc on the dev host, which alone would blow the harness's runtime
    budget on a cold seed. The values also label the artifacts as eval
    fixtures in the KB UI.
    """
    ids: dict[str, str] = {}
    for entry in corpus:
        filename = entry["filename"]
        domain = entry["domain"]
        content = read_fixture(filename)
        payload = {
            "content": content,
            "domain": domain,
            "metadata": {
                "filename": filename,
                "sub_category": "eval-fixture",
                "tags_json": json.dumps(["eval-fixture"]),
            },
            "source_id": filename,
        }
        resp = None
        # Bounded retry: an 18-doc burst can trip the API rate limit (429) and
        # a loaded host can exceed the client read timeout — both observed on
        # the first live runs (2026-07-13). Retries are per-doc and bounded so
        # the seed phase can't hang the harness.
        for attempt in range(SEED_MAX_ATTEMPTS):
            try:
                resp = client.post("/ingest/structured", json=payload)
            except httpx.TimeoutException as exc:
                log_swallowed_error(_MODULE, exc)
                resp = None
                time.sleep(SEED_RETRY_BASE_S)
                continue
            if resp.status_code == httpx.codes.TOO_MANY_REQUESTS:
                retry_after = float(
                    resp.headers.get("Retry-After", SEED_RETRY_BASE_S) or SEED_RETRY_BASE_S
                )
                time.sleep(max(retry_after, SEED_RETRY_BASE_S) * (attempt + 1))
                continue
            break
        if resp is None:
            raise RuntimeError(
                f"seed_corpus: ingest of {filename} timed out after "
                f"{SEED_MAX_ATTEMPTS} attempts"
            )
        resp.raise_for_status()
        artifact_id = resp.json().get("artifact_id", "") or fixture_artifact_id(content)
        ids[filename] = artifact_id
        time.sleep(SEED_PACE_S)  # pace the burst below the rate limit
    return ids


def _probe_query(filename: str) -> str:
    """Derive a readiness probe query from the fixture's own title line.

    The probe's only job is "is this doc indexed and retrievable at all" —
    NOT "does it win a ranking contest". A generic question built from the
    filename slug ("What do my notes say about rate limiter?") sits mid-pack
    in pack-heavy domains and gets shuffled out of top-10 by legitimate
    ranking features (observed live 2026-07-14: the newly-real quality boost
    demoted uncurated fixtures below curated pack docs for weak queries,
    reporting an indexed corpus as not-ready). The fixture's distinctive
    title line ("Zephyr API Gateway — Rate Limiter") ranks first on any
    functioning index regardless of boost reshuffles.

    A question suffix is kept so the adaptive retrieval gate
    (``core.retrieval.retrieval_gate.classify_retrieval_need``) never skips
    the probe as ``too_short`` (observed live 2026-07-13: bare 2-word slug
    probes returned empty forever — 90 consecutive gate-skips).
    """
    stem = filename[len(FIXTURE_PREFIX):].removesuffix(".md")
    parts = stem.split("-")
    slug = " ".join(parts[1:]) if len(parts) > 1 else stem
    title = ""
    try:
        first_line = read_fixture(filename).lstrip().splitlines()[0]
        title = first_line.lstrip("# ").replace("—", " ").replace("*", "").strip()
    except Exception:  # noqa: BLE001 — missing fixture: fall back to the slug
        title = ""
    subject = f"{title} {slug}".strip() if title else slug
    return f"What do I have about {subject}?"


def wait_until_retrievable(
    client: httpx.Client,
    corpus: list[dict[str, str]],
    *,
    timeout_s: float = SEED_READY_TIMEOUT_S,
) -> list[str]:
    """Poll ``/query`` until every seeded doc is retrievable (or timeout).

    Ingestion is synchronous (chunks are committed before the POST returns), so
    this normally passes on the first probe; the bounded poll guards against
    embedding/index lag. Returns the list of filenames still not retrievable at
    timeout (empty = all ready).
    """
    pending = {entry["filename"]: entry["domain"] for entry in corpus}
    deadline = time.monotonic() + timeout_s
    while pending and time.monotonic() < deadline:
        now_ready: list[str] = []
        for filename, domain in pending.items():
            # Deadline is enforced per-probe, not per-round: one round over a
            # slow live host can take minutes, and a round-level check would
            # let the poll overshoot the budget several-fold.
            if time.monotonic() >= deadline:
                break
            ranked = query_ranked(
                client, _probe_query(filename), [domain], top_k=READY_PROBE_TOP_K
            )
            if filename in {s.get("filename", "") for s in ranked}:
                now_ready.append(filename)
        for filename in now_ready:
            del pending[filename]
        if pending and time.monotonic() < deadline:
            time.sleep(SEED_POLL_INTERVAL_S)
    return list(pending)


def cleanup_corpus(client: httpx.Client, artifact_ids: list[str]) -> int:
    """Delete seeded artifacts via ``DELETE /admin/artifacts/{id}``.

    Best-effort — a failed delete is logged, not raised, so teardown never
    masks the eval's own result. Returns the count actually deleted.
    """
    deleted = 0
    for artifact_id in artifact_ids:
        try:
            resp = client.delete(
                f"/admin/artifacts/{artifact_id}", timeout=DELETE_TIMEOUT_S
            )
            if resp.status_code == httpx.codes.OK and resp.json().get("deleted"):
                deleted += 1
        except (httpx.HTTPError, ValueError) as exc:  # ValueError: bad JSON body
            log_swallowed_error(_MODULE, exc)
    return deleted


def cleanup_by_content(client: httpx.Client, corpus: list[dict[str, str]]) -> int:
    """Delete fixtures by recomputing their content-addressed ids locally.

    Used by ``--cleanup`` when no in-process seed map exists.
    """
    ids = [fixture_artifact_id(read_fixture(e["filename"])) for e in corpus]
    return cleanup_corpus(client, ids)


# ---------------------------------------------------------------------------
# Live retrieval
# ---------------------------------------------------------------------------
def query_ranked(
    client: httpx.Client,
    query: str,
    search_domains: list[str],
    *,
    top_k: int,
) -> list[dict[str, Any]]:
    """Issue ``POST /query`` for each domain and return sources ranked by
    relevance (deduped by filename, best relevance wins).

    ``/query`` is single-domain (``QueryRequest.domain: str``), so a
    cross-domain query fans out one call per domain and merges. Relevance from
    separate calls is not perfectly comparable, but for single-domain queries
    (the common case) it is exact, and for the merge it is a reasonable order.
    """
    merged: dict[str, dict[str, Any]] = {}
    for domain in search_domains:
        resp = client.post(
            "/query",
            json={
                "query": query,
                "domain": domain,
                "top_k": top_k,
                # Opt into the eval budget: at the default 20s wall clock,
                # ambient host load (CI, background jobs) makes /query return
                # degraded-EMPTY envelopes that score as misses — measuring
                # load, not ranking (live 2026-07-14: every probe >=20.2s
                # missed; everything under budget hit). Same convention as
                # the July rag_benchmark fix.
                "budget_seconds": EVAL_QUERY_BUDGET_S,
                # Bypass the semantic cache: repeated harness runs reuse the
                # exact same queries, so a cached hit measures the PREVIOUS
                # configuration's results (live 2026-07-14: two fusion modes
                # scored identically to 3 decimals — arm 2 was reading arm
                # 1's cache).
                "skip_cache": True,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if payload.get("budget_exceeded"):
            logger.warning(
                "query_ranked: budget_exceeded even at %.0fs (query=%r domain=%s)",
                EVAL_QUERY_BUDGET_S, query[:60], domain,
            )
        for source in payload.get("sources", []):
            filename = source.get("filename", "")
            if not filename:
                continue
            prev = merged.get(filename)
            if prev is None or source.get("relevance", 0.0) > prev.get("relevance", 0.0):
                merged[filename] = source
    return sorted(
        merged.values(), key=lambda s: s.get("relevance", 0.0), reverse=True
    )


def ranked_filenames(sources: list[dict[str, Any]]) -> list[str]:
    """Filenames in rank order (for the IR metric functions)."""
    return [s.get("filename", "") for s in sources if s.get("filename")]
