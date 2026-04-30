# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Email parser — header preservation + reply-chain stripping.

Workstream E Phase 2b.4. Closes the audit's most acute gap ("Email:
no parser exists; no header capture, no thread stitching, no
quoted-reply stripping"). Each ``.eml`` file becomes:

* one :class:`EmailHeader` element — structured headers in metadata
  (From / To / Cc / Subject / Date / Message-ID / In-Reply-To /
  References / thread_id) with the rendered header block as text so
  retrieval can match on ``"from: alice@..."`` directly
* one :class:`EmailBody` element — the reply-stripped body text
  (``quotequail.unwrap``'s ``text_top``) with the quoted previous
  message dropped so chunks aren't dominated by older replies
* one :class:`EmailThreadEdge` element when ``In-Reply-To`` is set —
  zero embeddable text; carries thread linkage metadata that
  Phase 4 (graph) consumes to write ``REPLIES_TO`` Neo4j edges

Library choices:

* **mail-parser** — Apache-2.0, zero transitive deps. Cleaner header
  access than the stdlib ``email`` module (handles MIME quirks +
  multi-recipient parsing).
* **quotequail** — pure Python, zero non-stdlib deps. Successor to
  the deprecated ``cchardet``-tied ``talon`` for reply-chain
  unwrapping. Works on plain text + on the typed dict return shape.

Privacy: ``CERID_ANONYMIZE_EMAIL_HEADERS`` (already in settings.py)
governs whether the From/To/Cc addresses are redacted at ingest;
this parser respects it on the rendered header text BUT keeps the
raw structured headers in metadata so downstream privacy-aware
retrieval can re-redact per query context.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import config
from core.ingest.parsers import ParsedElement

logger = logging.getLogger("ai-companion.ingest.parsers.email")


def _format_recipients(recipients: Any) -> list[str]:
    """Convert mail-parser's ``[(name, addr), ...]`` shape to a flat
    list of email addresses for storage in metadata.

    mail-parser returns recipient fields as a list of
    ``(display_name, email_address)`` tuples. Display names contain
    PII more often than addresses do — strip them here so metadata
    stays minimally-revealing while remaining searchable.
    """
    if recipients is None:
        return []
    if isinstance(recipients, str):
        return [recipients]
    out: list[str] = []
    for entry in recipients:
        if isinstance(entry, (list, tuple)) and len(entry) >= 2:
            out.append(str(entry[1]))
        else:
            out.append(str(entry))
    return out


def _redact(addr: str) -> str:
    """Replace the local part of an email address with '[redacted]'.

    Domain is preserved for context (e.g. ``[redacted]@example.com``).
    Used only when ``CERID_ANONYMIZE_EMAIL_HEADERS=true``.
    """
    if "@" not in addr:
        return "[redacted]"
    domain = addr.rsplit("@", 1)[1]
    return f"[redacted]@{domain}"


def _render_header_block(meta: dict[str, Any], *, anonymize: bool) -> str:
    """Render structured headers as searchable text.

    The text is what gets embedded — column-name semantics survive so
    a query for "from alice" or "subject onboarding" can match.
    """
    def _render(key: str, val: Any) -> str:
        if isinstance(val, list):
            if anonymize:
                val = [_redact(v) for v in val]
            joined = ", ".join(val)
        else:
            if anonymize and key in {"from", "to", "cc", "bcc"}:
                joined = _redact(str(val))
            else:
                joined = str(val) if val is not None else ""
        return f"{key}: {joined}"

    lines = []
    for key in ("from", "to", "cc", "subject", "date", "message_id", "thread_id"):
        if key in meta and meta[key]:
            lines.append(_render(key, meta[key]))
    return "\n".join(lines)


def _resolve_thread_id(message_id: str, in_reply_to: str | None,
                      references: list[str]) -> str:
    """Pick the best thread anchor from header data.

    Priority: oldest entry in ``References`` (the conversation root) →
    ``In-Reply-To`` → fall back to this message's own ID. Operators
    that want strict RFC 2822 threading wire a separate resolver in
    a future commit; this heuristic is robust against the common case.
    """
    if references:
        return references[0]
    if in_reply_to:
        return in_reply_to
    return message_id


def parse_email_string(eml_text: str, *, source_name: str = "") -> list[ParsedElement]:
    """Parse a raw RFC822 email string into Header + Body + Edge elements.

    Args:
        eml_text: The raw email bytes/text as RFC822.
        source_name: Optional filename for inclusion in metadata.

    Returns:
        A list of :class:`ParsedElement` dicts: one ``EmailHeader``,
        one ``EmailBody``, and (when ``In-Reply-To`` is set) one
        ``EmailThreadEdge``. Returns ``[]`` when the input is empty
        or unparseable.
    """
    if not eml_text or not eml_text.strip():
        return []

    # Lazy import — keeps the module loadable even when the libs aren't
    # installed (community community-tier installs that don't ingest email).
    from mailparser import parse_from_string

    try:
        m = parse_from_string(eml_text)
    except Exception as exc:  # noqa: BLE001 — let upstream dispatcher fall back
        logger.warning("email_parse_failed source=%s error=%s", source_name, exc)
        return []

    # mail-parser's m.from_ is [(display, addr), ...] — take the first address only
    from_addrs = _format_recipients(m.from_)
    from_addr = from_addrs[0] if from_addrs else ""

    message_id = (m.message_id or "").strip()
    in_reply_to = (m.in_reply_to or "").strip() or None

    # m.references is sometimes a string with whitespace-separated ids,
    # sometimes a list. Normalise to list[str].
    raw_refs = m.references
    if isinstance(raw_refs, str):
        references = [r for r in raw_refs.split() if r]
    elif isinstance(raw_refs, list):
        references = [str(r) for r in raw_refs if r]
    else:
        references = []

    thread_id = _resolve_thread_id(message_id, in_reply_to, references)

    structured = {
        "from": from_addr,
        "to": _format_recipients(m.to),
        "cc": _format_recipients(m.cc),
        "subject": (m.subject or "").strip(),
        "date": str(m.date) if m.date else "",
        "message_id": message_id,
        "in_reply_to": in_reply_to,
        "references": references,
        "thread_id": thread_id,
        "source": source_name,
    }

    anonymize = bool(getattr(config, "ANONYMIZE_EMAIL_HEADERS", True))
    header_text = _render_header_block(structured, anonymize=anonymize)

    elements: list[ParsedElement] = [
        {
            "text": header_text,
            "element_type": "EmailHeader",
            "metadata": dict(structured),
        },
    ]

    body = (m.body or "").strip()
    if body:
        try:
            from quotequail import unwrap
            unwrapped = unwrap(body)
            # `unwrap` returns dict for replies, None for unstructured bodies
            if isinstance(unwrapped, dict) and unwrapped.get("text_top"):
                stripped_body = unwrapped["text_top"].strip()
            else:
                stripped_body = body
        except Exception as exc:  # noqa: BLE001 — fall back to raw body
            logger.debug("email_quote_unwrap_failed: %s", exc)
            stripped_body = body

        if stripped_body:
            elements.append(
                {
                    "text": stripped_body,
                    "element_type": "EmailBody",
                    "metadata": {
                        "thread_id": thread_id,
                        "message_id": message_id,
                        "source": source_name,
                    },
                },
            )

    # Synthetic thread-edge for Phase 4's graph layer. Carries no
    # embeddable text (downstream chunker emits empty-body chunks
    # but the metadata is what gets persisted as a Neo4j edge).
    if in_reply_to:
        elements.append(
            {
                "text": "",
                "element_type": "EmailThreadEdge",
                "metadata": {
                    "thread_id": thread_id,
                    "message_id": message_id,
                    "in_reply_to": in_reply_to,
                    "references": references,
                },
            },
        )

    return elements


def parse_email(path: str | Path, *, encoding: str = "utf-8") -> list[ParsedElement]:
    """Parse an `.eml` file. See :func:`parse_email_string` for shape."""
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(f"Email file not found: {p}")
    raw = p.read_text(encoding=encoding, errors="replace")
    return parse_email_string(raw, source_name=p.name)
