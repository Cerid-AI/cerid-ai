# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Canonical in-memory double for the semantic-cache ``_CacheBackend`` protocol.

**Pinned to the shipped server: chromadb 1.5.9** (`docker-compose.yml`), client
`chromadb>=1,<2` (`requirements.txt`). Behaviours below mirror that version —
update them together when the pin moves.

Why this file exists: three hand-rolled ``_FakeBackend`` clones had diverged to
the point of contradicting each other on the *same* input — one cleared the
collection on an empty ``where``, one raised, one silently did nothing. The
clear-all clone encoded chromadb **0.5** semantics, which let the production
clear-all path throw on every mutation while the tests stayed green (2026-07-29
audit). Fake duplication was the drift engine, so there is one fake now.

Faithful behaviours worth preserving:

* ``delete(where={})`` **raises** ``ValueError`` — 1.x rejects an empty where
  rather than treating it as clear-all. This is the exact divergence that hid a
  production defect.
* ``delete(ids=[...])`` actually removes rows, so orphan-eviction assertions
  are not vacuous.
* ``get()`` returns ``{"ids": [...]}`` — the shape the delete-by-id clear path
  consumes.
"""

from __future__ import annotations

import threading
from typing import Any

import numpy as np


class FakeChromaBackend:
    """Brute-force cosine backend conforming to ``_CacheBackend``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ids: list[str] = []
        self._embs: list[Any] = []
        self._meta: list[dict[str, Any]] = []

    # -- reads ------------------------------------------------------------
    def query(
        self,
        *,
        query_embeddings: list[list[float]],
        n_results: int = 1,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        with self._lock:
            if not self._ids:
                return {"ids": [[]], "distances": [[]], "metadatas": [[]]}
            q = np.asarray(query_embeddings[0], dtype=float)
            qn = q / max(float(np.linalg.norm(q)), 1e-12)
            sims = [
                (
                    eid,
                    float(np.dot(qn, emb / max(float(np.linalg.norm(emb)), 1e-12))),
                    meta,
                )
                for eid, emb, meta in zip(self._ids, self._embs, self._meta)
            ]
            sims.sort(key=lambda t: t[1], reverse=True)
            top = sims[:n_results]
            return {
                "ids": [[t[0] for t in top]],
                "distances": [[1.0 - t[1] for t in top]],
                "metadatas": [[t[2] for t in top]],
            }

    def get(self) -> dict[str, Any]:
        """Mirror the 1.x ``get()`` shape used by the delete-by-id clear path."""
        with self._lock:
            return {"ids": list(self._ids)}

    def count(self) -> int:
        with self._lock:
            return len(self._ids)

    # -- writes -----------------------------------------------------------
    def upsert(
        self,
        *,
        ids: list[str],
        embeddings: list[list[float]],
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        with self._lock:
            for i, eid in enumerate(ids):
                emb = np.asarray(embeddings[i], dtype=float)
                meta = (metadatas or [{}] * len(ids))[i]
                if eid in self._ids:
                    j = self._ids.index(eid)
                    self._embs[j], self._meta[j] = emb, meta
                    continue
                self._ids.append(eid)
                self._embs.append(emb)
                self._meta.append(meta)

    def delete(
        self,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
    ) -> None:
        with self._lock:
            if where is not None and not ids:
                if not where:
                    # chromadb 1.x rejects an empty where. Faking clear-all here
                    # is what hid a production defect for three releases.
                    raise ValueError(
                        "Expected where to have exactly one operator, got {} in delete"
                    )
                self._ids.clear()
                self._embs.clear()
                self._meta.clear()
                return
            for eid in ids or []:
                if eid in self._ids:
                    j = self._ids.index(eid)
                    del self._ids[j]
                    del self._embs[j]
                    del self._meta[j]


class FakeChromaCollection:
    """In-memory double for a chromadb 1.5.9 ``Collection`` as the paging jobs
    use it (``get`` by ``ids``/``where``/``limit``/``offset``/``include``,
    ``update``, ``upsert``, ``delete`` by ids).

    Faithful behaviours:

    * ``get(include=["embeddings"])`` hands back numpy arrays, not lists.
    * ``update(ids=, documents=)`` with no ``embeddings=`` recomputes the vector
      through the collection's bound embedding function — the contract the
      managed re-embed job relies on; ``update(ids=, metadatas=)`` alone touches
      only metadata.
    * ``get`` always returns ``ids``; the other keys follow ``include``.
    """

    def __init__(self, name: str, embedding_function: Any | None = None) -> None:
        self.name = name
        self._ef = embedding_function
        self._ids: list[str] = []
        self._docs: list[str | None] = []
        self._embs: list[Any] = []
        self._meta: list[dict[str, Any]] = []

    def _embed(self, documents: list[str]) -> list[Any]:
        if self._ef is None:
            raise ValueError("collection has no embedding function and no embeddings were given")
        return [np.asarray(v, dtype=float) for v in self._ef(documents)]

    def count(self) -> int:
        return len(self._ids)

    def upsert(
        self,
        *,
        ids: list[str],
        documents: list[str] | None = None,
        embeddings: list[list[float]] | None = None,
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        if embeddings is None:
            embeddings = self._embed(list(documents or []))
        for i, cid in enumerate(ids):
            emb = np.asarray(embeddings[i], dtype=float)
            doc = documents[i] if documents else None
            meta = dict((metadatas or [{}] * len(ids))[i])
            if cid in self._ids:
                j = self._ids.index(cid)
                self._docs[j], self._embs[j], self._meta[j] = doc, emb, meta
                continue
            self._ids.append(cid)
            self._docs.append(doc)
            self._embs.append(emb)
            self._meta.append(meta)

    def get(
        self,
        ids: list[str] | None = None,
        where: dict[str, Any] | None = None,
        limit: int | None = None,
        offset: int = 0,
        include: list[str] | None = None,
    ) -> dict[str, Any]:
        include = list(include) if include is not None else ["documents", "metadatas"]
        if ids is not None:
            rows = [self._ids.index(i) for i in ids if i in self._ids]
        else:
            rows = list(range(len(self._ids)))
            if where:
                rows = [j for j in rows if all(self._meta[j].get(k) == v for k, v in where.items())]
            rows = rows[offset:]
            if limit is not None:
                rows = rows[:limit]
        out: dict[str, Any] = {"ids": [self._ids[j] for j in rows]}
        if "documents" in include:
            out["documents"] = [self._docs[j] for j in rows]
        if "metadatas" in include:
            out["metadatas"] = [dict(self._meta[j]) for j in rows]
        if "embeddings" in include:
            out["embeddings"] = [np.array(self._embs[j]) for j in rows]
        return out

    def update(
        self,
        *,
        ids: list[str],
        documents: list[str] | None = None,
        embeddings: list[list[float]] | None = None,
        metadatas: list[dict[str, Any]] | None = None,
    ) -> None:
        if documents is not None and embeddings is None:
            embeddings = self._embed(documents)
        for i, cid in enumerate(ids):
            j = self._ids.index(cid)
            if documents is not None:
                self._docs[j] = documents[i]
            if embeddings is not None:
                self._embs[j] = np.asarray(embeddings[i], dtype=float)
            if metadatas is not None:
                self._meta[j] = dict(metadatas[i])

    def delete(self, ids: list[str] | None = None) -> None:
        for cid in ids or []:
            if cid in self._ids:
                j = self._ids.index(cid)
                del self._ids[j], self._docs[j], self._embs[j], self._meta[j]

    def embedding_of(self, cid: str) -> Any:
        """Test accessor: the stored vector for one id."""
        return np.array(self._embs[self._ids.index(cid)])

    def metadata_of(self, cid: str) -> dict[str, Any]:
        """Test accessor: the stored metadata for one id."""
        return dict(self._meta[self._ids.index(cid)])


class FakeChromaClient:
    """Client double over named :class:`FakeChromaCollection` instances.

    ``get_collection`` is keyword-only, like ``app.deps._EmbeddingAwareClient``
    (a positional name raised there and left a boot probe silently inert), and
    a missing name raises as chromadb 1.x does.
    """

    def __init__(self, collections: list[FakeChromaCollection]) -> None:
        self._cols = {c.name: c for c in collections}

    def list_collections(self) -> list[FakeChromaCollection]:
        return list(self._cols.values())

    def get_collection(self, **kwargs: Any) -> FakeChromaCollection:
        name = kwargs["name"]
        if name not in self._cols:
            raise ValueError(f"Collection {name} does not exist.")
        return self._cols[name]

    def get_or_create_collection(self, **kwargs: Any) -> FakeChromaCollection:
        name = kwargs["name"]
        if name not in self._cols:
            self._cols[name] = FakeChromaCollection(name)
        return self._cols[name]
