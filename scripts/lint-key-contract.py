#!/usr/bin/env python3
# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Gate 5 — producer/consumer key contract (consolidated audit 2026-08-11 §3).

Three audited channels where a producer's literal key-set and a consumer's
literal key-set silently diverged:

1. ``chroma-metadata`` — keys written into Chroma chunk metadata (ingestion,
   recategorize, verified-memory promotion, knowledge-pack provenance,
   triage enrichment) vs the keys retrieval reads back
   (``query_agent.py`` ``metadata.get("...")``). AF-064 is the flagship:
   recategorize writes ``tags`` while retrieval reads only ``tags_json``.

2. ``ingest-result`` — the success-path return dict of
   ``ingestion.ingest_content``/``ingest_file`` vs what ``folder_scanner``
   reads from it. AF-015: the scanner reads ``quality_score`` and
   ``duplicate``; the success dict emits neither, so every fresh ingest is
   tagged low_quality and real duplicates fall into the same branch.

3. ``structured-ingest-metadata`` — the desktop connectors' ``postStructured``
   metadata payloads vs the server contract
   ``StructuredIngestRequest.metadata: dict[str, str]``
   (``routers/ingestion.py``). WB-60: Calendar sends ``attendees`` (array)
   and Photos sends numbers/booleans/arrays, so every item 422s at FastAPI
   parse time — "Ingested 0 · 312 failed".

Failure semantics:
  * ERROR (exit 1): a consumer reads a key no producer writes; a connector
    metadata value that cannot be a string under a ``dict[str, str]``
    contract; a connector ``metadata:`` payload the scanner cannot resolve
    to an object literal in the file (built by a helper call, imported
    builder, or spread of an unresolvable name — the laundering bypass a
    2026-08-11 adversarial review reproduced live). Unless allowlisted.
  * WARNING (exit 0): a producer writes a key the channel's consumer never
    reads (other components may read it — the union consumer here is the
    audited retrieval path only), and metadata values this parser cannot
    classify. Allowlisted warnings are suppressed.

Metadata laundered through a local variable (``const md = {...}`` then
``metadata: md``, or the ``metadata`` shorthand) is resolved in-file and its
properties classified exactly as an inline literal's; anything that cannot
be resolved is an ERROR, never a silent skip.

The allowlist below is seeded with the violations the audits documented,
present at HEAD by instruction ("the next commit should be a gate, not a
fix"). Every entry carries a reason citing a finding id — either an audit
ledger id (AF-nnn / WB-nn) or a gate-discovered id (KC-nnn) defined in
``GATE_FINDINGS`` below; ``main`` enforces this shape. Remove the entry
when the finding is fixed; the gate then fails if the defect returns.

Scope is deliberately tight: whole-repo key analysis drowns in noise, so
producers and consumers are enumerated per channel. Keys that reach a channel
only through dynamic flow (``base_meta.update(metadata)``) are declared in
the channel spec with their verified origin, not guessed.

LIMITATIONS:
  * Alias bypass out of scope. ``const send = postStructured; send(...)`` (or
    any other re-binding of the function reference) is invisible to
    ``extract_ts_metadata_payloads``, which matches the literal call name
    ``postStructured(``. This gate is a drift catcher for the connectors as
    they are actually written today, not an adversarial static analyzer —
    every real connector in this repo calls ``postStructured`` directly, and
    the audits that motivated the laundering fixes above (local-variable
    metadata, helper calls, shorthand, spreads) were bypasses a reviewer
    demonstrated existing code could accidentally produce, not deliberate
    evasion. Renaming the call to dodge this gate is not a plausible accident
    the way building ``metadata`` in a local variable is. If a connector
    ever introduces an alias, treat it the same as any other unrecognized
    shape this gate can't see and add coverage deliberately rather than
    generalizing the regex speculatively.

Usage:
    python scripts/lint-key-contract.py          # report
    python scripts/lint-key-contract.py --check  # CI gate (same exit code)
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# ── Gate-discovered findings ─────────────────────────────────────────────────
# Defects present at HEAD that match an audited defect CLASS but appear in no
# audit ledger — they were found by building this gate. Filing them under a
# neighbouring AF/WB id would misstate that finding's scope (WB-60's file
# list names only Calendar/Photos), and omitting the id would launder a real
# defect through the allowlist as narrative. So they carry first-class ids
# here, disclosed for adoption into tasks/remediation/findings.json by the
# wave that owns that file (this gate must not edit it).
GATE_FINDINGS: dict[str, str] = {
    "KC-001": (
        "AF-064-class Chroma key drift, new instance: ingest_file's AI path "
        "writes 'keywords_json' (ingestion.py:1903) but retrieval reads "
        "'keywords' (query_agent.py:115); only triage.py writes the key "
        "retrieval reads, so AI-extracted keywords never reach retrieval."
    ),
    "KC-002": (
        "WB-60-class non-string metadata, new instance: apple_reminders.ts:189 "
        "(pre-fix line) sent completed (boolean — interface field) into "
        "StructuredIngestRequest.metadata: dict[str, str], so every reminder "
        "422'd exactly as WB-60 documents for Calendar/Photos. WB-60's own "
        "file list does not include apple_reminders.ts; this is a distinct "
        "finding, not WB-60. FIXED 2026-08-12 (coerced to '1'/'0'); id kept "
        "so history parses."
    ),
    "KC-003": (
        "WB-60-class non-string metadata, new instance: apple_reminders.ts:190 "
        "(pre-fix line) sent priority (number); same channel and failure mode "
        "as KC-002. FIXED 2026-08-12 (String(priority)); id kept so history "
        "parses."
    ),
}

# ── Channel scope: chroma keys whose verified consumer is NOT retrieval ──────
# The chroma-metadata channel audits ONE consumer — query_agent's
# ``metadata.get`` reads. The keys below are written into chunk metadata for
# a different, verified consumer (a Chroma where-filter, an artifact/memory
# surface, a background job) or as a deliberate write-only provenance stamp.
# They are channel-scope declarations, not defects, so they do not belong in
# the ALLOWLIST (every entry there must cite a finding id). Each entry names
# the verified consumer; an entry whose key is no longer written anywhere is
# reported stale, same as a stale allowlist entry.
CHROMA_OUT_OF_CHANNEL: dict[str, str] = {
    "recategorized_at": (
        "write-only provenance stamp on recategorize (artifacts.py, AF-064's "
        "writer); no reader by design"
    ),
    "domain": (
        "consumed as a Chroma where-filter (query_agent.py:105,915 scope "
        "clauses), not via metadata.get"
    ),
    "tenant_id": (
        "consumed as a Chroma where-filter (core tenancy scope), not via "
        "metadata.get"
    ),
    "summary": "read by artifact surfaces (routers/artifacts.py:260)",
    "ai_categorized": "categorization provenance; read by artifact surfaces",
    "categorize_mode": "categorization provenance; read by artifact surfaces",
    "memory_source_type": (
        "verified-memory provenance (verified_memory.py); read by memory "
        "surfaces"
    ),
    "decay_anchor": "memory-decay input; read by the decay job",
    "pack_version": (
        "knowledge-pack provenance (knowledge_packs.py Phase-8a); read by "
        "pack admin surfaces"
    ),
    "pack_license_spdx": "knowledge-pack provenance; see pack_version",
    "pack_license_category": "knowledge-pack provenance; see pack_version",
    "pack_source_url": "knowledge-pack provenance; see pack_version",
    "pack_curator": "knowledge-pack provenance; see pack_version",
    "pack_adapter": "knowledge-pack provenance; see pack_version",
    "pack_sha256": "knowledge-pack provenance; see pack_version",
    "pack_retrieved_at": "knowledge-pack provenance; see pack_version",
    "client_source": (
        "caller provenance (X-Client-ID / connector name); read back at "
        "ingest time and by artifact surfaces (ingestion.py:1040)"
    ),
    "updated_at": (
        "frontmatter timestamp override (ingestion.py:1081); provenance for "
        "artifact surfaces"
    ),
    "source_type": (
        "source-tier input read back at ingest time (ingestion.py:693,1184 "
        "quality floors)"
    ),
    "file_type": "triage structural metadata; read by artifact surfaces",
    "page_count": "triage structural metadata; read by artifact surfaces",
}

# ── Allowlist ────────────────────────────────────────────────────────────────
# code → one-line reason. Codes:
#   "<channel>|read-unwritten|<key>"            (suppresses an ERROR)
#   "<channel>|written-unread|<key>"            (suppresses a WARNING)
#   "<channel>|non-string|<file>:<key>"         (suppresses an ERROR)
#   "<channel>|unresolvable|<file>:<expr>"      (suppresses an ERROR)
# Every reason MUST cite a finding id: AF-nnn / WB-nn (audit ledgers) or
# KC-nnn (GATE_FINDINGS above). Enforced at runtime by main().
ALLOWLIST: dict[str, str] = {
    # AF-064's "tags" entry removed 2026-08-12: recategorize now builds
    # tags_json (artifacts.py), the key retrieval actually reads.
    # ── chroma-metadata (gate-discovered KC-001) ─────────────────────────
    "chroma-metadata|written-unread|keywords_json": (
        "KC-001 (gate-discovered, see GATE_FINDINGS): ingest_file's AI path "
        "writes 'keywords_json' but retrieval reads 'keywords'. Fix the "
        "writer, then drop this entry."
    ),
    # ── structured-ingest-metadata (WB-60) ───────────────────────────────
    # All WB-60 / KC-002 / KC-003 entries removed 2026-08-12: the connectors
    # now coerce every metadata value to a string (apple_calendar.ts,
    # apple_photos.ts, apple_reminders.ts), so the gate fails if the defect
    # class returns.
    "structured-ingest-metadata|non-string|apple_notes.ts:modified_at": (
        "Gate false positive, not WB-60: apple_notes.ts declares two "
        "interfaces that both have a modified_at field — AppleNote."
        "modified_at: string (line 47, what ingestAppleNotes' `note` "
        "parameter actually is) and NoteRow.modified_at: number | null "
        "(line 234, the raw DB row before coreDataToIso() converts it to an "
        "ISO string at line 339). _interface_field_types keys its map by "
        "field name only, not per-interface, so it resolves note."
        "modified_at against NoteRow's numeric type instead of AppleNote's "
        "string type. At the actual write site (line 471) the value is "
        "genuinely a string. Fixing this for real needs interface-scoped "
        "field-type resolution in the gate, out of scope here."
    ),
}

# Keys that reach the chroma-metadata channel only through dynamic flow
# (``base_meta.update(metadata)``) — origin verified by reading the caller,
# not extractable as a literal write in the producer files below.
CHROMA_DYNAMIC_PRODUCER_KEYS: dict[str, str] = {
    # Add entries as "key": "verified origin file:line" if a new consumer
    # read is fed only through caller-supplied metadata.
    #
    # AF-057/AF-059 readers (2026-08-12): these per-chunk keys reach Chroma
    # through the pre_chunked path — parser/chunker dict literals merged
    # over base_meta in ingestion.py's `merged[k] = _coerce_chroma_meta(v)`
    # loops, which this gate's literal extraction cannot see.
    "window_text": (
        "src/mcp/core/ingest/chunkers/sentence_window_strategy.py:105,125"
    ),
    "sheet_name": "src/mcp/core/ingest/parsers/xlsx_parser.py:131",
    "row_idx": (
        "src/mcp/core/ingest/parsers/csv_parser.py:95 + xlsx_parser.py:132"
    ),
    "column_headers": (
        "src/mcp/core/ingest/parsers/csv_parser.py:96 + xlsx_parser.py:133"
    ),
}


# ── Python AST extraction ────────────────────────────────────────────────────

def _dict_literal_keys(node: ast.Dict) -> set[str]:
    return {
        k.value
        for k in node.keys
        if isinstance(k, ast.Constant) and isinstance(k.value, str)
    }


def _dict_spread_names(node: ast.Dict) -> set[str]:
    """Names spread into a dict literal via ``{**name, ...}``."""
    names: set[str] = set()
    for k, v in zip(node.keys, node.values):
        if k is None and isinstance(v, ast.Name):
            names.add(v.id)
    return names


def extract_py_producer_keys(
    path: Path,
    names: frozenset[str] = frozenset(),
    subscript_targets: frozenset[tuple[str, str]] = frozenset(),
    metadatas_kwarg: bool = False,
    spread_of: frozenset[str] = frozenset(),
    status_success_dicts: bool = False,
) -> set[str]:
    """Literal keys written by the configured producer sites in one file.

    - ``names``: variables whose ``name["key"] = ...`` subscript writes and
      ``name = {...}`` dict-literal (re)bindings count.
    - ``subscript_targets``: ``(name, key)`` pairs — a dict literal assigned
      to ``name["key"]`` contributes its keys (triage's ``updates["metadata"]``).
    - ``metadatas_kwarg``: dict literals anywhere inside a ``metadatas=``
      call keyword (the direct Chroma ``collection.add`` shape).
    - ``spread_of``: dict literals containing ``**name`` for one of these
      names contribute their literal keys (the chunk-metadata comprehension).
    - ``status_success_dicts``: dict literals whose ``"status"`` key is the
      constant ``"success"`` contribute their keys (ingest_content's
      success-path return — the shape AF-015 is about).
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    keys: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.AnnAssign):
            # `base_provenance: dict[str, str] = {...}` — annotated binding
            if (
                isinstance(node.target, ast.Name)
                and node.target.id in names
                and isinstance(node.value, ast.Dict)
            ):
                keys |= _dict_literal_keys(node.value)
        elif isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Subscript) and isinstance(tgt.value, ast.Name):
                    sl = tgt.slice
                    if isinstance(sl, ast.Constant) and isinstance(sl.value, str):
                        if tgt.value.id in names:
                            keys.add(sl.value)
                        if (tgt.value.id, sl.value) in subscript_targets and isinstance(
                            node.value, ast.Dict
                        ):
                            keys |= _dict_literal_keys(node.value)
                elif (
                    isinstance(tgt, ast.Name)
                    and tgt.id in names
                    and isinstance(node.value, ast.Dict)
                ):
                    keys |= _dict_literal_keys(node.value)
        elif isinstance(node, ast.Dict):
            if spread_of & _dict_spread_names(node):
                keys |= _dict_literal_keys(node)
            if status_success_dicts:
                for k, v in zip(node.keys, node.values):
                    if (
                        isinstance(k, ast.Constant)
                        and k.value == "status"
                        and isinstance(v, ast.Constant)
                        and v.value == "success"
                    ):
                        keys |= _dict_literal_keys(node)
        elif metadatas_kwarg and isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "metadatas":
                    for sub in ast.walk(kw.value):
                        if isinstance(sub, ast.Dict):
                            keys |= _dict_literal_keys(sub)
    return keys


def extract_py_consumer_reads(
    path: Path, names: frozenset[str], subscript_loads: bool = True
) -> set[str]:
    """Literal keys read via ``name.get("key", ...)`` or ``name["key"]``."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    reads: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "get"
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id in names
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            reads.add(node.args[0].value)
        elif (
            subscript_loads
            and isinstance(node, ast.Subscript)
            and isinstance(node.ctx, ast.Load)
            and isinstance(node.value, ast.Name)
            and node.value.id in names
            and isinstance(node.slice, ast.Constant)
            and isinstance(node.slice.value, str)
        ):
            reads.add(node.slice.value)
    return reads


# ── TypeScript connector extraction (structured-ingest-metadata) ─────────────

# Exported interfaces only: those are the wire shapes the connectors send.
# Non-exported ones are raw DB-row types whose same-named fields (e.g.
# RawMessageRow.mailbox: number | null vs AppleMailMessage.mailbox: string)
# would poison the field-type map.
_IFACE_RE = re.compile(r"^export\s+interface\s+\w+\s*\{", re.M)
_FIELD_RE = re.compile(r"^\s*(\w+)\??:\s*(.+?)\s*$")
_PROP_RE = re.compile(r"^\s*(\w+):\s*(.+?),?\s*$")
_STR_LITERAL_RE = re.compile(r"""^(['"`]).*\1$""", re.S)
_MEMBER_RE = re.compile(r"^[\w.]+\.(\w+)$")
# Built-in properties that are numeric regardless of the receiver's declared
# type — `item.sizes.length` never appears in `field_types` (only `sizes`
# does), so without this the chained access degraded to unknown->WARNING and
# a numeric .length reached a dict[str, str] wire silently (see LIMITATIONS).
_TERMINAL_NUMERIC_PROPS = ("length", "size", "byteLength", "count")
_TERMINAL_NUMERIC_PROP_RE = re.compile(
    r"^[\w][\w.]*\.(?:" + "|".join(_TERMINAL_NUMERIC_PROPS) + r")$"
)
# A single top-level binary operator split. `-`, `*`, `/`, `%` coerce both
# operands to number in JS/TS, so any use is unambiguously numeric; `+` is
# ambiguous with string concatenation, so it only counts when at least one
# operand is a bare numeric literal. Guarded against any quote character so
# it never reclassifies genuine string concatenation (`'x-' + item.id`).
_BINOP_RE = re.compile(r"^(.+?)\s*([+\-*/%])\s*(.+)$")
_NUMERIC_LITERAL_RE = re.compile(r"^-?\d+(\.\d+)?$")


def _interface_field_types(src: str) -> dict[str, str]:
    """``fieldName -> declared type`` union across all interfaces in a file."""
    types: dict[str, str] = {}
    for m in _IFACE_RE.finditer(src):
        depth = 0
        opened = False
        for line in src[m.start():].splitlines():
            depth += line.count("{") - line.count("}")
            if depth > 0:
                opened = True
                code = line.split("//")[0].rstrip()
                fm = _FIELD_RE.match(code)
                if fm and "{" not in code:
                    types[fm.group(1)] = fm.group(2)
            elif opened:
                break
    return types


def _strip_comment(value: str) -> str:
    return value.split("//")[0].strip().rstrip(",")


def classify_ts_value(
    value: str,
    field_types: dict[str, str],
    consts: dict[str, str] | None = None,
    _depth: int = 0,
) -> str:
    """'string' | 'non-string' | 'unknown' for a metadata property value.

    ``consts`` maps local ``const`` names to their initializer expressions
    (built by ``_const_initializers``) so a value laundered through a local
    binding — ``display_name: displayName`` — classifies like its
    initializer instead of falling out as unknown.
    """
    v = _strip_comment(value)
    if _STR_LITERAL_RE.match(v):
        return "string"
    if re.match(r"^(String|JSON\.stringify)\(", v):
        return "string"
    if ".join(" in v:
        return "string"
    # ternary with two string-literal branches: x ? 'a' : 'b'
    tern = re.match(r"^.+\?\s*(['\"`].*['\"`])\s*:\s*(['\"`].*['\"`])$", v)
    if tern:
        return "string"
    # nullish fallback to a string literal: expr ?? ''
    nullish = re.match(r"^(.+?)\s*\?\?\s*(['\"`].*['\"`])$", v)
    if nullish:
        left = classify_ts_value(nullish.group(1), field_types, consts, _depth)
        # `string | null ?? ''` is a string; a non-string lhs stays broken
        return "string" if left in ("string", "unknown") else "non-string"
    if (
        re.match(r"^-?\d+(\.\d+)?$", v)
        or v in ("true", "false", "null")
        or v.startswith("[")
        or v.startswith("{")
    ):
        return "non-string"
    if _TERMINAL_NUMERIC_PROP_RE.match(v):
        return "non-string"
    if "'" not in v and '"' not in v and "`" not in v:
        binop = _BINOP_RE.match(v)
        if binop:
            op, left, right = binop.group(2), binop.group(1).strip(), binop.group(3).strip()
            if op in ("-", "*", "/", "%"):
                return "non-string"
            if op == "+" and (
                _NUMERIC_LITERAL_RE.match(left) or _NUMERIC_LITERAL_RE.match(right)
            ):
                return "non-string"
    if re.fullmatch(r"\w+", v) and consts and v in consts and _depth < 3:
        return classify_ts_value(consts[v], field_types, consts, _depth + 1)
    member = _MEMBER_RE.match(v)
    if member:
        t = field_types.get(member.group(1))
        if t is None:
            return "unknown"
        t = t.split("//")[0].strip()
        if re.search(r"\[\]|Array|number|boolean", t):
            # `string | null` without an array/number/boolean part is only
            # reached below; anything typed number/boolean/array cannot
            # serialize to a JSON string.
            return "non-string"
        if "string" in t:
            return "string"
        return "unknown"
    return "unknown"


_CONT_OP_RE = re.compile(r"(\?\?|\|\||&&|[+,(?:=])\s*$")


def _const_initializers(lines: list[str]) -> dict[str, str]:
    """``const name = <expr>`` initializers (non-object), continuations joined.

    Feeds identifier classification. Object-literal bindings are excluded —
    those are handled by ``_resolve_local_object``.
    """
    inits: dict[str, str] = {}
    decl = re.compile(r"^\s*(?:export\s+)?const\s+(\w+)(?:\s*:\s*[^=]+?)?\s*=\s*(.*)$")
    for i, raw in enumerate(lines):
        m = decl.match(raw.split("//")[0])
        if not m:
            continue
        expr = m.group(2).strip()
        if expr.startswith("{"):
            continue
        j = i
        while (expr == "" or _CONT_OP_RE.search(expr)) and j + 1 < len(lines) and j - i < 5:
            j += 1
            expr = (expr + " " + lines[j].split("//")[0].strip()).strip()
        inits[m.group(1)] = expr.rstrip(";").rstrip(",")
    return inits


_SPREAD_RE = re.compile(r"^\s*\.\.\.([\w.]+),?\s*$")
_SHORTHAND_RE = re.compile(r"^\s*(\w+),?\s*$")
_INLINE_PROP_RE = re.compile(r"(\w+):\s*('[^']*'|\"[^\"]*\"|`[^`]*`|[^,}]+)")
_INLINE_SPREAD_RE = re.compile(r"\.\.\.([\w.]+)")


def _collect_block_props(
    lines: list[str], open_idx: int
) -> tuple[list[tuple[str, str]], list[str], int]:
    """Props + spread names of the object literal opened at ``open_idx``.

    Returns (props, spread_names, last_line_index). Shorthand properties
    (``count,``) contribute ``(name, name)`` so the classifier sees them —
    a shorthand is just an identifier value, not a free pass.
    """
    depth = 1
    props: list[tuple[str, str]] = []
    spreads: list[str] = []
    j = open_idx + 1
    while j < len(lines) and depth > 0:
        code = lines[j].split("//")[0]
        at_top = depth == 1
        depth += code.count("{") - code.count("}")
        if depth <= 0:
            break
        if at_top:
            sm = _SPREAD_RE.match(code)
            shm = _SHORTHAND_RE.match(code)
            pm = _PROP_RE.match(code)
            if sm:
                spreads.append(sm.group(1))
            elif pm:
                props.append((pm.group(1), pm.group(2)))
            elif shm:
                props.append((shm.group(1), shm.group(1)))
        j += 1
    return props, spreads, j


def _parse_inline_object(content: str) -> tuple[list[tuple[str, str]], list[str]]:
    """Props + spread names of a single-line ``{ ... }`` literal body."""
    spreads = _INLINE_SPREAD_RE.findall(content)
    props = [
        (k, v.strip()) for k, v in _INLINE_PROP_RE.findall(content) if k != "metadata"
    ]
    return props, spreads


def _resolve_local_object(
    lines: list[str], name: str, _visited: frozenset[str] = frozenset()
) -> list[tuple[str, str]] | None:
    """Resolve a local identifier to object-literal props, or None.

    Handles ``const/let/var name = { ... }`` bindings (multi-line or inline),
    subsequent ``name.key = v`` / ``name['key'] = v`` augmentations, and
    spreads of further resolvable locals. Returns None — unresolvable — when
    the name is bound to anything but an object literal in this file (helper
    call, import, parameter): the laundering shapes the gate must not skip.
    """
    if name in _visited or "." in name:
        return None
    visited = _visited | {name}
    props: list[tuple[str, str]] = []
    spreads: list[str] = []
    found_literal = False
    bind = re.compile(
        rf"^\s*(?:export\s+)?(?:const|let|var)\s+{re.escape(name)}"
        rf"(?:\s*:\s*[^=]+?)?\s*=\s*(.*)$"
    )
    aug = re.compile(
        rf"^\s*{re.escape(name)}(?:\.(\w+)|\[['\"](\w+)['\"]\])\s*=\s*(.*)$"
    )
    for i, raw in enumerate(lines):
        code = raw.split("//")[0]
        bm = bind.match(code)
        if bm:
            rest = bm.group(1).strip().rstrip(";").rstrip(",")
            if rest == "{":
                found_literal = True
                blk_props, blk_spreads, _ = _collect_block_props(lines, i)
                props += blk_props
                spreads += blk_spreads
            elif rest.startswith("{") and rest.endswith("}"):
                found_literal = True
                blk_props, blk_spreads = _parse_inline_object(rest[1:-1])
                props += blk_props
                spreads += blk_spreads
            else:
                return None
            continue
        am = aug.match(code)
        if am:
            props.append(
                (am.group(1) or am.group(2), am.group(3).strip().rstrip(";"))
            )
    if not found_literal:
        return None
    for spread in spreads:
        sub = _resolve_local_object(lines, spread, visited)
        if sub is None:
            return None
        props += sub
    return props


def _match_brace(s: str) -> int:
    """Index of the ``}`` matching the ``{`` at s[0], or -1 if unclosed."""
    depth = 0
    for idx, ch in enumerate(s):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return idx
    return -1


def extract_ts_metadata_payloads(
    src: str,
) -> tuple[list[tuple[str, str]], list[str]]:
    """(props, unresolvable-expressions) across every ``postStructured`` call.

    Unlike the pre-review extractor — which only matched a literal
    ``metadata: {`` and therefore silently skipped ``metadata: md`` built in
    a local variable or helper (bypass reproduced live by the 2026-08-11
    adversarial review) — this walks each call's argument span and accounts
    for the metadata property in every form: inline literal, multi-line
    literal, local-variable indirection, shorthand, and spreads. Whatever it
    cannot resolve to an in-file object literal is returned as unresolvable
    for the caller to report as an ERROR.
    """
    lines = src.splitlines()
    props: list[tuple[str, str]] = []
    unresolved: list[str] = []

    def resolve_ident(name: str) -> None:
        resolved = _resolve_local_object(lines, name)
        if resolved is None:
            unresolved.append(name)
        else:
            props.extend(resolved)

    i = 0
    while i < len(lines):
        line = lines[i]
        call = re.search(r"(?<![.\w])postStructured\s*\(", line)
        if not call or re.search(r"\bfunction\s+postStructured\b", line):
            i += 1
            continue
        # Walk the call's argument span by paren depth (capped).
        depth = 0
        j = i
        end = min(i + 120, len(lines))
        while j < end:
            seg = line[call.start():] if j == i else lines[j]
            code = seg.split("//")[0]
            mm = re.search(r"\bmetadata\s*:\s*(.*)$", code)
            shorthand = re.search(r"[{,]\s*metadata\s*[,}]", code) or re.match(
                r"^\s*metadata\s*,?\s*$", code
            )
            if mm:
                rest = mm.group(1).strip()
                if rest.startswith("{"):
                    k = _match_brace(rest)
                    if k >= 0:
                        in_props, in_spreads = _parse_inline_object(rest[1:k])
                        props += in_props
                        for name in in_spreads:
                            resolve_ident(name)
                    else:
                        blk_props, blk_spreads, last = _collect_block_props(
                            lines, j
                        )
                        props += blk_props
                        for name in blk_spreads:
                            resolve_ident(name)
                        j = last
                else:
                    im = re.match(r"^(\w+)\s*(?:[,)}\]]|$)", rest)
                    if im:
                        resolve_ident(im.group(1))
                    else:
                        unresolved.append(rest.rstrip(",").rstrip()[:60])
            elif shorthand:
                resolve_ident("metadata")
            depth += code.count("(") - code.count(")")
            if depth <= 0:
                break
            j += 1
        i = max(j, i) + 1
    return props, unresolved


# ── Channel runners ──────────────────────────────────────────────────────────

class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.allowlisted = 0
        self.used_codes: set[str] = set()
        self.used_out_of_channel: set[str] = set()

    def violation(self, code: str, message: str, fail: bool) -> None:
        if code in ALLOWLIST:
            self.allowlisted += 1
            self.used_codes.add(code)
            return
        (self.errors if fail else self.warnings).append(f"{code}\n    {message}")


def run_chroma_metadata(report: Report, root: Path) -> None:
    channel = "chroma-metadata"
    ingestion = root / "src/mcp/app/services/ingestion.py"
    artifacts = root / "src/mcp/app/routers/artifacts.py"
    verified = root / "src/mcp/core/agents/verified_memory.py"
    packs = root / "src/mcp/app/services/knowledge_packs.py"
    triage = root / "src/mcp/app/agents/triage.py"
    query_agent = root / "src/mcp/core/agents/query_agent.py"

    written: set[str] = set()
    written |= extract_py_producer_keys(
        ingestion,
        names=frozenset({"base_meta", "merged", "meta"}),
        spread_of=frozenset({"base_meta"}),
    )
    written |= extract_py_producer_keys(artifacts, names=frozenset({"meta"}))
    written |= extract_py_producer_keys(verified, metadatas_kwarg=True)
    written |= extract_py_producer_keys(packs, names=frozenset({"base_provenance"}))
    written |= extract_py_producer_keys(
        triage,
        names=frozenset({"meta", "merged"}),
        subscript_targets=frozenset({("updates", "metadata")}),
    )
    written |= set(CHROMA_DYNAMIC_PRODUCER_KEYS)

    # The audited consumer: retrieval's metadata.get reads. Subscript loads
    # excluded — query_agent has same-named dicts that are not Chroma
    # metadata (the endorsement map), and .get is the pattern that channel
    # actually uses.
    read = extract_py_consumer_reads(
        query_agent, names=frozenset({"metadata"}), subscript_loads=False
    )

    for key in sorted(read - written):
        report.violation(
            f"{channel}|read-unwritten|{key}",
            f"query_agent.py reads Chroma metadata '{key}' but no audited "
            f"producer writes it — retrieval sees the default forever.",
            fail=True,
        )
    for key in sorted(written - read):
        if key in CHROMA_OUT_OF_CHANNEL:
            report.used_out_of_channel.add(key)
            continue
        report.violation(
            f"{channel}|written-unread|{key}",
            f"Chroma metadata '{key}' is written but the retrieval consumer "
            f"never reads it — possible AF-064-class key drift.",
            fail=False,
        )


def run_ingest_result(report: Report, root: Path) -> None:
    channel = "ingest-result"
    ingestion = root / "src/mcp/app/services/ingestion.py"
    scanner = root / "src/mcp/app/services/folder_scanner.py"

    written = extract_py_producer_keys(
        ingestion, names=frozenset({"result"}), status_success_dicts=True
    )
    read = extract_py_consumer_reads(scanner, names=frozenset({"result"}))

    for key in sorted(read - written):
        report.violation(
            f"{channel}|read-unwritten|{key}",
            f"folder_scanner.py reads '{key}' from ingest_content's result; "
            f"the success dict never emits it (AF-015 class).",
            fail=True,
        )
    # No written-unread direction: ingest_content has many consumers beyond
    # the scanner; unread-by-scanner is not a signal.


def run_structured_ingest(report: Report, root: Path) -> None:
    channel = "structured-ingest-metadata"
    ingestion_router = root / "src/mcp/app/routers/ingestion.py"
    connectors = root / "packages/desktop/src/main/connectors"

    router_src = ingestion_router.read_text(encoding="utf-8")
    m = re.search(
        r"class StructuredIngestRequest\(BaseModel\):(.*?)(?:\nclass |\Z)",
        router_src,
        re.S,
    )
    block = m.group(1) if m else ""
    if not re.search(r"metadata:\s*dict\[str,\s*str\]", block):
        print(
            "[key-contract] note: StructuredIngestRequest.metadata is no "
            "longer dict[str, str]; string-shape checks skipped."
        )
        return

    # rglob, not glob: a connector added one directory deeper must not
    # silently evade the scan (reviewer-planted bypass, 2026-08-11). And no
    # "metadata: {" pre-filter: requiring the literal shape is what let a
    # laundered `metadata: md` skip the file entirely (second reviewer
    # bypass, same day).
    for ts in sorted(connectors.rglob("*.ts")):
        src = ts.read_text(encoding="utf-8")
        if "postStructured" not in src:
            continue
        types = _interface_field_types(src)
        consts = _const_initializers(src.splitlines())
        payload_props, unresolved = extract_ts_metadata_payloads(src)
        for expr in unresolved:
            report.violation(
                f"{channel}|unresolvable|{ts.name}:{expr}",
                f"{ts.name} passes metadata built from '{expr}', which does "
                f"not resolve to an object literal in this file — the gate "
                f"cannot verify the dict[str, str] contract. Inline the "
                f"literal or allowlist with a finding id.",
                fail=True,
            )
        for key, value in payload_props:
            kind = classify_ts_value(value, types, consts)
            if kind == "non-string":
                report.violation(
                    f"{channel}|non-string|{ts.name}:{key}",
                    f"{ts.name} sends metadata['{key}'] = {value.strip()} — "
                    f"not a string; StructuredIngestRequest.metadata is "
                    f"dict[str, str], so FastAPI 422s the whole item (WB-60).",
                    fail=True,
                )
            elif kind == "unknown":
                report.violation(
                    f"{channel}|non-string|{ts.name}:{key}",
                    f"{ts.name} metadata['{key}'] = {value.strip()} — could "
                    f"not classify; verify it serializes to a string.",
                    fail=False,
                )


# ── Entry point ──────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="CI gate mode")
    parser.add_argument(
        "--root", type=Path, default=REPO_ROOT, help="repo root (tests only)"
    )
    args = parser.parse_args(argv)

    report = Report()

    # Allowlist integrity: every entry must cite a finding id — an audit
    # ledger id (AF-nnn / WB-nn) or a KC-nnn defined in GATE_FINDINGS. This
    # is the bar the 2026-08-11 adversarial review found 26 entries short of.
    id_re = re.compile(r"\b(AF-\d+|WB-\d+|KC-\d+)\b")
    for code, reason in ALLOWLIST.items():
        m = id_re.search(reason)
        if not m:
            report.errors.append(
                f"allowlist-integrity\n    entry '{code}' cites no finding "
                f"id (AF-nnn / WB-nn / KC-nnn)."
            )
        elif m.group(1).startswith("KC-") and m.group(1) not in GATE_FINDINGS:
            report.errors.append(
                f"allowlist-integrity\n    entry '{code}' cites {m.group(1)} "
                f"which is not defined in GATE_FINDINGS."
            )

    run_chroma_metadata(report, args.root)
    run_ingest_result(report, args.root)
    run_structured_ingest(report, args.root)

    stale = sorted(set(ALLOWLIST) - report.used_codes)
    stale_scope = sorted(set(CHROMA_OUT_OF_CHANNEL) - report.used_out_of_channel)

    for w in report.warnings:
        print(f"[key-contract] WARN {w}")
    for s in stale:
        print(
            f"[key-contract] WARN stale allowlist entry (no longer matches "
            f"anything — remove it): {s}"
        )
    for s in stale_scope:
        print(
            f"[key-contract] WARN stale CHROMA_OUT_OF_CHANNEL entry (key no "
            f"longer written by any audited producer — remove it): {s}"
        )
    for e in report.errors:
        print(f"[key-contract] ERROR {e}")

    print(
        f"[key-contract] {len(report.errors)} error(s), "
        f"{len(report.warnings)} warning(s), "
        f"{report.allowlisted} allowlisted, {len(stale)} stale allowlist "
        f"entr{'y' if len(stale) == 1 else 'ies'}"
    )
    if report.errors:
        print("[key-contract] FAIL — a consumer and producer disagree on keys.")
        return 1
    print("[key-contract] OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
