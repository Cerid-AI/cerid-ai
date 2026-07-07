# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""RAG C3.4 — vault-writeback tests for WeeklySynthesisJob.

Mirror of ``test_brief_generation_vault_write.py`` for the weekly
synthesis path. Same opt-in semantics, same failure-isolation contract,
different default filename (``synthesis-YYYY-MM-DD.md``) and frontmatter.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.processor.jobs.weekly_synthesis import WeeklySynthesisJob

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_progress(pct: float) -> None:  # noqa: ARG001
    pass


def _make_brief_record(brief_id: str = "wk-001", status: str = "generated") -> MagicMock:
    record = MagicMock()
    record.brief_id = brief_id
    record.status = status
    record.sections = {
        "EMERGING_THESIS": "emerging",
        "CONTRADICTIONS": "contra",
        "KNOWLEDGE_GAPS": "gaps",
        "ONE_ACTION": "do this",
    }
    return record


def _patch_pipeline_internals(record: MagicMock):
    """Patch BriefService + Neo4j + contradiction_log; let _run_pipeline run."""
    from contextlib import ExitStack

    mock_service = AsyncMock()
    mock_service.generate_weekly.return_value = record
    mock_service.store.return_value = None

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.processor.jobs.weekly_synthesis._get_brief_service",
            return_value=mock_service,
        )
    )
    stack.enter_context(
        patch(
            "app.processor.jobs.weekly_synthesis._get_neo4j",
            return_value=MagicMock(),
        )
    )
    stack.enter_context(
        patch(
            "app.processor.jobs.weekly_synthesis._build_vault_snapshot",
            return_value="vault snapshot text",
        )
    )
    stack.enter_context(
        patch(
            "app.services.contradiction_log.list_recent",
            new=AsyncMock(return_value=[]),
        )
    )
    # Task 2.1b claim-verification deps — stubbed out here since these
    # tests exercise vault-write behavior, not verification.
    stack.enter_context(
        patch("app.processor.jobs.weekly_synthesis._get_chroma", return_value=MagicMock())
    )
    stack.enter_context(
        patch("app.processor.jobs.weekly_synthesis._get_redis", return_value=MagicMock())
    )
    stack.enter_context(
        patch(
            "app.services.briefs.verification.verify_brief_claims",
            new=AsyncMock(return_value=[]),
        )
    )
    return stack


# ---------------------------------------------------------------------------
# Opt-in semantics
# ---------------------------------------------------------------------------


class TestOptIn:
    async def test_default_does_not_call_vault_write(self):
        job = WeeklySynthesisJob(week_ending="2026-05-11")
        record = _make_brief_record()

        with _patch_pipeline_internals(record), patch(
            "app.services.vault_write.write_note"
        ) as mock_write:
            await job.run(_noop_progress)

        mock_write.assert_not_called()

    async def test_write_to_vault_without_vault_id_is_noop(self):
        job = WeeklySynthesisJob(
            week_ending="2026-05-11",
            write_to_vault=True,
            vault_id=None,
        )
        record = _make_brief_record()

        with _patch_pipeline_internals(record), patch(
            "app.services.vault_write.write_note"
        ) as mock_write:
            result = await job.run(_noop_progress)

        mock_write.assert_not_called()
        assert result.metadata["vault_written"] is False


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


class TestVaultWriteCalled:
    async def test_writes_at_expected_path_and_mode(self):
        job = WeeklySynthesisJob(
            week_ending="2026-05-11",
            write_to_vault=True,
            vault_id="vault-xyz",
        )
        record = _make_brief_record()

        with _patch_pipeline_internals(record), patch(
            "app.services.vault_write.write_note"
        ) as mock_write, patch(
            "app.deps.get_redis", return_value=MagicMock(),
        ):
            result = await job.run(_noop_progress)

        mock_write.assert_called_once()
        req = mock_write.call_args.args[0]
        assert req.vault_id == "vault-xyz"
        assert req.path == "_briefs/synthesis-2026-05-11.md"
        assert req.mode == "append"
        assert req.allow_synthesis_input is False
        assert req.frontmatter == {
            "cerid:job_type": "weekly_synthesis",
            "cerid:week_ending": "2026-05-11",
        }
        assert "Weekly Synthesis — week ending 2026-05-11" in req.content
        assert "## EMERGING_THESIS" in req.content
        assert result.metadata["vault_written"] is True

    async def test_custom_vault_folder_applied(self):
        job = WeeklySynthesisJob(
            week_ending="2026-05-11",
            write_to_vault=True,
            vault_id="vault-xyz",
            vault_folder="syntheses",
        )
        record = _make_brief_record()

        with _patch_pipeline_internals(record), patch(
            "app.services.vault_write.write_note"
        ) as mock_write, patch(
            "app.deps.get_redis", return_value=MagicMock(),
        ):
            await job.run(_noop_progress)

        req = mock_write.call_args.args[0]
        assert req.path == "syntheses/synthesis-2026-05-11.md"


# ---------------------------------------------------------------------------
# Failure isolation
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    async def test_vault_write_failure_does_not_fail_job(self):
        job = WeeklySynthesisJob(
            week_ending="2026-05-11",
            write_to_vault=True,
            vault_id="vault-xyz",
        )
        record = _make_brief_record()

        with _patch_pipeline_internals(record), patch(
            "app.services.vault_write.write_note",
            side_effect=RuntimeError("disk full"),
        ), patch(
            "app.deps.get_redis", return_value=MagicMock(),
        ), patch(
            "app.processor.jobs.weekly_synthesis.log_swallowed_error"
        ) as mock_log:
            result = await job.run(_noop_progress)

        mock_log.assert_called()
        assert result.metadata["brief_id"] == "wk-001"
        # vault_written reflects ACTUAL outcome: False when the write
        # failed (audit fix — was True before).
        assert result.metadata["vault_written"] is False


# ---------------------------------------------------------------------------
# Init wiring
# ---------------------------------------------------------------------------


class TestInit:
    def test_defaults(self):
        job = WeeklySynthesisJob(week_ending="2026-05-11")
        assert job._write_to_vault is False
        assert job._vault_id is None
        assert job._vault_folder == "_briefs"

    def test_kwargs_threaded(self):
        job = WeeklySynthesisJob(
            week_ending="2026-05-11",
            write_to_vault=True,
            vault_id="abc",
            vault_folder="custom",
        )
        assert job._write_to_vault is True
        assert job._vault_id == "abc"
        assert job._vault_folder == "custom"

    @pytest.mark.parametrize("vault_folder", [None, ""])
    def test_blank_folder_defaults(self, vault_folder):
        job = WeeklySynthesisJob(
            week_ending="2026-05-11",
            write_to_vault=True,
            vault_id="abc",
            vault_folder=vault_folder,
        )
        assert job._vault_folder == "_briefs"
