# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Eval-suite fixtures.

The isolated retrieval/hybrid evals seed chunks that carry no ``cerid_state`` and
run single-user, so they need the pending-exclusion filter OFF and multi-user
mode disabled. Those env vars are read live (``os.getenv``) by
``query_agent._exclude_pending`` / ``with_tenant_scope`` at query time, so they
must be set before a query runs — but they MUST NOT leak into the rest of the
suite. Setting them at module import (as the harness used to) pollutes the whole
pytest session during collection and breaks order-independent tests elsewhere
(e.g. ``test_tenant_scope`` then sees the pending filter disabled). Scoping them
to this package via an autouse ``monkeypatch`` fixture sets them only for eval
tests and auto-restores after each one.
"""
import os

import pytest

# Captured at collection time — BEFORE any fixture runs. The parent suite's
# autouse ``_reset_llm_client`` (tests/conftest.py) replaces
# OPENROUTER_API_KEY with "test-dummy-key" so unit tests hit mocked clients;
# the eval gates make REAL judge calls, and with a dummy key they 401
# (found 2026-07-09: the ragas gate could never pass with the key present —
# masked in CI by the missing repo secret, which made it skip-as-success).
_REAL_OPENROUTER_KEY = os.getenv("OPENROUTER_API_KEY")


@pytest.fixture(autouse=True)
def _eval_retrieval_env(monkeypatch):
    """Per-test, auto-restored env for the isolated retrieval/hybrid evals."""
    monkeypatch.setenv("CERID_FILTER_PENDING_CHUNKS", "false")
    monkeypatch.setenv("TOKENIZERS_PARALLELISM", "false")
    monkeypatch.delenv("CERID_MULTI_USER", raising=False)
    # Restore the real judge key over the parent conftest's dummy (package
    # fixtures instantiate after parent-conftest fixtures, so this wins).
    if _REAL_OPENROUTER_KEY:
        monkeypatch.setenv("OPENROUTER_API_KEY", _REAL_OPENROUTER_KEY)
    yield
