"""
BM25 keyword index management for hybrid search (Phase 4B.1).

Maintains per-domain BM25 indexes alongside ChromaDB vector stores.
Indexes are persisted as JSONL corpus files and rebuilt on load.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import config

logger = logging.getLogger("ai-companion.bm25")

# Lazy import to allow graceful degradation
_bm25_available = True
try:
    from rank_bm25 import BM25Okapi
except ImportError:
    _bm25_available = False
    logger.warning("rank_bm25 not installed — BM25 hybrid search disabled")


def _tokenize(text: str) -> List[str]:
    """Simple whitespace tokenizer with lowercasing and punctuation removal."""
    text = re.sub(r"[^\w\s]", " ", text.lower())
    return [t for t in text.split() if len(t) > 1]


class BM25Index:
    """
    Per-domain BM25 index backed by a JSONL corpus file.

    The corpus (tokenized docs + chunk IDs) is persisted to disk.
    The BM25 object is rebuilt from the corpus on load (fast for <100k docs).
    """

    def __init__(self, domain: str, data_dir: str = "data/bm25"):
        self.domain = domain
        self.data_dir = Path(data_dir)
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self._corpus_file = self.data_dir / f"{domain}.jsonl"

        self._corpus: List[List[str]] = []
        self._doc_ids: List[str] = []
        self._doc_id_set: set = set()
        self._bm25: Optional[Any] = None  # BM25Okapi when available

        self._load()

    def add_documents(self, chunk_ids: List[str], texts: List[str]) -> int:
        """Add documents to the index. Skips duplicates. Returns count added."""
        if not _bm25_available:
            return 0

        new_entries: List[Dict] = []
        for chunk_id, text in zip(chunk_ids, texts):
            if chunk_id in self._doc_id_set:
                continue
            tokens = _tokenize(text)
            if not tokens:
                continue
            self._corpus.append(tokens)
            self._doc_ids.append(chunk_id)
            self._doc_id_set.add(chunk_id)
            new_entries.append({"id": chunk_id, "tokens": tokens})

        if new_entries:
            self._rebuild()
            self._append_to_disk(new_entries)

        return len(new_entries)

    def search(self, query: str, top_k: int = 10) -> List[Tuple[str, float]]:
        """
        Search the index. Returns (chunk_id, normalized_score) tuples.
        Scores are normalized to [0, 1] by dividing by the max score.
        """
        if not _bm25_available or self._bm25 is None or not self._corpus:
            return []

        tokens = _tokenize(query)
        if not tokens:
            return []

        scores = self._bm25.get_scores(tokens)

        max_score = max(scores) if len(scores) > 0 else 0
        if max_score <= 0:
            return []

        indexed = [(i, s / max_score) for i, s in enumerate(scores) if s > 0]
        indexed.sort(key=lambda x: x[1], reverse=True)

        return [
            (self._doc_ids[idx], round(norm, 4))
            for idx, norm in indexed[:top_k]
        ]

    @property
    def size(self) -> int:
        return len(self._doc_ids)

    def _rebuild(self) -> None:
        if self._corpus and _bm25_available:
            self._bm25 = BM25Okapi(self._corpus)
        else:
            self._bm25 = None

    def _load(self) -> None:
        if not self._corpus_file.exists():
            return
        try:
            with open(self._corpus_file, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    chunk_id = entry["id"]
                    if chunk_id not in self._doc_id_set:
                        self._corpus.append(entry["tokens"])
                        self._doc_ids.append(chunk_id)
                        self._doc_id_set.add(chunk_id)
            self._rebuild()
            if self._doc_ids:
                logger.info(
                    f"BM25 index loaded for {self.domain}: {len(self._doc_ids)} docs"
                )
        except Exception as e:
            logger.error(f"Failed to load BM25 index for {self.domain}: {e}")
            self._corpus = []
            self._doc_ids = []
            self._doc_id_set = set()
            self._bm25 = None

    def _append_to_disk(self, entries: List[Dict]) -> None:
        try:
            with open(self._corpus_file, "a") as f:
                for entry in entries:
                    f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.error(f"Failed to persist BM25 entries for {self.domain}: {e}")


# ---------------------------------------------------------------------------
# Module-level index cache
# ---------------------------------------------------------------------------

_indexes: Dict[str, BM25Index] = {}


def get_index(domain: str) -> BM25Index:
    """Get or create a BM25 index for the given domain."""
    if domain not in _indexes:
        _indexes[domain] = BM25Index(domain, config.BM25_DATA_DIR)
    return _indexes[domain]


def index_chunks(domain: str, chunk_ids: List[str], texts: List[str]) -> int:
    """Index chunks for BM25 search. Called during ingestion."""
    idx = get_index(domain)
    return idx.add_documents(chunk_ids, texts)


def search_bm25(
    domain: str, query: str, top_k: int = 10
) -> List[Tuple[str, float]]:
    """Search a domain's BM25 index. Returns (chunk_id, score) tuples."""
    idx = get_index(domain)
    return idx.search(query, top_k)


def is_available() -> bool:
    """Check if BM25 is available (rank_bm25 installed)."""
    return _bm25_available
