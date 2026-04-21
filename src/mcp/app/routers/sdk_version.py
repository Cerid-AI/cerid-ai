# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Single source of truth for the /sdk/v1/* API version.

Imported by `app.routers.sdk` (routes), `app.routers.sdk_openapi`
(published spec), and `packages/sdk/python/tests/test_client.py`
(contract fixture). Bumping the version here flows to all three
and is caught by the sdk-openapi-drift CI job."""
from __future__ import annotations

SDK_VERSION = "1.1.0"
