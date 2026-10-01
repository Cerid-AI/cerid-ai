# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The one rule for the word "verified".

A claim counts as verified only when a source the user can open supports it:
a KB artifact, or a web result with a URL. A second model agreeing from its own
training carries no source; its verdict keeps ``status == "verified"`` on the
wire and is counted and labelled as agreement, not as verified.

The rule reads fields the verdict already carries. A verdict reached on a KB
path was graded against the user's own documents, so the method alone marks it
as backed even when the matched chunk has no artifact id.

Mirrored in the web client by ``isSourceBacked`` in
``src/web/src/lib/verification-utils.ts``.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

KB_METHODS = frozenset({"kb", "kb_nli", "kb_batch", "kb_only_timeout"})


def is_source_backed(claim: Mapping[str, Any]) -> bool:
    """True when the claim rests on the KB, a KB artifact, or at least one URL."""
    if claim.get("verification_method") in KB_METHODS:
        return True
    if claim.get("source_artifact_id"):
        return True
    return any(claim.get("source_urls") or [])


def counts_as_verified(claim: Mapping[str, Any]) -> bool:
    """True when the verdict is positive and a source backs it."""
    return claim.get("status") == "verified" and is_source_backed(claim)


def is_agreement_only(claim: Mapping[str, Any]) -> bool:
    """True when the verdict is positive and no source backs it."""
    return claim.get("status") == "verified" and not is_source_backed(claim)
