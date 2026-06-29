# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``core.knowledge.adapter_drugfacts`` — DrugFactsAdapter."""
from __future__ import annotations

import pytest

from core.knowledge.adapter_drugfacts import DrugFactsAdapter, DrugFactsConfig
from core.knowledge.adapters import get_adapter, list_registered_adapters
from core.knowledge.packs import BuildSpec, PackError, PackManifest

_LABEL = {"results": [{
    "openfda": {"generic_name": ["atorvastatin"], "brand_name": ["Lipitor"]},
    "indications_and_usage": ["Atorvastatin is indicated to reduce LDL cholesterol."],
    "dosage_and_administration": ["The recommended starting dose is 10 to 20 mg once daily."],
    "warnings_and_cautions": ["Rare cases of rhabdomyolysis have been reported."],
}]}
_EMPTY: dict = {"results": []}

_ODS_HTML = (
    "<html><body><main><h1>Vitamin D</h1>"
    "<p>Vitamin D helps the body absorb calcium and is important for bone health.</p>"
    "</main></body></html>"
)


def _manifest(cfg: dict) -> PackManifest:
    return PackManifest.from_dict({
        "id": "drug-fixture", "name": "fx", "version": "1.0.0",
        "description": "fx", "domain": "personal", "license": "us-gov-pd",
        "provenance": {"source": "https://api.fda.gov/drug/label.json"},
        "build": {"adapter": "drug_facts", "config": cfg},
    })


def _json(url: str) -> dict:
    return _LABEL if "atorvastatin" in url else _EMPTY


def test_drug_facts_renders_drugs_and_supplements(tmp_path):
    adapter = DrugFactsAdapter(json_fetch=_json, text_fetch=lambda url: _ODS_HTML)
    manifest = _manifest({
        "drugs": ["atorvastatin", "nonexistent-drug"],
        "supplements": ["Vitamin D"],
        "include_sections": [
            "indications_and_usage", "dosage_and_administration", "warnings_and_cautions",
        ],
        "min_text_chars": 20,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    names = sorted(p.name for p in result.files)
    assert names == ["drug-atorvastatin.md", "supplement-vitamin-d.md"]
    drug = (result.content_root / "drug-atorvastatin.md").read_text()
    assert drug.startswith("# Atorvastatin (Lipitor)")
    assert "## Indications and Usage" in drug
    assert "LDL cholesterol" in drug
    assert "## Warnings and Precautions" in drug
    supp = (result.content_root / "supplement-vitamin-d.md").read_text()
    assert "Vitamin D helps the body absorb calcium" in supp


def test_drug_facts_skips_drug_with_no_label(tmp_path):
    adapter = DrugFactsAdapter(json_fetch=lambda url: _EMPTY, text_fetch=lambda url: "")
    manifest = _manifest({"drugs": ["nonexistent"], "min_text_chars": 20})
    with pytest.raises(PackError, match="no drug or supplement"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_drug_facts_defaults_to_bundled_drug_list():
    cfg = DrugFactsConfig.from_build(BuildSpec(adapter="drug_facts", config={}))
    assert "atorvastatin" in cfg.drugs
    assert len(cfg.drugs) > 20


def test_drug_facts_rejects_non_https_endpoint():
    with pytest.raises(PackError, match="openfda_endpoint must be https"):
        DrugFactsConfig.from_build(BuildSpec(
            adapter="drug_facts", config={"openfda_endpoint": "http://api.fda.gov/x"},
        ))


def test_drug_facts_registered():
    assert "drug_facts" in list_registered_adapters()
    assert isinstance(get_adapter("drug_facts"), DrugFactsAdapter)
