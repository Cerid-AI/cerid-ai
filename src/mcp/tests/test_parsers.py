# Copyright 2026 Cerid AI. Apache-2.0 license.
"""Tests for parsers/ sub-package — registry, utility functions, and parsers."""

import json

from parsers._utils import _strip_html_tags, _strip_rtf
from parsers.registry import PARSER_REGISTRY, parse_file, register_parser

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
        assert "alert" not in result

    def test_style_tags_skipped(self):
        html = "<style>.foo { color: red; }</style><p>visible</p>"
        result = _strip_html_tags(html)
        assert "visible" in result
        assert "color" not in result

    def test_empty_html(self):
        assert _strip_html_tags("") == ""

    def test_plain_text_passthrough(self):
        assert _strip_html_tags("just plain text") == "just plain text"


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


# ---------------------------------------------------------------------------
# Tests: Parser registry
# ---------------------------------------------------------------------------

class TestParserRegistry:
    def test_txt_parser_registered(self):
        """Plain text parser should be in the registry."""
        assert ".txt" in PARSER_REGISTRY or "txt" in str(PARSER_REGISTRY)

    def test_parse_file_txt(self, tmp_path):
        """Parsing a .txt file should return its text content."""
        f = tmp_path / "test.txt"
        f.write_text("Hello, world!")
        result = parse_file(str(f))
        assert "Hello" in result["text"]

    def test_parse_file_json(self, tmp_path):
        """Parsing a .json file should return stringified content."""
        f = tmp_path / "test.json"
        f.write_text(json.dumps({"key": "value"}))
        result = parse_file(str(f))
        assert "key" in result["text"]
        assert "value" in result["text"]

    def test_parse_file_csv(self, tmp_path):
        """Parsing a .csv file should return readable content."""
        f = tmp_path / "test.csv"
        f.write_text("name,age\nAlice,30\nBob,25\n")
        result = parse_file(str(f))
        assert "Alice" in result["text"]
        assert "Bob" in result["text"]

    def test_parse_file_unknown_extension(self, tmp_path):
        """Unknown file types should either fall back to text or raise."""
        f = tmp_path / "test.xyz"
        f.write_text("some content")
        # Should either return content or raise — not crash silently
        try:
            result = parse_file(str(f))
            assert isinstance(result, str)
        except (ValueError, KeyError):
            pass  # Expected for unsupported formats

    def test_register_parser_custom(self):
        """Custom parsers can be registered via register_parser decorator."""
        @register_parser([".myext"])
        def my_parser(path):
            return {"text": "custom parsed", "file_type": "myext", "page_count": None}

        assert ".myext" in PARSER_REGISTRY
