# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""
BM25 keyword index management for hybrid search.

Maintains per-domain BM25 indexes alongside ChromaDB vector stores.
Indexes are persisted as JSONL corpus files and rebuilt with bm25s.
Uses PyStemmer for English stemming and built-in stopword removal.
"""

from __future__ import annotations

import json
import logging
import os
import warnings
from pathlib import Path
from typing import Any

import sentry_sdk

import config
from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.bm25")

# Lazy import to allow graceful degradation
_bm25s_available = True
try:
    import bm25s
    import Stemmer

    _stemmer = Stemmer.Stemmer("english")
except ImportError:
    _bm25s_available = False
    logger.warning("bm25s/PyStemmer not installed — BM25 hybrid search disabled")


def _tokenize(text: str) -> list[str]:
    """Tokenize with stemming and stopword removal via bm25s."""
    if not _bm25s_available:
        return []
    tokens = bm25s.tokenize(text, stopwords="en", stemmer=_stemmer, return_ids=False)
    if tokens is None or len(tokens) == 0:
        return []
    # bm25s.tokenize returns a list of token lists (one per input text)
    return [str(t) for t in tokens[0] if t]


class BM25Index:
    """
    Per-domain BM25 index backed by a JSONL corpus file.

    The corpus (raw texts + chunk IDs) is persisted to disk as JSONL.
    The bm25s retriever is rebuilt from the corpus on load.
    Supports migration from old format (pre-tokenized) to new format (raw text).
    """

    def __init__(self, domain: str, data_dir: str = "data/bm25"):
        self.domain = domain
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._corpus_file = self.data_dir / f"{domain}.jsonl"

        self._texts: list[str] = []
        self._doc_ids: list[str] = []
        self._doc_id_set: set = set()
        # Workstream E Phase 0: tenant-scoped BM25 search. Each chunk_id
        # carries its tenant_id so search(tenant_id=...) can post-filter at
        # the index layer instead of relying on the caller-side post-filter.
        self._doc_tenant: dict[str, str] = {}
        self._retriever: Any | None = None

        self._load()

    def add_documents(
        self,
        chunk_ids: list[str],
        texts: list[str],
        tenant_id: str | None = None,
    ) -> int:
        """Add documents to the index. Skips duplicates. Returns count added.

        ``tenant_id`` (Workstream E Phase 0) is stamped on each document so
        :meth:`search` can scope results at the index layer. Defaults to
        ``config.DEFAULT_TENANT_ID`` for backward compatibility with callers
        that don't yet pass tenant.
        """
        if not _bm25s_available:
            return 0

        tenant = tenant_id if tenant_id is not None else config.DEFAULT_TENANT_ID

        new_entries: list[dict] = []
        for chunk_id, text in zip(chunk_ids, texts):
            if chunk_id in self._doc_id_set:
                continue
            if not text or not text.strip():
                continue
            self._texts.append(text)
            self._doc_ids.append(chunk_id)
            self._doc_id_set.add(chunk_id)
            self._doc_tenant[chunk_id] = tenant
            new_entries.append({"id": chunk_id, "text": text, "tenant_id": tenant})

        if new_entries:
            self._rebuild()
            self._append_to_disk(new_entries)

        return len(new_entries)

    def search(
        self,
        query: str,
        top_k: int = 10,
        tenant_id: str | None = None,
    ) -> list[tuple[str, float]]:
        """
        Search the index. Returns (chunk_id, normalized_score) tuples.
        Scores are normalized to [0, 1] by dividing by the max score.

        When ``tenant_id`` is provided (Workstream E Phase 0), results are
        filtered at the index layer to match. When ``None``, all tenants
        are returned and the caller is expected to apply
        :func:`core.context.identity.chunk_matches_tenant` (deprecated path).
        """
        if not _bm25s_available or self._retriever is None or not self._texts:
            return []

        query_tokens = bm25s.tokenize(
            query, stopwords="en", stemmer=_stemmer, return_ids=False
        )
        if query_tokens is None or len(query_tokens) == 0:
            return []
        # Check if query produced any actual tokens
        if len(query_tokens[0]) == 0:
            return []

        # Over-fetch slightly when tenant filtering is on so we still return
        # ~top_k matches after the post-filter trims cross-tenant hits.
        fetch_k = min(top_k * 4 if tenant_id is not None else top_k, len(self._texts))
        results, scores = self._retriever.retrieve(query_tokens, k=fetch_k)

        # results shape: (1, k) - indices into corpus
        # scores shape: (1, k) - BM25 scores (descending)
        if scores.shape[1] == 0:
            return []

        max_score = float(scores[0, 0])
        if max_score <= 0:
            return []

        output: list[tuple[str, float]] = []
        for i in range(scores.shape[1]):
            score = float(scores[0, i])
            if score <= 0:
                break
            idx = int(results[0, i])
            chunk_id = self._doc_ids[idx]
            if tenant_id is not None:
                doc_tenant = self._doc_tenant.get(chunk_id, config.DEFAULT_TENANT_ID)
                if doc_tenant != tenant_id:
                    continue
            output.append((chunk_id, round(score / max_score, 4)))
            if len(output) >= top_k:
                break

        return output

    @property
    def size(self) -> int:
        return len(self._doc_ids)

    def _rebuild(self) -> None:
        if not self._texts or not _bm25s_available:
            self._retriever = None
            return

        corpus_tokens = bm25s.tokenize(
            self._texts, stopwords="en", stemmer=_stemmer
        )
        retriever = bm25s.BM25()
        retriever.index(corpus_tokens)
        self._retriever = retriever

    def _load(self) -> None:
        if not self._corpus_file.exists():
            return
        migrated = False
        try:
            with open(self._corpus_file) as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    chunk_id = entry["id"]
                    if chunk_id in self._doc_id_set:
                        continue

                    # New format: raw text; old format: pre-tokenized list
                    if "text" in entry:
                        text = entry["text"]
                    elif "tokens" in entry:
                        text = " ".join(entry["tokens"])
                        migrated = True
                    else:
                        continue

                    self._texts.append(text)
                    self._doc_ids.append(chunk_id)
                    self._doc_id_set.add(chunk_id)
                    # Workstream E Phase 0: pre-tenant corpus entries default
                    # to DEFAULT_TENANT_ID for backward compat. New ingest
                    # writes always carry an explicit tenant_id field.
                    self._doc_tenant[chunk_id] = entry.get(
                        "tenant_id", config.DEFAULT_TENANT_ID,
                    )

            self._rebuild()
            if self._doc_ids:
                logger.info(
                    f"BM25 index loaded for {self.domain}: {len(self._doc_ids)} docs"
                )
            if migrated:
                logger.warning(
                    f"BM25 corpus for {self.domain} uses old token format. "
                    "Consider re-ingesting for improved tokenization."
                )
        except Exception:
            logger.exception("bm25.index_load_failed", extra={"domain": self.domain})
            sentry_sdk.capture_exception()
            self._texts = []
            self._doc_ids = []
            self._doc_id_set = set()
            self._doc_tenant = {}
            self._retriever = None

    def _append_to_disk(self, entries: list[dict]) -> None:
        try:
            with open(self._corpus_file, "a") as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")
                # Workstream E Phase 0: explicit flush+fsync to close the
                # crash-window where a kill -9 between append and OS flush
                # left the BM25 corpus drifted ahead of ChromaDB. fsync
                # gets its own try/except because it can fail spuriously
                # on macOS under heavy I/O without invalidating the write.
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError as fsync_exc:
                    log_swallowed_error("core.retrieval.bm25.fsync", fsync_exc)
        except Exception:
            logger.exception("bm25.persist_failed", extra={"domain": self.domain})
            sentry_sdk.capture_exception()


# ---------------------------------------------------------------------------
# Module-level index cache
# ---------------------------------------------------------------------------

_indexes: dict[str, BM25Index] = {}


def get_index(domain: str) -> BM25Index:
    """Get or create a BM25 index for the given domain."""
    if domain not in _indexes:
        _indexes[domain] = BM25Index(domain, config.BM25_DATA_DIR)
    return _indexes[domain]


def index_chunks(
    domain: str,
    chunk_ids: list[str],
    texts: list[str],
    tenant_id: str | None = None,
) -> int:
    """Index chunks for BM25 search. Called during ingestion.

    ``tenant_id`` (Workstream E Phase 0) is forwarded to
    :meth:`BM25Index.add_documents`. None defaults to
    ``config.DEFAULT_TENANT_ID``.
    """
    idx = get_index(domain)
    return idx.add_documents(chunk_ids, texts, tenant_id=tenant_id)


def search_bm25(
    domain: str,
    query: str,
    top_k: int = 10,
    tenant_id: str | None = None,
) -> list[tuple[str, float]]:
    """Search a domain's BM25 index. Returns (chunk_id, score) tuples.

    ``tenant_id`` (Workstream E Phase 0) scopes results at the index
    layer. Calling without ``tenant_id`` emits a :class:`DeprecationWarning`
    — the index-layer filter is the canonical path; callers should migrate
    off the post-filter at ``query_agent.py`` (chunk_matches_tenant).
    """
    if tenant_id is None:
        warnings.warn(
            "search_bm25 called without tenant_id; tenant scoping will be "
            "enforced at the caller layer via chunk_matches_tenant. "
            "Pass tenant_id to scope at the BM25 index instead. This "
            "deprecation will be removed after Workstream E Phase 0.5.",
            DeprecationWarning,
            stacklevel=2,
        )
    idx = get_index(domain)
    return idx.search(query, top_k, tenant_id=tenant_id)


def rebuild_all() -> int:
    """Reload all BM25 indexes from disk (including newly synced domains)."""
    rebuilt = 0
    for domain in config.DOMAINS:
        if domain in _indexes:
            idx = _indexes[domain]
            idx._texts.clear()
            idx._doc_ids.clear()
            idx._doc_id_set.clear()
            idx._doc_tenant.clear()
            idx._retriever = None
            idx._load()
        else:
            _indexes[domain] = BM25Index(domain, config.BM25_DATA_DIR)
        rebuilt += 1
    return rebuilt


def is_available() -> bool:
    """Check if BM25 is available (bm25s installed)."""
    return _bm25s_available
