# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""RAG C3.4 — vault-writeback tests for BriefGenerationJob.

These tests patch the real ``app.services.vault_write.write_note``
out of the picture and assert the job's vault-write opt-in semantics:

  * ``write_to_vault=False`` (default) — NO call to ``write_note``.
  * ``write_to_vault=True`` with ``vault_id`` — exactly one call with the
    canonical path and frontmatter.
  * A failing vault write must NOT fail the job (the brief is already
    persisted to Neo4j).

The ``_run_pipeline`` flow is allowed to run through (with the
BriefService and Neo4j helpers mocked) so the vault-write branch
actually executes — patching the pipeline would defeat the purpose.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.processor.jobs.brief_generation import BriefGenerationJob

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _noop_progress(pct: float) -> None:  # noqa: ARG001
    pass


def _make_brief_record(brief_id: str = "br-001", status: str = "generated") -> MagicMock:
    record = MagicMock()
    record.brief_id = brief_id
    record.status = status
    record.sections = {"CONNECTIONS": "stuff", "PATTERN": "p", "QUESTION": "?"}
    return record


def _patch_pipeline_internals(record: MagicMock):
    """Patch BriefService + Neo4j helpers; let the real _run_pipeline body run."""
    from contextlib import ExitStack

    mock_service = AsyncMock()
    mock_service.generate_daily.return_value = record
    mock_service.store.return_value = None

    stack = ExitStack()
    stack.enter_context(
        patch(
            "app.processor.jobs.brief_generation._get_brief_service",
            return_value=mock_service,
        )
    )
    stack.enter_context(
        patch(
            "app.processor.jobs.brief_generation._get_neo4j",
            return_value=MagicMock(),
        )
    )
    stack.enter_context(
        patch(
            "app.processor.jobs.brief_generation._assemble_corpus",
            return_value=("inbox text", "notes text"),
        )
    )
    return stack


# ---------------------------------------------------------------------------
# Opt-in semantics
# ---------------------------------------------------------------------------


class TestOptIn:
    async def test_default_does_not_call_vault_write(self):
        """write_to_vault defaults to False — no vault_write side effect."""
        job = BriefGenerationJob(target_date="2026-05-10")
        record = _make_brief_record()

        with _patch_pipeline_internals(record), patch(
            "app.services.vault_write.write_note"
        ) as mock_write:
            await job.run(_noop_progress)

        mock_write.assert_not_called()

    async def test_write_to_vault_without_vault_id_is_noop(self):
        """write_to_vault=True but vault_id=None must still skip the call."""
        job = BriefGenerationJob(
            target_date="2026-05-10",
            write_to_vault=True,
            vault_id=None,
        )
        record = _make_brief_record()

        with _patch_pipeline_internals(record), patch(
            "app.services.vault_write.write_note"
        ) as mock_write:
            result = await job.run(_noop_progress)

        mock_write.assert_not_called()
        # Metadata must reflect that no vault write happened.
        assert result.metadata["vault_written"] is False


# ---------------------------------------------------------------------------
# Happy path — vault_write called with the expected request
# ---------------------------------------------------------------------------


class TestVaultWriteCalled:
    async def test_writes_at_expected_path_and_mode(self):
        """Default folder + filename + append mode + loop-breaker flag."""
        job = BriefGenerationJob(
            target_date="2026-05-10",
            write_to_vault=True,
            vault_id="vault-xyz",
        )
        record = _make_brief_record()

        with _patch_pipeline_internals(record), patch(
            "app.services.vault_write.write_note"
        ) as mock_write, patch(
            "app.deps.get_redis",
            return_value=MagicMock(),
        ):
            result = await job.run(_noop_progress)

        mock_write.assert_called_once()
        req = mock_write.call_args.args[0]
        assert req.vault_id == "vault-xyz"
        assert req.path == "_briefs/brief-2026-05-10.md"
        assert req.mode == "append"
        # Critical loop-breaker: briefs MUST NOT feed back into synthesis.
        assert req.allow_synthesis_input is False
        # Frontmatter records job context for downstream debugging.
        assert req.frontmatter == {
            "cerid:job_type": "brief_generation",
            "cerid:target_date": "2026-05-10",
        }
        assert "Daily Brief — 2026-05-10" in req.content
        # Sections from the BriefRecord show up in the body.
        assert "## CONNECTIONS" in req.content
        # Metadata flags the writeback occurred.
        assert result.metadata["vault_written"] is True

    async def test_custom_vault_folder_applied(self):
        """``vault_folder`` override controls the path prefix."""
        job = BriefGenerationJob(
            target_date="2026-05-10",
            write_to_vault=True,
            vault_id="vault-xyz",
            vault_folder="my-briefs",
        )
        record = _make_brief_record()

        with _patch_pipeline_internals(record), patch(
            "app.services.vault_write.write_note"
        ) as mock_write, patch(
            "app.deps.get_redis",
            return_value=MagicMock(),
        ):
            await job.run(_noop_progress)

        req = mock_write.call_args.args[0]
        assert req.path == "my-briefs/brief-2026-05-10.md"


# ---------------------------------------------------------------------------
# Failure isolation — vault failure must not fail the brief job
# ---------------------------------------------------------------------------


class TestFailureIsolation:
    async def test_vault_write_failure_does_not_fail_job(self):
        """A raising write_note must be swallowed; the JobResult is still returned."""
        job = BriefGenerationJob(
            target_date="2026-05-10",
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
            "app.processor.jobs.brief_generation.log_swallowed_error"
        ) as mock_log:
            # Must NOT raise.
            result = await job.run(_noop_progress)

        # Failure was logged via the observability boundary.
        mock_log.assert_called()
        # The job still returns a result — Neo4j persistence is what counts.
        assert result.metadata["brief_id"] == "br-001"
        # vault_written reflects ACTUAL outcome: False when the write
        # failed, even though the branch was entered. The failure detail
        # lives in the swallowed-error log; the metadata field is
        # honest about what happened. (Audit fix — was True before.)
        assert result.metadata["vault_written"] is False


# ---------------------------------------------------------------------------
# Init wiring — constructor accepts and stores the new kwargs
# ---------------------------------------------------------------------------


class TestInit:
    def test_defaults(self):
        job = BriefGenerationJob(target_date="2026-05-10")
        assert job._write_to_vault is False
        assert job._vault_id is None
        assert job._vault_folder == "_briefs"

    def test_kwargs_threaded(self):
        job = BriefGenerationJob(
            target_date="2026-05-10",
            write_to_vault=True,
            vault_id="abc",
            vault_folder="custom",
        )
        assert job._write_to_vault is True
        assert job._vault_id == "abc"
        assert job._vault_folder == "custom"

    @pytest.mark.parametrize("vault_folder", [None, ""])
    def test_blank_folder_defaults(self, vault_folder):
        job = BriefGenerationJob(
            target_date="2026-05-10",
            write_to_vault=True,
            vault_id="abc",
            vault_folder=vault_folder,
        )
        assert job._vault_folder == "_briefs"
