# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""kb-weak-match: should artifact tags be indexed into the BM25 corpus?

WHERE TO RUN: **in the container** — it reads the live BM25 corpora under
``/app/data/bm25`` and calls the live artifacts API.

    docker exec -i -e PYTHONPATH=/app \\
      -e PROVIDER_STAGE_TAG_LABEL_ACCURACY_EVAL=openrouter \\
      -e PROVIDER_STAGE_TAG_LABEL_ACCURACY_EVAL_MODEL=openrouter/openai/gpt-4o-mini \\
      -w /app ai-companion-mcp python tests/eval/tag_index_eval.py

The stage-routing vars are load-bearing. The container's default provider is
quenchforge/llama3.1-8b, which produced incoherent judgements on the
known-answer smoke ("the order number is incorrect, it should be 115" for a
shipping email). Sending the local alias to OpenRouter without the model
override 400s — that is the documented trap in `cerid-environment-parity`.

WHY THE OBVIOUS METRIC IS WORTHLESS
-----------------------------------
The tempting design — define ground truth as "artifacts carrying tag T", index
tags, measure recall for query T — is circular. Ground truth is tag membership
and the treatment indexes tags, so control is ~0 by construction and treatment
is ~1 by construction. It measures the harness, not the product. It was run
once during design and produced a spurious +0.727 recall@10; it is
deliberately NOT carried in this harness, so that number cannot be quoted from
here as a benefit.

The non-circular question is whether the tag is a TRUE label. If it is, the
rescued documents are relevant. If it is not, indexing tags routes false
matches through the ``bm25_only`` exemption, which bypasses
QUALITY_MIN_RELEVANCE_THRESHOLD — turning "found nothing" into "confidently
returned the wrong document", which is worse.

VALIDITY CONTROL
----------------
Half the judged pairs are DECOYS: a tag the artifact does not carry, drawn from
another artifact in the same domain, framed identically. If decoys are accepted
at the same rate as real tags, the judge is non-discriminating and the run
decides nothing. Parsing is fail-closed; unparseable items are EXCLUDED rather
than averaged in as zeros.

RESULT (2026-08-14, personal corpus, gpt-4o-mini judge)
-------------------------------------------------------
* addressable population: 67.1% of non-system tag instances are absent from
  their artifact's body text, so the treatment does have material.
* counter-metric: content self-retrieval@10 flat (236/318 -> 237/318 across six
  domains; reconfirmed across all 11 domains on the consolidated run) —
  appending tags does not damage existing retrieval.
* tag accuracy, judged blind: real 25.0% / 37.5% / 40.0% accepted (three runs,
  chunk level), 45.0% at artifact level. Decoys 7.5-12.0% — the judge
  discriminates (+28 to +30pts), so the low number is about the tags, not the
  instrument. The spread across runs is wide at n=25-40; every run lands far
  below any bar that would justify bypassing a quality floor, which is what
  makes the decision robust rather than the precision of any single mean.
* specific tags (33%) score WORSE than generic ones (50%). The tags that
  motivated the hypothesis are the worst in the corpus: invoice 0/11,
  receipt 0/8, tutorial 0/8, tax-return 0/4, expense 0/4.

DECISION: do NOT index tags into BM25. The floor is not the problem; tag
extraction quality is. See tasks/open-findings.json.
"""
from __future__ import annotations

import asyncio
import collections
import glob
import json
import os
import random
import re
import shutil
import sys
import urllib.request

sys.path.insert(0, "/app")

from core.retrieval.bm25 import BM25Index  # noqa: E402
from core.utils.internal_llm import call_internal_llm  # noqa: E402

API_BASE = os.environ.get("EVAL_API_BASE", "http://localhost:8888")
KEY = os.environ["CERID_API_KEY"]
SEED = int(os.environ.get("EVAL_SEED", "20260814"))
N_PER_ARM = int(os.environ.get("EVAL_N", "40"))
TOP_K = 10
CORPUS_DIR = os.environ.get("EVAL_CORPUS_DIR", "/app/data/bm25")
WORK = "/tmp/tag_index_eval"
SYSTEM_TAG = re.compile(r"^(pack|pack-version|source|client|ingest)[:\-]", re.I)

JUDGE_PROMPT = """You are labelling a document collection.

Document excerpt:
---
{excerpt}
---

Candidate topic label: "{tag}"

Does this label accurately describe what the document is about? A label is
accurate if a person searching for that topic would be satisfied to find this
document. It is inaccurate if the label is unrelated, or describes only a
trivial incidental mention.

Reply with JSON only: {{"accurate": true|false, "reason": "<one short sentence>"}}
"""


def _api(path: str):
    req = urllib.request.Request(API_BASE + path, headers={"X-API-Key": KEY})
    return json.load(urllib.request.urlopen(req, timeout=90))


def _norm_list(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            p = json.loads(v)
            return p if isinstance(p, list) else ([p] if p else [])
        except ValueError:  # tags may be CSV, not JSON — the comma split IS the fallback
            return [t.strip() for t in v.split(",") if t.strip()]
    return []


def _terms(tag: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", tag.lower()) if len(t) > 2]


def load_corpus() -> tuple[list[dict], dict[str, str], dict[str, list[dict]]]:
    """Artifacts, chunk_id -> text, and the per-domain BM25 corpora."""
    arts, offset = [], 0
    while True:
        batch = _api(f"/artifacts?limit=200&offset={offset}")
        items = batch if isinstance(batch, list) else (batch.get("artifacts") or [])
        if not items:
            break
        arts.extend(items)
        offset += len(items)
        if len(items) < 200 or offset > 8000:
            break

    corpora: dict[str, list[dict]] = {}
    chunk_text: dict[str, str] = {}
    for f in glob.glob(os.path.join(CORPUS_DIR, "*.jsonl")):
        domain = os.path.basename(f)[: -len(".jsonl")]
        rows = []
        with open(f) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    e = json.loads(line)
                except ValueError:  # a malformed corpus line is skipped by design
                    continue
                if "id" in e and "text" in e:
                    rows.append(e)
                    chunk_text[e["id"]] = e["text"]
        if rows:
            corpora[domain] = rows
    return arts, chunk_text, corpora


def index_maps(arts: list[dict]):
    chunk_to_art, art_tags, art_is_pack, art_domain = {}, {}, {}, {}
    for a in arts:
        aid = a.get("id")
        raw = _norm_list(a.get("tags"))
        art_is_pack[aid] = any(str(t).lower().startswith("pack:") for t in raw)
        art_tags[aid] = [str(t) for t in raw if not SYSTEM_TAG.match(str(t))]
        art_domain[aid] = a.get("domain")
        for c in _norm_list(a.get("chunk_ids")):
            chunk_to_art[c] = aid
    return chunk_to_art, art_tags, art_is_pack, art_domain


def measure_addressability(chunk_text, chunk_to_art, art_tags) -> dict:
    """How many tag instances are ABSENT from their own body text?

    This is the denominator question: if tags already appear in the text, BM25
    matches them today and the treatment can do nothing.
    """
    present = absent = 0
    for cid, text in chunk_text.items():
        aid = chunk_to_art.get(cid)
        if aid is None:
            continue
        body = text.lower()
        for tag in art_tags.get(aid, []):
            ts = _terms(tag)
            if not ts:
                continue
            if all(t in body for t in ts):
                present += 1
            else:
                absent += 1
    total = present + absent
    return {"present": present, "absent": absent, "total": total,
            "addressable_rate": (absent / total) if total else None}


def _build(domain: str, rows: list[dict], variant: str) -> BM25Index:
    d = os.path.join(WORK, variant)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, f"{domain}.jsonl"), "w") as fh:
        for e in rows:
            fh.write(json.dumps(e) + "\n")
    return BM25Index(domain, d)


def measure_counter_metric(corpora, chunk_to_art, art_tags) -> list[dict]:
    """Content queries must not lose recall when tags lengthen documents.

    This arm is NOT circular: the queries are drawn from chunk text and the
    treatment adds no term they contain.
    """
    rnd = random.Random(SEED)
    out = []
    for domain, rows in sorted(corpora.items()):
        control = _build(domain, rows, "control")
        treated = []
        for e in rows:
            aid = chunk_to_art.get(e["id"])
            tags = art_tags.get(aid, []) if aid else []
            t = dict(e)
            if tags:
                t["text"] = e["text"] + "\n\nTags: " + " ".join(tags)
            treated.append(t)
        treatment = _build(domain, treated, "treatment")

        c_hit = t_hit = n = 0
        for e in rnd.sample(rows, min(60, len(rows))):
            words = [w for w in re.split(r"[^a-zA-Z0-9]+", e["text"]) if len(w) > 4]
            if len(words) < 6:
                continue
            q = " ".join(words[:6])
            n += 1
            c_hit += 1 if e["id"] in [c for c, _ in control.search(q, TOP_K)] else 0
            t_hit += 1 if e["id"] in [c for c, _ in treatment.search(q, TOP_K)] else 0
        out.append({"domain": domain, "n": n, "control": c_hit, "treatment": t_hit})
    return out


def _parse_verdict(text: str | None):
    """JSON -> explicit key -> parse_failed. Never a positional token."""
    if not text:
        return None, "empty response"
    cleaned = re.sub(r"^\s*```(?:json)?|```\s*$", "", text.strip(),
                     flags=re.MULTILINE).strip()
    # Fail-closed: JSON first, explicit key second, otherwise None. A verdict
    # that cannot be parsed is EXCLUDED, never scored as a 0.
    try:
        obj = json.loads(cleaned)
    except ValueError:
        obj = None
    if isinstance(obj, dict) and isinstance(obj.get("accurate"), bool):
        return obj["accurate"], str(obj.get("reason", ""))[:160]
    m = re.search(r'"accurate"\s*:\s*(true|false)', cleaned, re.I)
    if m:
        return m.group(1).lower() == "true", "(key-extracted)"
    return None, f"parse_failed: {cleaned[:80]!r}"


async def _judge(pair: dict, sem: asyncio.Semaphore) -> dict:
    async with sem:
        try:
            out = await call_internal_llm(
                [{"role": "user", "content": JUDGE_PROMPT.format(
                    excerpt=pair["excerpt"], tag=pair["tag"])}],
                stage="tag_label_accuracy_eval", max_tokens=150,
            )
        except Exception as e:  # noqa: BLE001 — instrument boundary
            return {**pair, "verdict": None, "reason": f"call_failed: {e}"[:160]}
        verdict, reason = _parse_verdict(out if isinstance(out, str) else str(out))
        return {**pair, "verdict": verdict, "reason": reason}


async def measure_tag_accuracy(chunk_text, chunk_to_art, art_tags,
                               art_is_pack, art_domain) -> list[dict]:
    """Blind judge over real vs decoy (chunk, tag) pairs. The decision arm."""
    rnd = random.Random(SEED)
    pool, by_domain_tags = [], collections.defaultdict(set)
    for cid, text in chunk_text.items():
        aid = chunk_to_art.get(cid)
        if aid is None or art_is_pack.get(aid):
            continue
        body = text.lower()
        for tag in art_tags.get(aid, []):
            ts = _terms(tag)
            by_domain_tags[art_domain.get(aid)].add(tag)
            if ts and not all(t in body for t in ts):
                pool.append({"chunk_id": cid, "artifact_id": aid, "tag": tag,
                             "domain": art_domain.get(aid),
                             "excerpt": text[:1200]})
    rnd.shuffle(pool)
    real = [{**p, "arm": "real"} for p in pool[:N_PER_ARM]]

    decoys = []
    for p in pool[N_PER_ARM:]:
        if len(decoys) >= N_PER_ARM:
            break
        opts = [t for t in by_domain_tags.get(p["domain"], set())
                if t not in art_tags.get(p["artifact_id"], [])]
        if opts:
            decoys.append({**p, "arm": "decoy", "tag": rnd.choice(opts)})

    pairs = real + decoys
    rnd.shuffle(pairs)
    sem = asyncio.Semaphore(4)
    return list(await asyncio.gather(*[_judge(p, sem) for p in pairs]))


def report(results: list[dict]) -> None:
    stats = collections.defaultdict(lambda: {"yes": 0, "no": 0, "failed": 0})
    for r in results:
        s = stats[r["arm"]]
        if r["verdict"] is None:
            s["failed"] += 1
        elif r["verdict"]:
            s["yes"] += 1
        else:
            s["no"] += 1
    rates = {}
    for arm in ("real", "decoy"):
        s = stats[arm]
        scored = s["yes"] + s["no"]
        rates[arm] = (s["yes"] / scored) if scored else float("nan")
        print(f"{arm:6s} accepted {s['yes']}/{scored} = {rates[arm]:.1%}"
              f"   (excluded failures: {s['failed']})")
    sep = rates["real"] - rates["decoy"]
    print(f"separation (real - decoy) = {sep:+.1%}")
    if sep < 0.20:
        print("!! judge non-discriminating — this run decides nothing")


async def main() -> None:
    shutil.rmtree(WORK, ignore_errors=True)
    os.makedirs(WORK, exist_ok=True)

    arts, chunk_text, corpora = load_corpus()
    chunk_to_art, art_tags, art_is_pack, art_domain = index_maps(arts)
    print(f"artifacts={len(arts)} chunks={len(chunk_text)} domains={len(corpora)}")

    addr = measure_addressability(chunk_text, chunk_to_art, art_tags)
    print(f"\n=== ADDRESSABLE POPULATION ===\n"
          f"tag instances={addr['total']} absent-from-body={addr['absent']} "
          f"({addr['addressable_rate']:.1%})")

    print("\n=== COUNTER-METRIC (content self-retrieval must not drop) ===")
    for row in measure_counter_metric(corpora, chunk_to_art, art_tags):
        print(f"  {row['domain']:<14} control {row['control']}/{row['n']}"
              f"   treatment {row['treatment']}/{row['n']}")

    print("\n=== TAG ACCURACY (the decision arm) ===")
    results = await measure_tag_accuracy(
        chunk_text, chunk_to_art, art_tags, art_is_pack, art_domain)
    report(results)

    print("\n=== rejected real tags (read these, not the mean) ===")
    for r in [x for x in results if x["arm"] == "real" and x["verdict"] is False][:8]:
        print(f"  {r['tag']!r} [{r['domain']}]: {r['reason']}")

    out = f"/tmp/tag_index_eval_{SEED}.json"
    with open(out, "w") as fh:
        json.dump({"addressability": addr, "judgements": results}, fh, indent=2)
    print(f"\nwrote {out}")


if __name__ == "__main__":
    asyncio.run(main())
