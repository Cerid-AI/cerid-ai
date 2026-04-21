# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Canonical Pydantic models for the hallucination / verification layer.

Consolidation goal (Sprint B of the 2026-04-19 program): one shape
for a verification claim, validated at every boundary. Before this
module, the same logical datum — "we verified a claim against a
source" — traveled through the codebase as a dict with three
overlapping schemas:

    a. Flat singular: ``{source_artifact_id, source_urls, ...}``
       — the production shape emitted by ``verify_claim``.
    b. Nested list:   ``{sources: [{artifact_id, url, ...}]}``
       — speculative future shape; partially handled by
       ``save_verification_report`` and ``m0001``.
    c. Legacy:        ``{source_filename, source_snippet}`` only
       — older pre-v0.84 reports.

Three shapes, four consumers, at least one silent-drop bug
(P1.4 — ``save_verification_report`` ignored flat singular).

This module declares ONE canonical shape (matches the production
(a)) and ONE adapter (``ClaimVerification.from_legacy_dict``) that
normalizes any of the three variants. Every new consumer reads
from the Pydantic model; every new producer emits the model. Wire
format stays backward-compatible via ``model_dump(mode='json')``.
"""
from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ClaimStatus(str, Enum):
    """Verification verdicts the pipeline emits.

    * ``verified`` — claim supported by a source with entailment >=
      threshold.
    * ``unverified`` — claim contradicted by a source.
    * ``uncertain`` — neither entailed nor contradicted (neutral NLI,
      insufficient retrieval, or model non-answer).
    * ``skipped`` — claim was filtered (too short, no factual
      pattern, below response length gate).
    * ``error`` — the verifier itself failed (e.g. 402 credit
      exhaustion, timeout).
    """

    verified = "verified"
    unverified = "unverified"
    uncertain = "uncertain"
    skipped = "skipped"
    error = "error"


class ClaimType(str, Enum):
    """Extraction category the claim came from."""

    factual = "factual"
    evasion = "evasion"
    ignorance = "ignorance"
    citation = "citation"


class ClaimVerification(BaseModel):
    """One verified claim — the canonical shape.

    Wire-format-compatible: ``model_dump()`` returns exactly the dict
    shape the frontend's ``HallucinationClaim`` TypeScript type
    expects (see ``src/web/src/lib/types.ts``). Internal code reads
    model attributes; adapters at the boundaries handle legacy
    inputs. Use ``.sources()`` to iterate sources uniformly
    regardless of whether the claim was verified against a single KB
    artifact or multiple web URLs — this replaces the ``claim.get("source_artifact_id")``
    / ``claim.get("source_urls", [])`` split that caused the P1.4 bug.
    """

    model_config = ConfigDict(
        extra="allow",  # Keep optional/experimental fields surfacing
        str_strip_whitespace=True,
    )

    # Defaults to empty string rather than required field — some test
    # fixtures and edge-case callers supply provenance-only dicts
    # (no claim text). The writer still extracts artifact_ids and
    # source_urls correctly in that case; rejecting via validation
    # would silently drop them and strand :VerificationReport nodes
    # without provenance — exactly the P1.4 class of bug Sprint B
    # closed. The validation layer enforces shape on the fields that
    # matter (``status``, ``similarity``, ``confidence``).
    claim: str = ""
    claim_type: ClaimType = ClaimType.factual
    status: ClaimStatus = ClaimStatus.uncertain
    similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason: str = ""

    # Flat source attribution — the production wire shape.
    source_artifact_id: str = ""
    source_filename: str = ""
    source_domain: str = ""
    source_snippet: str = ""
    source_urls: list[str] = Field(default_factory=list)

    # Verifier provenance
    verification_method: str = "kb"
    verification_model: str | None = None
    verification_answer: str = ""

    # NLI-specific (optional; set only by kb_nli path)
    nli_entailment: float | None = None
    nli_contradiction: float | None = None

    # Flags
    memory_source: bool = False
    circular_source: bool = False

    @classmethod
    def from_legacy_dict(cls, raw: dict[str, Any] | "ClaimVerification") -> "ClaimVerification":
        """Normalize any of the three historical claim shapes.

        Handles:
          1. Pre-v0.84 dicts with only ``source_filename``/``source_snippet``
          2. v0.84+ flat dicts with ``source_artifact_id`` + ``source_urls``
          3. Speculative nested dicts with ``sources: [{artifact_id, url, ...}]``

        For case 3, pulls the first ``artifact_id`` / first ``url`` into
        the canonical flat fields. When the SDK or FE later needs
        genuine multi-source claims, change this to preserve the list
        — but by then all consumers read ``.sources()``, so the
        change is at exactly one place.
        """
        if isinstance(raw, cls):
            return raw
        if not isinstance(raw, dict):
            raise TypeError(
                f"ClaimVerification.from_legacy_dict expects dict or ClaimVerification; "
                f"got {type(raw).__name__}"
            )

        data = dict(raw)  # shallow copy — don't mutate caller's dict

        # Handle speculative nested "sources" shape. First entry wins.
        nested = data.pop("sources", None)
        if isinstance(nested, list) and nested:
            first = nested[0]
            if isinstance(first, dict):
                if not data.get("source_artifact_id") and first.get("artifact_id"):
                    data["source_artifact_id"] = first["artifact_id"]
                if not data.get("source_domain") and first.get("domain"):
                    data["source_domain"] = first["domain"]
                if not data.get("source_filename") and first.get("filename"):
                    data["source_filename"] = first["filename"]
                if not data.get("source_snippet") and first.get("snippet"):
                    data["source_snippet"] = first["snippet"]
            # Collect every URL from the nested list into the flat list.
            urls: list[str] = list(data.get("source_urls") or [])
            for s in nested:
                if isinstance(s, dict):
                    url = s.get("url") or s.get("source_url")
                    if url and url not in urls:
                        urls.append(url)
            if urls:
                data["source_urls"] = urls

        # ``source`` was an older alias for ``source_filename``.
        if "source" in data and not data.get("source_filename"):
            data["source_filename"] = data.pop("source")
        else:
            data.pop("source", None)

        # Coerce unknown verification_method values rather than 422-ing.
        if not data.get("verification_method"):
            data["verification_method"] = "kb"

        return cls.model_validate(data)

    def artifact_ids(self) -> list[str]:
        """Canonical iterator for Neo4j edge creation — any caller
        that needs to link ``:EXTRACTED_FROM``/``:VERIFIED`` edges
        should iterate this, never touch the raw field."""
        return [self.source_artifact_id] if self.source_artifact_id else []

    def has_provenance(self) -> bool:
        """True when at least one of the three writer-contract
        provenance channels is populated (edges, source_urls, or
        verification_methods via ``verification_method``).

        Matches the m0002 / preservation-harness predicate.
        """
        return bool(
            self.source_artifact_id
            or self.source_urls
            or (self.verification_method and self.verification_method != "none")
        )


class ClaimVerificationList(BaseModel):
    """Typed container for list-of-claims payloads. Using a
    dedicated model (rather than ``list[ClaimVerification]``) makes
    ``model_validate_json`` work directly on pipeline-serialized
    claims arrays."""

    claims: list[ClaimVerification]

    @classmethod
    def from_legacy(cls, raw: list[dict[str, Any]] | list[ClaimVerification] | None) -> "ClaimVerificationList":
        items = [ClaimVerification.from_legacy_dict(c) for c in (raw or [])]
        return cls(claims=items)
