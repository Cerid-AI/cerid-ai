#!/usr/bin/env python3
"""Targeted mutation testing — does the suite DETECT faults, or just run lines?

Usage::

    make mutation-check          # or: .venv/bin/python scripts/mutation_check.py

Injects a realistic fault into a critical module, runs the relevant tests, and
reports whether anything failed. A **SURVIVED** mutant is a blind spot: the code
changed in a way that matters and the suite stayed green.

Why this exists
---------------
On 2026-07-29 a defect shipped that made every backup silently discard 100% of
the vector store, with 8,000+ tests green. Coverage said the path was tested;
the only test touching it patched the function under test out of existence.
This harness caught that empirically — the first run killed 5/9, and all four
survivors were in ``app/sync/export.py``: the entire backup fix could have been
reverted without a single test failing. After ``test_sync_export_chroma_wire.py``
landed, 9/9.

Why not mutmut
--------------
mutmut 3.x copies the tree to ``mutants/`` and runs pytest in-process, which
collides with this repo's ``src/mcp`` rootdir + ``PYTHONPATH`` convention; it
failed at baseline collection in three configurations. A ``[tool.mutmut]``
section remains in ``pyproject.toml`` if someone wants to retry it.

Extending
---------
Add to ``MUTANTS`` when you fix a defect worth never re-shipping. Prefer faults
that mirror real defect classes (wrong status-code guard, dropped auth prefix,
fail-open gate) over mechanical operator flips — every survivor is then directly
actionable instead of a possible equivalent-mutant false positive.

Add the covering test file to ``TESTS`` too, or the mutant will survive for lack
of selection rather than lack of coverage.

Safety: each file is restored in a ``finally`` block, and the run aborts if the
baseline is red (mutation results against a red baseline are meaningless).
"""
from __future__ import annotations

import fcntl
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
TESTS = [
    "src/mcp/tests/test_sync.py",
    "src/mcp/tests/test_sync_full_surface.py",
    "src/mcp/tests/test_middleware_auth.py",
    "src/mcp/tests/test_mcp_auth_lan_gating.py",
    "src/mcp/tests/test_domain_privacy.py",
    "src/mcp/tests/test_answer_citation_binding.py",
    "src/mcp/tests/test_llm_error_envelope.py",
    "src/mcp/tests/test_fake_backend_fidelity.py",
    "src/mcp/tests/test_sync_export_chroma_wire.py",
    "src/mcp/tests/test_verified_memory_provenance.py",
    "src/mcp/tests/test_claim_type_wire_parity.py",
    "src/mcp/tests/test_graph_map_link_scoping.py",
    "src/mcp/tests/test_usage_metering_wired.py",
    "src/mcp/tests/test_private_mode_redis_failure.py",
    "src/mcp/tests/test_graph_archived_filter.py",
    "src/mcp/tests/test_sync_chroma_roundtrip.py",
]

# (label, file, original, mutated)
MUTANTS: list[tuple[str, str, str, str]] = [
    # --- app/sync/export.py — the silent-data-loss path -------------------
    ("export: treat any HTTP error as 'collection missing' (silent skip)",
     "src/mcp/app/sync/export.py",
     "            if coll_resp.status_code in (400, 404):",
     "            if coll_resp.status_code >= 400:"),
    ("export: drop failed_domains from the result envelope",
     "src/mcp/app/sync/export.py",
     '        "failed_domains": failed_domains,',
     '        "failed_domains": {},'),
    ("export: revert to the retired v1 collections API",
     "src/mcp/app/sync/export.py",
     '                f"{_v2_collections_base(chroma_url)}/{coll_name}",',
     '                f"{chroma_url}/api/v1/collections/{coll_name}",'),
    ("export: stop counting chunks (total_chunks always 0)",
     "src/mcp/app/sync/export.py",
     "        total_chunks += chunk_count",
     "        total_chunks += 0"),

    # --- app/middleware/auth.py — the LAN auth hole ------------------------
    ("auth: exempt /api/ from the API-key check",
     "src/mcp/app/middleware/auth.py",
     'EXEMPT_PREFIXES = ("/health/", "/mcp/", "/auth/", "/a2a/")',
     'EXEMPT_PREFIXES = ("/health/", "/mcp/", "/auth/", "/a2a/", "/api/")'),
    ("auth: treat every bind address as loopback (reopens the LAN hole)",
     "src/mcp/app/middleware/auth.py",
     '    return bind in ("127.0.0.1", "::1", "localhost", "")',
     "    return True"),
    ("auth: accept any non-empty key (skip constant-time compare)",
     "src/mcp/app/middleware/auth.py",
     "        if not provided or not hmac.compare_digest(provided, self.api_key):",
     "        if not provided:"),

    # --- core/utils/llm_client.py — the silent-zero class ------------------
    ("llm: swallow provider error envelopes back into ''",
     "src/mcp/core/utils/llm_client.py",
     "    err = data.get(\"error\")\n    if err:",
     "    err = None\n    if err:"),

    # --- app/mcp_tools/retrieval.py — citation binding --------------------
    ("citations: read the key the envelope does not emit",
     "src/mcp/app/mcp_tools/retrieval.py",
     '        (r.get("content", ""), r) for r in results',
     '        (r.get("text", ""), r) for r in results'),

    # --- core/agents/verified_memory.py — provenance + the dead filter -----
    ("verified_memory: read provenance from the shape production never emits",
     "src/mcp/core/agents/verified_memory.py",
     '                primary_artifact_id = claim_data.get("source_artifact_id", "")',
     '                primary_artifact_id = (claim_data.get("sources") or [{}])[0].get("artifact_id", "")'),
    ("verified_memory: gate meta-claims on the legacy key only (filter goes dead)",
     "src/mcp/core/agents/verified_memory.py",
     '            claim_type = claim_data.get("claim_type") or claim_data.get("type") or "factual"',
     '            claim_type = claim_data.get("type", "factual")'),

    # --- core/agents/hallucination — the emitter half of the same defect ---
    ("streaming: stop stamping claim_type into the promotion input",
     "src/mcp/core/agents/hallucination/streaming.py",
     '            _claim["claim_type"] = _claim_type(str(_claim.get("claim", "")))',
     '            _claim["claim_type"] = "factual"'),
    ("models: drop recency from ClaimType (model cannot hold real traffic)",
     "src/mcp/core/agents/hallucination/models.py",
     '    citation = "citation"\n    recency = "recency"',
     '    citation = "citation"'),

    # --- app/routers/graph.py — the false-orphan cap ----------------------
    ("graph: select links globally, scope them in Python (false orphans)",
     "src/mcp/app/routers/graph.py",
     "        UNWIND $scope_ids AS sid\n        MATCH (a:Entity {canonical_id: sid})-[r:CO_MENTIONED|SIMILAR_TO]->(b:Entity)\n        WHERE b.canonical_id IN $scope_ids\n          AND a.umap_x IS NOT NULL AND b.umap_x IS NOT NULL",
     "        MATCH (a:Entity)-[r:CO_MENTIONED|SIMILAR_TO]->(b:Entity)\n        WHERE a.umap_x IS NOT NULL AND b.umap_x IS NOT NULL"),
    ("graph: hide link-cap saturation from callers",
     "src/mcp/app/routers/graph.py",
     "    truncated = len(edge_rows) >= _EMBEDDINGS_3D_MAX_LINKS",
     "    truncated = False"),
    ("graph: drop the orphan-rescue pass (583 false orphans return)",
     "src/mcp/app/routers/graph.py",
     "    if not missing_ids:\n        return []",
     "    if missing_ids is not None:\n        return []"),

    # --- app/routers/auth.py — the unwired meter --------------------------
    ("auth: claim the usage meter is wired when nothing records to it",
     "src/mcp/app/routers/auth.py",
     "_USAGE_METERING_WIRED = False",
     "_USAGE_METERING_WIRED = True"),

    # --- app/sync/import_.py — the restore half of backup ------------------
    ("import: drop failed_domains (failed restore looks like an empty one)",
     "src/mcp/app/sync/import_.py",
     '        "failed_domains": failed_domains,',
     '        "failed_domains": {},'),
    ("import: revert the restore path to the retired v1 collections API",
     "src/mcp/app/sync/import_.py",
     '                        f"{_v2_collections_base(chroma_url)}/{collection_id}/add",',
     '                        f"{chroma_url}/api/v1/collections/{collection_id}/add",'),
    ("import: silently drop embeddings on restore",
     "src/mcp/app/sync/import_.py",
     "                batch_embs.append(embedding)",
     "                batch_embs.append([])"),

    # --- app/services/private_mode.py — fail-open privacy -----------------
    ("private_mode: fail open to level 0 when Redis errors",
     "src/mcp/app/services/private_mode.py",
     "        return _last_known_level",
     "        return 0"),

    # --- app/routers/graph.py — archived-content leak ---------------------
    ("graph: stop excluding archived artifacts from the timeline track",
     "src/mcp/app/routers/graph.py",
     "        WHERE m.created_at >= $start AND m.created_at <= $end\n          AND coalesce(a.archived, false) = false\n        WITH a, m",
     "        WHERE m.created_at >= $start AND m.created_at <= $end\n        WITH a, m"),
]


def run_tests() -> bool:
    """True when the suite passes."""
    proc = subprocess.run(
        [str(REPO / ".venv/bin/pytest"), "-x", "-q", "-p", "no:randomly", *TESTS],
        cwd=REPO,
        env={"PATH": "/usr/bin:/bin", "PYTHONPATH": "src/mcp", "HOME": str(Path.home())},
        capture_output=True,
        text=True,
        timeout=600,
    )
    return proc.returncode == 0


def main() -> int:
    # This script edits files in the working tree in place. Anything else
    # reading those files while it runs — a parallel pytest, a watch task, the
    # editor's type-checker — sees mutated source and reports failures that
    # have nothing to do with the change under test. Hold an exclusive lock so
    # a second run, or a run started while one is already going, blocks instead
    # of interleaving.
    lock_path = REPO / ".mutation-check.lock"
    with open(lock_path, "w") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            print(
                "another mutation_check.py is already running — refusing to "
                "mutate the same tree twice (results would be meaningless)"
            )
            return 3
        return _run()


def _run() -> int:
    if not run_tests():
        print("BASELINE RED — aborting (mutation results would be meaningless)")
        print("Do not run other tests against this tree while the harness runs.")
        return 2
    print(f"baseline green · {len(MUTANTS)} mutants\n")

    survived: list[str] = []
    for label, relpath, original, mutated in MUTANTS:
        path = REPO / relpath
        src = path.read_text(encoding="utf-8")
        if original not in src:
            print(f"  SKIP    {label}\n          (anchor not found in {relpath})")
            continue
        path.write_text(src.replace(original, mutated, 1), encoding="utf-8")
        try:
            caught = not run_tests()
        finally:
            path.write_text(src, encoding="utf-8")  # always restore
        print(f"  {'KILLED ' if caught else 'SURVIVED'} {label}")
        if not caught:
            survived.append(label)

    total = len(MUTANTS)
    print(f"\nkilled {total - len(survived)}/{total}")
    if survived:
        print("\nBLIND SPOTS — these changes broke nothing:")
        for s in survived:
            print(f"  · {s}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
