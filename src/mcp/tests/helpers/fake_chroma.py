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
