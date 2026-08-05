# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Build-time PII gate for knowledge-pack content (Phase 8b).

Scans a directory of markdown/text files for high-confidence PII
detections via Microsoft Presidio. Returns a structured report so the
build CLIs (``scripts/build_knowledge_pack``, ``scripts/build_catalog``)
can refuse to seal a tarball when findings exist.

Why a gate, not redaction
=========================

The harness already enforces an upstream allow-list and a SPDX license
gate at install time. Those defend against a *deliberately*
mislabelled pack. This gate defends against an *accidental* leak: a
curator who built a pack from a directory that happens to contain a
stray email address or SSN. The right response to a leak is "don't
ship it" — not "ship it with the leak redacted in place" — so the
gate fails the build and lets the curator fix the source.

DI for tests
============

``presidio-analyzer`` is a heavy optional dep (pulls spacy + transformer
weights). Importing it at module load would force every harness install
to pay that cost. The default analyzer factory is lazy-imported inside
:func:`build_default_analyzer` and the public scan functions accept a
DI'd analyzer so tests run hermetically.

Recognizer policy
=================

Presidio's default recognizer set includes a few that are notoriously
noisy on encyclopedic text:

- ``PERSON`` — NER-based; flags every named-entity (Marcus Aurelius,
  Edmund Burke, Vienna, etc.). Useless for catalog packs that are
  literally about people.
- ``URL`` / ``DATE_TIME`` / ``LOCATION`` — not PII at all in this context.
- ``IN_*`` / ``AU_*`` / ``UK_*`` / ``SG_*`` — country-specific IDs
  which produce false positives on numeric tokens.

The default denylist (:data:`DEFAULT_DENYLIST`) drops all of those.
What remains is the high-confidence-PII core: ``EMAIL_ADDRESS``,
``PHONE_NUMBER``, ``US_SSN``, ``CREDIT_CARD``, ``IBAN_CODE``,
``IP_ADDRESS``, ``MEDICAL_LICENSE``, ``US_DRIVER_LICENSE``,
``US_PASSPORT``, ``US_BANK_NUMBER``.
"""
from __future__ import annotations

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from core.knowledge.packs import PackError

logger = logging.getLogger("ai-companion.knowledge_packs.pii_gate")


DEFAULT_DENYLIST: frozenset[str] = frozenset({
    "PERSON",
    "URL",
    "DATE_TIME",
    "LOCATION",
    "NRP",
    "ORGANIZATION",
    # Country-specific IDs that flag stray digit runs:
    "AU_ABN", "AU_ACN", "AU_TFN", "AU_MEDICARE",
    "IN_PAN", "IN_AADHAAR", "IN_VEHICLE_REGISTRATION",
    "UK_NHS", "UK_NINO",
    "SG_NRIC_FIN", "SG_UEN",
    "ES_NIE", "ES_NIF",
    "IT_FISCAL_CODE", "IT_VAT_CODE", "IT_PASSPORT", "IT_DRIVER_LICENSE",
    "IT_IDENTITY_CARD",
    "PL_PESEL",
    "FI_PERSONAL_IDENTITY_CODE",
})

# Presidio detection score threshold. Above this, the gate fails the
# build. Default is intentionally high — anything below 0.85 is
# pattern-only without surrounding context, and that's exactly where
# Presidio's known false-positive rate sits.
DEFAULT_THRESHOLD: float = 0.85
DEFAULT_LANGUAGE: str = "en"


@dataclass(frozen=True)
class PiiFinding:
    """A single PII detection in a scanned file."""

    file_path: str
    entity_type: str
    score: float
    line_number: int
    snippet: str  # ~80 chars around the hit, redacted with "•••"

    def to_dict(self) -> dict[str, Any]:
        return {
            "file_path": self.file_path,
            "entity_type": self.entity_type,
            "score": round(self.score, 3),
            "line_number": self.line_number,
            "snippet": self.snippet,
        }


@dataclass(frozen=True)
class PiiScanReport:
    """Aggregate of :func:`scan_directory` results."""

    files_scanned: int
    findings: tuple[PiiFinding, ...] = ()
    skipped_files: tuple[str, ...] = ()

    @property
    def is_clean(self) -> bool:
        return not self.findings

    def by_file(self) -> dict[str, list[PiiFinding]]:
        out: dict[str, list[PiiFinding]] = {}
        for f in self.findings:
            out.setdefault(f.file_path, []).append(f)
        return out

    def summary_text(self) -> str:
        if self.is_clean:
            return f"PII gate: clean ({self.files_scanned} files scanned)"
        by_type: dict[str, int] = {}
        for f in self.findings:
            by_type[f.entity_type] = by_type.get(f.entity_type, 0) + 1
        kinds = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
        return (
            f"PII gate: {len(self.findings)} finding(s) across "
            f"{len(self.by_file())} file(s) — {kinds}"
        )


# ── Analyzer protocol (DI for tests) ────────────────────────────────

class _AnalyzerProto(Protocol):
    """Subset of Presidio's ``AnalyzerEngine`` surface that we use."""

    def analyze(
        self, text: str, language: str, entities: Iterable[str] | None,
    ) -> list[Any]: ...


def build_default_analyzer(*, language: str = DEFAULT_LANGUAGE) -> _AnalyzerProto:
    """Lazy-import + construct Presidio's analyzer.

    Raises :class:`PackError` with a clear install hint if the heavy
    ``presidio-analyzer`` package isn't installed. The error mentions
    the spacy model dependency too — Presidio loads spacy ``en_core_web_lg``
    by default, which is a 500 MB download on first use.
    """
    try:
        from presidio_analyzer import AnalyzerEngine  # type: ignore[import-not-found]
    except ImportError as exc:
        raise PackError(
            "PII gate: the `presidio-analyzer` package is not installed. "
            "Install with `pip install presidio-analyzer` and download the "
            "spacy model `python -m spacy download en_core_web_lg` (heavy: "
            "~500 MB). Or run the build with --no-pii-scan to skip the gate.",
        ) from exc
    return AnalyzerEngine(default_score_threshold=0.0)


# ── Scan helpers ─────────────────────────────────────────────────────

def _snippet_for(text: str, start: int, end: int, *, context: int = 30) -> str:
    """Extract a redacted snippet around an offset for debugging.

    The actual matched span is replaced with ``•••`` so reports never
    re-leak the very PII the gate is meant to keep out of the tarball.
    """
    a = max(0, start - context)
    b = min(len(text), end + context)
    before = text[a:start].replace("\n", " ")
    after = text[end:b].replace("\n", " ")
    return f"{before}•••{after}"


def _line_number(text: str, offset: int) -> int:
    return text.count("\n", 0, offset) + 1


def scan_text(
    text: str,
    *,
    file_path: str,
    analyzer: _AnalyzerProto,
    threshold: float = DEFAULT_THRESHOLD,
    denylist: Iterable[str] = DEFAULT_DENYLIST,
    language: str = DEFAULT_LANGUAGE,
) -> list[PiiFinding]:
    """Run the analyzer on a single text blob and return high-confidence findings.

    Findings below ``threshold`` are dropped; entity types in
    ``denylist`` are dropped regardless of score. The remaining
    findings are returned sorted by ``(line_number, start_offset)`` so
    a CLI can present them in document order.
    """
    deny = frozenset(denylist)
    raw = analyzer.analyze(text, language=language, entities=None)
    findings: list[PiiFinding] = []
    for r in raw:
        entity_type = getattr(r, "entity_type", "")
        score = float(getattr(r, "score", 0.0))
        if entity_type in deny or score < threshold:
            continue
        start = int(getattr(r, "start", 0))
        end = int(getattr(r, "end", start + 1))
        findings.append(PiiFinding(
            file_path=file_path,
            entity_type=entity_type,
            score=score,
            line_number=_line_number(text, start),
            snippet=_snippet_for(text, start, end),
        ))
    findings.sort(key=lambda f: (f.line_number, f.entity_type))
    return findings


def scan_directory(
    content_root: Path,
    *,
    analyzer: _AnalyzerProto | None = None,
    threshold: float = DEFAULT_THRESHOLD,
    denylist: Iterable[str] = DEFAULT_DENYLIST,
    language: str = DEFAULT_LANGUAGE,
    suffix_allowlist: Iterable[str] = (".md", ".txt", ".rst"),
    max_file_bytes: int = 2 * 1024 * 1024,
) -> PiiScanReport:
    """Recursively scan a directory of pack content.

    Files larger than ``max_file_bytes`` are skipped (Presidio's
    transformer pass scales poorly past ~2 MB; oversized files are
    typically auto-generated indexes that don't carry PII anyway). The
    skipped paths are reported separately so a curator can eyeball
    them.
    """
    if analyzer is None:
        analyzer = build_default_analyzer(language=language)

    findings: list[PiiFinding] = []
    skipped: list[str] = []
    files_scanned = 0
    suffixes = {s.lower() for s in suffix_allowlist}
    for path in sorted(content_root.rglob("*")):
        if not path.is_file():
            continue
        if path.suffix.lower() not in suffixes:
            continue
        size = path.stat().st_size
        rel = path.relative_to(content_root).as_posix()
        if size > max_file_bytes:
            skipped.append(rel)
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        files_scanned += 1
        findings.extend(scan_text(
            text,
            file_path=rel,
            analyzer=analyzer,
            threshold=threshold,
            denylist=denylist,
            language=language,
        ))
    return PiiScanReport(
        files_scanned=files_scanned,
        findings=tuple(findings),
        skipped_files=tuple(skipped),
    )


__all__ = [
    "DEFAULT_DENYLIST",
    "DEFAULT_LANGUAGE",
    "DEFAULT_THRESHOLD",
    "PiiFinding",
    "PiiScanReport",
    "build_default_analyzer",
    "scan_directory",
    "scan_text",
]
