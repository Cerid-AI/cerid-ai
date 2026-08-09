# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Tests for ``core.knowledge.pii_gate``.

DI-driven analyzer — tests construct fake ``RecognizerResult``-shaped
objects and pass them through. No Presidio install required.
"""
from __future__ import annotations

from dataclasses import dataclass

import pytest

from core.knowledge.packs import PackError
from core.knowledge.pii_gate import (
    DEFAULT_DENYLIST,
    DEFAULT_THRESHOLD,
    PiiFinding,
    PiiScanReport,
    build_default_analyzer,
    scan_directory,
    scan_text,
)


@dataclass
class _FakeResult:
    """Mirrors the shape of ``presidio_analyzer.RecognizerResult``."""

    entity_type: str
    score: float
    start: int
    end: int


class _FakeAnalyzer:
    """In-memory analyzer that returns predefined results per text input."""

    def __init__(self, lookup):
        self._lookup = lookup
        self.calls: list[str] = []

    def analyze(self, text, language, entities):
        self.calls.append(text)
        for needle, result_list in self._lookup.items():
            if needle in text:
                return list(result_list)
        return []


# ── scan_text ──────────────────────────────────────────────────────────

def test_scan_text_emits_finding_for_high_score_email():
    text = "Contact me at alice@example.com to reset."
    start = text.index("alice@example.com")
    end = start + len("alice@example.com")
    analyzer = _FakeAnalyzer({
        "alice@example.com": [
            _FakeResult("EMAIL_ADDRESS", 0.95, start, end),
        ],
    })
    findings = scan_text(text, file_path="x.md", analyzer=analyzer)
    assert len(findings) == 1
    assert findings[0].entity_type == "EMAIL_ADDRESS"
    assert findings[0].score == 0.95
    # Snippet is redacted with bullet — never re-leaks the PII.
    assert "alice@example.com" not in findings[0].snippet
    assert "•••" in findings[0].snippet


def test_scan_text_drops_below_threshold():
    text = "alice@example.com"
    analyzer = _FakeAnalyzer({
        "alice@example.com": [
            _FakeResult("EMAIL_ADDRESS", 0.5, 0, 17),
        ],
    })
    assert scan_text(text, file_path="x.md", analyzer=analyzer) == []


def test_scan_text_drops_denylisted_entity_types():
    """PERSON, URL, LOCATION, etc. must never make it past the gate."""
    text = "Marcus Aurelius wrote in 161 AD."
    analyzer = _FakeAnalyzer({
        "Marcus": [
            _FakeResult("PERSON", 0.99, 0, 15),
            _FakeResult("DATE_TIME", 0.99, 25, 31),
            _FakeResult("LOCATION", 0.99, 0, 6),
        ],
    })
    assert scan_text(text, file_path="x.md", analyzer=analyzer) == []


def test_scan_text_custom_denylist_overrides_default():
    """A curator can opt back into a default-denied entity if needed."""
    text = "PERSON marker."
    analyzer = _FakeAnalyzer({
        "PERSON": [_FakeResult("PERSON", 0.99, 0, 6)],
    })
    findings = scan_text(
        text, file_path="x.md", analyzer=analyzer,
        denylist=frozenset(),
    )
    assert len(findings) == 1
    assert findings[0].entity_type == "PERSON"


def test_scan_text_line_number_calculated_correctly():
    text = "header\n\nLine three line: alice@example.com\nfooter"
    start = text.index("alice@")
    analyzer = _FakeAnalyzer({
        "alice@example.com": [
            _FakeResult("EMAIL_ADDRESS", 0.95, start, start + 17),
        ],
    })
    findings = scan_text(text, file_path="x.md", analyzer=analyzer)
    assert findings[0].line_number == 3


def test_scan_text_results_sorted_by_line_then_type():
    text = "a a a\nb b b\nc c c"
    analyzer = _FakeAnalyzer({
        "a": [
            _FakeResult("EMAIL_ADDRESS", 0.95, 12, 13),  # line 3
            _FakeResult("US_SSN", 0.95, 0, 1),           # line 1
            _FakeResult("PHONE_NUMBER", 0.95, 6, 7),     # line 2
        ],
    })
    findings = scan_text(text, file_path="x.md", analyzer=analyzer)
    assert [f.line_number for f in findings] == [1, 2, 3]


# ── scan_directory ──────────────────────────────────────────────────────

def test_scan_directory_walks_only_matching_suffixes(tmp_path):
    (tmp_path / "doc.md").write_text("clean content here")
    (tmp_path / "data.json").write_text("alice@example.com")  # not scanned
    (tmp_path / "deep").mkdir()
    (tmp_path / "deep" / "nested.md").write_text("alice@example.com")
    analyzer = _FakeAnalyzer({
        "alice@example.com": [_FakeResult("EMAIL_ADDRESS", 0.95, 0, 17)],
    })
    report = scan_directory(tmp_path, analyzer=analyzer)
    # Two .md files scanned; .json ignored; only the nested one had a finding.
    assert report.files_scanned == 2
    assert len(report.findings) == 1
    assert report.findings[0].file_path == "deep/nested.md"


def test_scan_directory_skips_oversized_files(tmp_path):
    big = tmp_path / "big.md"
    big.write_text("alice@example.com\n" + "x" * 5_000_000)
    small = tmp_path / "small.md"
    small.write_text("clean")
    analyzer = _FakeAnalyzer({
        "alice@example.com": [_FakeResult("EMAIL_ADDRESS", 0.95, 0, 17)],
    })
    report = scan_directory(
        tmp_path, analyzer=analyzer, max_file_bytes=1024,
    )
    assert "big.md" in report.skipped_files
    assert report.files_scanned == 1
    assert report.findings == ()


def test_scan_directory_returns_clean_report_when_no_findings(tmp_path):
    (tmp_path / "a.md").write_text("Lorem ipsum dolor sit amet.")
    (tmp_path / "b.md").write_text("More clean content.")
    analyzer = _FakeAnalyzer({})
    report = scan_directory(tmp_path, analyzer=analyzer)
    assert report.is_clean
    assert report.files_scanned == 2
    assert "clean" in report.summary_text()


def test_scan_directory_summary_groups_by_entity_type(tmp_path):
    (tmp_path / "a.md").write_text("alice@example.com")
    (tmp_path / "b.md").write_text("alice@example.com")
    (tmp_path / "c.md").write_text("123-45-6789")
    analyzer = _FakeAnalyzer({
        "alice@example.com": [_FakeResult("EMAIL_ADDRESS", 0.95, 0, 17)],
        "123-45-6789": [_FakeResult("US_SSN", 0.95, 0, 11)],
    })
    report = scan_directory(tmp_path, analyzer=analyzer)
    summary = report.summary_text()
    assert "EMAIL_ADDRESS=2" in summary
    assert "US_SSN=1" in summary


# ── PiiFinding / PiiScanReport ─────────────────────────────────────────

def test_finding_to_dict_round_trip():
    f = PiiFinding(
        file_path="x.md", entity_type="EMAIL_ADDRESS",
        score=0.95, line_number=3, snippet="...•••...",
    )
    d = f.to_dict()
    assert d["entity_type"] == "EMAIL_ADDRESS"
    assert d["score"] == 0.95
    assert d["line_number"] == 3


def test_report_by_file_groups_correctly():
    a1 = PiiFinding("a.md", "EMAIL_ADDRESS", 0.95, 1, "x")
    a2 = PiiFinding("a.md", "US_SSN", 0.99, 5, "y")
    b1 = PiiFinding("b.md", "EMAIL_ADDRESS", 0.95, 2, "z")
    report = PiiScanReport(
        files_scanned=2, findings=(a1, a2, b1),
    )
    by_file = report.by_file()
    assert by_file["a.md"] == [a1, a2]
    assert by_file["b.md"] == [b1]


# ── Default analyzer factory ───────────────────────────────────────────

def test_build_default_analyzer_raises_when_presidio_missing(monkeypatch):
    """If Presidio is not installed, the factory must surface a hint."""
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *args, **kwargs):
        if name == "presidio_analyzer":
            raise ImportError("simulated absence")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with pytest.raises(PackError, match="presidio-analyzer.*not installed"):
        build_default_analyzer()


# ── Defaults ───────────────────────────────────────────────────────────

def test_default_denylist_contains_noisy_recognizers():
    assert "PERSON" in DEFAULT_DENYLIST
    assert "URL" in DEFAULT_DENYLIST
    assert "DATE_TIME" in DEFAULT_DENYLIST
    assert "LOCATION" in DEFAULT_DENYLIST
    # And EMAIL_ADDRESS / US_SSN / etc. are NOT in the denylist.
    assert "EMAIL_ADDRESS" not in DEFAULT_DENYLIST
    assert "US_SSN" not in DEFAULT_DENYLIST


def test_default_threshold_is_high_enough_to_avoid_known_fp_band():
    """0.85 threshold matches the research-cited Presidio false-positive cutoff."""
    assert DEFAULT_THRESHOLD >= 0.85
