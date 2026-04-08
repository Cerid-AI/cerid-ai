# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

# Re-export bridge — see app/middleware/auth.py for implementation.
from app.middleware.auth import *  # noqa: F401,F403
from app.middleware.auth import _redact_ip  # noqa: F401
