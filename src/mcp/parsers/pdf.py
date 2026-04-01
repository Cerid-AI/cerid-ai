# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""PDF parser — memory-safe, chunked page-by-page extraction with table support.

Processes pages one at a time and releases each page from memory before
proceeding. This prevents OOM on complex PDFs (e.g., IRS tax returns with
dense form fields). Falls back to lightweight text-only extraction when
full table parsing fails on a page.
"""

from __future__ import annotations

import gc
import os
import resource
from pathlib import Path
from typing import Any

from errors import IngestionError
from parsers.registry import _MAX_TEXT_CHARS, logger, register_parser

# ---------------------------------------------------------------------------
# Configuration (env-configurable)
# ---------------------------------------------------------------------------
PDF_MAX_PAGES = int(os.getenv("PDF_MAX_PAGES", "200"))
PDF_MEMORY_LIMIT_MB = int(os.getenv("PDF_MEMORY_LIMIT_MB", "1024"))  # 1GB default
PDF_LITE_THRESHOLD_PAGES = int(os.getenv("PDF_LITE_THRESHOLD_PAGES", "50"))


def _get_memory_mb() -> float:
    """Return current process RSS in MB (Linux/macOS)."""
    try:
        ru = resource.getrusage(resource.RUSAGE_SELF)
        # macOS returns bytes, Linux returns KB
        if hasattr(ru, "ru_maxrss"):
            return ru.ru_maxrss / (1024 * 1024) if os.uname().sysname == "Darwin" else ru.ru_maxrss / 1024
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as exc:
        logger.debug("Suppressed error: %s", exc)
    return 0.0


def _extract_page_lite(page: Any) -> str:
    """Lightweight extraction — text only, no table detection."""
    try:
        text = page.extract_text()
        return text.strip() if text else ""
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        return ""


def _extract_page_full(page: Any, page_num: int, page_count: int) -> tuple[str, int]:
    """Full extraction — tables as Markdown + remaining text. Returns (text, table_count)."""
    table_count = 0
    page_parts: list[str] = []

    try:
        tables = page.find_tables()
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
        # Table detection can fail on complex pages — fall back to text only
        return _extract_page_lite(page), 0

    if tables:
        table_bboxes = [t.bbox for t in tables]

        for table in tables:
            table_count += 1
            try:
                rows = table.extract()
            except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
                continue
            if not rows:
                continue

            md_rows = []
            for row in rows:
                cells = [
                    (cell or "").strip().replace("\n", " ")
                    for cell in row
                ]
                md_rows.append("| " + " | ".join(cells) + " |")
            if len(md_rows) > 1:
                col_count = len(rows[0]) if rows[0] else 1
                md_rows.insert(1, "| " + " | ".join(["---"] * col_count) + " |")
            page_parts.append("\n".join(md_rows))

        # Extract text outside table bounding boxes
        try:
            filtered = page
            for bbox in table_bboxes:
                filtered = filtered.outside_bounding_box(bbox)  # type: ignore[attr-defined]
            plain_text = filtered.extract_text()
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
            plain_text = page.extract_text()

        if plain_text and plain_text.strip():
            page_parts.insert(0, plain_text.strip())
    else:
        plain_text = page.extract_text()
        if plain_text and plain_text.strip():
            page_parts.append(plain_text.strip())

    return "\n\n".join(page_parts), table_count


@register_parser([".pdf"])
def parse_pdf(file_path: str) -> dict[str, Any]:
    """Memory-safe, chunked PDF extraction.

    - Processes pages one at a time (releases each page after extraction)
    - Single PDF open (form fields extracted during the same pass)
    - Falls back to lightweight text-only mode for complex pages
    - Respects PDF_MAX_PAGES and PDF_MEMORY_LIMIT_MB constraints
    - Uses lite mode for PDFs exceeding PDF_LITE_THRESHOLD_PAGES
    """
    import pdfplumber

    try:
        pdf = pdfplumber.open(file_path)
    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
        raise ValueError(
            f"Failed to read PDF '{Path(file_path).name}': {e}. "
            f"File may be corrupted, password-protected, or not a valid PDF."
        ) from e

    page_count = len(pdf.pages)
    effective_pages = min(page_count, PDF_MAX_PAGES)
    use_lite = page_count > PDF_LITE_THRESHOLD_PAGES

    if page_count > PDF_MAX_PAGES:
        logger.warning(
            f"PDF '{Path(file_path).name}' has {page_count} pages, "
            f"capping at {PDF_MAX_PAGES}"
        )

    pages: list[str] = []
    form_fields: list[str] = []
    table_count = 0
    baseline_mem = _get_memory_mb()
    pages_with_errors = 0

    try:
        for i in range(effective_pages):
            # Memory guard — check before processing each page
            current_mem = _get_memory_mb()
            if current_mem - baseline_mem > PDF_MEMORY_LIMIT_MB:
                logger.warning(
                    f"PDF '{Path(file_path).name}': memory limit reached at page {i+1}/{effective_pages} "
                    f"({current_mem:.0f}MB used, limit {PDF_MEMORY_LIMIT_MB}MB). "
                    f"Returning partial results."
                )
                break

            page = pdf.pages[i]
            try:
                # Extract content
                if use_lite:
                    page_text = _extract_page_lite(page)
                    page_tables = 0
                else:
                    try:
                        page_text, page_tables = _extract_page_full(page, i, effective_pages)
                    except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
                        # Full extraction failed — try lite
                        page_text = _extract_page_lite(page)
                        page_tables = 0

                table_count += page_tables

                if page_text:
                    pages.append(page_text)

                # Extract form fields during the same pass (single open)
                try:
                    annots = page.annots or []
                    for annot in annots:
                        if isinstance(annot, dict):
                            field_name = annot.get("T", "")
                            field_value = annot.get("V", "")
                            if field_name or field_value:
                                form_fields.append(
                                    f"{field_name}: {field_value}" if field_name
                                    else str(field_value)
                                )
                except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError):
                    pass  # form field extraction is best-effort

            except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as e:
                pages_with_errors += 1
                logger.warning(f"PDF page {i+1}/{effective_pages} failed: {e}")
            finally:
                # Release page object immediately to free memory
                page.flush_cache()
                del page

            # Periodic GC to prevent memory accumulation
            if (i + 1) % 10 == 0:
                gc.collect()

            # Early exit if we already have enough text
            total_chars = sum(len(p) for p in pages)
            if total_chars >= _MAX_TEXT_CHARS:
                logger.info(
                    f"PDF '{Path(file_path).name}': text limit reached at page {i+1}/{effective_pages}"
                )
                break
    finally:
        pdf.close()
        gc.collect()

    text = "\n\n".join(pages)

    if form_fields:
        text += "\n\n--- Form Fields ---\n" + "\n".join(form_fields[:100])

    if not text.strip() and page_count > 0:
        # Attempt OCR fallback for scanned/image-only PDFs (Pro tier)
        try:
            from config.features import is_feature_enabled
            if is_feature_enabled("ocr_parsing"):
                from plugins.ocr.plugin import parse_image_ocr
                logger.info("PDF '%s' has no text — attempting OCR fallback", Path(file_path).name)
                ocr_result = parse_image_ocr(file_path)
                if ocr_result.get("text", "").strip():
                    text = ocr_result["text"]
        except (IngestionError, ValueError, OSError, RuntimeError, AttributeError, TypeError, KeyError) as ocr_exc:
            logger.debug("OCR fallback failed for PDF: %s", ocr_exc)

        if not text.strip():
            raise ValueError(
                f"No text extracted from PDF '{Path(file_path).name}' "
                f"({page_count} pages). This is likely a scanned/image-only PDF. "
                f"Enable Pro tier with OCR to process this file."
            )

    if table_count or pages_with_errors:
        logger.info(
            f"PDF '{Path(file_path).name}': {effective_pages}/{page_count} pages, "
            f"{table_count} tables, {pages_with_errors} page errors, "
            f"{'lite' if use_lite else 'full'} mode"
        )

    result: dict[str, Any] = {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "pdf",
        "page_count": page_count,
        "table_count": table_count,
    }
    if form_fields:
        result["form_field_count"] = len(form_fields)
    if pages_with_errors:
        result["pages_with_errors"] = pages_with_errors

    return result
