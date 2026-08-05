# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""CL-7 / AF-012: rehydrate operator-created ``:Domain`` nodes into runtime
config at boot.

``POST /taxonomy/domain`` writes a ``:Domain`` node to Neo4j AND mutates the
in-memory ``config.TAXONOMY`` / ``config.DOMAINS``, but nothing ever read those
nodes back at startup — so after a restart the custom domain vanished from
``config.DOMAINS``. Its live consumers then diverged: new ingests clamped to
``general`` (``ingestion.py``), ``clear_domain`` 404'd (``kb_admin`` checks
``domain in config.DOMAINS``), and ``reembed`` skipped it — leaving the
artifacts and their Chroma collection unreachable by any admin endpoint.

Running this at boot restores those domains into ``config`` before the live
readers use it. Add-only: a persisted domain is merged only if it is not already
in the static taxonomy; statically-configured domains are never removed.

Note (AF-033, deferred): sites that bind ``DOMAINS`` at import time (the
decomposer, main-pre-warm) still snapshot the pre-rehydration set for the current
process; converting those to live ``config.DOMAINS`` reads is a follow-up. This
module closes the acute cross-restart data-loss (AF-012).
"""
from __future__ import annotations

import logging
from typing import Any

import config

logger = logging.getLogger("ai-companion.startup")


def _read_persisted_domains(driver: Any) -> list[dict[str, Any]]:
    """Read every ``:Domain`` node + its ``:SubCategory`` children from Neo4j."""
    with driver.session() as session:
        rows = list(
            session.run(
                "MATCH (d:Domain) "
                "OPTIONAL MATCH (sc:SubCategory)-[:BELONGS_TO]->(d) "
                "RETURN d.name AS name, d.description AS description, "
                "       d.icon AS icon, collect(sc.name) AS sub_categories "
                "ORDER BY d.name"
            )
        )
    out: list[dict[str, Any]] = []
    for r in rows:
        name = r["name"]
        if not name:
            continue
        out.append(
            {
                "name": name,
                "description": r["description"] or "",
                "icon": r["icon"] or "",
                "sub_categories": [s for s in (r["sub_categories"] or []) if s],
            }
        )
    return out


def merge_persisted_domains(persisted: list[dict[str, Any]]) -> int:
    """Merge persisted domains into ``config.TAXONOMY`` / ``config.DOMAINS``
    (add-only, mirroring the ``/taxonomy/domain`` endpoint's mutation). Returns
    the number of domains newly added. Pure w.r.t. Neo4j — unit-testable."""
    added: list[str] = []
    for d in persisted:
        name = d["name"]
        if name and name not in config.TAXONOMY:
            config.TAXONOMY[name] = {
                "description": d.get("description", ""),
                "icon": d.get("icon", ""),
                "sub_categories": d.get("sub_categories", []),
            }
            added.append(name)
    if added:
        config.DOMAINS = list(config.TAXONOMY.keys())
        logger.info("Rehydrated %d runtime domain(s) from Neo4j: %s", len(added), added)
    return len(added)


def rehydrate_runtime_domains(driver: Any) -> int:
    """Boot entry point — read persisted :Domain nodes and merge them into config.
    Best-effort: a Neo4j read failure is logged and never blocks startup."""
    if driver is None:
        return 0
    try:
        persisted = _read_persisted_domains(driver)
    except Exception as exc:  # noqa: BLE001 — startup rehydration is best-effort
        logger.warning("Domain rehydration read failed (non-fatal): %s", exc)
        return 0
    return merge_persisted_domains(persisted)
