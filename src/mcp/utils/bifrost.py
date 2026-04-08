# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Re-export bridge — implementation lives in core.utils.bifrost."""

from core.utils.bifrost import *  # noqa: F401,F403
from core.utils.bifrost import (  # noqa: F401
    call_bifrost,
    close_bifrost_client,
    extract_content,
    get_bifrost_client,
    set_redis_getter,
)

# Wire Redis getter so core/utils/bifrost.py can cache credit-exhaustion state
try:
    from deps import get_redis
    set_redis_getter(get_redis)
except ImportError:
    pass
