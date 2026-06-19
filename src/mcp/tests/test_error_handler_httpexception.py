# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""`@handle_errors` must let HTTPException propagate to FastAPI rather than
swallowing it into a fallback/RoutingError — otherwise routes that raise
404/422 silently return 200-with-fallback or a 500."""
from __future__ import annotations

import pytest
from fastapi import HTTPException

from utils.error_handler import handle_errors


@pytest.mark.asyncio
async def test_httpexception_propagates_through_fallback() -> None:
    @handle_errors(fallback={"ok": False})
    async def route() -> dict:
        raise HTTPException(status_code=404, detail="missing")

    with pytest.raises(HTTPException) as exc:
        await route()
    assert exc.value.status_code == 404
    assert exc.value.detail == "missing"


def test_httpexception_propagates_sync() -> None:
    @handle_errors(fallback={"ok": False})
    def route() -> dict:
        raise HTTPException(status_code=422, detail="bad")

    with pytest.raises(HTTPException) as exc:
        route()
    assert exc.value.status_code == 422


@pytest.mark.asyncio
async def test_non_http_error_still_uses_fallback() -> None:
    @handle_errors(fallback={"ok": False})
    async def route() -> dict:
        raise ValueError("boom")

    assert await route() == {"ok": False}
