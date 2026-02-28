# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""PDF parser — structure-aware extraction with table support."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict

from parsers.registry import _MAX_TEXT_CHARS, logger, register_parser


@register_parser([".pdf"])
def parse_pdf(file_path: str) -> Dict[str, Any]:
    """Structure-aware PDF extraction — tables as Markdown, plain text for the rest."""
    import pdfplumber

    try:
        pdf = pdfplumber.open(file_path)
    except Exception as e:
        raise ValueError(
            f"Failed to read PDF '{Path(file_path).name}': {e}. "
            f"File may be corrupted, password-protected, or not a valid PDF."
        ) from e

    page_count = len(pdf.pages)
    pages = []
    table_count = 0

    try:
        for i, page in enumerate(pdf.pages):
            try:
                page_parts = []

                tables = page.find_tables()
                if tables:
                    table_bboxes = [t.bbox for t in tables]

                    for table in tables:
                        table_count += 1
                        rows = table.extract()
                        if rows:
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

                    # Crop out table regions to avoid duplicating table content
                    try:
                        filtered = page
                        for bbox in table_bboxes:
                            filtered = filtered.outside_bounding_box(bbox)
                        plain_text = filtered.extract_text()
                    except Exception:
                        plain_text = page.extract_text()

                    if plain_text and plain_text.strip():
                        page_parts.insert(0, plain_text.strip())
                else:
                    plain_text = page.extract_text()
                    if plain_text and plain_text.strip():
                        page_parts.append(plain_text.strip())

                if page_parts:
                    pages.append("\n\n".join(page_parts))
            except Exception as e:
                logger.warning(f"PDF page {i+1}/{page_count} failed to extract: {e}")
    finally:
        pdf.close()
    text = "\n\n".join(pages)

    form_fields = []
    try:
        pdf2 = pdfplumber.open(file_path)
        try:
            for page in pdf2.pages:
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
        finally:
            pdf2.close()
    except Exception:
        pass  # form field extraction is best-effort

    if form_fields:
        text += "\n\n--- Form Fields ---\n" + "\n".join(form_fields[:100])

    if not text.strip() and page_count > 0:
        raise ValueError(
            f"No text extracted from PDF '{Path(file_path).name}' "
            f"({page_count} pages). This is likely a scanned/image-only PDF. "
            f"OCR support (e.g. Docling) is needed to process this file."
        )

    if table_count:
        logger.info(
            f"PDF '{Path(file_path).name}': {page_count} pages, "
            f"{table_count} tables extracted as Markdown"
        )

    result: Dict[str, Any] = {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "pdf",
        "page_count": page_count,
        "table_count": table_count,
    }
    if form_fields:
        result["form_field_count"] = len(form_fields)

    return result
