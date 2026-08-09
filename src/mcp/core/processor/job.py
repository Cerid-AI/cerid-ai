# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""BaseJob ABC and associated value objects.

Every unit of background work in Cerid is a ``BaseJob`` subclass.
The contract is intentionally thin — the worker and queue adapter
(Phase 3) rely only on these primitives, not on any app-layer concern.

``JobRecord`` is the persisted representation; ``BaseJob`` is the
in-process executable. A concrete job holds its payload in the subclass
and is responsible for building a ``JobRecord`` via ``to_record()``.
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Awaitable, Callable

from core.processor.cost import CostEstimate
from core.processor.priority import Priority

# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------

class JobState(str, Enum):
    """Lifecycle states of a persisted job.

    Valid transitions
    -----------------
    pending  → running    (worker picks it up)
    running  → completed  (successful run)
    running  → failed     (exception or timeout)
    pending  → paused     (processor disabled while job is waiting)
    paused   → pending    (processor re-enabled)

    Terminal states ``completed`` and ``failed`` cannot transition further.
    """

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    PAUSED = "paused"
    # Terminal state for a job stopped by a cost-cap / budget hold (CL-5/AF-017).
    # Distinct from COMPLETED so a held job is never mistaken for a success.
    HELD = "held"


# Allowed (from_state → to_state) pairs. Enforced by ``validate_transition``.
_VALID_TRANSITIONS: frozenset[tuple[JobState, JobState]] = frozenset(
    {
        (JobState.PENDING, JobState.RUNNING),
        (JobState.RUNNING, JobState.COMPLETED),
        (JobState.RUNNING, JobState.FAILED),
        (JobState.RUNNING, JobState.HELD),
        (JobState.PENDING, JobState.PAUSED),
        (JobState.PAUSED, JobState.PENDING),
    }
)


def validate_transition(from_state: JobState, to_state: JobState) -> None:
    """Raise ``ValueError`` if the state transition is not permitted."""
    if (from_state, to_state) not in _VALID_TRANSITIONS:
        raise ValueError(
            f"Invalid job state transition: {from_state!r} → {to_state!r}"
        )


# ---------------------------------------------------------------------------
# Value objects
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class JobResult:
    """Outcome of a successful job execution.

    ``metadata`` carries job-type-specific supplementary data (e.g.
    entity counts, token actuals) without forcing a rigid schema on
    every job type.
    """

    job_id: str
    actual_tokens_in: int
    actual_tokens_out: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class JobRecord:
    """Persisted job state — stored in Redis by the queue adapter.

    Mutable because the worker updates ``state``, ``started_at``,
    ``completed_at``, ``actual_tokens``, and ``error_message`` in place
    before re-serialising to Redis.
    """

    id: str
    job_type: str
    state: JobState
    priority: Priority
    payload: dict[str, Any]
    enqueued_at: datetime
    retry_count: int = 0
    started_at: datetime | None = None
    completed_at: datetime | None = None
    estimated_tokens_in: int = 0
    estimated_tokens_out: int = 0
    actual_tokens_in: int | None = None
    actual_tokens_out: int | None = None
    requires_llm: bool = False
    model: str | None = None
    error_message: str | None = None
    # CL-5: the worker/queue persist a completed job's ``JobResult.metadata`` here
    # (job-type-specific outcome data — entity counts, skip reasons, held marker)
    # and the latest progress checkpoint, so ``GET /processor/recent`` can finally
    # surface both. Declared as real fields (slots=True forbids monkey-setting).
    metadata: dict[str, Any] = field(default_factory=dict)
    progress: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict for Redis / JSON persistence."""
        return {
            "id": self.id,
            "job_type": self.job_type,
            "state": self.state.value,
            "priority": self.priority.value,
            "payload": self.payload,
            "enqueued_at": self.enqueued_at.isoformat(),
            "retry_count": self.retry_count,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": (
                self.completed_at.isoformat() if self.completed_at else None
            ),
            "estimated_tokens_in": self.estimated_tokens_in,
            "estimated_tokens_out": self.estimated_tokens_out,
            "actual_tokens_in": self.actual_tokens_in,
            "actual_tokens_out": self.actual_tokens_out,
            "requires_llm": self.requires_llm,
            "model": self.model,
            "error_message": self.error_message,
            "metadata": self.metadata,
            "progress": self.progress,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> JobRecord:
        """Deserialise from a plain dict (Redis / JSON)."""
        return cls(
            id=data["id"],
            job_type=data["job_type"],
            state=JobState(data["state"]),
            priority=Priority(data["priority"]),
            payload=data["payload"],
            enqueued_at=datetime.fromisoformat(data["enqueued_at"]),
            retry_count=data.get("retry_count", 0),
            started_at=(
                datetime.fromisoformat(data["started_at"])
                if data.get("started_at")
                else None
            ),
            completed_at=(
                datetime.fromisoformat(data["completed_at"])
                if data.get("completed_at")
                else None
            ),
            estimated_tokens_in=data.get("estimated_tokens_in", 0),
            estimated_tokens_out=data.get("estimated_tokens_out", 0),
            actual_tokens_in=data.get("actual_tokens_in"),
            actual_tokens_out=data.get("actual_tokens_out"),
            requires_llm=data.get("requires_llm", False),
            model=data.get("model"),
            error_message=data.get("error_message"),
            metadata=data.get("metadata") or {},
            progress=float(data.get("progress") or 0.0),
        )


# ---------------------------------------------------------------------------
# Base job ABC
# ---------------------------------------------------------------------------

#: Callback type that jobs use to report incremental progress (0.0–1.0).
ProgressCallback = Callable[[float], Awaitable[None]]


class BaseJob(ABC):
    """Abstract base class for all Cerid background jobs.

    Subclasses must:
    1. Set ``job_type`` as a class attribute (stable string registry key).
    2. Implement ``priority`` as a property returning a ``Priority``.
    3. Implement ``estimate_cost() -> CostEstimate``.
    4. Implement ``async run(progress_cb) -> JobResult``.

    The ``job_type`` string is stored in ``JobRecord`` and used by the
    worker to dispatch deserialized jobs back to the right subclass.
    Changing it is a breaking migration — treat it as a stable API.
    """

    #: Stable registry key. Override in every concrete subclass.
    job_type: str = ""

    def __init_subclass__(cls, **kwargs: Any) -> None:
        """Ensure concrete subclasses declare a non-empty ``job_type``."""
        super().__init_subclass__(**kwargs)
        # Allow abstract intermediaries (job_type still empty) but
        # reject concrete classes that forget to set it.
        if not getattr(cls, "__abstractmethods__", None) and not cls.job_type:
            raise TypeError(
                f"{cls.__name__} must define a non-empty ``job_type`` class attribute."
            )

    @property
    @abstractmethod
    def priority(self) -> Priority:
        """Scheduling priority for this job instance."""

    @abstractmethod
    def estimate_cost(self) -> CostEstimate:
        """Return a pre-execution cost estimate.

        Called before enqueueing when the processor is in Hybrid mode to
        show the user a cost projection and enforce the cost cap.
        """

    @abstractmethod
    async def run(self, progress_cb: ProgressCallback) -> JobResult:
        """Execute the job.

        Parameters
        ----------
        progress_cb
            Async callable receiving a float in [0.0, 1.0]. Jobs should
            call it at meaningful checkpoints so the processor pane can
            show live progress.
        """

    def new_record(self, *, payload: dict[str, Any] | None = None) -> JobRecord:
        """Build a fresh ``JobRecord`` for this job.

        Convenience factory — the worker may call this before enqueueing
        so the record ID is determined before the Redis write.
        """
        estimate = self.estimate_cost()
        return JobRecord(
            id=str(uuid.uuid4()),
            job_type=self.job_type,
            state=JobState.PENDING,
            priority=self.priority,
            payload=payload or {},
            enqueued_at=datetime.now(tz=timezone.utc),
            estimated_tokens_in=estimate.estimated_tokens_in,
            estimated_tokens_out=estimate.estimated_tokens_out,
            requires_llm=estimate.estimated_tokens_in > 0,
            model=estimate.model if estimate.estimated_tokens_in > 0 else None,
        )
