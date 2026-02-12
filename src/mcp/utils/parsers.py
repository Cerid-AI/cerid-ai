"""
Extensible file parser registry.

Parsers are registered by file extension via the @register_parser decorator.
To add a new parser (e.g. Docling): define a function and decorate it.
To override an existing parser: register the same extension — last wins.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Callable, Dict, List

logger = logging.getLogger("ai-companion.parsers")

# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
PARSER_REGISTRY: Dict[str, Callable[[str], Dict[str, Any]]] = {}


def register_parser(extensions: List[str]):
    """Decorator that maps file extensions to a parser function."""
    def decorator(func: Callable[[str], Dict[str, Any]]):
        for ext in extensions:
            PARSER_REGISTRY[ext.lower()] = func
        return func
    return decorator


def parse_file(file_path: str) -> Dict[str, Any]:
    """
    Parse a file and return its text content + metadata.

    Returns:
        {"text": str, "file_type": str, "page_count": int | None}

    Raises:
        ValueError: unsupported extension or parse failure with clear message
        FileNotFoundError: file does not exist
    """
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")
    if path.stat().st_size == 0:
        raise ValueError(f"File is empty (0 bytes): {path.name}")

    ext = path.suffix.lower()
    parser = PARSER_REGISTRY.get(ext)
    if not parser:
        raise ValueError(
            f"Unsupported file type: {ext}. "
            f"Supported: {sorted(PARSER_REGISTRY.keys())}"
        )
    return parser(file_path)


# ---------------------------------------------------------------------------
# Built-in parsers
# ---------------------------------------------------------------------------

# Maximum text output size to prevent memory issues with huge files
_MAX_TEXT_CHARS = 2_000_000  # ~2MB of text


@register_parser([".pdf"])
def parse_pdf(file_path: str) -> Dict[str, Any]:
    try:
        from pypdf import PdfReader
    except ImportError:
        from PyPDF2 import PdfReader

    try:
        reader = PdfReader(file_path)
    except Exception as e:
        raise ValueError(
            f"Failed to read PDF '{Path(file_path).name}': {e}. "
            f"File may be corrupted, password-protected, or not a valid PDF."
        ) from e

    page_count = len(reader.pages)
    pages = []
    for i, page in enumerate(reader.pages):
        try:
            text = page.extract_text()
            if text:
                pages.append(text)
        except Exception as e:
            logger.warning(f"PDF page {i+1}/{page_count} failed to extract: {e}")

    text = "\n\n".join(pages)

    # Detect image-only PDFs (pages exist but no text extracted)
    if not text.strip() and page_count > 0:
        raise ValueError(
            f"No text extracted from PDF '{Path(file_path).name}' "
            f"({page_count} pages). This is likely a scanned/image-only PDF. "
            f"OCR support (e.g. Docling) is needed to process this file."
        )

    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "pdf",
        "page_count": page_count,
    }


@register_parser([".docx"])
def parse_docx(file_path: str) -> Dict[str, Any]:
    import docx

    try:
        doc = docx.Document(file_path)
    except Exception as e:
        raise ValueError(
            f"Failed to read DOCX '{Path(file_path).name}': {e}. "
            f"File may be corrupted or not a valid .docx file."
        ) from e

    paragraphs = [p.text for p in doc.paragraphs if p.text.strip()]

    # Extract tables (often contain critical data missed by paragraph-only extraction)
    table_texts = []
    for table in doc.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                rows.append("\t".join(cells))
        if rows:
            table_texts.append("\n".join(rows))

    parts = paragraphs
    if table_texts:
        parts.append("\n--- Tables ---")
        parts.extend(table_texts)

    text = "\n\n".join(parts)
    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "docx",
        "page_count": None,
    }


@register_parser([".xlsx"])
def parse_xlsx(file_path: str) -> Dict[str, Any]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(file_path, read_only=True, data_only=True)
    except Exception as e:
        raise ValueError(
            f"Failed to read XLSX '{Path(file_path).name}': {e}. "
            f"File may be corrupted or not a valid .xlsx file."
        ) from e

    # Capture sheet names before iterating (avoid access-after-close)
    sheet_names = list(wb.sheetnames)
    sheets_text = []
    for sheet_name in sheet_names:
        ws = wb[sheet_name]
        rows = []
        for row in ws.iter_rows(values_only=True):
            cell_values = [str(c) if c is not None else "" for c in row]
            if any(cell_values):
                rows.append("\t".join(cell_values))
        if rows:
            sheets_text.append(f"--- Sheet: {sheet_name} ---\n" + "\n".join(rows))
    wb.close()

    text = "\n\n".join(sheets_text)
    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "xlsx",
        "page_count": len(sheet_names),
    }


@register_parser([".csv"])
def parse_csv(file_path: str) -> Dict[str, Any]:
    import pandas as pd

    try:
        # Try UTF-8 first, fall back to latin-1 (handles Windows-generated CSVs)
        try:
            df = pd.read_csv(file_path, encoding="utf-8")
        except UnicodeDecodeError:
            df = pd.read_csv(file_path, encoding="latin-1")
    except Exception as e:
        raise ValueError(
            f"Failed to read CSV '{Path(file_path).name}': {e}. "
            f"File may be corrupted or not a valid CSV."
        ) from e

    # Limit output size for very large CSVs
    row_count = len(df)
    if row_count > 5000:
        logger.warning(
            f"CSV '{Path(file_path).name}' has {row_count} rows, "
            f"truncating to first 5000 for ingestion"
        )
        df = df.head(5000)

    text = df.to_string(index=False)
    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "csv",
        "page_count": None,
        "row_count": row_count,
        "columns": json.dumps(list(df.columns)),
    }


@register_parser([".html", ".htm"])
def parse_html(file_path: str) -> Dict[str, Any]:
    """Parse HTML files, stripping tags to extract readable text."""
    path = Path(file_path)
    raw = path.read_text(encoding="utf-8", errors="replace")

    try:
        from html.parser import HTMLParser

        class _TextExtractor(HTMLParser):
            def __init__(self):
                super().__init__()
                self._parts: List[str] = []
                self._skip = False

            def handle_starttag(self, tag, attrs):
                self._skip = tag.lower() in ("script", "style", "noscript")

            def handle_endtag(self, tag):
                if tag.lower() in ("script", "style", "noscript"):
                    self._skip = False

            def handle_data(self, data):
                if not self._skip:
                    stripped = data.strip()
                    if stripped:
                        self._parts.append(stripped)

        extractor = _TextExtractor()
        extractor.feed(raw)
        text = "\n".join(extractor._parts)
    except Exception:
        # Fallback: return raw if parsing fails
        text = raw

    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "html",
        "page_count": None,
    }


@register_parser([
    ".txt", ".md", ".rst", ".log",
    ".py", ".js", ".ts", ".jsx", ".tsx",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".sh", ".bash",
    ".xml",
    ".java", ".go", ".rs", ".rb", ".cpp", ".c", ".h", ".cs",
    ".sql", ".r", ".swift", ".kt",
])
def parse_text(file_path: str) -> Dict[str, Any]:
    path = Path(file_path)

    # Binary file detection: check first 512 bytes for null bytes
    try:
        with open(file_path, "rb") as f:
            sample = f.read(512)
        if b"\x00" in sample:
            raise ValueError(
                f"File '{path.name}' appears to be a binary file "
                f"(null bytes detected). Only text files are supported."
            )
    except ValueError:
        raise
    except Exception:
        pass  # proceed with text read if binary check fails

    text = path.read_text(encoding="utf-8", errors="replace")
    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": path.suffix.lstrip("."),
        "page_count": None,
    }
