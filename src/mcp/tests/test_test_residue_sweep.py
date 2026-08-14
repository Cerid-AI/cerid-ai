# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Test-residue purge + guard (UX-14/20).

The guard's proof obligation: newly-planted residue names — fresh UUIDs
under the namespace prefixes, not fixture literals — must be caught, and
ordinary user content must never be. The grace window keeps an in-flight
live-stack test run's probes alive.
"""
from __future__ import annotations

import uuid
from datetime import timedelta
from unittest.mock import MagicMock, patch

from core.utils.test_residue import is_test_residue_name, is_test_residue_text
from core.utils.time import utcnow


class TestResidueNamespace:
    def test_catches_newly_planted_residue(self):
        # Fresh names, not fixture literals — the namespace, not a list.
        fresh = uuid.uuid4().hex[:12]
        assert is_test_residue_name(f"e2e-marker-{fresh}") is True
        assert is_test_residue_name(f"preservation-probe-{fresh}") is True
        assert is_test_residue_name(f"audit-tr_{fresh}") is True
        assert is_test_residue_name(
            f"memory_project_context_herd-fad_20260812_{fresh}"
        ) is True
        assert is_test_residue_name(
            f"memory_decision_audit-tr_20260716_{fresh}"
        ) is True

    def test_seeded_demo_notes_are_residue(self):
        assert is_test_residue_name("Project Aurora") is True
        assert is_test_residue_name("GreenTech Inc.") is True

    def test_user_content_is_never_residue(self):
        for name in (
            "quarterly-report.pdf",
            "Meeting with the vendor about Q3",
            "memory_decision_a1b2c3d4_20260812_0",  # real conversation memory
            "GreenTech expansion notes",             # prefix-similar, not exact
            "",
        ):
            assert is_test_residue_name(name) is False, name

    def test_marker_in_text_is_residue(self):
        """The live probes ingest content with NO filename — the artifact is
        named text_input and the marker only exists in the summary/content.
        A filename-only matcher is structurally blind to them."""
        fresh = uuid.uuid4().hex[:12]
        assert is_test_residue_text(
            f"This document contains the unique preservation test token "
            f"preservation-probe-{fresh}. It describes the harness."
        ) is True
        assert is_test_residue_text(
            f"e2e-marker-{fresh} — the unique phrase that lets E-04 verify "
            "the artifact actually landed in the KB."
        ) is True

    def test_prose_about_the_test_suite_is_not_residue(self):
        for text in (
            "The e2e suite plants e2e-marker artifacts during drives.",
            "Rename the preservation-probe fixtures before the next run.",
            "Quarterly report covering vendor onboarding.",
            "",
        ):
            assert is_test_residue_text(text) is False, text


def _neo4j_returning(artifact_rows, entity_rows):
    driver = MagicMock()
    session = MagicMock()
    session.__enter__ = MagicMock(return_value=session)
    session.__exit__ = MagicMock(return_value=False)

    def _run(query, **_params):
        result = MagicMock()
        if ":Artifact" in query:
            result.__iter__ = MagicMock(return_value=iter(artifact_rows))
        elif "MATCH (e:Entity {canonical_id" in query or "DETACH DELETE" in query:
            result.__iter__ = MagicMock(return_value=iter([]))
        else:
            result.__iter__ = MagicMock(return_value=iter(entity_rows))
        return result

    session.run.side_effect = _run
    driver.session.return_value = session
    return driver, session


def _old_iso() -> str:
    return (utcnow() - timedelta(days=2)).isoformat()


class TestSweep:
    def _rows(self):
        fresh = uuid.uuid4().hex[:8]
        artifact_rows = [
            {
                "id": "res-1",
                "filename": f"e2e-marker-{fresh}",
                "summary": "",
                "ingested_at": _old_iso(),
            },
            {
                # The observed live shape: probes ingest content with no
                # filename, so the artifact is named text_input and the
                # marker only exists in the summary.
                "id": "res-2",
                "filename": "text_input",
                "summary": (
                    "This document contains the unique preservation test "
                    f"token preservation-probe-{fresh}. It describes the "
                    "capability-preservation harness."
                ),
                "ingested_at": _old_iso(),
            },
            {
                # Cypher prefilter is a superset; Python must re-reject.
                "id": "keep-1",
                "filename": "memory_decision_a1b2c3d4_20260812_0",
                "summary": "User decided to switch coffee grinders.",
                "ingested_at": _old_iso(),
            },
            {
                # Prose ABOUT the suite, no concrete marker id — user content.
                "id": "keep-2",
                "filename": "test-infrastructure-notes.md",
                "summary": "The e2e suite plants e2e-marker artifacts.",
                "ingested_at": _old_iso(),
            },
            {
                # Fresh residue inside the grace window — an in-flight test run.
                "id": "grace-1",
                "filename": f"preservation-probe-{fresh}",
                "summary": "",
                "ingested_at": utcnow().isoformat(),
            },
        ]
        entity_rows = [
            {
                "canonical_id": "org:greentech-inc",
                "name": "GreenTech Inc.",
                "updated_at": _old_iso(),
            },
        ]
        return artifact_rows, entity_rows

    def test_dry_run_reports_without_deleting(self):
        driver, _session = _neo4j_returning(*self._rows())
        with patch(
            "app.services.content_lifecycle.remove_content"
        ) as mock_remove:
            from app.services.kb_hygiene import sweep_test_residue

            summary = sweep_test_residue(driver, MagicMock(), apply=False)
        assert summary["artifacts_found"] == 2
        assert summary["entities_found"] == 1
        assert summary["skipped_in_grace"] == 1
        assert summary["artifacts_purged"] == 0
        assert summary["entities_purged"] == 0
        mock_remove.assert_not_called()

    def test_apply_purges_residue_only(self):
        artifact_rows, entity_rows = self._rows()
        driver, session = _neo4j_returning(artifact_rows, entity_rows)
        removal = MagicMock()
        removal.found = True
        with patch(
            "app.services.content_lifecycle.remove_content",
            return_value=removal,
        ) as mock_remove:
            from app.services.kb_hygiene import sweep_test_residue

            summary = sweep_test_residue(driver, MagicMock(), apply=True)

        assert summary["artifacts_purged"] == 2
        assert summary["entities_purged"] == 1
        removed_ids = [c.args[0] for c in mock_remove.call_args_list]
        assert removed_ids == ["res-1", "res-2"], (
            "only abandoned residue may be deleted — never user memories, "
            "never in-grace probes"
        )
        delete_calls = [
            c for c in session.run.call_args_list
            if "DETACH DELETE" in str(c.args[0])
        ]
        assert len(delete_calls) == 1
        assert delete_calls[0].kwargs["ids"] == ["org:greentech-inc"]
