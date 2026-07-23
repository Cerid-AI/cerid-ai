# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E1 Phase-5 verifiability harness — A2A task-record PRIVATE-MODE persistence.

Registry: ``docs/superpowers/specs/2026-07-17-audit-e1-findings-registry.jsonl``
(CR-085, sub-claim 4 — the only surviving half after adversarial validation:
the private-mode-bypass / consumer-isolation / hall:{cid} sub-claims were all
closed by the Phase-1 RequestContext gate + ``saves_blocked()``).

Post-remediation audit (2026-07-23) R1/R12: CR-085 redacted input/output in Redis
but ``A2ATask.input`` stayed required ``dict``, so GET/cancel 500'd on the
redacted record; ``metadata`` was never redacted. This harness covers both.

The fix redacts ``input``/``output``/``metadata`` from the persisted copy at L1+
via ``_persist_view`` inside the real ``_save_task``; ``create_task`` still
returns the real result inline. Probes drive the REAL ``_save_task`` (NOT
monkeypatched — the redaction lives there) with a fake Redis. RED-then-GREEN.
"""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError


class _FakeRedis:
    """Minimal dict-backed Redis for the task lifecycle (set/get/rpush/expire)."""

    def __init__(self) -> None:
        self.store: dict = {}

    def set(self, key, value, ex=None):  # noqa: ARG002
        self.store[key] = value

    def get(self, key):
        return self.store.get(key)

    def rpush(self, key, value):
        self.store.setdefault(key, []).append(value)

    def expire(self, key, ttl):  # noqa: ARG002
        pass


def _wire(monkeypatch, *, level: int):
    """Point a2a at a fake Redis and pin the global private-mode level."""
    import app.routers.a2a as a2a_mod
    import app.services.private_mode as pm_mod

    fake = _FakeRedis()
    monkeypatch.setattr(a2a_mod, "get_redis", lambda: fake)
    # saves_blocked() -> private_blocks(1) -> get_private_mode_level(); pin it.
    monkeypatch.setattr(pm_mod, "get_private_mode_level", lambda: level)

    async def _skill(_inp):
        return {"answer": "SECRET_KB_CONTENT"}

    monkeypatch.setitem(a2a_mod.SKILL_MAP, "knowledge-query", _skill)
    return a2a_mod, fake


def _persisted_task(fake: _FakeRedis) -> dict:
    prefix = "cerid:a2a:tasks:"
    key = next(k for k in fake.store if k.startswith(prefix))
    return json.loads(fake.store[key])


@pytest.mark.preservation
async def test_private_mode_redacts_persisted_a2a_task(monkeypatch):
    """At L1+, the durable Redis task record must NOT carry the caller input,
    skill output, or correlation metadata — while create_task still returns
    them inline. RED on pre-R1 HEAD: metadata survived; GET/cancel 500'd."""
    a2a_mod, fake = _wire(monkeypatch, level=1)

    returned = await a2a_mod.create_task(
        a2a_mod.A2ATaskRequest(
            skill_id="knowledge-query",
            input={"query": "SECRET_QUERY"},
            metadata={"trace_id": "SECRET_TRACE"},
        )
    )

    # The synchronous response still carries the real result — the caller is
    # unaffected by the durable-store redaction.
    assert returned["status"] == "completed"
    assert returned["output"] == {"answer": "SECRET_KB_CONTENT"}
    assert returned["input"] == {"query": "SECRET_QUERY"}
    assert returned["metadata"] == {"trace_id": "SECRET_TRACE"}

    # The durable Redis copy is payload-redacted (R1 + R12).
    persisted = _persisted_task(fake)
    assert persisted["output"] is None
    assert persisted["input"] is None
    assert persisted["metadata"] is None
    # Non-conversation fields survive (status/id/skill_id/timestamps).
    assert persisted["status"] == "completed"
    assert persisted["skill_id"] == "knowledge-query"


@pytest.mark.preservation
async def test_private_mode_off_persists_full_a2a_task(monkeypatch):
    """At L0 (private mode off) the durable record is unchanged — the redaction
    must be scoped to L1+, never a blanket strip."""
    a2a_mod, fake = _wire(monkeypatch, level=0)

    await a2a_mod.create_task(
        a2a_mod.A2ATaskRequest(
            skill_id="knowledge-query",
            input={"query": "PUBLIC_QUERY"},
            metadata={"trace_id": "PUBLIC_TRACE"},
        )
    )

    persisted = _persisted_task(fake)
    assert persisted["output"] == {"answer": "SECRET_KB_CONTENT"}
    assert persisted["input"] == {"query": "PUBLIC_QUERY"}
    assert persisted["metadata"] == {"trace_id": "PUBLIC_TRACE"}


@pytest.mark.preservation
async def test_get_task_accepts_redacted_private_record(monkeypatch):
    """R1: GET /a2a/tasks/{id} must return 200 for an L1+ redacted record.
    Pre-fix: A2ATask.input was required dict → ValidationError / 500."""
    a2a_mod, fake = _wire(monkeypatch, level=1)

    created = await a2a_mod.create_task(
        a2a_mod.A2ATaskRequest(
            skill_id="knowledge-query",
            input={"query": "SECRET_QUERY"},
            metadata={"trace_id": "SECRET_TRACE"},
        )
    )
    task_id = created["id"]

    # Response-model validation is what 500'd — exercise A2ATask on the load path.
    loaded = await a2a_mod.get_task(task_id)
    validated = a2a_mod.A2ATask.model_validate(loaded)
    assert validated.id == task_id
    assert validated.status == "completed"
    assert validated.input is None
    assert validated.output is None
    assert validated.metadata is None


@pytest.mark.preservation
async def test_cancel_accepts_redacted_working_record(monkeypatch):
    """R1: cancel must also tolerate a redacted durable record (same response_model)."""
    a2a_mod, _fake = _wire(monkeypatch, level=1)

    # Persist a working task directly (skill still "running" from cancel's POV).
    task_id = "task-r1-cancel"
    a2a_mod._save_task(
        {
            "id": task_id,
            "skill_id": "knowledge-query",
            "status": "working",
            "input": None,
            "output": None,
            "error": None,
            "created_at": "2026-07-23T00:00:00+00:00",
            "updated_at": "2026-07-23T00:00:00+00:00",
            "metadata": None,
        }
    )

    canceled = await a2a_mod.cancel_task(task_id)
    validated = a2a_mod.A2ATask.model_validate(canceled)
    assert validated.status == "canceled"
    assert validated.input is None
    assert validated.metadata is None


@pytest.mark.preservation
def test_a2a_task_model_accepts_null_payload_fields():
    """Direct model contract: null input/output/metadata are valid (R1/R12)."""
    task = {
        "id": "t1",
        "skill_id": "knowledge-query",
        "status": "completed",
        "input": None,
        "output": None,
        "error": None,
        "created_at": "2026-07-23T00:00:00+00:00",
        "updated_at": "2026-07-23T00:00:00+00:00",
        "metadata": None,
    }
    from app.routers.a2a import A2ATask

    validated = A2ATask.model_validate(task)
    assert validated.input is None
    assert validated.output is None
    assert validated.metadata is None

    # Sanity: required identity fields still required.
    with pytest.raises(ValidationError):
        A2ATask.model_validate({**task, "id": None})
