# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Hardened XML parsing for untrusted remote documents (RSS/Atom feeds).

The stdlib ``xml.etree.ElementTree`` expands internal entity definitions and
(depending on the parser) resolves external ones — the billion-laughs and
XXE vectors. This module routes all untrusted-feed parsing through
``defusedxml``, which forbids entity declarations and external references by
default, and normalises its rejections to ``xml.etree.ElementTree.ParseError``
so callers need only the one except branch they already have.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET

import defusedxml.ElementTree as DET
from defusedxml.common import DefusedXmlException


def safe_fromstring(body: bytes | str) -> ET.Element:
    """Parse untrusted XML with entity/external-reference attacks rejected.

    Raises ``xml.etree.ElementTree.ParseError`` both for malformed XML and for
    defused-rejected payloads (billion-laughs, XXE), so existing
    ``except ET.ParseError`` handlers cover both.
    """
    try:
        return DET.fromstring(body)
    except DefusedXmlException as exc:
        raise ET.ParseError(f"unsafe XML rejected: {exc}") from exc
