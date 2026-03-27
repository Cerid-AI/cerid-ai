# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Re-export bridge — implementation lives in core.utils.bifrost."""

from core.utils.bifrost import *  # noqa: F401,F403
from core.utils.bifrost import call_bifrost, close_bifrost_client, extract_content, get_bifrost_client  # noqa: F401
