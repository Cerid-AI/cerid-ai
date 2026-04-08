# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Central configuration — re-exports from sub-modules for backward compatibility.

All existing ``import config`` / ``from config import X`` statements continue
to work unchanged.  Internally the settings are split into:

- ``config.taxonomy``  — domains, extensions, cross-domain affinity
- ``config.settings``  — chunking, URLs, scheduling, search tuning
- ``config.features``  — feature flags, toggles, plugin system
"""

from config.features import *  # noqa: F401,F403
from config.settings import *  # noqa: F401,F403
from config.taxonomy import *  # noqa: F401,F403
