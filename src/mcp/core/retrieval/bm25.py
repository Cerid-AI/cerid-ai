# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

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
import threading
import time
from pathlib import Path
from typing import Any

import sentry_sdk

import config
from config.constants import BM25_REBUILD_DEBOUNCE_SECONDS
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

        # Serializes corpus mutations (add/remove) against the rebuild so a
        # lazy rebuild triggered on a search thread never observes a
        # half-mutated corpus. The retrieve itself runs lock-free off the
        # atomically-published ``_snapshot``.
        self._lock = threading.Lock()
        self._reset_index_state()

        self._load()

    def _reset_index_state(self) -> None:
        """Reset the retriever + rebuild bookkeeping to the empty state."""
        # ``_snapshot`` is the (retriever, indexed_ids) pair the last rebuild
        # published, assigned as one tuple so a lock-free search never pairs a
        # retriever with the wrong id list. Search maps retriever result
        # indices through ``indexed_ids`` — NOT the live ``_doc_ids``, which
        # may have drifted ahead of the retriever between debounced rebuilds.
        self._snapshot: tuple[Any | None, tuple[str, ...]] = (None, ())
        # Set by add/remove; a query rebuilds when dirty and the debounce
        # cooldown has elapsed (or no retriever exists yet).
        self._dirty: bool = False
        # ids removed since the last rebuild — their entries still occupy a
        # slot in the current retriever snapshot and must be filtered out of
        # results until the next rebuild drops them for real.
        self._stale_ids: set[str] = set()
        self._last_rebuild: float = 0.0

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

        The (whole-corpus) BM25 rebuild is deferred off this ingest path:
        new docs are persisted durably and the index is marked dirty; the
        next eligible :meth:`search` rebuilds. New chunks become searchable
        within ``BM25_REBUILD_DEBOUNCE_SECONDS`` (see :meth:`_maybe_rebuild`).
        """
        if not _bm25s_available:
            return 0

        tenant = tenant_id if tenant_id is not None else config.DEFAULT_TENANT_ID

        with self._lock:
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
                new_entries.append(
                    {"id": chunk_id, "text": text, "tenant_id": tenant}
                )

            if new_entries:
                self._dirty = True
                self._append_to_disk(new_entries)

        return len(new_entries)

    def remove_documents(self, chunk_ids: list[str]) -> int:
        """Remove documents by chunk_id from the index and the on-disk
        corpus. Returns the count removed.

        Used on re-ingest: an edited artifact keeps its chunk_ids, so
        ``add_documents`` dedup-skips them and the keyword index would
        otherwise keep serving the PRE-edit text while ChromaDB holds the
        new text. Removing first lets the caller re-add the fresh text.

        The rebuild is deferred (as with :meth:`add_documents`): the removed
        ids are tombstoned in ``_stale_ids`` so :meth:`search` filters their
        still-indexed entries out immediately, and the next eligible search
        drops them from the retriever for real.
        """
        with self._lock:
            remove_set = {c for c in chunk_ids if c in self._doc_id_set}
            if not remove_set:
                return 0

            kept_texts: list[str] = []
            kept_ids: list[str] = []
            for cid, text in zip(self._doc_ids, self._texts):
                if cid in remove_set:
                    continue
                kept_texts.append(text)
                kept_ids.append(cid)

            self._texts = kept_texts
            self._doc_ids = kept_ids
            self._doc_id_set = set(kept_ids)
            for cid in remove_set:
                self._doc_tenant.pop(cid, None)

            self._stale_ids.update(remove_set)
            self._dirty = True
            self._rewrite_disk()
        return len(remove_set)

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
        are returned and the caller applies its own tenant post-filter
        (:func:`core.context.identity.chunk_matches_tenant` on the
        BM25-only fallback path in ``query_agent``).
        """
        if not _bm25s_available:
            return []

        # Rebuild the retriever from the live corpus if ingest has moved it
        # ahead of the last snapshot (deferred off the ingest path).
        self._maybe_rebuild()
        retriever, indexed_ids = self._snapshot
        if retriever is None or not indexed_ids:
            return []

        query_tokens = bm25s.tokenize(
            query, stopwords="en", stemmer=_stemmer, return_ids=False
        )
        if query_tokens is None or len(query_tokens) == 0:
            return []
        # Check if query produced any actual tokens
        if len(query_tokens[0]) == 0:
            return []

        # Over-fetch when a post-filter is active — tenant scoping, or
        # tombstoned removals still present in the snapshot — so we still
        # return ~top_k after trimming.
        overfetch = tenant_id is not None or bool(self._stale_ids)
        fetch_k = min(top_k * 4 if overfetch else top_k, len(indexed_ids))
        results, scores = retriever.retrieve(query_tokens, k=fetch_k)

        # results shape: (1, k) - indices into the snapshot corpus
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
            chunk_id = indexed_ids[idx]
            # Removed-since-rebuild ids still occupy a slot in the snapshot;
            # honor the removal immediately by skipping them.
            if chunk_id in self._stale_ids:
                continue
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

    def _maybe_rebuild(self) -> None:
        """Rebuild the retriever from the live corpus if it has drifted.

        Called on the read (search) path. Rebuilds when the corpus is dirty
        AND either no retriever exists yet or the debounce cooldown
        (:data:`BM25_REBUILD_DEBOUNCE_SECONDS`) has elapsed since the last
        rebuild. This coalesces a burst of ingest ``add_documents`` calls
        into at most one rebuild per cooldown window instead of one
        whole-corpus rebuild per document. The double-checked ``_dirty``
        flag keeps the steady-state (clean) search path lock-free.
        """
        if not self._dirty:
            return
        retriever = self._snapshot[0]
        if retriever is not None and (
            time.monotonic() - self._last_rebuild
        ) < BM25_REBUILD_DEBOUNCE_SECONDS:
            return
        with self._lock:
            if not self._dirty:
                return
            retriever = self._snapshot[0]
            if retriever is not None and (
                time.monotonic() - self._last_rebuild
            ) < BM25_REBUILD_DEBOUNCE_SECONDS:
                return
            self._rebuild_locked()

    def _rebuild_locked(self) -> None:
        """Tokenize the whole live corpus and publish a fresh retriever.

        Caller must hold ``self._lock`` (or run single-threaded, as during
        construction / :meth:`_load`). ``_snapshot`` is published as one
        atomic assignment so a concurrent lock-free search never sees a
        retriever paired with the wrong id list.
        """
        if not self._texts or not _bm25s_available:
            self._snapshot = (None, ())
            self._stale_ids.clear()
            self._dirty = False
            self._last_rebuild = time.monotonic()
            return

        corpus_tokens = bm25s.tokenize(
            self._texts, stopwords="en", stemmer=_stemmer
        )
        retriever = bm25s.BM25()
        retriever.index(corpus_tokens)
        # Snapshot the id ordering the retriever was built against; search
        # maps retriever result indices through this, not the live _doc_ids.
        self._snapshot = (retriever, tuple(self._doc_ids))
        self._stale_ids.clear()
        self._dirty = False
        self._last_rebuild = time.monotonic()

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

            self._rebuild_locked()
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
            self._reset_index_state()

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

    def _rewrite_disk(self) -> None:
        """Rewrite the whole JSONL corpus from current in-memory state,
        atomically (temp file + ``os.replace``). The normal write path is
        append-only; removal needs a full rewrite to drop stale lines.
        """
        try:
            tmp = self._corpus_file.with_suffix(".jsonl.tmp")
            with open(tmp, "w") as f:
                for cid, text in zip(self._doc_ids, self._texts):
                    entry = {
                        "id": cid,
                        "text": text,
                        "tenant_id": self._doc_tenant.get(
                            cid, config.DEFAULT_TENANT_ID
                        ),
                    }
                    f.write(json.dumps(entry) + "\n")
                f.flush()
                try:
                    os.fsync(f.fileno())
                except OSError as fsync_exc:
                    log_swallowed_error("core.retrieval.bm25.fsync", fsync_exc)
            os.replace(tmp, self._corpus_file)
        except Exception:
            logger.exception("bm25.rewrite_failed", extra={"domain": self.domain})
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


def remove_chunks(domain: str, chunk_ids: list[str]) -> int:
    """Remove chunks from a domain's BM25 index (in-memory + disk).

    Called on re-ingest before re-adding an edited artifact's chunks so the
    keyword index never serves stale pre-edit text. No-op when BM25 is
    unavailable or the ids aren't present.
    """
    idx = get_index(domain)
    return idx.remove_documents(chunk_ids)


def search_bm25(
    domain: str,
    query: str,
    top_k: int = 10,
    tenant_id: str | None = None,
) -> list[tuple[str, float]]:
    """Search a domain's BM25 index. Returns (chunk_id, score) tuples.

    ``tenant_id`` scopes results at the index layer. When omitted, all
    tenants are returned and the caller applies its own tenant post-filter
    (:func:`core.context.identity.chunk_matches_tenant` on the BM25-only
    fallback path in ``query_agent``). Both call styles are supported —
    the Workstream E Phase 0.5 deprecation shim that nagged on every
    hot-path query has been retired.
    """
    idx = get_index(domain)
    return idx.search(query, top_k, tenant_id=tenant_id)


def rebuild_all() -> int:
    """Reload all BM25 indexes from disk (including newly synced domains)."""
    rebuilt = 0
    for domain in config.DOMAINS:
        if domain in _indexes:
            idx = _indexes[domain]
            with idx._lock:
                idx._texts.clear()
                idx._doc_ids.clear()
                idx._doc_id_set.clear()
                idx._doc_tenant.clear()
                idx._reset_index_state()
                idx._load()
        else:
            _indexes[domain] = BM25Index(domain, config.BM25_DATA_DIR)
        rebuilt += 1
    return rebuilt


def is_available() -> bool:
    """Check if BM25 is available (bm25s installed)."""
    return _bm25s_available
