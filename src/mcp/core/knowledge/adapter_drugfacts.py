# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Curated drug + supplement key-facts adapter (``drug_facts``).

Closes the drugs/supplements gap MedQuAD excludes, **without** ingesting
the multi-GB full DailyMed label corpus (which is boilerplate-heavy and
tanks RAG precision). Two public sources:

- **Drugs** — openFDA ``drug/label.json`` (the structured, queryable form
  of FDA Structured Product Labeling). For each generic name in a curated
  list we keep only the high-value sections (indications, dosage,
  contraindications, warnings, interactions, adverse reactions).
- **Supplements** — NIH Office of Dietary Supplements consumer fact
  sheets (HTML → prose via the stdlib ``extract_html_content``).

The drug list is bundled (``_DEFAULT_DRUGS``, the most-prescribed US
generics) and recipe-overridable via ``drugs``; the pack's claim is
"common-drug key facts" — complete for the bundled/configured list, not
"all drugs". FDA labeling + NIH ODS fact sheets are US-government public
information.
"""
from __future__ import annotations

import json
import logging
import urllib.parse
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, ClassVar

from core.knowledge.adapter_hf import _slugify
from core.knowledge.adapter_html_scrape import extract_html_content
from core.knowledge.adapters import (
    FetchResult,
    PackSourceAdapter,
    register_adapter,
)
from core.knowledge.packs import BuildSpec, PackError, PackManifest

logger = logging.getLogger("ai-companion.knowledge_packs.adapters.drugfacts")

# Curated default: the most-prescribed US generics (covers the bulk of
# prescription volume, which is heavily concentrated). Recipe-overridable
# via the ``drugs`` config key; the pack claim is sized to this list.
_DEFAULT_DRUGS: tuple[str, ...] = (
    "atorvastatin", "levothyroxine", "metformin", "lisinopril", "amlodipine",
    "metoprolol", "albuterol", "omeprazole", "losartan", "gabapentin",
    "hydrochlorothiazide", "sertraline", "simvastatin", "montelukast",
    "escitalopram", "rosuvastatin", "bupropion", "furosemide", "pantoprazole",
    "trazodone", "fluoxetine", "tamsulosin", "atenolol", "prednisone",
    "citalopram", "clopidogrel", "duloxetine", "amoxicillin", "meloxicam",
    "carvedilol", "clonazepam", "glipizide", "naproxen", "pravastatin",
    "venlafaxine", "warfarin", "tramadol", "apixaban", "allopurinol",
    "spironolactone", "lamotrigine", "cyclobenzaprine", "levetiracetam",
    "propranolol", "insulin glargine",
)

# openFDA label field → human heading. Order defines the section order.
_SECTION_HEADINGS: dict[str, str] = {
    "indications_and_usage": "Indications and Usage",
    "dosage_and_administration": "Dosage and Administration",
    "contraindications": "Contraindications",
    "warnings_and_cautions": "Warnings and Precautions",
    "warnings": "Warnings",
    "drug_interactions": "Drug Interactions",
    "adverse_reactions": "Adverse Reactions",
}


@dataclass(frozen=True)
class DrugFactsConfig:
    """Validated config for :class:`DrugFactsAdapter`."""

    drugs: tuple[str, ...] = _DEFAULT_DRUGS
    supplements: tuple[str, ...] = ()
    include_sections: tuple[str, ...] = tuple(_SECTION_HEADINGS)
    openfda_endpoint: str = "https://api.fda.gov/drug/label.json"
    ods_factsheet_base: str = "https://ods.od.nih.gov/factsheets/"
    min_text_chars: int = 200

    @classmethod
    def from_build(cls, build: BuildSpec) -> "DrugFactsConfig":
        cfg = build.config
        drugs = tuple(str(d).strip() for d in cfg.get("drugs", ()) if str(d).strip())
        endpoint = str(cfg.get("openfda_endpoint", "https://api.fda.gov/drug/label.json"))
        if not endpoint.startswith("https://"):
            raise PackError(f"drug_facts config: openfda_endpoint must be https://, got {endpoint!r}")
        ods_base = str(cfg.get("ods_factsheet_base", "https://ods.od.nih.gov/factsheets/"))
        if not ods_base.startswith("https://"):
            raise PackError(f"drug_facts config: ods_factsheet_base must be https://, got {ods_base!r}")
        sections = tuple(str(s) for s in cfg.get("include_sections", ())) or tuple(_SECTION_HEADINGS)
        return cls(
            drugs=drugs or _DEFAULT_DRUGS,
            supplements=tuple(str(s).strip() for s in cfg.get("supplements", ()) if str(s).strip()),
            include_sections=sections,
            openfda_endpoint=endpoint,
            ods_factsheet_base=ods_base,
            min_text_chars=int(cfg.get("min_text_chars", 200)),
        )


JsonFetch = Callable[[str], dict[str, Any]]
TextFetch = Callable[[str], str]


def _render_drug(generic: str, result: dict[str, Any], sections: tuple[str, ...]) -> str | None:
    """Render one openFDA label result → markdown, keeping only key sections."""
    openfda = result.get("openfda") or {}
    brands = openfda.get("brand_name") or []
    brand = f" ({brands[0]})" if brands else ""
    title = generic.title() + brand
    parts: list[str] = [f"# {title}"]
    body_found = False
    for key in sections:
        heading = _SECTION_HEADINGS.get(key, key.replace("_", " ").title())
        value = result.get(key)
        if not value:
            continue
        text = " ".join(value) if isinstance(value, list) else str(value)
        text = text.strip()
        if not text:
            continue
        parts.append(f"\n## {heading}\n\n{text}")
        body_found = True
    if not body_found:
        return None
    return "\n".join(parts) + "\n"


class DrugFactsAdapter(PackSourceAdapter):
    """Build a curated drug/supplement key-facts corpus (openFDA + NIH ODS).

    DI-injectable ``json_fetch`` (openFDA) and ``text_fetch`` (ODS HTML)
    keep the adapter unit-testable with no network.
    """

    name: ClassVar[str] = "drug_facts"

    def __init__(
        self,
        *,
        json_fetch: JsonFetch | None = None,
        text_fetch: TextFetch | None = None,
    ) -> None:
        self._json_fetch = json_fetch or _httpx_json_fetch
        self._text_fetch = text_fetch or _httpx_text_fetch

    def fetch(self, manifest: PackManifest, *, staging_root: Path) -> FetchResult:
        if manifest.build is None:
            raise PackError(f"Pack {manifest.id!r} has no build spec")
        config = DrugFactsConfig.from_build(manifest.build)
        content_root = staging_root / "content"
        content_root.mkdir(parents=True, exist_ok=True)
        seen: dict[str, int] = {}
        kept: list[Path] = []

        for generic in config.drugs:
            query = urllib.parse.quote(f'openfda.generic_name:"{generic}"')
            url = f"{config.openfda_endpoint}?search={query}&limit=1"
            try:
                payload = self._json_fetch(url)
            except Exception as exc:  # noqa: BLE001 — observability boundary
                from core.utils.swallowed import log_swallowed_error
                log_swallowed_error("core.knowledge.adapter_drugfacts.openfda", exc)
                logger.warning("drug_facts: skip drug %s (%s)", generic, exc)
                continue
            results = payload.get("results") or []
            if not results:
                continue
            body = _render_drug(generic, results[0], config.include_sections)
            if body is None or len(body) < config.min_text_chars:
                continue
            self._write(content_root, f"drug-{generic}", body, seen, kept, manifest)

        for supp in config.supplements:
            slug = _slugify(supp)
            url = f"{config.ods_factsheet_base}{slug}-Consumer/"
            try:
                html = self._text_fetch(url)
            except Exception as exc:  # noqa: BLE001 — observability boundary
                from core.utils.swallowed import log_swallowed_error
                log_swallowed_error("core.knowledge.adapter_drugfacts.ods", exc)
                logger.warning("drug_facts: skip supplement %s (%s)", supp, exc)
                continue
            _, text = extract_html_content(html)
            if len(text) < config.min_text_chars:
                continue
            body = f"# {supp} (dietary supplement)\n\n{text}\n\n---\nsource: {url}\n"
            self._write(content_root, f"supplement-{supp}", body, seen, kept, manifest)

        if not kept:
            raise PackError(
                f"drug_facts {manifest.id}: no drug or supplement yielded content "
                f"(drugs={len(config.drugs)}, supplements={len(config.supplements)}).",
            )
        kept.sort()
        logger.info("drug_facts: %s — wrote %d files", manifest.id, len(kept))
        return FetchResult(content_root=content_root, files=tuple(kept))

    def _write(
        self, content_root: Path, name: str, body: str,
        seen: dict[str, int], kept: list[Path], manifest: PackManifest,
    ) -> None:
        slug = _slugify(name)
        counter = seen.get(slug, 0)
        base = slug if counter == 0 else f"{slug}-{counter}"
        seen[slug] = counter + 1
        out_rel = Path(f"{base}.md")
        target = (content_root / out_rel).resolve()
        try:
            target.relative_to(content_root.resolve())
        except ValueError as exc:
            raise PackError(
                f"drug_facts {manifest.id}: {name!r} escapes content_root",
            ) from exc
        target.write_text(body, encoding="utf-8")
        kept.append(out_rel)


def _httpx_json_fetch(url: str) -> dict[str, Any]:
    import httpx

    headers = {"User-Agent": "Cerid-AI-Knowledge-Pack-Builder/1.0 (+https://github.com/Cerid-AI/cerid-ai)"}
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True) as client:  # follow_redirects: fixed FDA openFDA endpoint (https-validated, build-time)
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        data = json.loads(resp.text)
    if not isinstance(data, dict):
        raise PackError("drug_facts: openFDA response was not a JSON object")
    return data


def _httpx_text_fetch(url: str) -> str:
    import httpx

    headers = {"User-Agent": "Cerid-AI-Knowledge-Pack-Builder/1.0 (+https://github.com/Cerid-AI/cerid-ai)"}
    with httpx.Client(timeout=httpx.Timeout(60.0, connect=10.0), follow_redirects=True) as client:  # follow_redirects: fixed NIH ODS factsheet endpoint (build-time)
        resp = client.get(url, headers=headers)
        resp.raise_for_status()
        return resp.text


register_adapter(DrugFactsAdapter())


__all__ = ["DrugFactsAdapter", "DrugFactsConfig"]
