# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The test-residue namespace — names that only test runs write.

Live-stack test suites (preservation probes, e2e drives, audit
transcripts) ingest real artifacts into the production KB and clean up
after themselves — unless the run crashes between ingest and teardown.
The leftovers then render in Constellation and the Library as if they
were user knowledge ("e2e-marker-…", "preservation-probe-…",
"memory_project_context_herd-fad_…" — UX-14/20).

This module is the single source of truth for that namespace. Consumers:

* ``app.services.kb_hygiene.sweep_test_residue`` — the purge (manual
  endpoint + weekly scheduler sweep).
* Tests that plant fresh residue and prove the sweep catches it.

Adding a new live-stack test marker? Give it one of the prefixes below,
or add the new prefix here in the same change — a marker this module
does not know about is invisible to the guard.
"""
from __future__ import annotations

import re

# Name prefixes that only test tooling emits.
TEST_RESIDUE_PREFIXES: tuple[str, ...] = (
    "e2e-marker-",
    "preservation-probe-",
    "audit-tr_",
)

# Memory artifacts extracted from seeded test conversations: the filename
# shape is ``memory_{type}_{convo_prefix}_…`` and these conversation
# prefixes came from test drives, never a user session (herd-fad drives,
# audit transcripts — conversation ids truncate to "audit-tr").
TEST_RESIDUE_MEMORY_CONVO_PREFIXES: tuple[str, ...] = (
    "herd-fad",
    "audit-tr",
)

# Seeded demo/test notes observed verbatim in the live KB (UX-20).
TEST_RESIDUE_EXACT_NAMES: tuple[str, ...] = (
    "Project Aurora",
    "GreenTech Inc.",
)


# Probe suites ingest content WITHOUT a filename (the artifact is stored as
# ``text_input``), so the marker only exists in the content/summary — a
# filename matcher is structurally blind to them. A concrete marker token
# (prefix + id tail) in the text is the residue signature; prose ABOUT the
# suite ("the e2e-marker artifacts", "preservation-probe fixtures") carries
# the bare prefix without an id and is not matched.
TEST_RESIDUE_TEXT_MARKERS: tuple[str, ...] = (
    "e2e-marker-",
    "preservation-probe-",
)

_TEXT_MARKER_RE = re.compile(
    # Id tails observed live: timestamp digits, uuid hex, "manual-check".
    r"(?:e2e-marker|preservation-probe)-[\w][\w-]{3,}", re.IGNORECASE,
)


def is_test_residue_text(text: str) -> bool:
    """True when ``text`` carries a concrete test-probe marker token."""
    return bool(_TEXT_MARKER_RE.search(text or ""))


def is_test_residue_name(name: str) -> bool:
    """True when ``name`` belongs to the test-residue namespace."""
    stripped = (name or "").strip()
    if not stripped:
        return False
    if any(stripped.startswith(p) for p in TEST_RESIDUE_PREFIXES):
        return True
    if stripped in TEST_RESIDUE_EXACT_NAMES:
        return True
    if stripped.startswith("memory_"):
        return any(
            f"_{convo}" in stripped
            for convo in TEST_RESIDUE_MEMORY_CONVO_PREFIXES
        )
    return False
