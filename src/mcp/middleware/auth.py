# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

# Re-export bridge — see app/middleware/auth.py for implementation.
from app.middleware.auth import *  # noqa: F401,F403
from app.middleware.auth import _redact_ip  # noqa: F401
