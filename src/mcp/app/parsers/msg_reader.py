"""Minimal Outlook ``.msg`` (MS-OXMSG) reader built on ``olefile``.

Replaces the ``extract-msg`` dependency, which is **GPL** and additionally
dragged in ``RTFDE`` (LGPL) -> ``oletools`` -> ``pcodedmp`` (**GPL-3.0**). This
product ships under FSL-1.1-ALv2 and its own dependency policy denies the GPL
family, so that chain could not stay. ``olefile`` is BSD, actively maintained,
and was already in the tree (``msoffcrypto-tool`` depends on it) — so this
removes four packages and adds none.

Scope is deliberately the surface ``app/parsers/email.py`` actually consumed:
canonical headers, a plain-text or HTML body, and attachment bytes. It is not a
general MAPI library, and it does not try to be one.

**Format, briefly.** A ``.msg`` file is a CFB (Compound File Binary) container.
Variable-length properties are individual streams named
``__substg1.0_<IIII><TTTT>`` where ``IIII`` is the property id and ``TTTT`` the
type, both uppercase hex. Fixed-length properties (timestamps, flags) are packed
into a single ``__properties_version1.0`` stream as 16-byte entries after a
header whose size depends on the storage: 32 bytes at the top level, 8 inside an
attachment. Attachments are sub-storages named ``__attach_version1.0_#XXXXXXXX``
carrying the same ``__substg1.0_*`` convention.

The decoding is a pure function over a ``{stream-name: bytes}`` mapping
(:func:`read_message`), so it is testable against real bytes without
constructing a CFB — which nothing in the Python standard library can write.
:func:`open_msg` is the thin adapter that lets ``olefile`` supply that mapping.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from typing import Mapping, Sequence

logger = logging.getLogger("ai-companion.parsers.msg")

# ── Property tags (MS-OXPROPS) ──────────────────────────────────────────────
PID_SUBJECT = 0x0037
PID_SENDER_NAME = 0x0C1A
PID_SENDER_EMAIL = 0x0C1F
PID_SENT_REPRESENTING_EMAIL = 0x0065
PID_DISPLAY_TO = 0x0E04
PID_DISPLAY_CC = 0x0E03
PID_BODY = 0x1000
PID_HTML = 0x1013
PID_INTERNET_MESSAGE_ID = 0x1035
PID_CLIENT_SUBMIT_TIME = 0x0039
PID_MESSAGE_DELIVERY_TIME = 0x0E06

PID_ATTACH_DATA = 0x3701
PID_ATTACH_LONG_FILENAME = 0x3707
PID_ATTACH_FILENAME = 0x3704

# ── Property types ──────────────────────────────────────────────────────────
PT_STRING8 = 0x001E   # 8-bit, codepage-dependent
PT_UNICODE = 0x001F   # UTF-16LE
PT_BINARY = 0x0102
PT_SYSTIME = 0x0040   # FILETIME

_SUBSTG_PREFIX = "__substg1.0_"
_PROPERTIES_STREAM = "__properties_version1.0"
_ATTACH_PREFIX = "__attach_version1.0_"

# Header before the 16-byte fixed-property entries. Differs by storage kind;
# only these two are reachable from what this reader opens.
_PROPS_HEADER_TOP_LEVEL = 32
_PROPS_HEADER_ATTACHMENT = 8

# CFB path depth, as olefile reports it: ``["stream"]`` at the message root,
# ``["__attach_version1.0_#00000000", "stream"]`` inside an attachment storage.
_DEPTH_TOP_LEVEL = 1
_DEPTH_ATTACHMENT = 2

# FILETIME epoch: 1601-01-01T00:00:00Z, counted in 100-nanosecond intervals.
_FILETIME_EPOCH = datetime(1601, 1, 1, tzinfo=timezone.utc)


@dataclass(frozen=True)
class MsgAttachment:
    """One attachment: the bytes plus whichever filename the message carried."""

    filename: str
    data: bytes


@dataclass
class MsgMessage:
    """The narrow view of a ``.msg`` this codebase consumes."""

    subject: str | None = None
    sender: str | None = None
    to: str | None = None
    cc: str | None = None
    date: str | None = None
    message_id: str | None = None
    body: str | None = None
    html_body: str | None = None
    attachments: list[MsgAttachment] = field(default_factory=list)


def _substg_name(prop_id: int, prop_type: int) -> str:
    return f"{_SUBSTG_PREFIX}{prop_id:04X}{prop_type:04X}"


def _decode_string(raw: bytes, prop_type: int) -> str:
    """Decode a string property, never raising on malformed bytes.

    A truncated final UTF-16 code unit is common in real messages produced by
    older clients; ``errors="replace"`` keeps one bad character from costing the
    whole message.
    """
    if prop_type == PT_UNICODE:
        return raw.decode("utf-16-le", errors="replace").rstrip("\x00")
    # PT_STRING8 has no codepage in the stream itself. cp1252 is the practical
    # default for Outlook-authored messages and is a superset of latin-1 for
    # the printable range; utf-8 is tried first since modern exporters use it.
    try:
        return raw.decode("utf-8").rstrip("\x00")
    except UnicodeDecodeError:
        return raw.decode("cp1252", errors="replace").rstrip("\x00")


def _read_string(streams: Mapping[str, bytes], prop_id: int) -> str | None:
    """Read a string property, preferring the Unicode form when both exist."""
    for prop_type in (PT_UNICODE, PT_STRING8):
        raw = streams.get(_substg_name(prop_id, prop_type))
        if raw:
            value = _decode_string(raw, prop_type)
            if value:
                return value
    return None


def _read_binary(streams: Mapping[str, bytes], prop_id: int) -> bytes | None:
    return streams.get(_substg_name(prop_id, PT_BINARY)) or None


def _filetime_to_datetime(ticks: int) -> datetime | None:
    """Convert a FILETIME tick count to an aware datetime, or None if absurd.

    Zero means unset. Values beyond the representable range appear in corrupt
    messages; a bad timestamp must not fail the whole parse.
    """
    if ticks <= 0:
        return None
    try:
        return _FILETIME_EPOCH + timedelta(microseconds=ticks // 10)
    except (OverflowError, ValueError):
        return None


def _read_fixed_properties(
    properties_stream: bytes | None, header_size: int
) -> dict[int, tuple[int, bytes]]:
    """Unpack ``__properties_version1.0`` into ``{prop_id: (type, 8-byte value)}``."""
    if not properties_stream or len(properties_stream) <= header_size:
        return {}
    out: dict[int, tuple[int, bytes]] = {}
    body = properties_stream[header_size:]
    for offset in range(0, len(body) - 15, 16):
        entry = body[offset : offset + 16]
        prop_type = int.from_bytes(entry[0:2], "little")
        prop_id = int.from_bytes(entry[2:4], "little")
        out[prop_id] = (prop_type, entry[8:16])
    return out


def _read_date(streams: Mapping[str, bytes]) -> str | None:
    """Submit time, falling back to delivery time, as an RFC 2822 Date header.

    RFC 2822 rather than ISO-8601 because the value lands in a ``Date:`` header
    beside the ``.eml`` path's, and the two must not disagree in format.
    """
    fixed = _read_fixed_properties(
        streams.get(_PROPERTIES_STREAM), _PROPS_HEADER_TOP_LEVEL
    )
    for prop_id in (PID_CLIENT_SUBMIT_TIME, PID_MESSAGE_DELIVERY_TIME):
        entry = fixed.get(prop_id)
        if not entry or entry[0] != PT_SYSTIME:
            continue
        parsed = _filetime_to_datetime(int.from_bytes(entry[1], "little"))
        if parsed:
            return format_datetime(parsed)
    return None


def _read_sender(streams: Mapping[str, bytes]) -> str | None:
    """Prefer a real address; fall back to the display name.

    ``PidTagSenderEmailAddress`` is absent on messages sent through Exchange in
    some configurations, where only the representing address or the display name
    survives — so all three are tried rather than assuming the first.
    """
    for prop_id in (PID_SENDER_EMAIL, PID_SENT_REPRESENTING_EMAIL, PID_SENDER_NAME):
        value = _read_string(streams, prop_id)
        if value:
            return value
    return None


def read_message(
    streams: Mapping[str, bytes],
    attachment_streams: Sequence[Mapping[str, bytes]] | None = None,
) -> MsgMessage:
    """Decode a ``.msg`` from its already-extracted streams.

    Pure: no filesystem, no ``olefile``. ``streams`` maps top-level stream names
    to their bytes; ``attachment_streams`` is one such mapping per attachment
    storage.
    """
    html_raw = _read_binary(streams, PID_HTML)
    html_body: str | None = None
    if html_raw:
        html_body = html_raw.decode("utf-8", errors="replace")
    else:
        # Some producers store the HTML body as a string property instead.
        html_body = _read_string(streams, PID_HTML)

    message = MsgMessage(
        subject=_read_string(streams, PID_SUBJECT),
        sender=_read_sender(streams),
        to=_read_string(streams, PID_DISPLAY_TO),
        cc=_read_string(streams, PID_DISPLAY_CC),
        date=_read_date(streams),
        message_id=_read_string(streams, PID_INTERNET_MESSAGE_ID),
        body=_read_string(streams, PID_BODY),
        html_body=html_body,
    )

    for att in attachment_streams or []:
        data = _read_binary(att, PID_ATTACH_DATA)
        if not data:
            continue
        filename = (
            _read_string(att, PID_ATTACH_LONG_FILENAME)
            or _read_string(att, PID_ATTACH_FILENAME)
            or "(unnamed)"
        )
        message.attachments.append(MsgAttachment(filename=filename, data=data))

    return message


def open_msg(path: str | Path) -> MsgMessage:
    """Read a ``.msg`` file from disk.

    Raises ``ValueError`` when the file is not a readable CFB container, matching
    what the parser layer above expects to translate into a 422.
    """
    try:
        import olefile
    except ImportError as exc:  # pragma: no cover - environment defect
        # Deliberately NOT a ValueError. The layer above turns ValueError into
        # "this file may not be a valid .msg", and a missing dependency is an
        # environment problem being blamed on the user's file — the same shape
        # as reporting a failed fetch as an empty result.
        raise RuntimeError(
            "olefile is required to read .msg files but is not installed. "
            "It is declared in src/mcp/requirements.txt."
        ) from exc

    path = Path(path)
    if not olefile.isOleFile(str(path)):
        raise ValueError(
            f"'{path.name}' is not an OLE compound file — .msg files begin with "
            f"the CFB signature D0 CF 11 E0."
        )

    ole = olefile.OleFileIO(str(path))
    try:
        top: dict[str, bytes] = {}
        by_attachment: dict[str, dict[str, bytes]] = {}

        for entry in ole.listdir(streams=True, storages=False):
            if len(entry) == _DEPTH_TOP_LEVEL:
                name = entry[0]
                if name.startswith(_SUBSTG_PREFIX) or name == _PROPERTIES_STREAM:
                    top[name] = ole.openstream(entry).read()
            elif len(entry) == _DEPTH_ATTACHMENT and entry[0].startswith(_ATTACH_PREFIX):
                storage, name = entry
                if name.startswith(_SUBSTG_PREFIX) or name == _PROPERTIES_STREAM:
                    by_attachment.setdefault(storage, {})[name] = ole.openstream(
                        entry
                    ).read()
            # Deeper nesting is an embedded message; out of scope, and skipping
            # it is why this reader cannot recurse into attached .msg files.

        # Sorted so attachment order is stable across reads of the same file.
        attachments = [by_attachment[k] for k in sorted(by_attachment)]
        return read_message(top, attachments)
    finally:
        ole.close()
