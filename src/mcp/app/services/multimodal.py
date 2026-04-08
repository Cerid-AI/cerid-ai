# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Multi-modal ingestion service — routes files to OCR, audio, or vision plugins.

Detects file type and delegates to the appropriate plugin for text extraction,
then ingests the extracted text into the knowledge base via the standard
ingestion pipeline.

Requires CERID_TIER=pro (all multi-modal plugins are pro-gated).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import config
from app.services.ingestion import ingest_content

logger = logging.getLogger("ai-companion.multimodal")

# Extension → plugin mapping
_AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".ogg", ".flac", ".webm"}
_OCR_EXTENSIONS = {".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"}
_VISION_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}


def _detect_plugin(file_path: str, plugin_override: str) -> str:
    """
    Determine which plugin to use based on file extension or explicit override.

    Returns:
        Plugin name: "ocr", "audio", or "vision"
    """
    if plugin_override:
        override = plugin_override.strip().lower()
        if override in ("ocr", "audio", "vision"):
            return override
        raise ValueError(
            f"Unknown plugin: '{plugin_override}'. Choose: ocr, audio, vision"
        )

    ext = Path(file_path).suffix.lower()

    if ext in _AUDIO_EXTENSIONS:
        return "audio"
    if ext in _OCR_EXTENSIONS:
        return "ocr"
    if ext in _VISION_EXTENSIONS:
        return "vision"

    raise ValueError(
        f"No multi-modal plugin for extension '{ext}'. "
        f"Supported: audio ({', '.join(sorted(_AUDIO_EXTENSIONS))}), "
        f"ocr ({', '.join(sorted(_OCR_EXTENSIONS))}), "
        f"vision ({', '.join(sorted(_VISION_EXTENSIONS))})"
    )


async def _extract_text(file_path: str, plugin_name: str) -> tuple[str, str]:
    """
    Extract text from file using the specified plugin.

    Returns:
        (extracted_text, plugin_used)
    """
    if plugin_name == "ocr":
        from app.parsers.registry import PARSER_REGISTRY

        parser = PARSER_REGISTRY.get(".png")
        if parser is None:
            raise RuntimeError(
                "OCR plugin not loaded. Ensure CERID_TIER=pro and "
                "pytesseract + tesseract-ocr are installed."
            )
        result = parser(file_path)
        return result["text"], "cerid-ocr"

    elif plugin_name == "audio":
        from app.parsers.registry import PARSER_REGISTRY

        parser = PARSER_REGISTRY.get(".mp3")
        if parser is None:
            raise RuntimeError(
                "Audio plugin not loaded. Ensure CERID_TIER=pro and "
                "faster-whisper + ffmpeg are installed."
            )
        result = parser(file_path)
        return result["text"], "cerid-audio"

    elif plugin_name == "vision":
        # Vision plugin uses async — import and call directly
        try:
            import importlib
            import sys

            vision_module = sys.modules.get("cerid_plugin_cerid-vision")
            if vision_module is None:
                # Plugin might not be loaded via the loader; try direct import
                from pathlib import Path as _P

                plugin_path = _P(config.PLUGIN_DIR) / "vision" / "plugin.py"
                if not plugin_path.exists():
                    raise RuntimeError("Vision plugin not found")

                spec = importlib.util.spec_from_file_location(
                    "cerid_plugin_vision_direct", str(plugin_path)
                )
                if spec is None or spec.loader is None:
                    raise RuntimeError("Failed to load vision plugin module")
                vision_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(vision_module)

            description = await vision_module.analyze_image(file_path)
            return description, "cerid-vision"

        except ImportError as e:
            raise RuntimeError(
                f"Vision plugin failed: {e}. Ensure CERID_TIER=pro and "
                f"Bifrost is running."
            ) from e

    raise ValueError(f"Unknown plugin: {plugin_name}")


async def ingest_multimodal(
    file_path: str,
    domain: str = "general",
    tags: str = "",
    plugin_override: str = "",
) -> dict[str, Any]:
    """
    Ingest an image or audio file by extracting text via the appropriate plugin,
    then storing in the KB.

    Args:
        file_path: Path to the file to ingest.
        domain: KB domain to store in.
        tags: Comma-separated tags.
        plugin_override: Force a specific plugin (ocr/audio/vision).

    Returns:
        Ingestion result dict with plugin_used and extracted_chars.
    """
    # Feature gate
    if config.FEATURE_TIER != "pro":
        return {
            "status": "error",
            "error": "Multi-modal ingestion requires CERID_TIER=pro",
        }

    path = Path(file_path)
    if not path.exists():
        return {"status": "error", "error": f"File not found: {file_path}"}

    try:
        plugin_name = _detect_plugin(file_path, plugin_override)
    except ValueError as e:
        return {"status": "error", "error": str(e)}

    logger.info(
        "Multi-modal ingest: %s → plugin=%s, domain=%s",
        path.name,
        plugin_name,
        domain,
    )

    try:
        text, plugin_used = await _extract_text(file_path, plugin_name)
    except Exception as e:
        logger.error("Plugin extraction failed for %s: %s", path.name, e)
        return {"status": "error", "error": f"Plugin '{plugin_name}' failed: {e}"}

    if not text.strip():
        return {
            "status": "error",
            "error": f"No text extracted from {path.name} via {plugin_name}",
        }

    # Build metadata
    metadata: dict[str, Any] = {
        "source_file": path.name,
        "source_type": f"multimodal/{plugin_name}",
        "plugin": plugin_used,
    }
    if tags:
        metadata["keywords_json"] = tags

    # Ingest via standard pipeline
    result = ingest_content(text, domain, metadata=metadata)
    result["plugin_used"] = plugin_used
    result["extracted_chars"] = len(text)

    logger.info(
        "Multi-modal ingest complete: %s → %d chars, %d chunks, plugin=%s",
        path.name,
        len(text),
        result.get("chunks", 0),
        plugin_used,
    )

    return result
