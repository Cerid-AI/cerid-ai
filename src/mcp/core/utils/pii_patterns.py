# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""PII and credential patterns shared by the privacy audit and write-time refusals.

Conservative: a false positive is reviewed by the operator, a false negative
exposes data.
"""
from __future__ import annotations

import re

PII_PATTERNS: dict[str, re.Pattern[str]] = {
    "email": re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
    "ssn_us": re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    "phone_us": re.compile(r"\b\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b"),
    "credit_card": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),  # noqa: DUO138 — {13,16} cap bounds backtracking
    "api_key_generic": re.compile(r"\b(sk-|pk-|api[_-]?key[=:]?\s*)[A-Za-z0-9_-]{20,}\b"),
    "aws_access_key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "private_key_pem": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"),
    "jwt": re.compile(r"\beyJ[A-Za-z0-9_-]+\.eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b"),
}

__all__ = ["PII_PATTERNS"]
