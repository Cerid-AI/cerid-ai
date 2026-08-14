# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Wrong-entity summary backstop (todo item 5).

``is_insufficient_summary`` catches "X is not mentioned in the excerpts"
but not "the entity in question is not X but rather Y" — the shape BTC
had. The detector is a backstop (the fabricating extractor is fixed
upstream), and the green half of this probe is the load-bearing part:
the naive widening of the absence patterns nearly deleted the real
"Matt Butcher" and "Azure Kubernetes Service" summaries, so the names
the detector must NOT flag are pinned here alongside the ones it must.
"""
from __future__ import annotations

import pytest

from core.agents.summary_quality import (
    is_insufficient_summary,
    is_wrong_entity_summary,
)


class TestWrongEntityDetected:
    """Summaries that open by redirecting their own subject are flagged."""

    @pytest.mark.parametrize("summary", [
        # The BTC shape: the page redirects its own subject.
        "The entity in question is not BTC but rather the Bitcoin whitepaper "
        "referenced across the excerpts, which discuss document provenance.",
        "The term in question here is not Helm; the excerpts actually "
        "describe Kubernetes package management in general terms.",
        "BTC does not refer to the cryptocurrency in these excerpts but to a "
        "file naming convention used by the ingestion pipeline.",
        "The name appears to refer to a different entity than BTC — the "
        "excerpts describe a database migration tool.",
        "BTC is not the entity discussed in the provided excerpts, which "
        "instead concern Kubernetes API deprecation policy.",
    ])
    def test_flags_subject_redirection(self, summary):
        assert is_wrong_entity_summary(summary) is True

    def test_the_gap_this_closes(self):
        """The wrong-entity shape is invisible to the insufficiency check —
        that non-overlap is why the backstop exists."""
        summary = (
            "The entity in question is not BTC but rather the Bitcoin "
            "whitepaper referenced across the excerpts."
        )
        assert is_insufficient_summary(summary) is False
        assert is_wrong_entity_summary(summary) is True


class TestProtectedNamesNotFlagged:
    """The names the naive widening nearly deleted — must never be flagged."""

    @pytest.mark.parametrize("summary", [
        # A legitimate fact stated as a contrast is not a redirection.
        "Matt Butcher is not a Microsoft employee but rather the CEO of "
        "Fermyon, described in the corpus through his work on Helm and "
        "WebAssembly tooling.",
        # "is not just X but Y" is emphasis about the subject, not a redirect.
        "Azure Kubernetes Service is not just a container runtime but a "
        "managed Kubernetes offering, discussed in the excerpts through its "
        "node-pool upgrade behaviour.",
        # Honest scoping after a substantive opening stays honest scoping.
        "Kubernetes is an API-driven orchestration system, described in the "
        "corpus through its versioning policy. The excerpts do not contain "
        "information about its release cadence.",
        # A summary quoting what something does NOT do is still about it.
        "Helm is a package manager for Kubernetes. It does not manage "
        "cluster provisioning itself, delegating that to other tooling.",
    ])
    def test_keeps_real_summaries(self, summary):
        assert is_wrong_entity_summary(summary) is False

    def test_empty_is_not_this_detectors_call(self):
        assert is_wrong_entity_summary("") is False
        assert is_wrong_entity_summary(None) is False
