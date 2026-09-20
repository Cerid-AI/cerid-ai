# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""SDK_PROTOCOL_VERSION is a compatibility contract, so it has to be checked.

The constant pins the wire protocol this client was built against. The only
place the server states its own protocol version is the version field on the
health / settings responses — so that is where the comparison happens, with no
extra round trip.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import httpx
import pytest
from cerid import CeridClient
from cerid.__version__ import SDK_PROTOCOL_VERSION
from cerid.errors import ProtocolVersionError

SDK_MAJOR = SDK_PROTOCOL_VERSION.split(".")[0]
OTHER_MAJOR = str(int(SDK_MAJOR) + 1)


def _mock_get(client: CeridClient, body: dict) -> None:
    client._http.get = MagicMock(
        return_value=httpx.Response(200, json=body, request=httpx.Request("GET", "http://test"))
    )


@pytest.mark.parametrize("method", ["health", "health_detailed", "settings"])
def test_major_version_mismatch_raises(method: str) -> None:
    with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
        _mock_get(client, {"status": "healthy", "version": f"{OTHER_MAJOR}.0.0", "services": {}, "tier": "community", "features": {}})
        with pytest.raises(ProtocolVersionError) as excinfo:
            getattr(client.system, method)()

    assert excinfo.value.server_version == f"{OTHER_MAJOR}.0.0"
    assert SDK_PROTOCOL_VERSION in str(excinfo.value)


def test_same_major_is_compatible() -> None:
    with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
        _mock_get(client, {"status": "healthy", "version": f"{SDK_MAJOR}.99.4", "services": {}, "features": {}})
        assert client.system.health().version == f"{SDK_MAJOR}.99.4"


@pytest.mark.parametrize("version", ["", 2, None])
def test_unstated_version_is_not_a_mismatch(version: object) -> None:
    """Older servers and the Any-typed settings route may not state a usable
    version — that is unknown, not incompatible."""
    with CeridClient(base_url="http://localhost:8888", client_id="test") as client:
        _mock_get(client, {"version": version, "tier": "community", "features": {}})
        client.system.settings()
