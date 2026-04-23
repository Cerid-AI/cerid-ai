# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Platform-aware ONNX Runtime execution provider resolver.

Both the embedding model (``core.utils.embeddings``) and the cross-encoder
reranker (``core.retrieval.reranker``) load ONNX sessions. Hardcoding
``["CPUExecutionProvider"]`` at the call site silently disables the GPU /
NPU acceleration that the host advertises (CUDA on Linux, CoreML on Apple
silicon, ROCm on AMD).  This module returns the highest-priority provider
available on the current host, with CPU always present as the safety net.

Operators may override the preference list via the
``ONNX_EXECUTION_PROVIDERS`` env var (comma-separated, in order).
"""
from __future__ import annotations

import logging

import onnxruntime as ort

logger = logging.getLogger("ai-companion.onnx_providers")

# Preference order applied when no env override is given.  CPU is always
# appended at the end as the universally-available fallback so a single
# missing provider never breaks model loading.
_DEFAULT_PRIORITY: tuple[str, ...] = (
    "CUDAExecutionProvider",
    "ROCMExecutionProvider",
    "CoreMLExecutionProvider",
    "DmlExecutionProvider",
    "CPUExecutionProvider",
)


def resolve_providers(override: str = "") -> list[str]:
    """Return the ORT execution providers to pass to ``InferenceSession``.

    The returned list is intersected with ``ort.get_available_providers()``
    (so missing providers degrade gracefully) and always ends with
    ``CPUExecutionProvider``.

    ``override`` is the raw value of the ``ONNX_EXECUTION_PROVIDERS`` env
    var.  When non-empty, its comma-separated entries take priority over
    the built-in default order.  Empty / whitespace entries are skipped.
    """
    available = set(ort.get_available_providers())

    requested: tuple[str, ...]
    if override.strip():
        requested = tuple(p.strip() for p in override.split(",") if p.strip())
    else:
        requested = _DEFAULT_PRIORITY

    # Preserve order, drop unavailable, dedupe, then guarantee CPU tail.
    seen: set[str] = set()
    chosen: list[str] = []
    for provider in requested:
        if provider in available and provider not in seen:
            chosen.append(provider)
            seen.add(provider)

    if "CPUExecutionProvider" not in seen:
        chosen.append("CPUExecutionProvider")

    if override.strip():
        # Caller explicitly asked for something — log if any entry was dropped.
        dropped = [p for p in requested if p not in available]
        if dropped:
            logger.warning(
                "ONNX_EXECUTION_PROVIDERS requested unavailable providers: %s "
                "(available: %s); using %s",
                dropped, sorted(available), chosen,
            )
        else:
            logger.info("ONNX providers (override): %s", chosen)
    else:
        logger.info("ONNX providers (auto): %s", chosen)

    return chosen
