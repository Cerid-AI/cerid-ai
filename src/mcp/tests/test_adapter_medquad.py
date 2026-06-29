# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``core.knowledge.adapter_medquad`` — QaXmlAdapter.

A DI downloader passes an in-memory zip so tests parse MedQuAD-shaped XML
with no network and no live ``datasets``/github fetch.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from core.knowledge.adapter_medquad import (
    QaXmlAdapter,
    QaXmlConfig,
    _render_document,
)
from core.knowledge.adapters import (
    get_adapter,
    list_registered_adapters,
)
from core.knowledge.packs import BuildSpec, PackError, PackManifest

_DOC = b"""<Document id="1" source="CancerGov" url="https://www.cancer.gov/x">
  <Focus>Melanoma</Focus>
  <QAPairs>
    <QAPair pid="1">
      <Question qid="q1">What is melanoma?</Question>
      <Answer>Melanoma is a serious skin cancer that begins in melanocytes.</Answer>
    </QAPair>
  </QAPairs>
</Document>"""

_EMPTY = b"""<Document id="2"><Focus>Empty</Focus><QAPairs>
  <QAPair><Question>Q?</Question><Answer></Answer></QAPair>
</QAPairs></Document>"""


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _stub(zip_bytes: bytes):
    def _dl(url: str, max_bytes: int) -> bytes:
        return zip_bytes
    return _dl


def _manifest(cfg: dict) -> PackManifest:
    return PackManifest.from_dict({
        "id": "medquad-fixture", "name": "fx", "version": "1.0.0",
        "description": "fx", "domain": "personal", "license": "CC-BY-4.0",
        "provenance": {"source": "https://github.com/abachaa/MedQuAD"},
        "build": {"adapter": "qa_xml", "config": cfg},
    })


def test_qa_xml_renders_qapairs_and_honours_excludes(tmp_path):
    z = _zip({
        "MedQuAD-master/1_CancerGov_QA/doc1.xml": _DOC,
        "MedQuAD-master/10_MPlus_ADAM_QA/adam.xml": _DOC,   # copyright-excluded subset
        "MedQuAD-master/1_CancerGov_QA/empty.xml": _EMPTY,  # no answer -> skipped
    })
    adapter = QaXmlAdapter(downloader=_stub(z))
    manifest = _manifest({
        "repo": "abachaa/MedQuAD", "ref": "master",
        "include_globs": ["*/*.xml"],
        "exclude_globs": ["10_MPlus_ADAM_QA/**"],
        "min_text_chars": 20,
    })
    result = adapter.fetch(manifest, staging_root=tmp_path)
    assert [p.name for p in result.files] == ["melanoma.md"]
    body = (result.content_root / "melanoma.md").read_text()
    assert body.startswith("# Melanoma")
    assert "## What is melanoma?" in body
    assert "serious skin cancer" in body


def test_qa_xml_raises_when_no_usable_pairs(tmp_path):
    z = _zip({"MedQuAD-master/sub/empty.xml": _EMPTY})
    adapter = QaXmlAdapter(downloader=_stub(z))
    manifest = _manifest({"repo": "abachaa/MedQuAD", "include_globs": ["**/*.xml"]})
    with pytest.raises(PackError, match="no document yielded"):
        adapter.fetch(manifest, staging_root=tmp_path)


def test_qa_xml_rejects_bad_repo():
    with pytest.raises(PackError, match="owner/name"):
        QaXmlConfig.from_build(BuildSpec(adapter="qa_xml", config={"repo": "noslash"}))


def test_qa_xml_registered():
    assert "qa_xml" in list_registered_adapters()
    assert isinstance(get_adapter("qa_xml"), QaXmlAdapter)


def test_render_document_rejects_malformed_xml():
    assert _render_document(b"<not-valid-xml") is None


def test_render_document_skips_when_no_answer():
    assert _render_document(_EMPTY) is None
