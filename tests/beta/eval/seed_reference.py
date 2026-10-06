# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Offline reference rankings over ``benchmark_seed.jsonl`` — no stack.

The retrieval floors in ``rag_benchmark.py`` are derived from these three
rankers run against the seed alone: a perfect ranker (the answer first), a
random permutation, and a lexical-overlap ranker. A live floor sits between
random and lexical so that a ranker which cannot tell the answering document
from its same-topic distractors fails, while a plain term-overlap ranker
passes. ``test_retrieval_scoring.py`` recomputes the numbers and pins the
ones quoted in the benchmark's docstring.
"""

from __future__ import annotations

import random
import re
import statistics

from metrics import mrr, ndcg_at_k

_WORD_RE = re.compile(r"[a-z0-9]+")
STOPWORDS = frozenset(
    "the a an and or of to in is are it its for how what does do you when should "
    "where with on that this by be as at from than".split()
)


def tokens(text: str) -> set[str]:
    return {t for t in _WORD_RE.findall(text.lower()) if t not in STOPWORDS}


def seed_documents(cases: list[dict]) -> list[tuple[str, str]]:
    """Every seeded document as ``(doc_id, text)``; ids are ``<i>:answer`` and ``<i>:d<j>``."""
    docs: list[tuple[str, str]] = []
    for i, case in enumerate(cases):
        docs.append((f"{i}:answer", case["answer"]))
        docs.extend((f"{i}:d{j + 1}", d) for j, d in enumerate(case["distractors"]))
    return docs


def lexical_ranking(query: str, docs: list[tuple[str, str]]) -> list[str]:
    """Documents ordered by the share of query terms they contain (stable on ties)."""
    q = tokens(query)
    scored = [(len(q & tokens(text)) / max(len(q), 1), n, doc_id) for n, (doc_id, text) in enumerate(docs)]
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [doc_id for _, _, doc_id in scored]


def _mean_scores(rankings: list[tuple[list[str], set[str]]], k: int) -> dict[str, float]:
    return {
        "ndcg": statistics.fmean(ndcg_at_k(r, rel, k) for r, rel in rankings),
        "mrr": statistics.fmean(mrr(r, rel) for r, rel in rankings),
    }


def reference_scores(cases: list[dict], *, k: int = 5, trials: int = 1000, seed: int = 0) -> dict[str, dict[str, float]]:
    """Mean NDCG@k and MRR over the seed for four rankers.

    ``perfect`` — answer first. ``random_pool`` — a uniform permutation of
    every seeded document (a ranker that cannot even find the topic).
    ``random_cluster`` — the answer and its own distractors in uniform random
    order ahead of everything else (a ranker that finds the topic but cannot
    discriminate within it; the case the distractors were built to expose).
    ``lexical`` — ``lexical_ranking`` over every seeded document.
    """
    docs = seed_documents(cases)
    all_ids = [doc_id for doc_id, _ in docs]
    rng = random.Random(seed)
    perfect, pool, cluster, lexical = [], [], [], []
    for i, case in enumerate(cases):
        relevant = {f"{i}:answer"}
        cluster_ids = [f"{i}:answer"] + [f"{i}:d{j + 1}" for j in range(len(case["distractors"]))]
        rest = [d for d in all_ids if d not in cluster_ids]
        perfect.append((cluster_ids + rest, relevant))
        lexical.append((lexical_ranking(case["query"], docs), relevant))
        for _ in range(trials):
            shuffled = all_ids[:]
            rng.shuffle(shuffled)
            pool.append((shuffled, relevant))
            c = cluster_ids[:]
            rng.shuffle(c)
            cluster.append((c + rest, relevant))
    return {
        "perfect": _mean_scores(perfect, k),
        "random_pool": _mean_scores(pool, k),
        "random_cluster": _mean_scores(cluster, k),
        "lexical": _mean_scores(lexical, k),
    }
