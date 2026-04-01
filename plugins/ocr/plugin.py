# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BSL-1.1

"""OCR plugin — extract text from images and scanned documents via Tesseract.

Requires system dependency: tesseract-ocr
  - macOS:  brew install tesseract
  - Debian: apt-get install tesseract-ocr
  - Docker: add to Dockerfile (apt-get install -y tesseract-ocr)

Python dependencies: pytesseract>=0.3.10, Pillow>=10.0
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-companion.plugins.ocr")

# Supported image extensions for OCR
SUPPORTED_EXTENSIONS = [".png", ".jpg", ".jpeg", ".tiff", ".tif", ".bmp"]


def _get_tesseract():
    """Lazy-import pytesseract with a clear error message."""
    try:
        import pytesseract

        return pytesseract
    except ImportError:
        raise ImportError(
            "pytesseract is required for OCR plugin. "
            "Install with: pip install pytesseract>=0.3.10 Pillow>=10.0\n"
            "Also requires tesseract-ocr system package."
        )


def _get_pil_image():
    """Lazy-import PIL.Image with a clear error message."""
    try:
        from PIL import Image

        return Image
    except ImportError:
        raise ImportError(
            "Pillow is required for OCR plugin. "
            "Install with: pip install Pillow>=10.0"
        )


def parse_image_ocr(file_path: str) -> dict[str, Any]:
    """
    Extract text from an image file using Tesseract OCR.

    Args:
        file_path: Path to image file (PNG, JPG, TIFF, BMP).

    Returns:
        {"text": str, "file_type": str, "page_count": int | None}
    """
    pytesseract = _get_tesseract()
    Image = _get_pil_image()

    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    ext = path.suffix.lower()
    logger.info("OCR parsing image: %s (type: %s)", path.name, ext)

    try:
        img = Image.open(file_path)
        # Convert to RGB if needed (e.g., RGBA PNGs, palette images)
        if img.mode not in ("L", "RGB"):
            img = img.convert("RGB")

        text = pytesseract.image_to_string(img).strip()

        if not text:
            logger.warning("OCR produced no text for: %s", path.name)
            return {"text": "", "file_type": ext, "page_count": None}

        logger.info(
            "OCR extracted %d characters from %s", len(text), path.name
        )
        return {"text": text, "file_type": ext, "page_count": None}

    except Exception as e:
        logger.error("OCR failed for %s: %s", path.name, e)
        raise


def register() -> None:
    """Register OCR parsers for image file types."""
    from parsers.registry import PARSER_REGISTRY

    for ext in SUPPORTED_EXTENSIONS:
        if ext in PARSER_REGISTRY:
            logger.info("OCR plugin overriding parser for %s", ext)
        PARSER_REGISTRY[ext] = parse_image_ocr

    logger.info(
        "OCR plugin registered parsers for: %s",
        ", ".join(SUPPORTED_EXTENSIONS),
    )
