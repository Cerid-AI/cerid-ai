# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Version constants for the Cerid Python SDK.

``SDK_PROTOCOL_VERSION`` pins the wire-protocol version this client was
built against and mirrors ``app.routers.sdk_version.SDK_VERSION`` in the
server. Drift between the two is caught by the ``sdk-openapi-drift`` CI
job + the test_client contract fixture. Bump both together."""
from __future__ import annotations

SDK_PROTOCOL_VERSION = "1.1.0"
