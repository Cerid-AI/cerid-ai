# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Inference routing introspection (v0.93.8).

Pure, side-effect-free snapshot of which provider is currently active
for each inference workload.  Consumed by:

* ``/health.inference_routing`` — operator observability surface
* The Settings UI's "GPU acceleration" indicator
* The AMD_GPU_MODEL_RECOMMENDATIONS doc's verify-routing snippet

The snapshot reads env vars at call time so it reflects live state
after PATCH /settings flips a flag.  It does NOT probe the daemon —
that's what /health.services does.
"""

from __future__ import annotations

import os
from typing import Any


def _truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_local_url() -> str:
    """The shared URL the LLM/embed/rerank routes hit when local."""
    return (
        os.getenv("QUENCHFORGE_URL")
        or os.getenv("OLLAMA_URL")
        or "http://localhost:11434"
    )


def get_routing_snapshot() -> dict[str, dict[str, Any]]:
    """Return a five-key dict describing the active inference router per workload.

    Shape:

    .. code:: python

        {
          "llm":    {"provider": "quenchforge"|"ollama"|"openrouter", "url": "...", "model": "..."},
          "embed":  {"provider": "quenchforge"|"sidecar"|"in-process", "url"?, "model"?},
          "rerank": {"provider": "quenchforge"|"sidecar"|"in-process", "url"?, "model"?},
          "sparse": {"provider": "sidecar"|"in-process"|"disabled", "note"?},
          "nli":    {"provider": "in-process", "note": "..."},
        }

    Never raises.  Missing values are reported as ``"unset"`` strings
    rather than ``None`` so the JSON wire is stable across operators
    with different env configurations.
    """
    llm_provider = os.getenv("INTERNAL_LLM_PROVIDER", "openrouter").strip().lower()
    embed_provider = os.getenv("EMBEDDINGS_PROVIDER", "sidecar").strip().lower()
    rerank_provider = os.getenv("RERANK_PROVIDER", "sidecar").strip().lower()
    local_url = _resolve_local_url()

    # LLM ------------------------------------------------------------------
    if llm_provider == "quenchforge":
        llm_block = {
            "provider": "quenchforge",
            "url": local_url,
            "model": os.getenv("QUENCHFORGE_DEFAULT_MODEL", "unset"),
        }
    elif llm_provider == "ollama":
        llm_block = {
            "provider": "ollama",
            "url": os.getenv("OLLAMA_URL", "http://localhost:11434"),
            "model": os.getenv("OLLAMA_DEFAULT_MODEL", "unset"),
        }
    else:
        llm_block = {
            "provider": "openrouter",
            "url": "https://openrouter.ai/api/v1",
            "model": os.getenv("LLM_INTERNAL_MODEL", "unset"),
        }

    # Embeddings -----------------------------------------------------------
    if embed_provider == "quenchforge":
        embed_block: dict[str, Any] = {
            "provider": "quenchforge",
            "url": local_url,
            "model": os.getenv("QUENCHFORGE_EMBED_MODEL", "unset"),
        }
    else:
        # ``sidecar`` is the auto-detected default; the actual sidecar
        # availability is reported by /health.services.  We surface
        # the operator's intent here, not the live probe result.
        embed_block = {
            "provider": "sidecar" if embed_provider == "sidecar" else "in-process",
        }
        if embed_provider == "sidecar":
            embed_block["url"] = os.getenv("CERID_SIDECAR_URL", "http://localhost:8889")

    # Reranking ------------------------------------------------------------
    if rerank_provider == "quenchforge":
        rerank_block: dict[str, Any] = {
            "provider": "quenchforge",
            "url": local_url,
            "model": os.getenv("QUENCHFORGE_RERANK_MODEL", "unset"),
        }
    else:
        rerank_block = {
            "provider": "sidecar" if rerank_provider == "sidecar" else "in-process",
        }
        if rerank_provider == "sidecar":
            rerank_block["url"] = os.getenv("CERID_SIDECAR_URL", "http://localhost:8889")

    # SPLADE sparse -------------------------------------------------------
    if not _truthy("RETRIEVAL_SPARSE_ENABLED"):
        sparse_block = {"provider": "disabled"}
    else:
        # The sidecar fast-path was wired into core/retrieval/sparse in
        # v0.93.8.  Detection here mirrors the actual dispatch logic.
        try:
            from utils.inference_config import get_inference_config
            cfg = get_inference_config()
            if cfg.provider == "fastembed-sidecar" and cfg.sidecar_available:
                sparse_block = {
                    "provider": "sidecar",
                    "url": os.getenv("CERID_SIDECAR_URL", "http://localhost:8889"),
                }
            else:
                sparse_block = {
                    "provider": "in-process",
                    "note": "Quenchforge has no sparse endpoint",
                }
        except Exception:  # noqa: BLE001 — observability fallback
            sparse_block = {
                "provider": "in-process",
                "note": "Quenchforge has no sparse endpoint",
            }

    # NLI -----------------------------------------------------------------
    # Hard-coded CPU.  No sidecar support today.  No Quenchforge endpoint.
    # Surface the constraint so operators can spot the bottleneck.
    nli_block = {
        "provider": "in-process",
        "note": "CPU only; no GPU path available",
    }

    return {
        "llm": llm_block,
        "embed": embed_block,
        "rerank": rerank_block,
        "sparse": sparse_block,
        "nli": nli_block,
    }
