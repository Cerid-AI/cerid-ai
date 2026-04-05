# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Bridge module for smart_router — re-exports from core.routing.smart_router.

Proxies mutable state attributes (_ollama_available, _ollama_checked_at,
_ollama_models) to the core module so tests that set them via this bridge
actually modify the authoritative state.
"""

import sys

import core.routing.smart_router as _core_mod

# Re-export everything
from core.routing.smart_router import *  # noqa: F401,F403
from core.routing.smart_router import (  # noqa: F401
    _check_ollama,
    _classify_complexity,
    _ollama_models,
)

# Mutable state names that must be proxied to the core module.
_PROXIED = frozenset({
    "_ollama_available", "_ollama_checked_at", "_ollama_models",
    "_check_ollama",  # function — proxied so patches on bridge affect core
})

_this = sys.modules[__name__]
_orig_class = type(_this)


class _ProxyModule(_orig_class):
    """Module subclass that proxies attribute access for mutable state."""

    def __getattr__(self, name: str):
        if name in _PROXIED:
            return getattr(_core_mod, name)
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name: str, value):
        if name in _PROXIED:
            setattr(_core_mod, name, value)
        else:
            super().__setattr__(name, value)


_this.__class__ = _ProxyModule
