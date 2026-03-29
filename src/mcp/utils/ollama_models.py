# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Ollama model management — auto-detection, recommendations, and validation.

Auto-detects available Ollama models at startup. Recommends optimal models
per pipeline stage based on task requirements and available resources.

Dependencies: config/settings.py (OLLAMA_URL), config/constants.py
Error types: none (model management never blocks — warns only)
"""
from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger("ai-companion.ollama.models")

# ---------------------------------------------------------------------------
# Recommended models per pipeline stage
# ---------------------------------------------------------------------------

RECOMMENDED_MODELS: dict[str, str] = {
    "claim_extraction": "llama3.2:3b",       # fast, 2GB, good at classification
    "query_decomposition": "llama3.2:3b",    # fast, structured output
    "topic_extraction": "llama3.2:3b",        # keyword extraction
    "memory_resolution": "llama3.2:3b",       # pattern matching
    "verification_simple": "llama3.3:8b",       # needs reasoning for factual claims
    "reranking": "llama3.2:3b",               # cross-encoder style
    "embedding": "nomic-embed-text",            # zero API cost embedding
}


def _ollama_url() -> str:
    return os.getenv("OLLAMA_URL", "http://localhost:11434")


async def detect_available_models() -> list[dict]:
    """Call Ollama API to list locally installed models.

    Returns list of ``{"name": ..., "size_gb": ..., "modified_at": ...}``.
    Returns empty list on any error (logs warning).
    """
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            resp = await client.get(f"{_ollama_url()}/api/tags")
            resp.raise_for_status()
            data = resp.json()
    except Exception as exc:
        logger.warning("Ollama model detection failed: %s", exc)
        return []

    results: list[dict] = []
    for m in data.get("models", []):
        size_bytes = m.get("size", 0)
        results.append({
            "name": m.get("name", ""),
            "size_gb": round(size_bytes / (1024**3), 2),
            "modified_at": m.get("modified_at", ""),
        })
    return results


def check_model_availability(available: list[dict]) -> dict:
    """Compare available models against RECOMMENDED_MODELS.

    Returns ``{"available": [...], "missing": [...], "warnings": [...]}``.
    """
    available_names = {m["name"] for m in available}
    # Also match without tag (e.g. "llama3.2:3b" matches "llama3.2:3b")
    # and base name (e.g. "nomic-embed-text" matches "nomic-embed-text:latest")
    available_bases = {n.split(":")[0] for n in available_names}

    present: list[str] = []
    missing: list[str] = []
    warnings: list[str] = []

    for stage, model in RECOMMENDED_MODELS.items():
        model_base = model.split(":")[0]
        if model in available_names or model_base in available_bases:
            present.append(model)
        else:
            missing.append(model)
            warnings.append(
                f"Stage '{stage}': recommended model '{model}' not pulled. "
                f"Run: ollama pull {model}"
            )

    return {"available": present, "missing": missing, "warnings": warnings}


async def startup_model_check() -> None:
    """Detect models and log availability report. Called during app startup."""
    models = await detect_available_models()
    if not models:
        logger.info("Ollama model check: no models detected (Ollama may be offline)")
        return

    logger.info("Ollama models detected: %d installed", len(models))
    for m in models:
        logger.debug("  %s (%.1f GB)", m["name"], m["size_gb"])

    report = check_model_availability(models)
    if report["missing"]:
        for w in report["warnings"]:
            logger.warning("Ollama: %s", w)
    else:
        logger.info("Ollama: all recommended pipeline models available")


__all__ = [
    "RECOMMENDED_MODELS",
    "check_model_availability",
    "detect_available_models",
    "startup_model_check",
]
