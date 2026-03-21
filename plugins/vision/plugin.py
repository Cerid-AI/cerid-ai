# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: BSL-1.1

"""Vision plugin — image understanding via vision-capable LLMs.

Sends images to a vision model (via Bifrost/OpenRouter) to generate
descriptive text suitable for knowledge base storage.

No additional Python dependencies required — uses httpx (already in core).

Environment variables:
  VISION_MODEL    — model ID for vision analysis (default: "openai/gpt-4o")
  BIFROST_URL     — Bifrost gateway URL (default: "http://localhost:8080")
"""

from __future__ import annotations

import base64
import logging
import mimetypes
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-companion.plugins.vision")

# Supported image extensions
SUPPORTED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"]

# MIME type mapping for base64 encoding
_MIME_TYPES = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".svg": "image/svg+xml",
}

_ANALYSIS_PROMPT = (
    "Describe this image in detail for knowledge base storage. "
    "Include: subject matter, key elements, text visible in the image, "
    "colors, layout, and any notable details. Be factual and comprehensive."
)


def _get_httpx():
    """Lazy-import httpx."""
    try:
        import httpx

        return httpx
    except ImportError:
        raise ImportError(
            "httpx is required for vision plugin (should be in core deps). "
            "Install with: pip install httpx>=0.24"
        )


def _encode_image(file_path: str) -> tuple[str, str]:
    """
    Read and base64-encode an image file.

    Returns:
        (base64_data, mime_type)
    """
    path = Path(file_path)
    ext = path.suffix.lower()

    mime_type = _MIME_TYPES.get(ext)
    if not mime_type:
        mime_type = mimetypes.guess_type(file_path)[0] or "application/octet-stream"

    with open(file_path, "rb") as f:
        data = f.read()

    # Cap at 20MB to avoid API limits
    if len(data) > 20 * 1024 * 1024:
        raise ValueError(
            f"Image too large for vision analysis: {len(data) / 1024 / 1024:.1f}MB "
            f"(max 20MB)"
        )

    return base64.b64encode(data).decode("utf-8"), mime_type


async def analyze_image(file_path: str, prompt: str | None = None) -> str:
    """
    Send image to a vision LLM and return a text description.

    Args:
        file_path: Path to image file.
        prompt: Custom prompt (defaults to standard KB description prompt).

    Returns:
        Text description of the image.
    """
    httpx = _get_httpx()

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = path.suffix.lower()
    if ext not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported image format: {ext}. Supported: {SUPPORTED_EXTENSIONS}"
        )

    model = os.getenv("VISION_MODEL", "openai/gpt-4o")
    bifrost_url = os.getenv("BIFROST_URL", "http://localhost:8080")

    logger.info("Vision analysis: %s (model: %s)", path.name, model)

    b64_data, mime_type = _encode_image(file_path)
    analysis_prompt = prompt or _ANALYSIS_PROMPT

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": analysis_prompt},
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:{mime_type};base64,{b64_data}",
                        },
                    },
                ],
            }
        ],
        "max_tokens": 1024,
        "temperature": 0.2,
    }

    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.post(
            f"{bifrost_url}/v1/chat/completions",
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        resp.raise_for_status()

    data = resp.json()
    description = data["choices"][0]["message"]["content"].strip()

    logger.info(
        "Vision analysis complete for %s: %d chars", path.name, len(description)
    )
    return description


def parse_image_vision(file_path: str) -> dict[str, Any]:
    """
    Parse image via vision LLM for KB ingestion (parser registry interface).

    Note: This is a sync wrapper around the async analyze_image function.
    The parser registry expects sync functions, so we use asyncio.run()
    or get the running loop.

    Args:
        file_path: Path to image file.

    Returns:
        {"text": str, "file_type": str, "page_count": None}
    """
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop and loop.is_running():
        # We're already in an async context — create a task
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            description = pool.submit(
                asyncio.run, analyze_image(file_path)
            ).result()
    else:
        description = asyncio.run(analyze_image(file_path))

    return {
        "text": description,
        "file_type": Path(file_path).suffix.lower(),
        "page_count": None,
    }


def register() -> None:
    """Register vision parsers for image file types.

    Note: Vision plugin registers with lower priority than OCR plugin.
    If OCR is also loaded, OCR handles the overlapping extensions (.png, .jpg, etc.)
    since OCR is loaded first alphabetically. Vision provides richer descriptions
    but requires an LLM call; use pkb_ingest_multimodal tool to explicitly
    choose vision analysis for images.
    """
    from parsers.registry import PARSER_REGISTRY

    for ext in SUPPORTED_EXTENSIONS:
        if ext in PARSER_REGISTRY:
            logger.info(
                "Vision plugin: parser for %s already registered (skipping — "
                "use pkb_ingest_multimodal for explicit vision analysis)",
                ext,
            )
            continue
        PARSER_REGISTRY[ext] = parse_image_vision

    logger.info(
        "Vision plugin registered for image types: %s",
        ", ".join(SUPPORTED_EXTENSIONS),
    )
