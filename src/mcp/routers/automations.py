# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Re-export bridge — see app/routers/automations.py for implementation.
from app.routers.automations import *  # noqa: F401,F403
from app.routers.automations import (  # noqa: F401
    _auto_key,
    _register_job,
    _unregister_job,
    _validate_cron,
    get_redis,
)
