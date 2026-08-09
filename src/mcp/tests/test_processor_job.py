# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for core.processor.job."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest

from core.processor.cost import CostEstimate
from core.processor.cost import estimate as cost_estimate
from core.processor.job import (
    BaseJob,
    JobRecord,
    JobResult,
    JobState,
    ProgressCallback,
    validate_transition,
)
from core.processor.priority import Priority

# ---------------------------------------------------------------------------
# Concrete subclass for testing
# ---------------------------------------------------------------------------

class _SampleJob(BaseJob):
    """Minimal concrete job used throughout the test suite."""

    job_type = "test.sample"

    @property
    def priority(self) -> Priority:
        return Priority.MEDIUM

    def estimate_cost(self) -> CostEstimate:
        return cost_estimate("ollama/local", 0, 0)

    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        await progress_cb(1.0)
        return JobResult(job_id="test-id", actual_tokens_in=0, actual_tokens_out=0)


# ---------------------------------------------------------------------------
# JobState transitions
# ---------------------------------------------------------------------------

class TestValidateTransition:
    def test_pending_to_running_allowed(self) -> None:
        validate_transition(JobState.PENDING, JobState.RUNNING)  # no exception

    def test_running_to_completed_allowed(self) -> None:
        validate_transition(JobState.RUNNING, JobState.COMPLETED)

    def test_running_to_failed_allowed(self) -> None:
        validate_transition(JobState.RUNNING, JobState.FAILED)

    def test_pending_to_paused_allowed(self) -> None:
        validate_transition(JobState.PENDING, JobState.PAUSED)

    def test_paused_to_pending_allowed(self) -> None:
        validate_transition(JobState.PAUSED, JobState.PENDING)

    def test_completed_to_running_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid job state transition"):
            validate_transition(JobState.COMPLETED, JobState.RUNNING)

    def test_failed_to_running_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid job state transition"):
            validate_transition(JobState.FAILED, JobState.RUNNING)

    def test_completed_to_pending_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid job state transition"):
            validate_transition(JobState.COMPLETED, JobState.PENDING)

    def test_running_to_pending_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid job state transition"):
            validate_transition(JobState.RUNNING, JobState.PENDING)


# ---------------------------------------------------------------------------
# JobRecord serialisation / deserialisation
# ---------------------------------------------------------------------------

def _make_record(**overrides: Any) -> JobRecord:
    defaults: dict[str, Any] = {
        "id": "abc-123",
        "job_type": "test.sample",
        "state": JobState.PENDING,
        "priority": Priority.MEDIUM,
        "payload": {"key": "value"},
        "enqueued_at": datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc),
    }
    defaults.update(overrides)
    return JobRecord(**defaults)


class TestJobRecordSerialisation:
    def test_to_dict_contains_required_fields(self) -> None:
        rec = _make_record()
        d = rec.to_dict()
        required = {
            "id", "job_type", "state", "priority", "payload",
            "enqueued_at", "retry_count", "started_at", "completed_at",
            "estimated_tokens_in", "estimated_tokens_out",
            "actual_tokens_in", "actual_tokens_out",
            "requires_llm", "model", "error_message",
            "metadata", "progress",
        }
        assert required.issubset(d.keys())

    def test_round_trip_metadata_and_progress(self) -> None:
        """CL-5/AF-008: a job's outcome metadata + progress checkpoint survive the
        Redis round-trip. Before the schema fix, JobRecord had neither field, so
        every job's rich outcome and progress were irrecoverable at the persist
        seam and /processor/recent could not surface them."""
        rec = _make_record(
            metadata={"written": 42, "orphans_cleared": 3, "held": False},
            progress=0.75,
        )
        restored = JobRecord.from_dict(rec.to_dict())
        assert restored.metadata == {"written": 42, "orphans_cleared": 3, "held": False}
        assert restored.progress == 0.75

    def test_held_state_serialises(self) -> None:
        """A HELD (cost-cap) job round-trips as HELD, never COMPLETED (AF-017)."""
        rec = _make_record(state=JobState.HELD)
        assert rec.to_dict()["state"] == "held"
        assert JobRecord.from_dict(rec.to_dict()).state is JobState.HELD

    def test_state_serialised_as_string(self) -> None:
        rec = _make_record(state=JobState.RUNNING)
        assert rec.to_dict()["state"] == "running"

    def test_priority_serialised_as_string(self) -> None:
        rec = _make_record(priority=Priority.HIGH)
        assert rec.to_dict()["priority"] == "high"

    def test_round_trip_basic(self) -> None:
        rec = _make_record()
        restored = JobRecord.from_dict(rec.to_dict())
        assert restored.id == rec.id
        assert restored.state == rec.state
        assert restored.priority == rec.priority
        assert restored.payload == rec.payload

    def test_round_trip_with_optional_fields(self) -> None:
        rec = _make_record(
            retry_count=2,
            requires_llm=True,
            model="anthropic/claude-sonnet-4-6",
            error_message="timeout",
        )
        restored = JobRecord.from_dict(rec.to_dict())
        assert restored.retry_count == 2
        assert restored.requires_llm is True
        assert restored.model == "anthropic/claude-sonnet-4-6"
        assert restored.error_message == "timeout"

    def test_none_optional_fields_survive_round_trip(self) -> None:
        rec = _make_record()
        restored = JobRecord.from_dict(rec.to_dict())
        assert restored.started_at is None
        assert restored.completed_at is None
        assert restored.actual_tokens_in is None
        assert restored.actual_tokens_out is None
        assert restored.model is None
        assert restored.error_message is None

    def test_enqueued_at_round_trips_correctly(self) -> None:
        enqueued = datetime(2026, 1, 15, 8, 30, 0, tzinfo=timezone.utc)
        rec = _make_record(enqueued_at=enqueued)
        restored = JobRecord.from_dict(rec.to_dict())
        assert restored.enqueued_at == enqueued

    def test_all_states_serialise(self) -> None:
        for state in JobState:
            rec = _make_record(state=state)
            restored = JobRecord.from_dict(rec.to_dict())
            assert restored.state == state


# ---------------------------------------------------------------------------
# BaseJob abstract enforcement
# ---------------------------------------------------------------------------

class TestBaseJobAbstractEnforcement:
    def test_concrete_subclass_instantiates(self) -> None:
        job = _SampleJob()
        assert job.priority == Priority.MEDIUM
        assert job.job_type == "test.sample"

    def test_missing_priority_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            class _BadJob(BaseJob):
                job_type = "test.bad"

                def estimate_cost(self) -> CostEstimate:  # type: ignore[override]
                    return cost_estimate("ollama/local", 0, 0)

                async def run(self, progress_cb: ProgressCallback) -> JobResult:  # type: ignore[override]
                    return JobResult(job_id="x", actual_tokens_in=0, actual_tokens_out=0)

            _BadJob()  # missing abstract priority

    def test_missing_estimate_cost_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            class _BadJob2(BaseJob):
                job_type = "test.bad2"

                @property
                def priority(self) -> Priority:
                    return Priority.LOW

                async def run(self, progress_cb: ProgressCallback) -> JobResult:  # type: ignore[override]
                    return JobResult(job_id="x", actual_tokens_in=0, actual_tokens_out=0)

            _BadJob2()

    def test_missing_run_raises_type_error(self) -> None:
        with pytest.raises(TypeError):
            class _BadJob3(BaseJob):
                job_type = "test.bad3"

                @property
                def priority(self) -> Priority:
                    return Priority.LOW

                def estimate_cost(self) -> CostEstimate:  # type: ignore[override]
                    return cost_estimate("ollama/local", 0, 0)

            _BadJob3()

    def test_empty_job_type_raises_type_error(self) -> None:
        with pytest.raises(TypeError, match="job_type"):
            class _NoJobType(BaseJob):
                # job_type intentionally omitted (defaults to "")
                @property
                def priority(self) -> Priority:
                    return Priority.LOW

                def estimate_cost(self) -> CostEstimate:  # type: ignore[override]
                    return cost_estimate("ollama/local", 0, 0)

                async def run(self, progress_cb: ProgressCallback) -> JobResult:  # type: ignore[override]
                    return JobResult(job_id="x", actual_tokens_in=0, actual_tokens_out=0)

    def test_new_record_returns_job_record(self) -> None:
        job = _SampleJob()
        rec = job.new_record(payload={"test": True})
        assert isinstance(rec, JobRecord)
        assert rec.job_type == "test.sample"
        assert rec.state == JobState.PENDING
        assert rec.payload == {"test": True}
