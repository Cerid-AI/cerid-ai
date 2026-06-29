# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``core.knowledge.adapter_medlineplus`` — MedlineplusXmlAdapter."""
from __future__ import annotations

import gzip

import pytest

from core.knowledge.adapter_medlineplus import (
    MedlineplusXmlAdapter,
    MedlineplusXmlConfig,
)
from core.knowledge.adapters import get_adapter, list_registered_adapters
from core.knowledge.packs import BuildSpec, PackError, PackManifest

_XML = b"""<?xml version="1.0"?>
<health-topics total="4">
  <health-topic id="1" title="Diabetes" url="https://medlineplus.gov/diabetes.html" language="English">
    <also-called>High blood sugar</also-called>
    <full-summary>&lt;p&gt;Diabetes is a disease that occurs when your blood glucose is too high.&lt;/p&gt;&lt;p&gt;Over time it can cause serious health problems.&lt;/p&gt;</full-summary>
    <group id="5">Diabetes Mellitus</group>
  </health-topic>
  <health-topic id="2" title="Diabetes en espanol" url="https://x" language="Spanish">
    <full-summary>&lt;p&gt;Spanish content filtered by language.&lt;/p&gt;</full-summary>
  </health-topic>
  <health-topic id="3" title="A.D.A.M. Encyclopedia Topic" url="https://x" language="English">
    <full-summary>&lt;p&gt;Copyrighted content excluded by title prefix.&lt;/p&gt;</full-summary>
  </health-topic>
  <health-topic id="4" title="St Johns Wort" url="https://x" language="English">
    <full-summary>&lt;p&gt;A supplement topic excluded by category.&lt;/p&gt;</full-summary>
    <group id="9">Herbs and Supplements</group>
  </health-topic>
</health-topics>"""


def _manifest(cfg: dict) -> PackManifest:
    return PackManifest.from_dict({
        "id": "mplus-fixture", "name": "fx", "version": "1.0.0",
        "description": "fx", "domain": "personal", "license": "CC0-1.0",
        "provenance": {"source": "https://medlineplus.gov/xml.html"},
        "build": {"adapter": "medlineplus_xml", "config": cfg},
    })


def _dl(payload: bytes):
    def _f(url: str, max_bytes: int) -> bytes:
        return payload
    return _f


def test_medlineplus_renders_english_and_filters(tmp_path):
    adapter = MedlineplusXmlAdapter(downloader=_dl(_XML))
    manifest = _manifest({
        "source_url": "https://medlineplus.gov/xml/mplus_topics_compressed.xml",
        "exclude_element_prefixes": ["A.D.A.M."],
        "exclude_categories": ["herbs and supplements"],
        "min_text_chars": 20,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert [p.name for p in result.files] == ["diabetes.md"]
    body = (result.content_root / "diabetes.md").read_text()
    assert body.startswith("# Diabetes")
    assert "blood glucose is too high" in body
    assert "Also called: High blood sugar" in body


def test_medlineplus_transparently_gunzips(tmp_path):
    adapter = MedlineplusXmlAdapter(downloader=_dl(gzip.compress(_XML)))
    manifest = _manifest({
        "source_url": "https://medlineplus.gov/x.xml",
        "exclude_element_prefixes": ["A.D.A.M."],
        "exclude_categories": ["herbs and supplements"],
        "min_text_chars": 20,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert [p.name for p in result.files] == ["diabetes.md"]


def test_medlineplus_requires_https_source():
    with pytest.raises(PackError, match="https"):
        MedlineplusXmlConfig.from_build(BuildSpec(
            adapter="medlineplus_xml", config={"source_url": "ftp://x/y.xml"},
        ))


def test_medlineplus_raises_when_no_topics(tmp_path):
    adapter = MedlineplusXmlAdapter(downloader=_dl(_XML))
    manifest = _manifest({
        "source_url": "https://medlineplus.gov/x.xml",
        "language": "Klingon",  # nothing matches
        "min_text_chars": 20,
    })
    with pytest.raises(PackError, match="no topic survived"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_medlineplus_registered():
    assert "medlineplus_xml" in list_registered_adapters()
    assert isinstance(get_adapter("medlineplus_xml"), MedlineplusXmlAdapter)
