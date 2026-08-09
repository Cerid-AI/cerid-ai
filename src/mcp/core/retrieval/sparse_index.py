# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""
Per-domain SPLADE-v3 sparse index for hybrid retrieval (Cycle 3.2).

Mirrors :mod:`core.retrieval.bm25` in shape and on-disk layout:

* one ``data/sparse/{domain}.jsonl`` per domain
* in-memory ``dict[int, list[(doc_id, weight)]]`` inverted index
* fsync-on-append for crash safety (the same crash window
  ``bm25.py`` closed in Workstream E Phase 0)
* tenant scoping at the index layer (Workstream E Phase 0 pattern)
* ``rebuild_all()`` for sync-driven warm-up

The index intentionally stays simple — no IDF re-weighting, no shard
pruning. SPLADE weights are already top-k pruned at encode time
(:mod:`core.retrieval.sparse`) which gives O(min(|q|, |d|))-style
search performance at corpus sizes Cerid targets (≤ 1M chunks).

Search returns ``(chunk_id, normalized_score)`` tuples with scores
normalized to ``[0, 1]`` by the top hit, matching the BM25 contract
so :mod:`core.retrieval.rrf` can rank-fuse without per-modality
normalization quirks.

This module never imports the model — it only consumes the sparse
vectors :mod:`core.retrieval.sparse` produces. Tests can therefore
unit-test the index in isolation with hand-built sparse dicts.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from collections import defaultdict
from pathlib import Path

import sentry_sdk

import config
from core.retrieval import sparse as _sparse
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.sparse_index")


SPARSE_DATA_DIR = os.path.join(os.getenv("DATA_DIR", "data"), "sparse")


# ---------------------------------------------------------------------------
# Index class
# ---------------------------------------------------------------------------

class SparseIndex:
    """Per-domain SPLADE-v3 inverted index.

    Each entry in the on-disk JSONL has the schema:

    .. code:: json

        {"id": "<chunk_id>", "tenant_id": "<tenant>", "v": {"<token_id>": <float>, ...}}

    ``v`` uses string keys because JSON has no integer-keyed objects;
    we cast back to ``int`` on load.
    """

    def __init__(self, domain: str, data_dir: str = SPARSE_DATA_DIR) -> None:
        self.domain = domain
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._corpus_file = self.data_dir / f"{domain}.jsonl"

        # doc_id → sparse vector
        self._docs: dict[str, dict[int, float]] = {}
        # token_id → list of (doc_id, weight) — the inverted index proper
        self._postings: dict[int, list[tuple[str, float]]] = defaultdict(list)
        # doc_id → tenant
        self._doc_tenant: dict[str, str] = {}

        self._load()

    # -- ingest --------------------------------------------------------------

    def add_documents(
        self,
        chunk_ids: list[str],
        texts: list[str],
        tenant_id: str | None = None,
    ) -> int:
        """Encode + index documents. Skips duplicates. Returns count added.

        Mirrors :meth:`core.retrieval.bm25.BM25Index.add_documents`. If
        the sparse encoder isn't available (model missing, flag off),
        returns ``0`` without touching disk — keeps the ingest path
        zero-cost when sparse is disabled.
        """
        if not _sparse.is_available():
            return 0
        if not chunk_ids:
            return 0

        tenant = tenant_id if tenant_id is not None else config.DEFAULT_TENANT_ID

        # Filter to new + non-empty texts before encoding so we don't pay
        # the model cost for known dupes.
        new_ids: list[str] = []
        new_texts: list[str] = []
        for cid, text in zip(chunk_ids, texts):
            if cid in self._docs:
                continue
            if not text or not text.strip():
                continue
            new_ids.append(cid)
            new_texts.append(text)
        if not new_ids:
            return 0

        try:
            vectors = _sparse.encode_batch(new_texts)
        except Exception as exc:  # noqa: BLE001 - encoder failure must not break ingest
            log_swallowed_error("core.retrieval.sparse_index.encode_batch", exc)
            sentry_sdk.capture_exception()
            return 0

        entries: list[dict] = []
        added = 0
        for cid, vec in zip(new_ids, vectors):
            if not vec:
                # All-zero output (rare) — skip; otherwise the inverted
                # index would carry empty postings and waste storage.
                continue
            self._docs[cid] = vec
            self._doc_tenant[cid] = tenant
            for tid, weight in vec.items():
                self._postings[tid].append((cid, weight))
            entries.append({
                "id": cid,
                "tenant_id": tenant,
                "v": {str(tid): weight for tid, weight in vec.items()},
            })
            added += 1

        if entries:
            self._append_to_disk(entries)
        return added

    def remove_documents(self, chunk_ids: list[str]) -> int:
        """Remove documents by chunk_id from the index and the on-disk
        corpus. Returns the count removed.

        Used on re-ingest: an edited artifact reuses its chunk_ids, so
        ``add_documents`` dedup-skips them and the sparse index would keep
        serving the PRE-edit vectors while ChromaDB holds the new text.
        Removing first lets the caller re-encode the fresh text.
        """
        remove_set = {c for c in chunk_ids if c in self._docs}
        if not remove_set:
            return 0
        for cid in remove_set:
            self._docs.pop(cid, None)
            self._doc_tenant.pop(cid, None)
        # Rebuild the inverted index from the surviving docs — simpler and
        # less error-prone than surgically filtering every posting list.
        self._postings = defaultdict(list)
        for cid, vec in self._docs.items():
            for tid, weight in vec.items():
                self._postings[tid].append((cid, weight))
        self._rewrite_disk()
        return len(remove_set)

    # -- search --------------------------------------------------------------

    def search(
        self,
        query: str,
        top_k: int = 10,
        tenant_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """Sparse dot-product search. Returns ``(doc_id, normalized_score)``.

        Identical contract to :meth:`core.retrieval.bm25.BM25Index.search`
        — normalized to ``[0, 1]`` by the top hit, tenant-scoped at the
        index layer when ``tenant_id`` is supplied.
        """
        if not self._docs:
            return []
        try:
            q_vec = _sparse.encode_text(query)
        except Exception as exc:  # noqa: BLE001 - encoder failure returns empty
            log_swallowed_error("core.retrieval.sparse_index.encode_query", exc)
            return []
        if not q_vec:
            return []

        # Iterate the query's tokens against the inverted index. This
        # is the standard "match-on-shared-token" sparse search loop
        # and stays O(sum(|posting_lists|)) which is small because
        # encode-time top-k pruning caps query weight count at ~256.
        scores: dict[str, float] = defaultdict(float)
        for tid, q_weight in q_vec.items():
            postings = self._postings.get(tid)
            if not postings:
                continue
            for doc_id, doc_weight in postings:
                scores[doc_id] += q_weight * doc_weight

        if not scores:
            return []

        ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
        # Tenant filter + normalization. Overfetch when scoping is on so
        # we still return ~top_k after cross-tenant trim, matching BM25.
        fetch_k = top_k * 4 if tenant_id is not None else top_k
        max_score = ranked[0][1] if ranked else 0.0
        if max_score <= 0:
            return []

        output: list[tuple[str, float]] = []
        for doc_id, score in ranked[:fetch_k]:
            if tenant_id is not None:
                if self._doc_tenant.get(doc_id, config.DEFAULT_TENANT_ID) != tenant_id:
                    continue
            output.append((doc_id, round(score / max_score, 4)))
            if len(output) >= top_k:
                break
        return output

    @property
    def size(self) -> int:
        return len(self._docs)

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        if not self._corpus_file.exists():
            return
        try:
            with open(self._corpus_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                    except json.JSONDecodeError as parse_exc:
                        # Match BM25's corrupted-line tolerance: skip + log
                        # rather than aborting the whole index, so a single
                        # truncated entry from a SIGKILL doesn't lose the
                        # whole corpus.
                        log_swallowed_error(
                            "core.retrieval.sparse_index.parse_line", parse_exc,
                        )
                        continue
                    cid = entry.get("id")
                    raw_vec = entry.get("v") or {}
                    if not cid or not raw_vec:
                        continue
                    if cid in self._docs:
                        continue
                    vec = {int(tid): float(w) for tid, w in raw_vec.items()}
                    self._docs[cid] = vec
                    self._doc_tenant[cid] = entry.get(
                        "tenant_id", config.DEFAULT_TENANT_ID,
                    )
                    for tid, w in vec.items():
                        self._postings[tid].append((cid, w))
            if self._docs:
                logger.info(
                    "sparse.index_loaded",
                    extra={"domain": self.domain, "docs": len(self._docs)},
                )
        except Exception:
            logger.exception("sparse.index_load_failed", extra={"domain": self.domain})
            sentry_sdk.capture_exception()
            self._docs = {}
            self._postings = defaultdict(list)
            self._doc_tenant = {}

    def _append_to_disk(self, entries: list[dict]) -> None:
        try:
            with open(self._corpus_file, "a") as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError as fsync_exc:
                    log_swallowed_error(
                        "core.retrieval.sparse_index.fsync", fsync_exc,
                    )
        except Exception:
            logger.exception(
                "sparse.persist_failed", extra={"domain": self.domain},
            )
            sentry_sdk.capture_exception()

    def _rewrite_disk(self) -> None:
        """Atomically rewrite the JSONL corpus from current in-memory state
        (temp file + ``os.replace``). The append path can't remove lines.
        """
        try:
            tmp = self._corpus_file.with_suffix(".jsonl.tmp")
            with open(tmp, "w") as f:
                for cid, vec in self._docs.items():
                    entry = {
                        "id": cid,
                        "tenant_id": self._doc_tenant.get(
                            cid, config.DEFAULT_TENANT_ID
                        ),
                        "v": {str(tid): w for tid, w in vec.items()},
                    }
                    f.write(json.dumps(entry) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError as fsync_exc:
                    log_swallowed_error(
                        "core.retrieval.sparse_index.fsync", fsync_exc,
                    )
            os.replace(tmp, self._corpus_file)
        except Exception:
            logger.exception(
                "sparse.rewrite_failed", extra={"domain": self.domain},
            )
            sentry_sdk.capture_exception()


# ---------------------------------------------------------------------------
# Module-level index cache
# ---------------------------------------------------------------------------

_indexes: dict[str, SparseIndex] = {}
# Guards the lazy-construction TOCTOU on `_indexes`. Two concurrent
# ingest calls for the same new domain (e.g., processor queue +
# file-watcher event) could both observe `domain not in _indexes`
# and each construct a SparseIndex; the second write would orphan the
# first index along with any documents it had already accepted.
_indexes_lock = threading.Lock()


def get_index(domain: str) -> SparseIndex:
    """Get or create the SparseIndex for the given domain."""
    if domain in _indexes:
        return _indexes[domain]
    with _indexes_lock:
        if domain not in _indexes:
            _indexes[domain] = SparseIndex(domain, SPARSE_DATA_DIR)
    return _indexes[domain]


def index_chunks(
    domain: str,
    chunk_ids: list[str],
    texts: list[str],
    tenant_id: str | None = None,
) -> int:
    """Index chunks for sparse search. Called from the ingestion pipeline.

    No-op (returns 0) when the encoder is unavailable so callers can
    invoke it unconditionally without branching themselves.
    """
    if not _sparse.is_available():
        return 0
    idx = get_index(domain)
    return idx.add_documents(chunk_ids, texts, tenant_id=tenant_id)


def remove_chunks(domain: str, chunk_ids: list[str]) -> int:
    """Remove chunks from a domain's sparse index (in-memory + disk).

    Called on re-ingest before re-adding an edited artifact's chunks.
    No-op when the encoder is unavailable.
    """
    if not _sparse.is_available():
        return 0
    idx = get_index(domain)
    return idx.remove_documents(chunk_ids)


def search_sparse(
    domain: str,
    query: str,
    top_k: int = 10,
    tenant_id: str | None = None,
) -> list[tuple[str, float]]:
    """Search the domain's sparse index. Returns ``(doc_id, normalized_score)``.

    Mirrors :func:`core.retrieval.bm25.search_bm25` but skips the
    deprecation warning — sparse retrieval was tenant-scoped from
    day one.
    """
    if not _sparse.is_available():
        return []
    idx = get_index(domain)
    return idx.search(query, top_k=top_k, tenant_id=tenant_id)


def rebuild_all() -> int:
    """Reload every domain's sparse index from disk.

    Mirrors :func:`core.retrieval.bm25.rebuild_all`. Used by the
    file-watcher / sync flow when new corpus files appear on disk.
    """
    rebuilt = 0
    for domain in config.DOMAINS:
        if domain in _indexes:
            idx = _indexes[domain]
            idx._docs.clear()
            idx._postings.clear()
            idx._doc_tenant.clear()
            idx._load()
        else:
            _indexes[domain] = SparseIndex(domain, SPARSE_DATA_DIR)
        rebuilt += 1
    return rebuilt


def is_available() -> bool:
    """Convenience re-export of :func:`core.retrieval.sparse.is_available`."""
    return _sparse.is_available()


def reset_for_test() -> None:
    """Drop the cached indexes — used by unit tests."""
    _indexes.clear()
