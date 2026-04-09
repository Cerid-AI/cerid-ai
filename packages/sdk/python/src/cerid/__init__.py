# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Cerid AI Python SDK -- typed client for the Cerid AI Knowledge Companion API."""

from cerid._async_client import AsyncCeridClient
from cerid.client import CeridClient
from cerid.errors import CeridSDKError

__all__ = [
    "CeridClient",
    "AsyncCeridClient",
    "CeridSDKError",
]

__version__ = "0.1.0"
