# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Early-load compatibility shims applied before chromadb imports.

TEMPORARY (track upstream): chromadb 0.5.23 calls
    posthog.capture(user_id, event_name, properties)
with three positional args, but posthog 7.x changed the module-level
`capture` signature to `(event: str, **kwargs)`. Even though chromadb
sets `posthog.disabled = True` when `anonymized_telemetry` is off (which
the MCP container does via ANONYMIZED_TELEMETRY=false in compose),
posthog 7.x validates the call signature *before* checking the disabled
flag — every chromadb client start raises a TypeError visible as
`chromadb.telemetry.product.posthog ERROR ... capture() takes 1 positional
argument but 3 were given`.

This module replaces `posthog.capture` with a no-op so the failed call
goes quiet. Telemetry stays off (unchanged); only the noisy log line is
suppressed. Remove once chromadb's posthog call site is fixed upstream
or the project upgrades to a chromadb release that targets posthog 7.x.

Must be imported *before* any module that does `import chromadb`
(directly or transitively). `app.main` imports this first.
"""

from __future__ import annotations

import posthog


def _noop_capture(*_args: object, **_kwargs: object) -> None:
    return None


posthog.capture = _noop_capture  # type: ignore[assignment]
posthog.disabled = True
