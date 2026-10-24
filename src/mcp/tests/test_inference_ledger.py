# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Local completions are counted. A truncation is still a completion."""
from __future__ import annotations

import inspect

from core.utils.inference_ledger import (
    note_local_completion,
    record_local_attempt,
    reset,
    snapshot,
    token_count,
)
from core.utils.internal_llm import _call_ollama


def setup_function() -> None:
    reset()


def test_a_completed_local_response_counts_the_tokens_the_server_reported() -> None:
    note_local_completion({"done_reason": "stop", "eval_count": 12, "prompt_eval_count": 4})
    assert snapshot() == {"attempts": 1, "tokens": 16, "truncated": 0, "failed": 0}


def test_a_length_truncation_is_counted_and_does_not_raise() -> None:
    note_local_completion({
        "done_reason": "length",
        "eval_count": 256,
        "message": {"content": "partial"},
    })
    assert snapshot()["truncated"] == 1
    assert snapshot()["failed"] == 1
    assert snapshot()["tokens"] == 256


def test_junk_token_fields_count_as_zero() -> None:
    assert token_count({"eval_count": "nope", "prompt_eval_count": None}) == 0


def test_a_failed_attempt_is_counted_on_its_own() -> None:
    record_local_attempt(failed=True)
    assert snapshot()["failed"] == 1
    assert snapshot()["attempts"] == 1
    assert snapshot()["tokens"] == 0


def test_the_local_call_records_both_completions_and_failures() -> None:
    source = inspect.getsource(_call_ollama)
    assert "note_local_completion" in source
    assert "record_local_attempt(failed=True)" in source
