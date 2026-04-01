# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: BSL-1.1

"""Docling advanced document parser plugin (scaffold).

Provides enhanced document parsing with:
- PDF: table extraction, layout analysis
- DOCX: heading hierarchy preservation
- PPTX: slide order and speaker notes
- XLSX: formula evaluation and sheet structure

This is a scaffold implementation. The actual docling library integration
points are marked with TODO comments. When Pro tier is enabled, this plugin
registers as an override in the parser registry for supported file types.
On Core tier, the existing parsers remain active (graceful fallback).

Configuration:
- CERID_DOCLING_ENABLED: Enable docling parsing (default: true when Pro tier)
- CERID_DOCLING_LLM_ENHANCED: Enable LLM-enhanced parsing (default: false)
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger("ai-companion.plugins.docling-parser")

# Configuration
DOCLING_ENABLED = os.getenv("CERID_DOCLING_ENABLED", "true").lower() == "true"
DOCLING_LLM_ENHANCED = os.getenv("CERID_DOCLING_LLM_ENHANCED", "false").lower() == "true"

# File types this plugin can handle
SUPPORTED_EXTENSIONS = [".pdf", ".docx", ".pptx", ".xlsx"]


@dataclass
class ParseResult:
    """Structured result from advanced document parsing.

    Attributes:
        text: Extracted plain text content.
        tables: List of extracted tables, each as a list of rows (list of strings).
        metadata: Additional metadata (page count, headings, slide count, etc.).
        images_described: List of image descriptions (when LLM-enhanced is enabled).
    """

    text: str = ""
    tables: list[list[list[str]]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    images_described: list[str] = field(default_factory=list)

    def to_parser_result(self) -> dict[str, Any]:
        """Convert to the standard parser result dict for the parser registry."""
        result: dict[str, Any] = {
            "text": self.text,
            "file_type": self.metadata.get("file_type", ""),
            "page_count": self.metadata.get("page_count"),
        }
        if self.tables:
            result["table_count"] = len(self.tables)
        if self.metadata:
            result.update({
                k: v for k, v in self.metadata.items()
                if k not in ("file_type", "page_count")
            })
        return result


def _check_docling_available() -> bool:
    """Check if the docling library is installed."""
    try:
        import docling  # noqa: F401
        return True
    except ImportError:
        return False


# ---------------------------------------------------------------------------
# PDF Parser
# ---------------------------------------------------------------------------

def parse_pdf(path: str) -> ParseResult:
    """Enhanced PDF parsing with table extraction.

    Args:
        path: Path to the PDF file.

    Returns:
        ParseResult with text, tables, and metadata.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"PDF file not found: {path}")

    result = ParseResult(metadata={"file_type": ".pdf"})

    if _check_docling_available():
        # TODO: Replace with actual docling PDF parsing when library is installed
        # from docling.document_converter import DocumentConverter
        # converter = DocumentConverter()
        # doc = converter.convert(str(file_path))
        # result.text = doc.document.export_to_markdown()
        # result.tables = _extract_docling_tables(doc)
        # result.metadata["page_count"] = doc.document.num_pages
        # if DOCLING_LLM_ENHANCED:
        #     result.images_described = _describe_images_llm(doc)
        logger.info("Docling PDF parsing: library available but integration pending")

    # Fallback to existing parser
    logger.info("Docling PDF: falling back to standard parser for %s", file_path.name)
    from parsers import parse_file
    fallback = parse_file(str(file_path))
    result.text = fallback.get("text", "")
    result.metadata["page_count"] = fallback.get("page_count")
    result.metadata["table_count"] = fallback.get("table_count")
    return result


# ---------------------------------------------------------------------------
# DOCX Parser
# ---------------------------------------------------------------------------

def parse_docx(path: str) -> ParseResult:
    """Enhanced DOCX parsing with heading hierarchy.

    Args:
        path: Path to the DOCX file.

    Returns:
        ParseResult with text, heading structure, and metadata.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"DOCX file not found: {path}")

    result = ParseResult(metadata={"file_type": ".docx"})

    if _check_docling_available():
        # TODO: Replace with actual docling DOCX parsing when library is installed
        # from docling.document_converter import DocumentConverter
        # converter = DocumentConverter()
        # doc = converter.convert(str(file_path))
        # result.text = doc.document.export_to_markdown()
        # result.metadata["headings"] = _extract_heading_hierarchy(doc)
        # result.tables = _extract_docling_tables(doc)
        logger.info("Docling DOCX parsing: library available but integration pending")

    # Fallback to existing parser
    logger.info("Docling DOCX: falling back to standard parser for %s", file_path.name)
    from parsers import parse_file
    fallback = parse_file(str(file_path))
    result.text = fallback.get("text", "")
    return result


# ---------------------------------------------------------------------------
# PPTX Parser
# ---------------------------------------------------------------------------

def parse_pptx(path: str) -> ParseResult:
    """Enhanced PPTX parsing with slide order and speaker notes.

    Args:
        path: Path to the PPTX file.

    Returns:
        ParseResult with text (slide-ordered), speaker notes, and metadata.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"PPTX file not found: {path}")

    result = ParseResult(metadata={"file_type": ".pptx"})

    if _check_docling_available():
        # TODO: Replace with actual docling PPTX parsing when library is installed
        # from docling.document_converter import DocumentConverter
        # converter = DocumentConverter()
        # doc = converter.convert(str(file_path))
        # slides = []
        # for slide_idx, slide in enumerate(doc.document.pages):
        #     slide_text = slide.export_to_markdown()
        #     speaker_notes = slide.notes or ""
        #     slides.append({
        #         "slide_number": slide_idx + 1,
        #         "content": slide_text,
        #         "speaker_notes": speaker_notes,
        #     })
        # result.metadata["slides"] = slides
        # result.metadata["slide_count"] = len(slides)
        # result.text = "\n\n---\n\n".join(
        #     f"Slide {s['slide_number']}:\n{s['content']}"
        #     + (f"\n\nSpeaker Notes: {s['speaker_notes']}" if s['speaker_notes'] else "")
        #     for s in slides
        # )
        logger.info("Docling PPTX parsing: library available but integration pending")

    # Fallback to existing parser
    logger.info("Docling PPTX: falling back to standard parser for %s", file_path.name)
    from parsers import parse_file
    fallback = parse_file(str(file_path))
    result.text = fallback.get("text", "")
    result.metadata["page_count"] = fallback.get("page_count")
    return result


# ---------------------------------------------------------------------------
# XLSX Parser
# ---------------------------------------------------------------------------

def parse_xlsx(path: str) -> ParseResult:
    """Enhanced XLSX parsing with formula evaluation.

    Args:
        path: Path to the XLSX file.

    Returns:
        ParseResult with text, tables per sheet, and metadata.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"XLSX file not found: {path}")

    result = ParseResult(metadata={"file_type": ".xlsx"})

    if _check_docling_available():
        # TODO: Replace with actual docling XLSX parsing when library is installed
        # from docling.document_converter import DocumentConverter
        # converter = DocumentConverter()
        # doc = converter.convert(str(file_path))
        # result.text = doc.document.export_to_markdown()
        # for table in doc.document.tables:
        #     rows = [[cell.text for cell in row] for row in table.rows]
        #     result.tables.append(rows)
        # result.metadata["sheet_count"] = doc.document.num_pages
        logger.info("Docling XLSX parsing: library available but integration pending")

    # Fallback to existing parser
    logger.info("Docling XLSX: falling back to standard parser for %s", file_path.name)
    from parsers import parse_file
    fallback = parse_file(str(file_path))
    result.text = fallback.get("text", "")
    return result


# ---------------------------------------------------------------------------
# Unified parse dispatcher
# ---------------------------------------------------------------------------

_PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".pptx": parse_pptx,
    ".xlsx": parse_xlsx,
}


def parse_document(file_path: str) -> dict[str, Any]:
    """Parse a document using the docling-enhanced parser.

    This is the parser registry entry point — it dispatches to the
    type-specific parser and returns the standard result dict.

    Args:
        file_path: Path to the document file.

    Returns:
        Standard parser result dict (text, file_type, page_count, etc.).
    """
    ext = Path(file_path).suffix.lower()
    parser = _PARSERS.get(ext)
    if parser is None:
        raise ValueError(f"Unsupported file type for docling parser: {ext}")

    result = parser(file_path)
    return result.to_parser_result()


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------

def register() -> None:
    """Register docling parsers as overrides in the parser registry.

    Only activates when:
    1. Pro tier is enabled (CERID_TIER=pro or enterprise)
    2. CERID_DOCLING_ENABLED is true (default when Pro)

    On Core tier, this is a no-op — existing parsers remain active.
    """
    from config.features import FEATURE_TIER, is_tier_met

    if not is_tier_met("pro"):
        logger.info(
            "Docling parser plugin: skipping registration (requires Pro tier, current: %s)",
            FEATURE_TIER,
        )
        return

    if not DOCLING_ENABLED:
        logger.info("Docling parser plugin: disabled via CERID_DOCLING_ENABLED=false")
        return

    from parsers.registry import PARSER_REGISTRY

    for ext in SUPPORTED_EXTENSIONS:
        if ext in PARSER_REGISTRY:
            logger.info("Docling parser overriding parser for %s", ext)
        PARSER_REGISTRY[ext] = parse_document

    logger.info(
        "Docling parser plugin registered for: %s (LLM-enhanced: %s)",
        ", ".join(SUPPORTED_EXTENSIONS),
        DOCLING_LLM_ENHANCED,
    )
