# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Email parsers — .eml and .mbox formats.

RAG Cycle C2.4 — attachment extraction
--------------------------------------
``parse_eml`` / ``parse_mbox`` extract attachment bytes alongside the
body text. The returned dict carries a private ``_attachments`` field
(list of :class:`core.ingest.attachments.AttachmentBlob`) that the
service layer consumes to recursively ingest each attachment as its own
Artifact and link it to the parent email via ``HAS_ATTACHMENT``.

* Attachments larger than ``EMAIL_ATTACHMENT_MAX_SIZE`` (50 MB) are
  listed in the email body text with a ``[skipped: too large]`` marker
  but never extracted.
* Cycle prevention: when called with ``skip_nested_attachments=True``
  the parser still emits the body text + header listing for nested
  ``.eml`` parts but suppresses recursive byte extraction. This is the
  hook the service layer uses to stop exponential blowup when an
  attachment is itself an email.
* The legacy ``"attachment_count"`` field on the return dict is kept
  for backward compatibility with existing callers.
"""

from __future__ import annotations

import re as _re
from contextvars import ContextVar
from pathlib import Path
from typing import Any

import config as _config
from app.parsers._utils import _strip_html_tags
from app.parsers.registry import _MAX_TEXT_CHARS, register_parser
from core.ingest.attachments import EMAIL_ATTACHMENT_MAX_SIZE, AttachmentBlob

# C2.4 cycle-prevention flag. The ingestion service flips this to True while
# recursively ingesting an attachment so a nested ``.eml`` attachment yields
# its body text + listing but does NOT extract its own attachments. Lives on
# the parser side (not the service side) so any caller of ``parse_eml`` /
# ``parse_file`` inherits the behaviour automatically — including the
# ``ingest_file`` dispatch path that thunks through the parser registry.
_SKIP_NESTED_ATTACHMENTS: ContextVar[bool] = ContextVar(
    "_eml_skip_nested_attachments",
    default=False,
)

_ANONYMIZE_KEYS = {"From", "To", "Cc"}

# Content types that are part of the email body / structural envelope —
# never treated as user-facing attachments even when no Content-Disposition
# header is present.
_BODY_CONTENT_TYPES: frozenset[str] = frozenset({
    "text/plain",
    "text/html",
    "multipart/alternative",
    "multipart/mixed",
    "multipart/related",
    "multipart/report",
    "multipart/signed",
    "multipart/digest",
    "message/delivery-status",
})


def _anonymize_header(value: str) -> str:
    """Replace email addresses with redacted form, preserving domain for context."""
    if not _config.ANONYMIZE_EMAIL_HEADERS:
        return value
    return _re.sub(
        r"[a-zA-Z0-9._%+\-]+@([a-zA-Z0-9.\-]+\.[a-zA-Z]{2,})",
        r"[redacted]@\1",
        value,
    )


def _extract_attachment_bytes(part: Any) -> tuple[bytes, str, str] | None:
    """Pull raw decoded bytes from one ``email.message.EmailMessage`` part.

    Returns ``(content_bytes, filename, content_type)`` or ``None`` if
    the part is not a leaf attachment (multipart envelope, body part, or
    a part with no payload).

    A leaf is considered an attachment when ANY of these hold:

    1. Its ``Content-Disposition`` header begins with ``attachment``
       (the canonical signal — what mail clients actually emit).
    2. It has a ``Content-Disposition: inline`` AND a ``filename``
       parameter AND a non-body content-type (e.g. an inline PDF that
       Outlook rendered alongside text).
    3. Its content-type is unrecognised AND it has a filename — the
       "looks like a binary blob with a filename" fallback.

    ``message/rfc822`` parts are reported as ``is_multipart() == True``
    by stdlib because the nested message IS a sub-message — but when
    the disposition says ``attachment`` we treat them as a single
    attachment payload (an attached email). The bytes are serialised
    via ``part.as_bytes()`` so the service layer can hand them to
    ``parse_eml`` for nested ingestion (with cycle-prevention engaged).
    """
    content_type = part.get_content_type()
    disposition = str(part.get("Content-Disposition", "")).strip().lower()
    filename = part.get_filename()
    is_rfc822 = content_type == "message/rfc822"

    # Multipart envelopes carry no payload of their own — except
    # ``message/rfc822`` parts attached with a disposition header.
    if part.is_multipart() and not (
        is_rfc822 and disposition.startswith("attachment")
    ):
        return None

    is_attachment_disposition = disposition.startswith("attachment")
    is_inline_with_filename = (
        disposition.startswith("inline")
        and filename
        and content_type not in _BODY_CONTENT_TYPES
    )
    is_unrecognised_with_filename = (
        not disposition
        and filename
        and content_type not in _BODY_CONTENT_TYPES
    )

    if not (
        is_attachment_disposition
        or is_inline_with_filename
        or is_unrecognised_with_filename
    ):
        return None

    if is_rfc822:
        # The nested message's payload is the EmailMessage itself —
        # serialise it back to bytes so downstream parsers see a real
        # ``.eml`` byte stream.
        nested = part.get_payload()
        if isinstance(nested, list):
            nested = nested[0] if nested else None
        if nested is None:
            return None
        try:
            payload = nested.as_bytes()
        except Exception:  # noqa: BLE001 — best-effort serialisation
            return None
    else:
        payload = part.get_payload(decode=True)
        if not isinstance(payload, bytes):
            return None

    return payload, filename or "(unnamed)", content_type


def _iter_parts_skipping_rfc822_children(msg: Any):
    """Yield parts of ``msg`` like ``msg.walk()`` but do NOT descend
    into ``message/rfc822`` subtrees.

    A nested ``.eml`` attachment is captured as a single attachment
    payload (the whole sub-message serialised back to bytes). If we
    also walked into its children, we'd extract the nested email's
    OWN attachments as siblings of the outer message — breaking the
    cycle-prevention contract before the service layer ever sees it.
    """
    stack: list[Any] = [msg]
    while stack:
        part = stack.pop(0)
        yield part
        if part.is_multipart() and part.get_content_type() != "message/rfc822":
            # Reverse so siblings come out in declaration order under
            # the pop(0) queue.
            children = list(part.iter_parts())
            stack[:0] = children


def _collect_attachments(
    msg: Any,
    *,
    extract_bytes: bool,
) -> tuple[list[AttachmentBlob], list[str]]:
    """Walk a parsed message; return ``(blobs, listing_lines)``.

    ``listing_lines`` is the human-readable text rendered into the body's
    ``--- Attachments ---`` block. Skipped (too-large) attachments are
    listed with a ``[skipped: too large]`` marker so the parent email's
    body text records that they existed.

    ``extract_bytes=False`` (set when the parent ingest is a nested
    ``.eml`` attachment) means: still list filenames + sizes for the
    body text, but do NOT return any ``AttachmentBlob`` instances so the
    service layer can't recurse a second time.

    The walker explicitly does NOT descend into ``message/rfc822``
    subtrees — a nested email is one attachment, not "an email plus
    each of its attachments". The service layer handles recursion at
    its own boundary so the cycle-prevention ContextVar can fire.
    """
    blobs: list[AttachmentBlob] = []
    listing: list[str] = []
    if not msg.is_multipart():
        return blobs, listing

    for part in _iter_parts_skipping_rfc822_children(msg):
        extracted = _extract_attachment_bytes(part)
        if extracted is None:
            continue
        payload, filename, content_type = extracted
        size = len(payload)

        if size > EMAIL_ATTACHMENT_MAX_SIZE:
            # Too large: list it in the body text so retrieval can still
            # surface that the attachment existed, but never extract.
            listing.append(
                f"{filename} ({size} bytes) [skipped: too large]"
            )
            continue

        listing.append(f"{filename} ({size} bytes)")
        if extract_bytes:
            blobs.append(
                AttachmentBlob(
                    filename=filename,
                    content_bytes=payload,
                    content_type=content_type,
                    size=size,
                )
            )

    return blobs, listing


def _parse_eml_bytes(
    raw: bytes,
    *,
    source_name: str,
    extract_bytes: bool,
) -> dict[str, Any]:
    """Shared body of :func:`parse_eml` — works on raw bytes.

    Splitting this out lets the service layer parse a nested ``.eml``
    attachment from in-memory bytes without writing it to disk twice.
    """
    import email
    import email.policy
    from email import message_from_bytes

    try:
        msg = message_from_bytes(raw, policy=email.policy.default)
    except Exception as e:
        raise ValueError(
            f"Failed to parse email '{source_name}': {e}. "
            f"File may not be a valid .eml file."
        ) from e

    headers = {}
    for key in ("From", "To", "Cc", "Subject", "Date", "Message-ID"):
        val = msg.get(key, "")
        if val:
            headers[key] = (
                _anonymize_header(str(val)) if key in _ANONYMIZE_KEYS else str(val)
            )

    header_text = "\n".join(f"{k}: {v}" for k, v in headers.items())

    body = ""
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get("Content-Disposition", "")).strip().lower()

            # Skip attachments — they're handled below.
            if disposition.startswith("attachment"):
                continue

            if content_type == "text/plain" and not body:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    body = payload.decode("utf-8", errors="replace")
            elif content_type == "text/html" and not body:
                payload = part.get_payload(decode=True)
                if isinstance(payload, bytes):
                    html = payload.decode("utf-8", errors="replace")
                    body = _strip_html_tags(html)
    else:
        content_type = msg.get_content_type()
        payload = msg.get_payload(decode=True)
        if isinstance(payload, bytes):
            raw_text = payload.decode("utf-8", errors="replace")
            if content_type == "text/html":
                body = _strip_html_tags(raw_text)
            else:
                body = raw_text

    blobs, listing = _collect_attachments(msg, extract_bytes=extract_bytes)

    parts: list[str] = [header_text]
    if body:
        parts.append(f"\n--- Body ---\n{body.strip()}")
    if listing:
        parts.append(
            f"\n--- Attachments ({len(listing)}) ---\n" + "\n".join(listing)
        )

    text = "\n".join(parts)

    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "eml",
        "page_count": None,
        # Legacy count — the number of attachments we *saw*, including
        # the skipped-too-large ones (matches pre-C2.4 behaviour).
        "attachment_count": len(listing),
        "subject": headers.get("Subject", ""),
        # Identifying email fields the service layer stamps onto each
        # attachment artifact's metadata so retrieval can join "all
        # attachments of email X" without a graph traversal.
        "message_id": headers.get("Message-ID", ""),
        "from": headers.get("From", ""),
        # C2.4: structured attachment payloads for recursive ingestion.
        # Empty list when ``extract_bytes=False`` (nested-eml cycle break).
        "_attachments": blobs,
    }


@register_parser([".eml"])
def parse_eml(file_path: str) -> dict[str, Any]:
    """Parse .eml — headers, body (prefers text/plain), attachment list.

    Returns ``{text, file_type, page_count, attachment_count, subject,
    _attachments}``. The ``_attachments`` field is a list of
    :class:`AttachmentBlob` — the service layer consumes it; other
    callers can ignore it.

    When ``_SKIP_NESTED_ATTACHMENTS`` is set in the calling context
    (service layer's attachment recursion), the bytes-extraction step
    is suppressed: the body text still lists the attachment filenames
    + sizes, but ``_attachments`` comes back empty. This is the C2.4
    cycle-prevention contract.
    """
    path = Path(file_path)
    raw = path.read_bytes()
    extract_bytes = not _SKIP_NESTED_ATTACHMENTS.get()
    return _parse_eml_bytes(raw, source_name=path.name, extract_bytes=extract_bytes)


@register_parser([".mbox"])
def parse_mbox(file_path: str) -> dict[str, Any]:
    """Parse .mbox — extract messages as sections (capped by ``MBOX_MESSAGE_CAP``).

    C2.4 — attachments across all extracted messages are pooled into the
    return dict's ``_attachments`` field so the service layer can ingest
    them recursively, one per email message. Each ``AttachmentBlob``'s
    metadata still travels with the parent message via the body text
    listing.

    C2.5 — surfaces truncation as structured fields:
      * ``mbox_truncated`` (bool) — true when the file contained more
        messages than the cap.
      * ``mbox_total_messages`` (int) — the raw count seen in the file
        (including the un-extracted tail).
      * ``mbox_message_cap`` (int) — the cap that was applied (echoes
        ``config.MBOX_MESSAGE_CAP`` at call time).
    The ingest service surfaces these on the response so the UI can
    warn the user instead of silently losing 99% of the archive.
    """
    import mailbox

    path = Path(file_path)
    try:
        mbox = mailbox.mbox(file_path)
    except Exception as e:
        raise ValueError(
            f"Failed to parse mbox '{path.name}': {e}. "
            f"File may not be a valid .mbox file."
        ) from e

    messages: list[str] = []
    pooled_attachments: list[AttachmentBlob] = []
    total_attachment_count = 0
    # Read MBOX_MESSAGE_CAP at call time (not module import) so test
    # monkey-patches of ``config.MBOX_MESSAGE_CAP`` take effect.
    max_messages = int(getattr(_config, "MBOX_MESSAGE_CAP", 100))
    total_count = 0
    # Honor the same cycle-break contract as ``parse_eml``: if we're
    # already inside an attachment-recursion frame, list filenames but
    # do not extract bytes a second level deep.
    extract_bytes = not _SKIP_NESTED_ATTACHMENTS.get()

    for msg in mbox:
        total_count += 1
        if len(messages) >= max_messages:
            continue  # count but don't extract

        subject = msg.get("Subject", "(no subject)")
        from_addr = _anonymize_header(msg.get("From", ""))
        date = msg.get("Date", "")

        body = ""
        if msg.is_multipart():
            for part in msg.walk():
                if part.get_content_type() == "text/plain":
                    payload = part.get_payload(decode=True)
                    if isinstance(payload, bytes):
                        body = payload.decode("utf-8", errors="replace")
                        break
        else:
            payload = msg.get_payload(decode=True)
            if isinstance(payload, bytes):
                body = payload.decode("utf-8", errors="replace")

        # C2.4 — pull attachment bytes off this message and pool them
        # for the service layer. The body-text listing per message
        # mirrors the per-mail .eml format.
        blobs, listing = _collect_attachments(msg, extract_bytes=extract_bytes)
        pooled_attachments.extend(blobs)
        total_attachment_count += len(listing)

        header = f"From: {from_addr}\nDate: {date}\nSubject: {subject}"
        body_section = f"{header}\n\n{body.strip()}"
        if listing:
            body_section += (
                f"\n--- Attachments ({len(listing)}) ---\n"
                + "\n".join(listing)
            )
        messages.append(body_section)

    mbox.close()

    sep = "\n\n" + "=" * 60 + "\n\n"
    text = sep.join(messages)
    truncated = total_count > max_messages
    if truncated:
        text += f"\n\n[... {total_count - max_messages} more messages truncated ...]"

    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "mbox",
        "page_count": total_count,
        "attachment_count": total_attachment_count,
        # C2.5 — structured truncation signal for the ingest response.
        "mbox_truncated": truncated,
        "mbox_total_messages": total_count,
        "mbox_message_cap": max_messages,
        "_attachments": pooled_attachments,
    }


@register_parser([".msg"])
def parse_msg(file_path: str) -> dict[str, Any]:
    """Parse a single Outlook ``.msg`` file.

    .msg is a CFB (Compound File Binary) Outlook envelope. We read the
    canonical headers + body via ``app.parsers.msg_reader`` (a small
    ``olefile``-backed MS-OXMSG reader), then funnel the result through the same
    dict shape ``parse_eml`` returns so retrieval and downstream chunkers don't
    need a special case.

    That reader replaced ``extract-msg``, which is GPL and pulled
    ``RTFDE`` -> ``oletools`` -> ``pcodedmp`` (GPL-3.0) behind it — a chain this
    product's licensing cannot carry. See ``msg_reader``'s module docstring.

    Attachments embedded in the .msg are surfaced as ``AttachmentBlob``
    entries in ``_attachments`` so C2.4's recursive ingestion picks them
    up — same contract as ``.eml``. Cycle-prevention (``_SKIP_NESTED_
    ATTACHMENTS``) is honoured.

    PST archives are NOT supported in this phase — ``.pst`` would require
    ``libpff-python`` (a C extension with system-level libpff) or the
    ``pst-scanpst`` sidecar, both of which fall outside the C2.5 scope.
    """
    from app.parsers.msg_reader import open_msg

    path = Path(file_path)
    try:
        msg = open_msg(path)
    except (ValueError, RuntimeError):
        # ValueError already says what is wrong with the file; RuntimeError means
        # the environment is broken (see open_msg). Neither should be reworded
        # into "this may not be a valid .msg file".
        raise
    except Exception as e:
        raise ValueError(
            f"Failed to parse Outlook message '{path.name}': {e}. "
            f"File may not be a valid .msg file."
        ) from e

    headers: dict[str, str] = {}
    for src_key, dst_key in (
        ("sender", "From"),
        ("to", "To"),
        ("cc", "Cc"),
        ("subject", "Subject"),
        ("date", "Date"),
        ("message_id", "Message-ID"),
    ):
        val = getattr(msg, src_key, None)
        if not val:
            continue
        val_str = str(val)
        headers[dst_key] = (
            _anonymize_header(val_str) if dst_key in _ANONYMIZE_KEYS else val_str
        )

    body = msg.body or ""
    # Some .msg files only carry HTML — fall back and strip tags.
    if not body and msg.html_body:
        body = _strip_html_tags(msg.html_body)

    extract_bytes = not _SKIP_NESTED_ATTACHMENTS.get()
    blobs: list[AttachmentBlob] = []
    listing: list[str] = []
    for att in msg.attachments:
        payload = att.data
        filename = att.filename
        size = len(payload)
        if size > EMAIL_ATTACHMENT_MAX_SIZE:
            listing.append(f"{filename} ({size} bytes) [skipped: too large]")
            continue
        listing.append(f"{filename} ({size} bytes)")
        if extract_bytes:
            blobs.append(
                AttachmentBlob(
                    filename=str(filename),
                    content_bytes=payload,
                    content_type="application/octet-stream",
                    size=size,
                )
            )

    header_text = "\n".join(f"{k}: {v}" for k, v in headers.items())
    parts: list[str] = [header_text]
    if body:
        parts.append(f"\n--- Body ---\n{body.strip()}")
    if listing:
        parts.append(
            f"\n--- Attachments ({len(listing)}) ---\n" + "\n".join(listing)
        )

    text = "\n".join(parts)
    return {
        "text": text[:_MAX_TEXT_CHARS],
        "file_type": "msg",
        "page_count": None,
        "attachment_count": len(listing),
        "subject": headers.get("Subject", ""),
        "message_id": headers.get("Message-ID", ""),
        "from": headers.get("From", ""),
        "_attachments": blobs,
    }
