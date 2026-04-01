# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Private mode utilities — check ephemeral session state."""

from __future__ import annotations

from deps import get_redis
from errors import ConfigError


def get_private_mode_level(client_id: str) -> int:
    """Return the private mode level for a client (0 = disabled)."""
    try:
        redis = get_redis()
        level = redis.get(f"cerid:private_mode:{client_id}")
        return int(level) if level is not None else 0
    except (ConfigError, ValueError, OSError, RuntimeError):
        return 0
