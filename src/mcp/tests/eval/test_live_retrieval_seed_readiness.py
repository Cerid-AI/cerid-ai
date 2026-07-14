# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Structural gate for the live-retrieval harness's readiness fail-loud
contract (Quality-Maximization Phase 2 kickoff).

Pure unit tests — no live stack, no network. The 2026-07-13 baseline
contamination (all 18 fixtures timed out on the readiness poll, and the
harness scored anyway) happened because the pass/fail decision lived inline
in ``run_eval``. ``decide_seed_failure`` factors it out so the contract is
testable in isolation, mirroring the "structural" layer pattern used by
``test_verification_cases_v2_schema.py`` for its dataset gate.
"""
from __future__ import annotations

from tests.eval import _live_eval_common as common
from tests.eval.live_retrieval_eval import _build_arg_parser, decide_seed_failure


def test_all_ready_never_fails() -> None:
    failed, reason = decide_seed_failure([], allow_not_ready=False)
    assert failed is False
    assert reason is None


def test_not_ready_fails_loudly_by_default() -> None:
    failed, reason = decide_seed_failure(
        ["eval-fixture-coding-a.md", "eval-fixture-finance-b.md"],
        allow_not_ready=False,
    )
    assert failed is True
    assert reason is not None
    assert "eval-fixture-coding-a.md" in reason
    assert "eval-fixture-finance-b.md" in reason
    assert "2" in reason  # count of not-ready fixtures


def test_allow_not_ready_escape_hatch_suppresses_failure() -> None:
    failed, reason = decide_seed_failure(
        ["eval-fixture-coding-a.md"], allow_not_ready=True
    )
    assert failed is False
    assert reason is None


def test_all_ready_with_allow_not_ready_still_never_fails() -> None:
    """The escape hatch is a no-op when there's nothing to escape."""
    failed, reason = decide_seed_failure([], allow_not_ready=True)
    assert failed is False
    assert reason is None


def test_cli_seed_ready_timeout_default_matches_named_constant() -> None:
    """The CLI flag's default must track the shared named constant, never a
    duplicated bare literal (magic-number ratchet)."""
    args = _build_arg_parser().parse_args([])
    assert args.seed_ready_timeout_s == common.SEED_READY_TIMEOUT_S


def test_cli_seed_ready_timeout_is_configurable() -> None:
    args = _build_arg_parser().parse_args(["--seed-ready-timeout-s", "90"])
    assert args.seed_ready_timeout_s == 90.0


def test_cli_allow_not_ready_defaults_off() -> None:
    args = _build_arg_parser().parse_args([])
    assert args.allow_not_ready is False


def test_cli_allow_not_ready_flag_parses() -> None:
    args = _build_arg_parser().parse_args(["--allow-not-ready"])
    assert args.allow_not_ready is True
