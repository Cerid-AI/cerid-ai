# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Serves the versioned SDK OpenAPI spec at ``/sdk/v1/openapi.json``.

Builds an isolated FastAPI sub-application containing only SDK routes so
that the published spec excludes the 28+ internal routers.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(tags=["SDK"])

_cached_spec: dict | None = None


def _build_sdk_spec() -> dict:
    """Build an OpenAPI 3.1 spec containing only /sdk/v1/* endpoints."""
    from fastapi import FastAPI

    from app.routers.sdk import router as sdk_router
    from app.routers.sdk_version import SDK_VERSION

    sdk_app = FastAPI(
        title="Cerid AI SDK",
        version=SDK_VERSION,
        description=(
            "Stable versioned API for Cerid AI consumers.  "
            "Send X-Client-ID for per-client rate limiting and domain scoping."
        ),
    )
    sdk_app.include_router(sdk_router)
    spec = sdk_app.openapi()
    spec["info"]["contact"] = {"name": "Cerid AI", "url": "https://github.com/Cerid-AI/cerid-ai"}
    spec["info"]["license"] = {"name": "Apache-2.0", "url": "https://www.apache.org/licenses/LICENSE-2.0"}
    return spec


@router.get(
    "/sdk/v1/openapi.json",
    response_class=JSONResponse,
    summary="SDK OpenAPI Specification",
    tags=["SDK"],
    include_in_schema=False,
)
def sdk_openapi():
    """Returns the OpenAPI 3.1 spec for the SDK endpoints only."""
    global _cached_spec
    if _cached_spec is None:
        _cached_spec = _build_sdk_spec()
    return JSONResponse(content=_cached_spec)
