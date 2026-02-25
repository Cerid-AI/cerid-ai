"""
OCR Plugin for Cerid AI — Pro Tier.

Provides OCR capabilities for scanned PDFs and image-only documents.
Overrides the default PDF parser with an OCR-aware version that:

1. Tries pdfplumber first (fast, no GPU needed)
2. If text extraction yields <50 chars per page → triggers OCR
3. Docling handles: scanned PDFs, mixed text+image PDFs, form fields
4. Falls back to Tesseract if Docling unavailable

Install dependencies:
    pip install docling>=2.0
    # OR for Tesseract fallback:
    pip install pytesseract Pillow
    # + system Tesseract binary: brew install tesseract (macOS) / apt install tesseract-ocr (Linux)

Environment variables:
    ENABLE_OCR=true           # Must be set to enable the plugin
    OCR_ENGINE=docling        # 'docling' or 'tesseract'
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Dict

logger = logging.getLogger("ai-companion.plugins.ocr")

# Minimum chars per page before OCR triggers
OCR_MIN_TEXT_PER_PAGE = int(os.getenv("OCR_MIN_TEXT_PER_PAGE", "50"))
OCR_ENGINE = os.getenv("OCR_ENGINE", "docling")

# Maximum text output size
_MAX_TEXT_CHARS = 2_000_000


def _ocr_with_docling(file_path: str) -> str:
    """Run OCR using IBM Docling document AI library."""
    try:
        from docling.document_converter import DocumentConverter

        converter = DocumentConverter()
        result = converter.convert(file_path)
        return result.document.export_to_markdown()
    except ImportError:
        raise ImportError(
            "Docling is not installed. Install with: pip install docling>=2.0"
        )
    except Exception as e:
        logger.error(f"Docling OCR failed for {file_path}: {e}")
        raise


def _ocr_with_tesseract(file_path: str) -> str:
    """Run OCR using Tesseract (fallback)."""
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError:
        raise ImportError(
            "Tesseract dependencies not installed. Install with: "
            "pip install pytesseract pdf2image Pillow"
        )

    try:
        images = convert_from_path(file_path)
        pages_text = []
        for i, img in enumerate(images):
            text = pytesseract.image_to_string(img)
            if text.strip():
                pages_text.append(text.strip())
        return "\n\n".join(pages_text)
    except Exception as e:
        logger.error(f"Tesseract OCR failed for {file_path}: {e}")
        raise


def parse_pdf_with_ocr(file_path: str) -> Dict[str, Any]:
    """
    OCR-aware PDF parser that extends the default pdfplumber parser.

    Strategy:
    1. Try pdfplumber extraction first (fast, handles text PDFs)
    2. If average chars per page < threshold → likely scanned → run OCR
    3. OCR via configured engine (Docling or Tesseract)
    """
    import pdfplumber

    path = Path(file_path)

    # Step 1: Try pdfplumber first
    try:
        pdf = pdfplumber.open(file_path)
    except Exception as e:
        raise ValueError(
            f"Failed to read PDF '{path.name}': {e}. "
            f"File may be corrupted, password-protected, or not a valid PDF."
        ) from e

    page_count = len(pdf.pages)
    pages_text = []
    total_chars = 0

    try:
        for page in pdf.pages:
            try:
                text = page.extract_text() or ""
                pages_text.append(text)
                total_chars += len(text)
            except Exception:
                pages_text.append("")
    finally:
        pdf.close()

    # Step 2: Check if OCR is needed
    avg_chars_per_page = total_chars / max(page_count, 1)
    needs_ocr = avg_chars_per_page < OCR_MIN_TEXT_PER_PAGE

    if not needs_ocr:
        # Good text extraction — return pdfplumber result
        text = "\n\n".join(p for p in pages_text if p.strip())
        return {
            "text": text[:_MAX_TEXT_CHARS],
            "file_type": "pdf",
            "page_count": page_count,
            "ocr_used": False,
        }

    # Step 3: Run OCR
    logger.info(
        f"PDF '{path.name}': avg {avg_chars_per_page:.0f} chars/page "
        f"(threshold: {OCR_MIN_TEXT_PER_PAGE}), triggering OCR via {OCR_ENGINE}"
    )

    try:
        if OCR_ENGINE == "tesseract":
            ocr_text = _ocr_with_tesseract(file_path)
        else:
            try:
                ocr_text = _ocr_with_docling(file_path)
            except ImportError:
                logger.warning("Docling not available, falling back to Tesseract")
                ocr_text = _ocr_with_tesseract(file_path)
    except Exception as e:
        # If OCR fails, return whatever pdfplumber got (even if sparse)
        logger.warning(f"OCR failed for '{path.name}': {e}. Using pdfplumber output.")
        text = "\n\n".join(p for p in pages_text if p.strip())
        if not text.strip():
            raise ValueError(
                f"No text extracted from PDF '{path.name}' "
                f"({page_count} pages). OCR also failed: {e}"
            )
        return {
            "text": text[:_MAX_TEXT_CHARS],
            "file_type": "pdf",
            "page_count": page_count,
            "ocr_used": False,
            "ocr_error": str(e),
        }

    return {
        "text": ocr_text[:_MAX_TEXT_CHARS],
        "file_type": "pdf",
        "page_count": page_count,
        "ocr_used": True,
        "ocr_engine": OCR_ENGINE,
    }


def register():
    """Register the OCR PDF parser, overriding the default pdfplumber parser."""
    from utils.parsers import PARSER_REGISTRY

    PARSER_REGISTRY[".pdf"] = parse_pdf_with_ocr
    logger.info("OCR plugin registered: .pdf parser overridden with OCR-aware version")
