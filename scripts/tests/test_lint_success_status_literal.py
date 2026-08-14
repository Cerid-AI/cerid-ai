# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Unit tests for scripts/lint-success-status-literal.py (Gate 4 —
success-status-on-failure).

Covers both detector shapes (literal "success" passed to a *_log_execution
call; a `.state = SomeState.COMPLETED` assignment), the three unguarded
triggers (counter interpolated in the same message, a loop-nested silent
except with no counter at all, an unguarded enum assignment), and the two
things that must NOT fire: an if/else that already gates on the failure
signal, and a function with no failure-signal concept at all (nothing to
guard against).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_success_status_literal", _ROOT / "scripts" / "lint-success-status-literal.py",
)
assert _SPEC is not None and _SPEC.loader is not None
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_success_status_literal"] = lint
_SPEC.loader.exec_module(lint)


def _check(source: str, rel: str = "app/fake.py") -> list["lint.Violation"]:
    return lint.check_source(source, rel)


class TestLiteralCallCounterInMessage:
    """AF-019 / quarantine_purge / folder_scan shape: a fail-named counter
    is computed and printed in the same success message, unguarded."""

    def test_unguarded_counter_in_inline_fstring_flagged(self) -> None:
        src = (
            "async def _run_thing() -> None:\n"
            "    failed = 0\n"
            "    for item in items:\n"
            "        try:\n"
            "            do(item)\n"
            "        except Exception:\n"
            "            failed += 1\n"
            "    _log_execution('thing', 'success', 1.0, f'{failed} failed')\n"
        )
        v = _check(src)
        assert len(v) == 1
        assert v[0].kind == "literal-call"
        assert v[0].lineno == 8

    def test_unguarded_counter_via_one_hop_variable_flagged(self) -> None:
        """The webhook_drain / folder_scan shape: `detail = f"...{failed}..."`
        assigned separately, then passed as a bare Name."""
        src = (
            "async def _run_thing() -> None:\n"
            "    failed = 0\n"
            "    for item in items:\n"
            "        try:\n"
            "            do(item)\n"
            "        except Exception:\n"
            "            failed += 1\n"
            "    detail = f'failed={failed}'\n"
            "    _log_execution('thing', 'success', 1.0, detail)\n"
        )
        v = _check(src)
        assert len(v) == 1
        assert v[0].kind == "literal-call"

    def test_if_else_gate_on_counter_not_flagged(self) -> None:
        """The sync_export shape already in the codebase: correctly gated."""
        src = (
            "async def _run_thing() -> None:\n"
            "    failed = 0\n"
            "    for item in items:\n"
            "        try:\n"
            "            do(item)\n"
            "        except Exception:\n"
            "            failed += 1\n"
            "    if failed:\n"
            "        _log_execution('thing', 'error', 1.0, f'{failed} failed')\n"
            "    else:\n"
            "        _log_execution('thing', 'success', 1.0, 'clean')\n"
        )
        assert _check(src) == []

    def test_no_failure_signal_at_all_not_flagged(self) -> None:
        """No counter, no swallowed exception — nothing to guard against."""
        src = (
            "async def _run_thing() -> None:\n"
            "    result = await do_the_thing()\n"
            "    _log_execution('thing', 'success', 1.0, str(result))\n"
        )
        assert _check(src) == []


class TestSilentSwallow:
    """AF-021 shape: a loop-nested except swallows failures with zero
    counting, so success is reported with no failure signal at all."""

    def test_silent_loop_except_before_success_flagged(self) -> None:
        src = (
            "async def _run_poll() -> None:\n"
            "    polled = ingested = 0\n"
            "    for src in sources:\n"
            "        polled += 1\n"
            "        try:\n"
            "            async for event in fetch(src):\n"
            "                ingested += 1\n"
            "        except Exception as exc:\n"
            "            log_swallowed_error('poll.fetch', exc)\n"
            "    detail = f'polled={polled} ingested={ingested}'\n"
            "    _log_execution('poll', 'success', 1.0, detail)\n"
        )
        v = _check(src)
        assert len(v) == 1
        assert v[0].kind == "silent-swallow"

    def test_silent_except_after_success_call_not_flagged(self) -> None:
        """A best-effort side effect (e.g. firing a webhook) AFTER the
        success line is reported must not retroactively taint it — matches
        _run_daily_digest's digest.ready webhook fire-and-forget."""
        src = (
            "async def _run_thing() -> None:\n"
            "    result = await do_the_thing()\n"
            "    _log_execution('thing', 'success', 1.0, str(result))\n"
            "    for hook in hooks:\n"
            "        try:\n"
            "            await fire(hook)\n"
            "        except Exception as exc:\n"
            "            log_swallowed_error('thing.hook', exc)\n"
        )
        assert _check(src) == []

    def test_silent_except_outside_loop_not_flagged(self) -> None:
        """A single best-effort optional read (not a per-item sweep) is not
        the AF-021 shape — matches sync_export's manifest-cursor read."""
        src = (
            "async def _run_thing() -> None:\n"
            "    since = None\n"
            "    try:\n"
            "        since = read_manifest()\n"
            "    except (FileNotFoundError, ValueError):\n"
            "        pass\n"
            "    _log_execution('thing', 'success', 1.0, 'ok')\n"
        )
        assert _check(src) == []

    def test_except_that_reraises_not_flagged(self) -> None:
        src = (
            "async def _run_thing() -> None:\n"
            "    for item in items:\n"
            "        try:\n"
            "            do(item)\n"
            "        except Exception:\n"
            "            raise\n"
            "    _log_execution('thing', 'success', 1.0, 'ok')\n"
        )
        assert _check(src) == []


class TestEnumAssign:
    """AF-037 shape: a `.state = XState.COMPLETED` assignment with no
    reference to the run's own result/failure signal."""

    def test_unguarded_completed_assign_flagged(self) -> None:
        src = (
            "async def mark_completed(self, job_id: str, result: JobResult) -> None:\n"
            "    record = await self._load_record(job_id)\n"
            "    if record is None:\n"
            "        return\n"
            "    record.state = JobState.COMPLETED\n"
        )
        v = _check(src)
        assert len(v) == 1
        assert v[0].kind == "enum-assign"
        assert v[0].lineno == 5

    def test_guarded_by_result_reference_not_flagged(self) -> None:
        src = (
            "async def mark_completed(self, job_id: str, result: JobResult) -> None:\n"
            "    record = await self._load_record(job_id)\n"
            "    if result.metadata.get('failed'):\n"
            "        record.state = JobState.FAILED\n"
            "    else:\n"
            "        record.state = JobState.COMPLETED\n"
        )
        assert _check(src) == []

    def test_status_suffix_enum_not_flagged(self) -> None:
        """`RunStatus` (not `*State`) inside an exception-based try/except
        with no internal counter is a different mechanism — see
        workflows.py's `run.status = RunStatus.COMPLETED`."""
        src = (
            "async def execute(self) -> None:\n"
            "    try:\n"
            "        for node in order:\n"
            "            step(node)\n"
            "        run.status = RunStatus.COMPLETED\n"
            "    except Exception as exc:\n"
            "        run.status = RunStatus.FAILED\n"
        )
        assert _check(src) == []

    def test_non_completed_member_not_flagged(self) -> None:
        src = "def f() -> None:\n    record.state = JobState.FAILED\n"
        assert _check(src) == []


class TestAllowlistShrinkOnly:
    def test_load_allowlist_parses_reason(self, tmp_path, monkeypatch) -> None:
        allow_file = tmp_path / "allow.txt"
        allow_file.write_text(
            "# comment\n\nsrc/mcp/app/x.py:10  # AF-999 — example reason\n",
        )
        monkeypatch.setattr(lint, "ALLOWLIST_PATH", allow_file)
        allow = lint._load_allowlist()
        assert allow == {"src/mcp/app/x.py:10": "AF-999 — example reason"}
