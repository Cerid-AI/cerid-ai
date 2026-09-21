# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cerid AI Python SDK -- typed client for the Cerid AI Knowledge Companion API."""

from cerid._async_client import AsyncCeridClient
from cerid.client import CeridClient
from cerid.errors import CeridSDKError, DomainRestrictedError, ProtocolVersionError

__all__ = [
    "CeridClient",
    "AsyncCeridClient",
    "CeridSDKError",
    "DomainRestrictedError",
    "ProtocolVersionError",
]

__version__ = "0.2.0"
