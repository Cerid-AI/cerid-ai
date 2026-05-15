# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Regression test for ingest_recovery's chroma.get_collection call shape.

The ``_EmbeddingAwareClient`` proxy in ``app/deps.py`` accepts ``**kwargs``
only. Pre-2026-05-15 ``scan_orphans`` and ``recover_orphan`` called
``chroma.get_collection`` positionally, which raised
``TypeError: takes 1 positional argument but 2 were given`` on every call
— silently swallowed via ``log_swallowed_error``. The recovery loop
appeared to run but never processed any orphan chunks.

This test pins the kwargs-only call shape so the regression can't
re-land.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


@pytest.mark.asyncio
async def test_scan_orphans_calls_get_collection_with_kwargs() -> None:
    """scan_orphans must pass ``name=`` to chroma.get_collection."""
    from app.services.ingest_recovery import scan_orphans

    fake_collection = MagicMock()
    fake_collection.name = "domain_test"
    chroma = MagicMock()
    chroma.get_collection = MagicMock(return_value=fake_collection)

    with (
        patch(
            "app.services.ingest_recovery.get_chroma",
            return_value=chroma,
        ),
        patch(
            "app.services.ingest_recovery._get_all_collections",
            return_value=[fake_collection],
        ),
        patch(
            "app.services.ingest_recovery._fetch_pending_chunks",
            return_value=[],
        ),
    ):
        await scan_orphans(max_age_seconds=60.0)

    # The proxy at app/deps.py:90 raises TypeError when called positionally.
    # Asserting kwargs-only fixes the contract.
    assert chroma.get_collection.called, "chroma.get_collection was not called"
    last = chroma.get_collection.call_args
    assert last.args == (), (
        f"get_collection must not be called positionally — got args={last.args!r}, "
        "kwargs={last.kwargs!r}"
    )
    assert "name" in last.kwargs, (
        f"get_collection must receive name= kwarg, got kwargs={last.kwargs!r}"
    )
    assert last.kwargs["name"] == "domain_test"


def test_recover_orphan_source_uses_get_collection_kwarg() -> None:
    """Source-level pin: recover_orphan calls get_collection with name= kwarg.

    Calling recover_orphan end-to-end requires mocking 6+ internal helpers
    that change shape between releases. The bug class we care about is
    purely the call signature, so a source-string match catches the
    regression without coupling to the recovery state machine.
    """
    import inspect

    from app.services import ingest_recovery

    src = inspect.getsource(ingest_recovery.recover_orphan)
    assert "chroma.get_collection, name=" in src or "chroma.get_collection, name =" in src, (
        "recover_orphan must call chroma.get_collection with name= kwarg "
        "(positional rejected by _EmbeddingAwareClient proxy at "
        "app/deps.py:90)"
    )
    # Also assert scan_orphans (the other call site) keeps the same form
    src2 = inspect.getsource(ingest_recovery.scan_orphans)
    assert "chroma.get_collection, name=" in src2
