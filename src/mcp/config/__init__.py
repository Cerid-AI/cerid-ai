# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Re-export bridge — central configuration backward-compat shim.

All existing ``import config`` / ``from config import X`` statements continue
to work unchanged.  Internally the settings are split into:

- ``config.taxonomy``  — domains, extensions, cross-domain affinity
- ``config.settings``  — chunking, URLs, scheduling, search tuning
- ``config.features``  — feature flags, toggles, plugin system

The "Re-export bridge" marker above opts this file out of the
``lint-import-star-without-all`` gate per the convention documented
in ``docs/CONVENTIONS.md::Re-export bridges``. Underscore names that
need re-export are listed in each sub-module's ``__all__``.
"""

from config.features import *  # noqa: F401,F403
from config.settings import *  # noqa: F401,F403
from config.taxonomy import *  # noqa: F401,F403
