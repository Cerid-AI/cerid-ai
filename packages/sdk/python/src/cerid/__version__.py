# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Version constants for the Cerid Python SDK.

``SDK_PROTOCOL_VERSION`` pins the wire-protocol version this client was
built against and mirrors ``app.routers.sdk_version.SDK_VERSION`` in the
server. Drift between the two is caught at build time by the spec drift
check (``scripts/gen_sdk_openapi.py --check``) and the contract fixture,
and at run time by
``_BaseClient._assert_protocol_compatible``: the health and settings
responses carry the server's protocol version, and a differing major
version raises ``ProtocolVersionError``. Bump both together."""
from __future__ import annotations

SDK_PROTOCOL_VERSION = "1.2.0"
