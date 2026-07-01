# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 4 — SSE-formatter convergence + A2A capability-drift guard.

* ``sse_event`` / ``sse_done`` produce byte-identical frames to the hand-rolled
  ``f"data: {json.dumps(x)}\\n\\n"`` lines they replace.
* The A2A agent card's advertised ``capabilities.streaming`` matches reality —
  the audit (2026-06-29) caught it falsely advertising ``true`` while every
  skill returns a buffered dict. This guard fails if the flag and the code drift
  apart again in either direction.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ── sse_event / sse_done ────────────────────────────────────────────────────

def test_sse_event_json_encodes_dict():
    from core.utils.sse import sse_event

    payload = {"type": "connected", "source_id": "abc"}
    assert sse_event(payload) == f"data: {json.dumps(payload)}\n\n"


def test_sse_event_passes_through_str_no_double_encode():
    from core.utils.sse import sse_event

    already = json.dumps({"a": 1})
    # A caller holding a pre-serialized string must not get it double-encoded.
    assert sse_event(already) == f"data: {already}\n\n"


def test_sse_event_named_event_matches_handrolled():
    from core.utils.sse import sse_event

    ts = {"ts": 123.5}
    # Byte-identical to agent_console's old heartbeat line.
    assert sse_event(ts, event="heartbeat") == f"event: heartbeat\ndata: {json.dumps(ts)}\n\n"


def test_sse_done_sentinel():
    from core.utils.sse import sse_done

    assert sse_done() == "data: [DONE]\n\n"


def test_agent_console_adopts_helper():
    """Regression: the migrated router uses the shared helper, not a raw f-string."""
    src = (Path(__file__).resolve().parent.parent / "app" / "routers" / "agent_console.py").read_text()
    assert "from core.utils.sse import sse_event" in src
    assert 'f"data: {json.dumps(' not in src, "agent_console re-grew a hand-rolled SSE line"


# ── A2A capability-drift guard ──────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a2a_streaming_capability_matches_reality():
    """The card's `streaming` flag must equal whether the A2A router streams.

    Reality proxy: the A2A router uses ``StreamingResponse`` iff it truly streams.
    Advertise ``streaming: true`` only when it does. This guard fails if someone
    flips the flag without adding streaming, or adds streaming without flipping
    the flag.
    """
    from app.routers.a2a import agent_card

    card = await agent_card()
    advertised = card["capabilities"]["streaming"]

    a2a_src = (Path(__file__).resolve().parent.parent / "app" / "routers" / "a2a.py").read_text()
    actually_streams = "StreamingResponse" in a2a_src

    assert advertised == actually_streams, (
        f"A2A agent card advertises streaming={advertised} but the router "
        f"{'uses' if actually_streams else 'does NOT use'} StreamingResponse — "
        "capability drift (audit 2026-06-29)."
    )


@pytest.mark.asyncio
async def test_a2a_card_is_well_formed():
    from app.routers.a2a import agent_card

    card = await agent_card()
    assert set(card["capabilities"]) >= {"streaming", "pushNotifications", "stateTransitionHistory"}
    assert card["skills"], "agent card must advertise at least one skill"
    for skill in card["skills"]:
        assert {"id", "name", "description"} <= set(skill)
