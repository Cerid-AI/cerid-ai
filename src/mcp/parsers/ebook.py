# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""E-book and rich text parsers — EPUB and RTF."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from errors import IngestionError
from parsers._utils import _strip_html_tags, _strip_rtf
from parsers.registry import _MAX_TEXT_CHARS, logger, register_parser


@register_parser([".epub"])
def parse_epub(file_path: str) -> dict[str, Any]:
    """Parse EPUB — extract XHTML chapters in reading order via OPF manifest."""
    import xml.etree.ElementTree as ET
    import zipfile

    path = Path(file_path)

    try:
        zf = zipfile.ZipFile(file_path, "r")
    except (IngestionError, ValueError, OSError, RuntimeError) as e:
        raise ValueError(
            f"Failed to read EPUB '{path.name}': {e}. "
            f"File may be corrupted or not a valid .epub file."
        ) from e

    chapters = []
    title = ""

    try:
        try:
            container_xml = zf.read("META-INF/container.xml")
            container_root = ET.fromstring(container_xml)  # nosec B314 — trusted EPUB internal XML
            # Handle namespace
            ns = {"c": "urn:oasis:names:tc:opendocument:xmlns:container"}
            rootfile = container_root.find(".//c:rootfile", ns)
            opf_path = rootfile.get("full-path", "") if rootfile is not None else ""
        except (IngestionError, ValueError, OSError, RuntimeError):
            opf_path = ""
            for name in zf.namelist():
                if name.endswith(".opf"):
                    opf_path = name
                    break

        if not opf_path:
            raise ValueError(f"EPUB '{path.name}': cannot find OPF manifest")

        opf_dir = str(Path(opf_path).parent)
        if opf_dir == ".":
            opf_dir = ""

        opf_data = zf.read(opf_path)
        opf_root = ET.fromstring(opf_data)  # nosec B314 — trusted EPUB internal XML

        opf_ns = {"opf": "http://www.idpf.org/2007/opf", "dc": "http://purl.org/dc/elements/1.1/"}

        title_el = opf_root.find(".//dc:title", opf_ns)
        if title_el is not None and title_el.text:
            title = title_el.text.strip()

        manifest = {}
        for item in opf_root.findall(".//opf:manifest/opf:item", opf_ns):
            item_id = item.get("id", "")
            href = item.get("href", "")
            media_type = item.get("media-type", "")
            if item_id and href:
                manifest[item_id] = {"href": href, "media_type": media_type}

        spine_refs = []
        for itemref in opf_root.findall(".//opf:spine/opf:itemref", opf_ns):
            idref = itemref.get("idref", "")
            if idref and idref in manifest:
                spine_refs.append(manifest[idref])

        if not spine_refs:
            spine_refs = [
                info for info in manifest.values()
                if info["media_type"] in ("application/xhtml+xml", "text/html")
            ]

        for ref in spine_refs:
            href = ref["href"]
            if opf_dir:
                full_path = f"{opf_dir}/{href}"
            else:
                full_path = href

            try:
                content = zf.read(full_path).decode("utf-8", errors="replace")
                chapter_text = _strip_html_tags(content)
                if chapter_text.strip():
                    chapters.append(chapter_text.strip())
            except (KeyError, IngestionError, ValueError, OSError, RuntimeError) as e:
                logger.debug(f"EPUB: skipping {href}: {e}")

    finally:
        zf.close()

    if not chapters:
        raise ValueError(
            f"No text content found in EPUB '{path.name}'. "
            f"File may contain only images or be DRM-protected."
        )

    header = f"Title: {title}\n\n" if title else ""
    text = header + "\n\n---\n\n".join(chapters)

    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "epub",
        "page_count": len(chapters),
        "title": title,
    }


@register_parser([".rtf"])
def parse_rtf(file_path: str) -> dict[str, Any]:
    """Parse RTF — extract plain text via state-machine RTF stripper."""
    path = Path(file_path)
    raw = path.read_bytes()

    try:
        text = _strip_rtf(raw)
    except (IngestionError, ValueError, OSError, RuntimeError) as e:
        raise ValueError(
            f"Failed to parse RTF '{path.name}': {e}. "
            f"File may be corrupted or not a valid .rtf file."
        ) from e

    if not text.strip():
        raise ValueError(f"No text content found in RTF '{path.name}'.")

    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "rtf",
        "page_count": None,
    }
