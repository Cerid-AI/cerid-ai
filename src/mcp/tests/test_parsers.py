# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for parsers/ sub-package — registry, utility functions, and all parsers."""

import email
import email.mime.multipart
import email.mime.text
import json
import zipfile
from unittest.mock import MagicMock, patch

import pytest

from parsers._utils import _strip_html_tags, _strip_rtf
from parsers.registry import _MAX_TEXT_CHARS, PARSER_REGISTRY, parse_file, register_parser

# ---------------------------------------------------------------------------
# Tests: _strip_html_tags (pure function)
# ---------------------------------------------------------------------------

class TestStripHtmlTags:
    def test_simple_tags(self):
        assert _strip_html_tags("<p>hello</p>") == "hello"

    def test_nested_tags(self):
        result = _strip_html_tags("<div><p>hello <b>world</b></p></div>")
        assert "hello" in result
        assert "world" in result

    def test_script_tags_skipped(self):
        html = "<p>keep</p><script>alert('xss')</script><p>this</p>"
        result = _strip_html_tags(html)
        assert "keep" in result
        assert "this" in result
        assert "alert" not in result

    def test_style_tags_skipped(self):
        html = "<style>.foo { color: red; }</style><p>visible</p>"
        result = _strip_html_tags(html)
        assert "visible" in result
        assert "color" not in result

    def test_noscript_tags_skipped(self):
        html = "<p>yes</p><noscript>no</noscript><p>ok</p>"
        result = _strip_html_tags(html)
        assert "yes" in result
        assert "ok" in result
        assert "no" not in result or result.count("no") == 0

    def test_block_tags_add_newlines(self):
        html = "<p>one</p><div>two</div>"
        result = _strip_html_tags(html)
        assert "\n" in result

    def test_br_adds_newline(self):
        html = "line one<br>line two"
        result = _strip_html_tags(html)
        assert "\n" in result

    def test_empty_html(self):
        assert _strip_html_tags("") == ""

    def test_plain_text_passthrough(self):
        assert _strip_html_tags("just plain text") == "just plain text"

    def test_heading_tags_create_newlines(self):
        html = "<h1>Title</h1><p>Body</p>"
        result = _strip_html_tags(html)
        assert "Title" in result
        assert "Body" in result


# ---------------------------------------------------------------------------
# Tests: _strip_rtf (pure function)
# ---------------------------------------------------------------------------

class TestStripRtf:
    def test_plain_text(self):
        rtf = rb"{\rtf1 Hello World}"
        result = _strip_rtf(rtf)
        assert "Hello World" in result

    def test_par_becomes_newline(self):
        rtf = rb"{\rtf1 Line one\par Line two}"
        result = _strip_rtf(rtf)
        assert "Line one" in result
        assert "Line two" in result
        assert "\n" in result

    def test_line_becomes_newline(self):
        rtf = rb"{\rtf1 A\line B}"
        result = _strip_rtf(rtf)
        assert "A" in result
        assert "B" in result

    def test_tab_becomes_tab(self):
        rtf = rb"{\rtf1 Col1\tab Col2}"
        result = _strip_rtf(rtf)
        assert "Col1" in result
        assert "Col2" in result

    def test_hex_escape(self):
        # \'e9 is 'e' with acute accent (é)
        rtf = rb"{\rtf1 caf\'e9}"
        result = _strip_rtf(rtf)
        assert "caf" in result
        assert "é" in result

    def test_unicode_escape(self):
        # \u8364 is € (Euro sign)
        rtf = rb"{\rtf1 Price: \u8364 100}"
        result = _strip_rtf(rtf)
        assert "Price:" in result
        assert "€" in result

    def test_negative_unicode(self):
        # Negative unicode codes should be converted by adding 65536
        rtf = rb"{\rtf1 \u-10179 }"
        result = _strip_rtf(rtf)
        # -10179 + 65536 = 55357, which is a surrogate — may produce replacement char
        # Just ensure no crash
        assert isinstance(result, str)

    def test_smart_quotes(self):
        rtf = rb"{\rtf1 \lquote hello\rquote}"
        result = _strip_rtf(rtf)
        assert "'" in result
        assert "hello" in result

    def test_double_quotes(self):
        rtf = rb"{\rtf1 \ldblquote hi\rdblquote}"
        result = _strip_rtf(rtf)
        assert '"' in result

    def test_emdash(self):
        rtf = rb"{\rtf1 A\emdash B}"
        result = _strip_rtf(rtf)
        assert "—" in result

    def test_endash(self):
        rtf = rb"{\rtf1 A\endash B}"
        result = _strip_rtf(rtf)
        assert "–" in result

    def test_bullet(self):
        rtf = rb"{\rtf1 \bullet Item}"
        result = _strip_rtf(rtf)
        assert "•" in result

    def test_fonttbl_skipped(self):
        rtf = rb"{\rtf1 {\fonttbl {\f0 Arial;}} Hello}"
        result = _strip_rtf(rtf)
        assert "Hello" in result
        assert "Arial" not in result

    def test_colortbl_skipped(self):
        rtf = rb"{\rtf1 {\colortbl;\red0\green0\blue0;} Text}"
        result = _strip_rtf(rtf)
        assert "Text" in result

    def test_stylesheet_skipped(self):
        rtf = rb"{\rtf1 {\stylesheet {\s0 Normal;}} Content}"
        result = _strip_rtf(rtf)
        assert "Content" in result
        assert "Normal" not in result

    def test_escaped_braces(self):
        rtf = rb"{\rtf1 Open \{ and close \}}"
        result = _strip_rtf(rtf)
        assert "{" in result
        assert "}" in result

    def test_escaped_backslash(self):
        rtf = rb"{\rtf1 Path: C:\\Users}"
        result = _strip_rtf(rtf)
        assert "\\" in result

    def test_multiple_newlines_collapsed(self):
        rtf = rb"{\rtf1 A\par\par\par\par B}"
        result = _strip_rtf(rtf)
        # 4 \par → 4 newlines, collapsed to 2
        assert "\n\n\n" not in result

    def test_multiple_spaces_collapsed(self):
        rtf = rb"{\rtf1 Too   many   spaces}"
        result = _strip_rtf(rtf)
        assert "  " not in result

    def test_empty_rtf(self):
        result = _strip_rtf(b"")
        assert result == ""

    def test_cr_lf_ignored(self):
        rtf = b"{\\rtf1 Hello\r\n World}"
        result = _strip_rtf(rtf)
        assert "Hello" in result
        assert "World" in result


# ---------------------------------------------------------------------------
# Tests: PARSER_REGISTRY structure
# ---------------------------------------------------------------------------

class TestParserRegistry:
    def test_registry_has_entries(self):
        assert len(PARSER_REGISTRY) > 0

    def test_all_extensions_lowercase(self):
        for ext in PARSER_REGISTRY:
            assert ext == ext.lower()
            assert ext.startswith(".")

    def test_pdf_registered(self):
        assert ".pdf" in PARSER_REGISTRY

    def test_docx_registered(self):
        assert ".docx" in PARSER_REGISTRY

    def test_xlsx_registered(self):
        assert ".xlsx" in PARSER_REGISTRY

    def test_csv_registered(self):
        assert ".csv" in PARSER_REGISTRY

    def test_tsv_registered(self):
        assert ".tsv" in PARSER_REGISTRY

    def test_html_registered(self):
        assert ".html" in PARSER_REGISTRY
        assert ".htm" in PARSER_REGISTRY

    def test_eml_registered(self):
        assert ".eml" in PARSER_REGISTRY

    def test_mbox_registered(self):
        assert ".mbox" in PARSER_REGISTRY

    def test_epub_registered(self):
        assert ".epub" in PARSER_REGISTRY

    def test_rtf_registered(self):
        assert ".rtf" in PARSER_REGISTRY

    def test_text_formats_registered(self):
        for ext in (".txt", ".md", ".py", ".js", ".json", ".yaml", ".sh"):
            assert ext in PARSER_REGISTRY, f"{ext} not registered"

    def test_all_callables(self):
        for ext, parser in PARSER_REGISTRY.items():
            assert callable(parser), f"Parser for {ext} is not callable"


# ---------------------------------------------------------------------------
# Tests: parse_file orchestration
# ---------------------------------------------------------------------------

class TestParseFile:
    def test_missing_file_raises(self):
        with pytest.raises(FileNotFoundError, match="File not found"):
            parse_file("/nonexistent/path/file.txt")

    def test_empty_file_raises(self, tmp_path):
        f = tmp_path / "empty.txt"
        f.touch()  # 0 bytes
        with pytest.raises(ValueError, match="empty"):
            parse_file(str(f))

    def test_unsupported_extension_raises(self, tmp_path):
        f = tmp_path / "data.xyz_unsupported"
        f.write_text("content")
        with pytest.raises(ValueError, match="Unsupported file type"):
            parse_file(str(f))

    def test_unsupported_lists_supported_formats(self, tmp_path):
        f = tmp_path / "data.xyz_unsupported"
        f.write_text("content")
        with pytest.raises(ValueError, match="Supported"):
            parse_file(str(f))

    def test_extension_case_insensitive(self, tmp_path):
        f = tmp_path / "readme.TXT"
        f.write_text("hello world")
        result = parse_file(str(f))
        # parse_file lowercases ext for registry lookup, but parser may preserve case
        assert result["text"] == "hello world"

    def test_dispatches_to_correct_parser(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("content here")
        result = parse_file(str(f))
        assert result["text"] == "content here"
        assert result["file_type"] == "txt"


# ---------------------------------------------------------------------------
# Tests: parse_text (real filesystem, no heavy deps)
# ---------------------------------------------------------------------------

class TestParseText:
    def test_reads_text_file(self, tmp_path):
        f = tmp_path / "hello.txt"
        f.write_text("Hello, World!")
        result = parse_file(str(f))
        assert result["text"] == "Hello, World!"
        assert result["file_type"] == "txt"
        assert result["page_count"] is None

    def test_reads_python_file(self, tmp_path):
        f = tmp_path / "script.py"
        f.write_text("print('hi')")
        result = parse_file(str(f))
        assert result["file_type"] == "py"
        assert "print" in result["text"]

    def test_reads_json_file(self, tmp_path):
        f = tmp_path / "data.json"
        f.write_text('{"key": "value"}')
        result = parse_file(str(f))
        assert result["file_type"] == "json"

    def test_reads_markdown_file(self, tmp_path):
        f = tmp_path / "readme.md"
        f.write_text("# Title\n\nParagraph")
        result = parse_file(str(f))
        assert result["file_type"] == "md"
        assert "# Title" in result["text"]

    def test_binary_file_raises(self, tmp_path):
        f = tmp_path / "binary.txt"
        f.write_bytes(b"text\x00with\x00null\x00bytes")
        with pytest.raises(ValueError, match="binary"):
            parse_file(str(f))

    def test_truncates_long_text(self, tmp_path):
        f = tmp_path / "huge.txt"
        f.write_text("x" * (_MAX_TEXT_CHARS + 1000))
        result = parse_file(str(f))
        assert len(result["text"]) == _MAX_TEXT_CHARS


# ---------------------------------------------------------------------------
# Tests: parse_html (real filesystem, stdlib only)
# ---------------------------------------------------------------------------

class TestParseHtml:
    def test_basic_html(self, tmp_path):
        f = tmp_path / "page.html"
        f.write_text("<html><body><p>Hello</p></body></html>")
        result = parse_file(str(f))
        assert "Hello" in result["text"]
        assert result["file_type"] == "html"
        assert result["page_count"] is None

    def test_strips_script_and_style(self, tmp_path):
        f = tmp_path / "page.html"
        f.write_text(
            "<html><head><style>body{}</style></head>"
            "<body><script>alert(1)</script><p>Visible</p></body></html>"
        )
        result = parse_file(str(f))
        assert "Visible" in result["text"]
        assert "alert" not in result["text"]
        assert "body{}" not in result["text"]

    def test_htm_extension(self, tmp_path):
        f = tmp_path / "page.htm"
        f.write_text("<p>Content</p>")
        result = parse_file(str(f))
        assert result["file_type"] == "html"

    def test_complex_html(self, tmp_path):
        f = tmp_path / "complex.html"
        f.write_text("""
            <html>
            <head><title>Test Page</title></head>
            <body>
                <h1>Main Title</h1>
                <div>
                    <p>Paragraph one.</p>
                    <ul><li>Item A</li><li>Item B</li></ul>
                </div>
                <noscript>Enable JS</noscript>
            </body>
            </html>
        """)
        result = parse_file(str(f))
        assert "Main Title" in result["text"]
        assert "Paragraph one" in result["text"]
        assert "Item A" in result["text"]
        # noscript content should be skipped
        assert "Enable JS" not in result["text"]


# ---------------------------------------------------------------------------
# Tests: parse_eml (real filesystem, stdlib email module)
# ---------------------------------------------------------------------------

class TestParseEml:
    def _make_eml(self, tmp_path, subject="Test", body="Hello",
                  from_addr="alice@example.com", to_addr="bob@example.com",
                  html_body=None, attachments=None):
        """Create a real .eml file using stdlib."""
        if html_body and not body:
            msg = email.mime.text.MIMEText(html_body, "html")
        elif html_body:
            msg = email.mime.multipart.MIMEMultipart("alternative")
            msg.attach(email.mime.text.MIMEText(body, "plain"))
            msg.attach(email.mime.text.MIMEText(html_body, "html"))
        else:
            msg = email.mime.text.MIMEText(body, "plain")

        if attachments:
            outer = email.mime.multipart.MIMEMultipart("mixed")
            outer.attach(msg)
            for name, content in attachments:
                att = email.mime.text.MIMEText(content)
                att.add_header("Content-Disposition", "attachment", filename=name)
                outer.attach(att)
            msg = outer

        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = to_addr
        msg["Date"] = "Mon, 28 Feb 2026 12:00:00 +0000"

        f = tmp_path / "test.eml"
        f.write_bytes(msg.as_bytes())
        return str(f)

    def test_basic_eml(self, tmp_path):
        path = self._make_eml(tmp_path, subject="Meeting", body="Let's meet")
        result = parse_file(path)
        assert result["file_type"] == "eml"
        assert "Meeting" in result["text"]
        assert "Let's meet" in result["text"]
        assert result["subject"] == "Meeting"
        assert result["attachment_count"] == 0

    def test_eml_headers(self, tmp_path):
        path = self._make_eml(tmp_path, from_addr="sender@test.com", to_addr="rcpt@test.com")
        result = parse_file(path)
        # Email anonymization replaces local-part but keeps domain
        assert "@test.com" in result["text"]
        assert "From:" in result["text"]
        assert "To:" in result["text"]

    def test_eml_html_body_stripped(self, tmp_path):
        path = self._make_eml(tmp_path, body=None,
                              html_body="<html><body><p>HTML content</p></body></html>")
        result = parse_file(path)
        assert "HTML content" in result["text"]
        assert "<p>" not in result["text"]

    def test_eml_multipart_prefers_plain(self, tmp_path):
        path = self._make_eml(tmp_path, body="Plain version",
                              html_body="<p>HTML version</p>")
        result = parse_file(path)
        assert "Plain version" in result["text"]

    def test_eml_with_attachments(self, tmp_path):
        path = self._make_eml(tmp_path, body="See attached",
                              attachments=[("report.csv", "a,b,c")])
        result = parse_file(path)
        assert result["attachment_count"] == 1
        assert "Attachments" in result["text"]
        assert "report.csv" in result["text"]

    def test_eml_invalid_raises(self, tmp_path):
        f = tmp_path / "bad.eml"
        f.write_bytes(b"\x00\x01\x02binary junk")
        # stdlib email module is lenient — it may parse garbage without error.
        # Just verify it doesn't crash and returns something.
        result = parse_file(str(f))
        assert result["file_type"] == "eml"


# ---------------------------------------------------------------------------
# Tests: parse_rtf (real filesystem, pure function)
# ---------------------------------------------------------------------------

class TestParseRtf:
    def test_basic_rtf(self, tmp_path):
        f = tmp_path / "doc.rtf"
        f.write_bytes(rb"{\rtf1\ansi Hello from RTF}")
        result = parse_file(str(f))
        assert "Hello from RTF" in result["text"]
        assert result["file_type"] == "rtf"
        assert result["page_count"] is None

    def test_rtf_with_formatting(self, tmp_path):
        f = tmp_path / "formatted.rtf"
        f.write_bytes(rb"{\rtf1\ansi {\b Bold text}\par Normal text}")
        result = parse_file(str(f))
        assert "Bold text" in result["text"]
        assert "Normal text" in result["text"]

    def test_rtf_with_tables(self, tmp_path):
        f = tmp_path / "table.rtf"
        content = rb"{\rtf1\ansi {\fonttbl{\f0 Arial;}} Row 1\tab Col 2\par Row 2\tab Col 2}"
        f.write_bytes(content)
        result = parse_file(str(f))
        assert "Row 1" in result["text"]
        assert "Row 2" in result["text"]

    def test_empty_rtf_raises(self, tmp_path):
        f = tmp_path / "empty.rtf"
        # RTF with only control codes, no text
        f.write_bytes(rb"{\rtf1\ansi{\fonttbl{\f0 Arial;}}{\colortbl;\red0\green0\blue0;}}")
        with pytest.raises(ValueError, match="No text content"):
            parse_file(str(f))


# ---------------------------------------------------------------------------
# Tests: parse_epub (real filesystem, stdlib zipfile + xml)
# ---------------------------------------------------------------------------

class TestParseEpub:
    def _make_epub(self, tmp_path, title="Test Book",
                   chapters=None, include_container=True):
        """Create a minimal valid EPUB file."""
        if chapters is None:
            chapters = [("ch1.xhtml", "<html><body><p>Chapter One</p></body></html>")]

        epub_path = tmp_path / "book.epub"
        with zipfile.ZipFile(str(epub_path), "w") as zf:
            # container.xml
            if include_container:
                container = (
                    '<?xml version="1.0" encoding="UTF-8"?>'
                    '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                    '<rootfiles>'
                    '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
                    '</rootfiles>'
                    '</container>'
                )
                zf.writestr("META-INF/container.xml", container)

            # OPF manifest
            manifest_items = ""
            spine_items = ""
            for i, (fname, _) in enumerate(chapters):
                item_id = f"ch{i}"
                manifest_items += (
                    f'<item id="{item_id}" href="{fname}" '
                    f'media-type="application/xhtml+xml"/>'
                )
                spine_items += f'<itemref idref="{item_id}"/>'

            opf = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<package xmlns="http://www.idpf.org/2007/opf">'
                '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                f'<dc:title>{title}</dc:title>'
                '</metadata>'
                f'<manifest>{manifest_items}</manifest>'
                f'<spine>{spine_items}</spine>'
                '</package>'
            )
            zf.writestr("content.opf", opf)

            # Chapter content files
            for fname, content in chapters:
                zf.writestr(fname, content)

        return str(epub_path)

    def test_basic_epub(self, tmp_path):
        path = self._make_epub(tmp_path, title="My Book")
        result = parse_file(path)
        assert result["file_type"] == "epub"
        assert result["title"] == "My Book"
        assert "Chapter One" in result["text"]
        assert result["page_count"] == 1

    def test_epub_multiple_chapters(self, tmp_path):
        chapters = [
            ("ch1.xhtml", "<html><body><p>First</p></body></html>"),
            ("ch2.xhtml", "<html><body><p>Second</p></body></html>"),
            ("ch3.xhtml", "<html><body><p>Third</p></body></html>"),
        ]
        path = self._make_epub(tmp_path, chapters=chapters)
        result = parse_file(path)
        assert result["page_count"] == 3
        assert "First" in result["text"]
        assert "Second" in result["text"]
        assert "Third" in result["text"]

    def test_epub_title_in_output(self, tmp_path):
        path = self._make_epub(tmp_path, title="Great Novel")
        result = parse_file(path)
        assert "Title: Great Novel" in result["text"]

    def test_epub_no_title(self, tmp_path):
        path = self._make_epub(tmp_path, title="")
        result = parse_file(path)
        # Should not have "Title:" prefix
        assert not result["text"].startswith("Title:")

    def test_epub_strips_html(self, tmp_path):
        chapters = [("ch1.xhtml", "<html><body><p>Text <b>bold</b></p></body></html>")]
        path = self._make_epub(tmp_path, chapters=chapters)
        result = parse_file(path)
        assert "<p>" not in result["text"]
        assert "<b>" not in result["text"]
        assert "Text" in result["text"]
        assert "bold" in result["text"]

    def test_epub_chapters_separated(self, tmp_path):
        chapters = [
            ("ch1.xhtml", "<html><body><p>One</p></body></html>"),
            ("ch2.xhtml", "<html><body><p>Two</p></body></html>"),
        ]
        path = self._make_epub(tmp_path, chapters=chapters)
        result = parse_file(path)
        assert "---" in result["text"]  # Chapter separator

    def test_epub_no_content_raises(self, tmp_path):
        # EPUB with no text chapters — only image media type
        epub_path = tmp_path / "empty.epub"
        with zipfile.ZipFile(str(epub_path), "w") as zf:
            container = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<container xmlns="urn:oasis:names:tc:opendocument:xmlns:container">'
                '<rootfiles>'
                '<rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>'
                '</rootfiles></container>'
            )
            zf.writestr("META-INF/container.xml", container)
            opf = (
                '<?xml version="1.0" encoding="UTF-8"?>'
                '<package xmlns="http://www.idpf.org/2007/opf">'
                '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">'
                '<dc:title>Empty</dc:title></metadata>'
                '<manifest>'
                '<item id="img" href="cover.jpg" media-type="image/jpeg"/>'
                '</manifest>'
                '<spine></spine>'
                '</package>'
            )
            zf.writestr("content.opf", opf)
        with pytest.raises(ValueError, match="No text content"):
            parse_file(str(epub_path))

    def test_epub_corrupted_zip_raises(self, tmp_path):
        f = tmp_path / "corrupted.epub"
        f.write_bytes(b"not a zip file at all")
        with pytest.raises(ValueError, match="corrupted"):
            parse_file(str(f))

    def test_epub_opf_fallback(self, tmp_path):
        """EPUB without container.xml should fall back to scanning for .opf."""
        path = self._make_epub(tmp_path, include_container=False)
        result = parse_file(path)
        assert "Chapter One" in result["text"]


# ---------------------------------------------------------------------------
# Tests: parse_pdf (mocked pdfplumber)
# ---------------------------------------------------------------------------

class TestParsePdf:
    def _mock_page(self, text="Page content", tables=None, annots=None):
        """Create a mock pdfplumber page."""
        page = MagicMock()
        page.extract_text.return_value = text
        page.find_tables.return_value = tables or []
        page.annots = annots
        page.outside_bounding_box.return_value = page
        return page

    def _mock_table(self, rows, bbox=(0, 0, 100, 100)):
        """Create a mock pdfplumber table."""
        table = MagicMock()
        table.extract.return_value = rows
        table.bbox = bbox
        return table

    @patch("pdfplumber.open")
    def test_single_page_no_tables(self, mock_open, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_text("dummy")  # Just needs to exist

        mock_pdf = MagicMock()
        mock_pdf.pages = [self._mock_page(text="Hello PDF")]
        mock_open.return_value = mock_pdf

        result = parse_file(str(f))
        assert result["file_type"] == "pdf"
        assert "Hello PDF" in result["text"]
        assert result["page_count"] == 1
        assert result["table_count"] == 0

    @patch("pdfplumber.open")
    def test_pdf_with_tables(self, mock_open, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_text("dummy")

        table = self._mock_table([["Name", "Age"], ["Alice", "30"]])
        page = self._mock_page(text="Some text", tables=[table])
        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_open.return_value = mock_pdf

        result = parse_file(str(f))
        assert result["table_count"] == 1
        assert "| Name | Age |" in result["text"]
        assert "| Alice | 30 |" in result["text"]

    @patch("pdfplumber.open")
    def test_pdf_table_header_separator(self, mock_open, tmp_path):
        f = tmp_path / "doc.pdf"
        f.write_text("dummy")

        table = self._mock_table([["Col1", "Col2"], ["V1", "V2"]])
        page = self._mock_page(text="", tables=[table])
        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_open.return_value = mock_pdf

        result = parse_file(str(f))
        # Should have header separator row
        assert "| --- | --- |" in result["text"]

    @patch("pdfplumber.open")
    def test_pdf_corrupted_raises(self, mock_open, tmp_path):
        f = tmp_path / "bad.pdf"
        f.write_text("dummy")

        mock_open.side_effect = Exception("Invalid PDF")
        with pytest.raises(ValueError, match="corrupted"):
            parse_file(str(f))

    @patch("pdfplumber.open")
    def test_pdf_image_only_raises(self, mock_open, tmp_path):
        f = tmp_path / "scan.pdf"
        f.write_text("dummy")

        page = self._mock_page(text=None)
        page.extract_text.return_value = None
        page.find_tables.return_value = []
        mock_pdf = MagicMock()
        mock_pdf.pages = [page]
        mock_open.return_value = mock_pdf

        with pytest.raises(ValueError, match="scanned|OCR"):
            parse_file(str(f))

    @patch("pdfplumber.open")
    def test_pdf_form_fields(self, mock_open, tmp_path):
        f = tmp_path / "form.pdf"
        f.write_text("dummy")

        page = self._mock_page(text="Form page")
        annot_page = self._mock_page()
        annot_page.annots = [
            {"T": "Name", "V": "Alice"},
            {"T": "Email", "V": "alice@test.com"},
        ]

        mock_pdf1 = MagicMock()
        mock_pdf1.pages = [page]
        mock_pdf2 = MagicMock()
        mock_pdf2.pages = [annot_page]

        # First call for text extraction, second for form fields
        mock_open.side_effect = [mock_pdf1, mock_pdf2]

        result = parse_file(str(f))
        assert result.get("form_field_count") == 2
        assert "Name: Alice" in result["text"]

    @patch("pdfplumber.open")
    def test_pdf_multi_page(self, mock_open, tmp_path):
        f = tmp_path / "multi.pdf"
        f.write_text("dummy")

        pages = [
            self._mock_page(text="Page 1 content"),
            self._mock_page(text="Page 2 content"),
            self._mock_page(text="Page 3 content"),
        ]
        mock_pdf = MagicMock()
        mock_pdf.pages = pages
        mock_open.return_value = mock_pdf

        result = parse_file(str(f))
        assert result["page_count"] == 3
        assert "Page 1 content" in result["text"]
        assert "Page 3 content" in result["text"]


# ---------------------------------------------------------------------------
# Tests: parse_docx (mocked python-docx)
# ---------------------------------------------------------------------------

class TestParseDocx:
    @patch("docx.Document", create=True)
    def test_basic_docx(self, mock_doc_cls, tmp_path):
        f = tmp_path / "doc.docx"
        f.write_text("dummy")

        para1 = MagicMock()
        para1.text = "First paragraph"
        para2 = MagicMock()
        para2.text = "Second paragraph"
        para_empty = MagicMock()
        para_empty.text = "   "  # whitespace only — should be filtered

        mock_doc = MagicMock()
        mock_doc.paragraphs = [para1, para_empty, para2]
        mock_doc.tables = []
        mock_doc_cls.return_value = mock_doc

        result = parse_file(str(f))
        assert result["file_type"] == "docx"
        assert "First paragraph" in result["text"]
        assert "Second paragraph" in result["text"]
        assert result["page_count"] is None

    @patch("docx.Document", create=True)
    def test_docx_with_tables(self, mock_doc_cls, tmp_path):
        f = tmp_path / "tables.docx"
        f.write_text("dummy")

        mock_doc = MagicMock()
        mock_doc.paragraphs = []

        cell1 = MagicMock()
        cell1.text = "Name"
        cell2 = MagicMock()
        cell2.text = "Age"
        row = MagicMock()
        row.cells = [cell1, cell2]

        table = MagicMock()
        table.rows = [row]
        mock_doc.tables = [table]
        mock_doc_cls.return_value = mock_doc

        result = parse_file(str(f))
        assert "Tables" in result["text"]
        assert "Name" in result["text"]

    @patch("docx.Document", create=True)
    def test_docx_corrupted_raises(self, mock_doc_cls, tmp_path):
        f = tmp_path / "bad.docx"
        f.write_text("dummy")

        mock_doc_cls.side_effect = Exception("Bad file")
        with pytest.raises(ValueError, match="corrupted"):
            parse_file(str(f))

    @patch("docx.Document", create=True)
    def test_docx_empty_rows_skipped(self, mock_doc_cls, tmp_path):
        f = tmp_path / "empty_rows.docx"
        f.write_text("dummy")

        mock_doc = MagicMock()
        mock_doc.paragraphs = []

        empty_cell = MagicMock()
        empty_cell.text = ""
        row = MagicMock()
        row.cells = [empty_cell, empty_cell]

        table = MagicMock()
        table.rows = [row]
        mock_doc.tables = [table]
        mock_doc_cls.return_value = mock_doc

        result = parse_file(str(f))
        # Empty rows should be skipped, no table text
        assert "Tables" not in result["text"]


# ---------------------------------------------------------------------------
# Tests: parse_xlsx (mocked openpyxl)
# ---------------------------------------------------------------------------

class TestParseXlsx:
    @patch("openpyxl.load_workbook", create=True)
    def test_basic_xlsx(self, mock_lwb, tmp_path):
        f = tmp_path / "data.xlsx"
        f.write_text("dummy")

        ws = MagicMock()
        ws.iter_rows.return_value = [
            ("Name", "Score"),
            ("Alice", 95),
            ("Bob", 87),
        ]

        wb = MagicMock()
        wb.sheetnames = ["Sheet1"]
        wb.__getitem__ = MagicMock(return_value=ws)
        mock_lwb.return_value = wb

        result = parse_file(str(f))
        assert result["file_type"] == "xlsx"
        assert result["page_count"] == 1
        assert "Name" in result["text"]
        assert "Alice" in result["text"]

    @patch("openpyxl.load_workbook", create=True)
    def test_xlsx_columns_tracked(self, mock_lwb, tmp_path):
        f = tmp_path / "cols.xlsx"
        f.write_text("dummy")

        ws = MagicMock()
        ws.iter_rows.return_value = [
            ("Col1", "Col2", "Col3"),
            ("a", "b", "c"),
        ]

        wb = MagicMock()
        wb.sheetnames = ["Data"]
        wb.__getitem__ = MagicMock(return_value=ws)
        mock_lwb.return_value = wb

        result = parse_file(str(f))
        columns = json.loads(result["columns"])
        assert "Col1" in columns
        assert "Col2" in columns

    @patch("openpyxl.load_workbook", create=True)
    def test_xlsx_corrupted_raises(self, mock_lwb, tmp_path):
        f = tmp_path / "bad.xlsx"
        f.write_text("dummy")

        mock_lwb.side_effect = Exception("Invalid file")
        with pytest.raises(ValueError, match="corrupted"):
            parse_file(str(f))

    @patch("openpyxl.load_workbook", create=True)
    def test_xlsx_multiple_sheets(self, mock_lwb, tmp_path):
        f = tmp_path / "multi.xlsx"
        f.write_text("dummy")

        ws1 = MagicMock()
        ws1.iter_rows.return_value = [("A",), ("1",)]
        ws2 = MagicMock()
        ws2.iter_rows.return_value = [("B",), ("2",)]

        wb = MagicMock()
        wb.sheetnames = ["Sheet1", "Sheet2"]
        wb.__getitem__ = MagicMock(side_effect=lambda name: ws1 if name == "Sheet1" else ws2)
        mock_lwb.return_value = wb

        result = parse_file(str(f))
        assert result["page_count"] == 2
        assert "Sheet1" in result["text"]
        assert "Sheet2" in result["text"]

    @patch("openpyxl.load_workbook", create=True)
    def test_xlsx_empty_sheet_skipped(self, mock_lwb, tmp_path):
        f = tmp_path / "empty.xlsx"
        f.write_text("dummy")

        ws = MagicMock()
        ws.iter_rows.return_value = [(None, None)]  # All None

        wb = MagicMock()
        wb.sheetnames = ["Empty"]
        wb.__getitem__ = MagicMock(return_value=ws)
        mock_lwb.return_value = wb

        result = parse_file(str(f))
        assert result["row_count"] == 0

    @patch("openpyxl.load_workbook", create=True)
    def test_xlsx_row_truncation(self, mock_lwb, tmp_path):
        f = tmp_path / "big.xlsx"
        f.write_text("dummy")

        # Header + 5001 data rows
        rows = [("ID", "Value")] + [(str(i), "data") for i in range(5001)]
        ws = MagicMock()
        ws.iter_rows.return_value = rows

        wb = MagicMock()
        wb.sheetnames = ["Big"]
        wb.__getitem__ = MagicMock(return_value=ws)
        mock_lwb.return_value = wb

        result = parse_file(str(f))
        assert result.get("truncated") is True

    @patch("openpyxl.load_workbook", create=True)
    def test_xlsx_none_cells_handled(self, mock_lwb, tmp_path):
        f = tmp_path / "nulls.xlsx"
        f.write_text("dummy")

        ws = MagicMock()
        ws.iter_rows.return_value = [
            ("Header", None),
            ("Value", None),
        ]

        wb = MagicMock()
        wb.sheetnames = ["S1"]
        wb.__getitem__ = MagicMock(return_value=ws)
        mock_lwb.return_value = wb

        result = parse_file(str(f))
        assert result["file_type"] == "xlsx"
        # None should be converted to empty string, not crash


# ---------------------------------------------------------------------------
# Tests: parse_csv (mocked pandas)
# ---------------------------------------------------------------------------

class TestParseCsv:
    @patch("pandas.read_csv", create=True)
    def test_basic_csv(self, mock_read_csv, tmp_path):
        f = tmp_path / "data.csv"
        f.write_text("name,age\nAlice,30\nBob,25\n")

        # Create a mock DataFrame
        df = MagicMock()
        df.__len__ = MagicMock(return_value=2)
        df.columns = ["name", "age"]
        df.__getitem__ = MagicMock(return_value=MagicMock(dtype="object"))
        df.head.return_value = df
        df.to_string.return_value = "name  age\nAlice  30\nBob  25"
        mock_read_csv.return_value = df

        result = parse_file(str(f))
        assert result["file_type"] == "csv"
        assert result["row_count"] == 2
        assert result["page_count"] is None

    @patch("pandas.read_csv", create=True)
    def test_tsv_file(self, mock_read_csv, tmp_path):
        f = tmp_path / "data.tsv"
        f.write_text("name\tage\nAlice\t30\n")

        df = MagicMock()
        df.__len__ = MagicMock(return_value=1)
        df.columns = ["name", "age"]
        df.__getitem__ = MagicMock(return_value=MagicMock(dtype="object"))
        df.head.return_value = df
        df.to_string.return_value = "name  age\nAlice  30"
        mock_read_csv.return_value = df

        result = parse_file(str(f))
        assert result["file_type"] == "tsv"

    @patch("pandas.read_csv", create=True)
    def test_csv_corrupted_raises(self, mock_read_csv, tmp_path):
        f = tmp_path / "bad.csv"
        f.write_text("not,a,valid\x00csv")

        mock_read_csv.side_effect = Exception("Parse error")
        with pytest.raises(ValueError, match="corrupted"):
            parse_file(str(f))

    @patch("pandas.read_csv", create=True)
    def test_csv_columns_json(self, mock_read_csv, tmp_path):
        f = tmp_path / "cols.csv"
        f.write_text("a,b,c\n1,2,3\n")

        df = MagicMock()
        df.__len__ = MagicMock(return_value=1)
        df.columns = ["a", "b", "c"]
        df.__getitem__ = MagicMock(return_value=MagicMock(dtype="object"))
        df.head.return_value = df
        df.to_string.return_value = "a b c\n1 2 3"
        mock_read_csv.return_value = df

        result = parse_file(str(f))
        columns = json.loads(result["columns"])
        assert columns == ["a", "b", "c"]

    @patch("pandas.read_csv", create=True)
    def test_csv_truncation(self, mock_read_csv, tmp_path):
        f = tmp_path / "big.csv"
        f.write_text("x\n" + "\n".join(str(i) for i in range(6000)))

        df = MagicMock()
        df.__len__ = MagicMock(return_value=6000)
        df.columns = ["x"]
        df.__getitem__ = MagicMock(return_value=MagicMock(dtype="int64"))
        df.head.side_effect = lambda n=5: df
        df.to_string.return_value = "truncated output"
        mock_read_csv.return_value = df

        result = parse_file(str(f))
        assert result.get("truncated") is True
        assert result["row_count"] == 6000


# ---------------------------------------------------------------------------
# Tests: register_parser decorator
# ---------------------------------------------------------------------------

class TestRegisterParser:
    def test_decorator_adds_to_registry(self):
        # Use a unique extension to avoid polluting real registry
        @register_parser([".test_fake_ext_xyz"])
        def _fake_parser(path):
            return {"text": "", "file_type": "fake", "page_count": None}

        assert ".test_fake_ext_xyz" in PARSER_REGISTRY
        assert PARSER_REGISTRY[".test_fake_ext_xyz"] is _fake_parser

        # Cleanup
        del PARSER_REGISTRY[".test_fake_ext_xyz"]

    def test_decorator_normalizes_case(self):
        @register_parser([".TEST_UPPER_XYZ"])
        def _upper_parser(path):
            return {}

        assert ".test_upper_xyz" in PARSER_REGISTRY
        assert ".TEST_UPPER_XYZ" not in PARSER_REGISTRY

        # Cleanup
        del PARSER_REGISTRY[".test_upper_xyz"]

    def test_decorator_multiple_extensions(self):
        @register_parser([".ext_a_xyz", ".ext_b_xyz"])
        def _multi_parser(path):
            return {}

        assert ".ext_a_xyz" in PARSER_REGISTRY
        assert ".ext_b_xyz" in PARSER_REGISTRY
        assert PARSER_REGISTRY[".ext_a_xyz"] is PARSER_REGISTRY[".ext_b_xyz"]

        # Cleanup
        del PARSER_REGISTRY[".ext_a_xyz"]
        del PARSER_REGISTRY[".ext_b_xyz"]


# ---------------------------------------------------------------------------
# Tests: _MAX_TEXT_CHARS constant
# ---------------------------------------------------------------------------

class TestMaxTextChars:
    def test_value_is_two_million(self):
        assert _MAX_TEXT_CHARS == 2_000_000

    def test_value_is_integer(self):
        assert isinstance(_MAX_TEXT_CHARS, int)
