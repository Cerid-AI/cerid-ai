# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""App-level exception-handler registration.

Kept as a reusable function (mirroring
``settings_secrets.register_redacted_validation_handler``) so app startup
(``app/main.py``) AND tests that build a minimal app wire the IDENTICAL handler
— no divergent copies (Phase 2 / audit CEG-1).
"""
from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from fastapi import FastAPI


def register_cerid_error_handler(app: FastAPI) -> None:
    """Render any uncaught ``CeridError`` as structured JSON at its mapped HTTP
    status: ``FeatureGateError`` → 403, provider credit/rate → 402/429, everything
    else → 500. Wires the otherwise-dead ``errors.error_response`` renderer so
    domain errors don't fall through to a bare FastAPI 500 (audit CEG-1/CEG-2).
    """
    from fastapi import Request
    from fastapi.responses import JSONResponse

    from errors import CeridError, error_response

    @app.exception_handler(CeridError)
    async def _cerid_error_handler(_request: Request, exc: CeridError) -> JSONResponse:
        return JSONResponse(
            status_code=getattr(exc, "http_status", 500),
            content=error_response(exc),
        )
