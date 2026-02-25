"""Tests for smart ingestion features (Phase 8B)."""
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock, patch

import pytest

# Ensure src/mcp is on the path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))


# ---------------------------------------------------------------------------
# Stub out heavy dependencies not available on the host
# ---------------------------------------------------------------------------

def _ensure_stub(name, stub_module):
    """Register a stub module if the real one isn't available."""
    if name not in sys.modules:
        sys.modules[name] = stub_module


# tiktoken stub
_tiktoken = ModuleType("tiktoken")


class _FakeEncoding:
    def encode(self, text):
        return text.split()


_tiktoken.get_encoding = lambda name: _FakeEncoding()
_ensure_stub("tiktoken", _tiktoken)

# httpx stub
_httpx = ModuleType("httpx")


class _AsyncClient:
    def __init__(self, **kwargs):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass

    async def post(self, *args, **kwargs):
        return MagicMock()


_httpx.AsyncClient = _AsyncClient
_ensure_stub("httpx", _httpx)

# spacy stub
_spacy = ModuleType("spacy")
_spacy.load = MagicMock(side_effect=OSError("stub"))
_ensure_stub("spacy", _spacy)

# chromadb stub (with submodules)
_chromadb = ModuleType("chromadb")
_chromadb.HttpClient = MagicMock
_chromadb_config = ModuleType("chromadb.config")
_chromadb_config.Settings = MagicMock
_chromadb.config = _chromadb_config
_ensure_stub("chromadb", _chromadb)
_ensure_stub("chromadb.config", _chromadb_config)

# neo4j stub
_neo4j = ModuleType("neo4j")
_neo4j.GraphDatabase = MagicMock()
_ensure_stub("neo4j", _neo4j)

# redis stub
_redis_mod = ModuleType("redis")
_redis_mod.Redis = MagicMock
_ensure_stub("redis", _redis_mod)

# pdfplumber stub
_pdfplumber = ModuleType("pdfplumber")
_ensure_stub("pdfplumber", _pdfplumber)

# openpyxl stub
_openpyxl = ModuleType("openpyxl")
_ensure_stub("openpyxl", _openpyxl)

# pandas stub
_pandas = ModuleType("pandas")
_ensure_stub("pandas", _pandas)

# docx stub
_docx = ModuleType("docx")
_ensure_stub("docx", _docx)


# ---------------------------------------------------------------------------
# Tests for new parsers (Phase 8B)
# ---------------------------------------------------------------------------


class TestEmlParser:
    """Test .eml email file parsing."""

    def test_parse_simple_eml(self, tmp_path):
        """Parse a simple text/plain email."""
        from utils.parsers import parse_eml

        eml_content = (
            "From: alice@example.com\r\n"
            "To: bob@example.com\r\n"
            "Subject: Test Email\r\n"
            "Date: Tue, 25 Feb 2026 10:00:00 -0500\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "Hello Bob,\r\n\r\n"
            "This is a test email.\r\n\r\n"
            "Best,\r\nAlice"
        )

        eml_path = tmp_path / "test.eml"
        eml_path.write_bytes(eml_content.encode("utf-8"))

        result = parse_eml(str(eml_path))

        assert result["file_type"] == "eml"
        assert "alice@example.com" in result["text"]
        assert "Test Email" in result["text"]
        assert "Hello Bob" in result["text"]
        assert result["subject"] == "Test Email"

    def test_parse_multipart_eml(self, tmp_path):
        """Parse a multipart email with text/plain body."""
        eml_content = (
            "From: alice@example.com\r\n"
            "To: bob@example.com\r\n"
            "Subject: Multipart Test\r\n"
            "MIME-Version: 1.0\r\n"
            "Content-Type: multipart/mixed; boundary=boundary123\r\n"
            "\r\n"
            "--boundary123\r\n"
            "Content-Type: text/plain; charset=utf-8\r\n"
            "\r\n"
            "Plain text body here.\r\n"
            "--boundary123\r\n"
            "Content-Type: application/pdf; name=\"report.pdf\"\r\n"
            "Content-Disposition: attachment; filename=\"report.pdf\"\r\n"
            "\r\n"
            "PDF-BINARY-DATA\r\n"
            "--boundary123--\r\n"
        )

        eml_path = tmp_path / "multipart.eml"
        eml_path.write_bytes(eml_content.encode("utf-8"))

        from utils.parsers import parse_eml

        result = parse_eml(str(eml_path))

        assert "Plain text body here" in result["text"]
        assert result["attachment_count"] == 1
        assert "report.pdf" in result["text"]

    def test_parse_empty_eml(self, tmp_path):
        """Empty email should parse without error."""
        eml_content = (
            "From: alice@example.com\r\n"
            "To: bob@example.com\r\n"
            "Subject: Empty\r\n"
            "Content-Type: text/plain\r\n"
            "\r\n"
        )

        eml_path = tmp_path / "empty.eml"
        eml_path.write_bytes(eml_content.encode("utf-8"))

        from utils.parsers import parse_eml

        result = parse_eml(str(eml_path))
        assert result["file_type"] == "eml"
        assert "alice@example.com" in result["text"]


class TestMboxParser:
    """Test .mbox mailbox file parsing."""

    def test_parse_mbox_single_message(self, tmp_path):
        """Parse an mbox with a single message."""
        mbox_content = (
            "From alice@example.com Tue Feb 25 10:00:00 2026\r\n"
            "From: alice@example.com\r\n"
            "Subject: Test Message\r\n"
            "Date: Tue, 25 Feb 2026 10:00:00 -0500\r\n"
            "\r\n"
            "This is the body.\r\n"
            "\r\n"
        )

        mbox_path = tmp_path / "test.mbox"
        mbox_path.write_bytes(mbox_content.encode("utf-8"))

        from utils.parsers import parse_mbox

        result = parse_mbox(str(mbox_path))

        assert result["file_type"] == "mbox"
        assert result["page_count"] == 1
        assert "Test Message" in result["text"]
        assert "This is the body" in result["text"]


class TestEpubParser:
    """Test .epub e-book parsing."""

    def _create_minimal_epub(self, epub_path):
        """Create a minimal valid EPUB file for testing."""
        with zipfile.ZipFile(epub_path, "w") as zf:
            # mimetype (must be first, uncompressed)
            zf.writestr("mimetype", "application/epub+zip")

            # container.xml
            container_xml = """<?xml version="1.0" encoding="UTF-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
            zf.writestr("META-INF/container.xml", container_xml)

            # content.opf
            opf = """<?xml version="1.0" encoding="UTF-8"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:title>Test Book</dc:title>
    <dc:identifier id="id">test-isbn-123</dc:identifier>
  </metadata>
  <manifest>
    <item id="ch1" href="chapter1.xhtml" media-type="application/xhtml+xml"/>
    <item id="ch2" href="chapter2.xhtml" media-type="application/xhtml+xml"/>
  </manifest>
  <spine>
    <itemref idref="ch1"/>
    <itemref idref="ch2"/>
  </spine>
</package>"""
            zf.writestr("OEBPS/content.opf", opf)

            # Chapter 1
            ch1 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 1</title></head>
<body>
<h1>Chapter One</h1>
<p>This is the first chapter of the test book.</p>
<p>It contains multiple paragraphs.</p>
</body>
</html>"""
            zf.writestr("OEBPS/chapter1.xhtml", ch1)

            # Chapter 2
            ch2 = """<?xml version="1.0" encoding="UTF-8"?>
<html xmlns="http://www.w3.org/1999/xhtml">
<head><title>Chapter 2</title></head>
<body>
<h1>Chapter Two</h1>
<p>This is the second chapter with more content.</p>
</body>
</html>"""
            zf.writestr("OEBPS/chapter2.xhtml", ch2)

    def test_parse_epub(self, tmp_path):
        """Parse a minimal EPUB and extract chapter text."""
        epub_path = tmp_path / "test.epub"
        self._create_minimal_epub(str(epub_path))

        from utils.parsers import parse_epub

        result = parse_epub(str(epub_path))

        assert result["file_type"] == "epub"
        assert result["page_count"] == 2
        assert result["title"] == "Test Book"
        assert "Chapter One" in result["text"]
        assert "first chapter" in result["text"]
        assert "Chapter Two" in result["text"]

    def test_parse_epub_not_zip(self, tmp_path):
        """Non-ZIP file raises ValueError."""
        bad_path = tmp_path / "not_an_epub.epub"
        bad_path.write_text("This is not a zip file")

        from utils.parsers import parse_epub

        with pytest.raises(ValueError, match="Failed to read EPUB"):
            parse_epub(str(bad_path))

    def test_parse_epub_no_content(self, tmp_path):
        """EPUB with no text content raises ValueError."""
        epub_path = tmp_path / "empty.epub"
        with zipfile.ZipFile(epub_path, "w") as zf:
            zf.writestr("mimetype", "application/epub+zip")
            container = """<?xml version="1.0"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles>
    <rootfile full-path="content.opf" media-type="application/oebps-package+xml"/>
  </rootfiles>
</container>"""
            zf.writestr("META-INF/container.xml", container)
            opf = """<?xml version="1.0"?>
<package xmlns="http://www.idpf.org/2007/opf" unique-identifier="id" version="3.0">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="id">test</dc:identifier>
  </metadata>
  <manifest/>
  <spine/>
</package>"""
            zf.writestr("content.opf", opf)

        from utils.parsers import parse_epub

        with pytest.raises(ValueError, match="No text content"):
            parse_epub(str(epub_path))


class TestRtfParser:
    """Test .rtf rich text format parsing."""

    def test_parse_simple_rtf(self, tmp_path):
        """Parse a simple RTF document."""
        from utils.parsers import parse_rtf

        rtf_content = rb"{\rtf1\ansi Hello, World! \par This is a test.}"
        rtf_path = tmp_path / "test.rtf"
        rtf_path.write_bytes(rtf_content)

        result = parse_rtf(str(rtf_path))

        assert result["file_type"] == "rtf"
        assert "Hello, World!" in result["text"]
        assert "This is a test" in result["text"]

    def test_parse_rtf_with_formatting(self, tmp_path):
        """Parse RTF with bold/italic control words."""
        rtf_content = rb"{\rtf1\ansi {\b Bold text} and {\i italic text} here.}"
        rtf_path = tmp_path / "formatted.rtf"
        rtf_path.write_bytes(rtf_content)

        from utils.parsers import parse_rtf

        result = parse_rtf(str(rtf_path))

        assert "Bold text" in result["text"]
        assert "italic text" in result["text"]

    def test_parse_rtf_with_unicode(self, tmp_path):
        """Parse RTF with Unicode escape sequences."""
        # \u233 is ë (e with diaeresis), followed by 'e' as replacement char
        rtf_content = rb"{\rtf1\ansi Caf\u233e is nice.}"
        rtf_path = tmp_path / "unicode.rtf"
        rtf_path.write_bytes(rtf_content)

        from utils.parsers import parse_rtf

        result = parse_rtf(str(rtf_path))

        assert "Caf" in result["text"]
        assert result["file_type"] == "rtf"

    def test_parse_rtf_skips_metadata(self, tmp_path):
        """RTF font tables and color tables should be stripped."""
        rtf_content = (
            rb"{\rtf1\ansi"
            rb"{\fonttbl{\f0 Times New Roman;}}"
            rb"{\colortbl;\red0\green0\blue0;}"
            rb" Visible text only.}"
        )
        rtf_path = tmp_path / "meta.rtf"
        rtf_path.write_bytes(rtf_content)

        from utils.parsers import parse_rtf

        result = parse_rtf(str(rtf_path))

        assert "Visible text only" in result["text"]
        assert "Times New Roman" not in result["text"]
        assert "colortbl" not in result["text"]

    def test_parse_empty_rtf(self, tmp_path):
        """RTF with no visible text raises ValueError."""
        rtf_content = rb"{\rtf1\ansi }"
        rtf_path = tmp_path / "empty.rtf"
        rtf_path.write_bytes(rtf_content)

        from utils.parsers import parse_rtf

        with pytest.raises(ValueError, match="No text content"):
            parse_rtf(str(rtf_path))


class TestHtmlTagStripper:
    """Test the shared _strip_html_tags utility."""

    def test_basic_strip(self):
        from utils.parsers import _strip_html_tags

        html = "<p>Hello <b>world</b></p>"
        assert "Hello" in _strip_html_tags(html)
        assert "world" in _strip_html_tags(html)

    def test_script_tags_removed(self):
        from utils.parsers import _strip_html_tags

        html = "<p>Visible</p><script>alert('xss')</script><p>More</p>"
        result = _strip_html_tags(html)
        assert "Visible" in result
        assert "More" in result
        assert "alert" not in result

    def test_style_tags_removed(self):
        from utils.parsers import _strip_html_tags

        html = "<style>body{color:red}</style><p>Content</p>"
        result = _strip_html_tags(html)
        assert "Content" in result
        assert "color" not in result


class TestRtfStripper:
    """Test the RTF text stripping utility."""

    def test_hex_chars(self):
        from utils.parsers import _strip_rtf

        # \'e9 = é
        raw = rb"{\rtf1 caf\\'e9}"
        result = _strip_rtf(raw)
        assert "caf" in result

    def test_special_symbols(self):
        from utils.parsers import _strip_rtf

        raw = rb"{\rtf1 line1\par line2\tab end}"
        result = _strip_rtf(raw)
        assert "line1" in result
        assert "line2" in result

    def test_escaped_braces(self):
        from utils.parsers import _strip_rtf

        raw = rb"{\rtf1 Open \{ and close \} braces}"
        result = _strip_rtf(raw)
        assert "{" in result
        assert "}" in result


# ---------------------------------------------------------------------------
# Tests for enhanced parsers
# ---------------------------------------------------------------------------


class TestEnhancedCsvParser:
    """Test enhanced CSV parser with delimiter detection and schema.

    Requires pandas — tests skip if pandas is not installed (host dev env).
    These tests run fully inside Docker where pandas is available.
    """

    @pytest.fixture(autouse=True)
    def _check_pandas(self):
        """Skip CSV tests if pandas is not available."""
        try:
            import pandas
            pandas.read_csv  # Verify it's the real module, not a stub
        except (ImportError, AttributeError):
            pytest.skip("pandas not available on host")

    def test_parse_csv_with_schema(self, tmp_path):
        """CSV parser returns schema summary and column types."""
        csv_content = "name,age,salary\nAlice,30,50000.50\nBob,25,45000.00\n"
        csv_path = tmp_path / "test.csv"
        csv_path.write_text(csv_content)

        from utils.parsers import parse_csv

        result = parse_csv(str(csv_path))

        assert result["file_type"] == "csv"
        assert result["row_count"] == 2
        columns = json.loads(result["columns"])
        assert "name" in columns
        assert "age" in columns
        assert "salary" in columns
        assert "schema" in result
        schema = json.loads(result["schema"])
        assert "name" in schema

    def test_parse_tsv(self, tmp_path):
        """TSV file parsed with tab delimiter."""
        tsv_content = "col1\tcol2\tcol3\nval1\tval2\tval3\n"
        tsv_path = tmp_path / "test.tsv"
        tsv_path.write_text(tsv_content)

        from utils.parsers import parse_csv

        result = parse_csv(str(tsv_path))

        assert result["file_type"] == "tsv"
        columns = json.loads(result["columns"])
        assert "col1" in columns

    def test_parse_semicolon_csv(self, tmp_path):
        """CSV with semicolon delimiter auto-detected."""
        csv_content = "name;value;count\nfoo;bar;10\nbaz;qux;20\n"
        csv_path = tmp_path / "semicolon.csv"
        csv_path.write_text(csv_content)

        from utils.parsers import parse_csv

        result = parse_csv(str(csv_path))

        assert result["row_count"] == 2
        assert "Schema:" in result["text"]
        assert "Sample" in result["text"]

    def test_csv_truncation_warning(self, tmp_path):
        """Large CSV sets truncated flag."""
        lines = ["id,value"]
        for i in range(6000):
            lines.append(f"{i},{i*10}")
        csv_path = tmp_path / "large.csv"
        csv_path.write_text("\n".join(lines))

        from utils.parsers import parse_csv

        result = parse_csv(str(csv_path))

        assert result["row_count"] == 6000
        assert result.get("truncated") is True


# ---------------------------------------------------------------------------
# Tests for parser registry
# ---------------------------------------------------------------------------


class TestParserRegistry:
    """Test that all new extensions are registered."""

    def test_new_extensions_in_registry(self):
        """All Phase 8B parsers are registered."""
        from utils.parsers import PARSER_REGISTRY

        new_exts = [".eml", ".mbox", ".epub", ".rtf", ".tsv"]
        for ext in new_exts:
            assert ext in PARSER_REGISTRY, f"{ext} not registered in PARSER_REGISTRY"

    def test_new_extensions_in_config(self):
        """All Phase 8B extensions are in SUPPORTED_EXTENSIONS."""
        import config

        new_exts = {".eml", ".mbox", ".epub", ".rtf", ".tsv"}
        for ext in new_exts:
            assert ext in config.SUPPORTED_EXTENSIONS, f"{ext} not in SUPPORTED_EXTENSIONS"

    def test_parse_file_dispatches_eml(self, tmp_path):
        """parse_file correctly dispatches .eml to parse_eml."""
        eml = (
            "From: test@test.com\r\n"
            "Subject: Dispatch Test\r\n"
            "Content-Type: text/plain\r\n"
            "\r\n"
            "Body text"
        )
        eml_path = tmp_path / "dispatch.eml"
        eml_path.write_bytes(eml.encode())

        from utils.parsers import parse_file

        result = parse_file(str(eml_path))
        assert result["file_type"] == "eml"
        assert "Dispatch Test" in result["text"]


# ---------------------------------------------------------------------------
# Tests for semantic dedup
# ---------------------------------------------------------------------------


class TestSemanticDedup:
    """Test semantic deduplication utility."""

    def test_no_dup_when_collection_empty(self):
        """No duplicate detected when collection is empty."""
        from utils.dedup import check_semantic_duplicate

        mock_collection = MagicMock()
        mock_collection.count.return_value = 0

        mock_chroma = MagicMock()
        mock_chroma.get_or_create_collection.return_value = mock_collection

        result = check_semantic_duplicate(
            text="Some document text",
            domain="coding",
            chroma_client=mock_chroma,
        )
        assert result is None

    def test_no_dup_when_distance_high(self):
        """No duplicate when similarity is below threshold."""
        from utils.dedup import check_semantic_duplicate

        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.return_value = {
            "ids": [["chunk_1"]],
            "distances": [[10.0]],  # High distance = low similarity
            "metadatas": [[{"artifact_id": "abc", "filename": "old.py"}]],
        }

        mock_chroma = MagicMock()
        mock_chroma.get_or_create_collection.return_value = mock_collection

        result = check_semantic_duplicate(
            text="Some document text",
            domain="coding",
            chroma_client=mock_chroma,
        )
        assert result is None

    def test_dup_detected_when_distance_low(self):
        """Near-duplicate detected when distance is very low."""
        from utils.dedup import check_semantic_duplicate

        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.return_value = {
            "ids": [["chunk_1"]],
            "distances": [[0.01]],  # Very low distance = very high similarity
            "metadatas": [[{"artifact_id": "abc-123", "filename": "original.py"}]],
        }

        mock_chroma = MagicMock()
        mock_chroma.get_or_create_collection.return_value = mock_collection

        result = check_semantic_duplicate(
            text="Some document text",
            domain="coding",
            chroma_client=mock_chroma,
        )
        assert result is not None
        assert result["artifact_id"] == "abc-123"
        assert result["filename"] == "original.py"
        assert result["similarity"] > 0.9

    def test_skip_self_match(self):
        """Exclude self-match when exclude_artifact_id is provided."""
        from utils.dedup import check_semantic_duplicate

        mock_collection = MagicMock()
        mock_collection.count.return_value = 5
        mock_collection.query.return_value = {
            "ids": [["chunk_1"]],
            "distances": [[0.01]],
            "metadatas": [[{"artifact_id": "self-id", "filename": "same.py"}]],
        }

        mock_chroma = MagicMock()
        mock_chroma.get_or_create_collection.return_value = mock_collection

        result = check_semantic_duplicate(
            text="Some document text",
            domain="coding",
            chroma_client=mock_chroma,
            exclude_artifact_id="self-id",
        )
        assert result is None

    def test_empty_text_returns_none(self):
        """Empty text returns None immediately."""
        from utils.dedup import check_semantic_duplicate

        result = check_semantic_duplicate(
            text="",
            domain="coding",
            chroma_client=MagicMock(),
        )
        assert result is None

    def test_batch_check(self):
        """Batch semantic duplicate check processes multiple docs."""
        from utils.dedup import check_semantic_duplicate_batch

        mock_collection = MagicMock()
        mock_collection.count.return_value = 0

        mock_chroma = MagicMock()
        mock_chroma.get_or_create_collection.return_value = mock_collection

        results = check_semantic_duplicate_batch(
            texts=["doc1", "doc2", "doc3"],
            domains=["coding", "coding", "finance"],
            chroma_client=mock_chroma,
        )
        assert len(results) == 3
        assert all(r is None for r in results)


# ---------------------------------------------------------------------------
# Tests for OCR plugin structure
# ---------------------------------------------------------------------------


class TestOCRPluginManifest:
    """Test OCR plugin manifest and structure."""

    def test_manifest_exists(self):
        """OCR plugin has a valid manifest.json."""
        manifest_path = Path(__file__).parent.parent / "plugins" / "ocr" / "manifest.json"
        assert manifest_path.exists(), "OCR plugin manifest.json not found"

        manifest = json.loads(manifest_path.read_text())
        assert manifest["name"] == "ocr"
        assert manifest["type"] == "parser"
        assert manifest["tier"] == "pro"
        assert "version" in manifest

    def test_plugin_module_exists(self):
        """OCR plugin has a plugin.py with register function."""
        plugin_path = Path(__file__).parent.parent / "plugins" / "ocr" / "plugin.py"
        assert plugin_path.exists(), "OCR plugin plugin.py not found"

        content = plugin_path.read_text()
        assert "def register():" in content
        assert "parse_pdf_with_ocr" in content

    def test_plugin_not_loaded_in_community_tier(self):
        """OCR plugin skipped in community tier (requires pro + docling)."""
        from plugins import _load_single_plugin

        plugin_dir = Path(__file__).parent.parent / "plugins" / "ocr"

        with patch("config.FEATURE_TIER", "community"):
            result = _load_single_plugin(plugin_dir)
            # Should be None because tier is community, not pro
            assert result is None


# ---------------------------------------------------------------------------
# Tests for feature flag integration
# ---------------------------------------------------------------------------


class TestFeatureFlagIntegration:
    """Test that Pro features are properly gated."""

    def test_semantic_dedup_disabled_in_community(self):
        """Semantic dedup feature flag is off in community tier."""
        import config

        # In community tier, semantic_dedup should be disabled
        if config.FEATURE_TIER == "community":
            assert config.FEATURE_FLAGS["semantic_dedup"] is False

    def test_ocr_disabled_in_community(self):
        """OCR feature flag is off in community tier."""
        import config

        if config.FEATURE_TIER == "community":
            assert config.FEATURE_FLAGS["ocr_parsing"] is False
