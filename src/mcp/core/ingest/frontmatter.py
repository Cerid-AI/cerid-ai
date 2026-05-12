# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""YAML frontmatter extraction (Workstream RAG Cycle C2.2).

Pure-Python parser for Obsidian/Jekyll-style ``---`` fenced YAML
frontmatter blocks at the head of a Markdown body.  Lives in ``core/``
so the markdown parser can call it without crossing the import-linter
``core ↛ app`` boundary.

Output policy (intentionally restrictive)
-----------------------------------------

* **Allowlist**: only the reserved keys + any ``cerid:``-prefixed key
  flow through.  Everything else is silently dropped — frontmatter is
  user-authored and noisy, and we'd rather miss a key than commit
  unknown user input as Neo4j node properties.

  Reserved keys (case-sensitive):
    ``tags``, ``aliases``, ``cssclass``, ``status``, ``created``,
    ``updated``, ``source``

  Custom prefix:
    Any key starting with ``cerid:`` (e.g. ``cerid:priority``).

* **Coercion**: ``aliases`` and ``tags`` accept either a YAML list or a
  single string (Obsidian's "implicit single-tag" form), and the
  single-string form is normalised to a one-element list so downstream
  consumers don't need to branch.

* **Failure mode**: malformed YAML, missing closing fence, and missing
  leading fence all return ``({}, original_text)`` — never raise.  A
  malformed frontmatter must not break ingestion; a debug-level log
  captures the YAML error for observability.
"""
from __future__ import annotations

import logging
from typing import Any

import yaml

from core.utils.swallowed import log_swallowed_error

logger = logging.getLogger("ai-companion.ingest.frontmatter")

_RESERVED_KEYS: frozenset[str] = frozenset(
    {"tags", "aliases", "cssclass", "status", "created", "updated", "source"},
)
_CUSTOM_PREFIX = "cerid:"

# Frontmatter delimiter — exactly three dashes on their own line.
_FENCE = "---"


def is_allowlisted(key: str) -> bool:
    """Return True iff ``key`` is reserved or carries the cerid: prefix."""
    return key in _RESERVED_KEYS or key.startswith(_CUSTOM_PREFIX)


def _coerce_string_list(value: Any) -> Any:
    """Normalise a single string into a one-element list.

    Obsidian's ``tags: foo`` (no list) form is common — turn it into
    ``["foo"]`` so downstream callers never have to branch on type.
    Leaves lists, dicts, and primitives untouched.
    """
    if isinstance(value, str):
        return [value]
    return value


def extract_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse a YAML frontmatter block at the start of ``text``.

    A frontmatter block is delimited by a leading line that is exactly
    ``---`` (modulo trailing whitespace) and a trailing line that
    matches the same pattern.  Returns:

    * ``(props, body)`` when a valid YAML block parses cleanly.  ``props``
      contains only allowlisted (reserved + ``cerid:``-prefixed) keys;
      everything else is silently dropped.  ``body`` is the input with
      the entire frontmatter block (delimiters included, plus its
      trailing newline) stripped.
    * ``({}, text)`` when there's no leading ``---``, the closing
      ``---`` is missing, or the YAML payload is malformed.  In the
      malformed-YAML case, a debug log fires but no exception bubbles.

    The function never raises — callers can wire it into the ingestion
    hot path without defensive try-blocks.
    """
    if not text:
        return {}, text

    lines = text.split("\n")
    # First non-empty line must be exactly ``---``.  We allow trailing
    # whitespace but no leading whitespace (BOM/UTF-8 oddities aside,
    # Obsidian rejects indented fences and so do we).
    if not lines or lines[0].rstrip() != _FENCE:
        return {}, text

    # Find the closing fence.  Start search at line 1 — line 0 is the
    # opening fence we just matched.
    closing_idx = -1
    for idx in range(1, len(lines)):
        if lines[idx].rstrip() == _FENCE:
            closing_idx = idx
            break
    if closing_idx == -1:
        # Missing closing fence — treat the whole document as body.
        return {}, text

    yaml_block = "\n".join(lines[1:closing_idx])
    try:
        parsed = yaml.safe_load(yaml_block)
    except yaml.YAMLError as e:
        logger.debug("frontmatter.yaml_parse_failed: %s", e)
        return {}, text
    except Exception as e:  # noqa: BLE001 — observability boundary
        log_swallowed_error("core.ingest.frontmatter.parse", e)
        return {}, text

    # Body is everything AFTER the closing fence.  Preserve the original
    # newline that followed the fence (if any) so chunkers see the same
    # offsets they'd see without frontmatter.
    body_lines = lines[closing_idx + 1:]
    body = "\n".join(body_lines)

    if not isinstance(parsed, dict):
        # Frontmatter block parsed as a scalar/list — not a mapping, so
        # there's no key/value frontmatter to extract.  Strip the fence
        # block anyway since the user clearly intended one.
        return {}, body

    out: dict[str, Any] = {}
    for key, value in parsed.items():
        if not isinstance(key, str):
            # YAML can produce int/bool/datetime keys; we only want
            # string keys (the allowlist is string-typed).
            continue
        if not is_allowlisted(key):
            continue
        if key in ("aliases", "tags"):
            value = _coerce_string_list(value)
        out[key] = value

    return out, body
