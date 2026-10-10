# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The beta benchmark's grounded turn must be the web client's grounded turn.

``tests/beta/eval/grounding.py`` copies the client's preamble, auto-inject
defaults and ``<document>`` format, because the eval container mounts only
``tests/beta``. Until 2026-10-07 the benchmark sent the bare question and
scored that ungrounded answer against the retrieved context, which read as a
grounding defect in the product. These tests hold the copy to the TypeScript
it mirrors, so a change to the client's prompt fails here instead of quietly
making the benchmark measure something the product does not do.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_WEB = _ROOT / "src" / "web" / "src"


def _load_grounding():
    spec = importlib.util.spec_from_file_location(
        "beta_grounding", _ROOT / "tests" / "beta" / "eval" / "grounding.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["beta_grounding"] = module
    spec.loader.exec_module(module)
    return module


grounding = _load_grounding()


def _ts(rel: str) -> str:
    return (_WEB / rel).read_text()


def test_the_preamble_is_the_clients_byte_for_byte():
    source = _ts("lib/rag-prompt.ts")
    sentinel = re.search(r'KB_CONTEXT_SENTINEL = "([^"]+)"', source)
    body = re.search(r"`\$\{KB_CONTEXT_SENTINEL\}\\n` \+\s*`([^`]+)`", source)
    assert sentinel and body, "rag-prompt.ts no longer has the shape this test reads"
    assert grounding.KB_CONTEXT_SENTINEL == sentinel.group(1)
    assert grounding.RAG_SYSTEM_PREAMBLE == f"{sentinel.group(1)}\n{body.group(1)}"


def test_the_auto_inject_defaults_are_the_clients():
    cap = re.search(r"const DEFAULT_AUTO_INJECT_MAX = (\d+)", _ts("hooks/use-chat-send.ts"))
    floor = re.search(r'readFloat\("cerid-auto-inject-threshold", ([0-9.]+)\)', _ts("hooks/use-settings.ts"))
    dedup = re.search(r"export function deduplicateChunks\([^)]*threshold = ([0-9.]+)", _ts("lib/kb-utils.ts"), re.S)
    assert cap and floor and dedup, "a client default moved; re-read it before updating the copy"
    assert grounding.AUTO_INJECT_MAX == int(cap.group(1))
    assert grounding.AUTO_INJECT_THRESHOLD == float(floor.group(1))
    assert grounding.JACCARD_DEDUP_THRESHOLD == float(dedup.group(1))


def test_document_attributes_come_in_the_clients_order():
    formatter = _ts("lib/kb-utils.ts").split("export function formatChunkWithHeader", 1)[1].split("\n}\n", 1)[0]
    client_order = re.findall(r"attrs\.push\(`(\w+)=", formatter)
    rendered = grounding.format_document({
        "artifact_id": "a1", "domain": "finance", "sub_category": "retirement",
        "filename": "f.md", "chunk_index": 0, "relevance": 0.9, "source_type": "kb",
        "created_at": "2025-01-02T03:04:05Z", "content": "x",
    })
    assert re.findall(r' (\w+)="', rendered.splitlines()[0]) == client_order


def test_history_renders_as_the_client_renders_it():
    """A result with earlier versions carries them in a <history> block inside
    its <document>, one "- until <date>: <value>" line each (kb-utils.ts historyBlock)."""
    kb_utils = _ts("lib/kb-utils.ts")
    assert "<history>" in kb_utils and "- until ${" in kb_utils
    rendered = grounding.format_document({
        "content": "Office is on the 9th floor",
        "history": [{"value": "Office is on the 5th floor", "valid_from": "2025-06-01",
                     "valid_to": "2026-05-01T00:00:00Z"}],
    })
    assert rendered == ("<document>\nOffice is on the 9th floor\n<history>\n"
                        "- until 2026-05-01: Office is on the 5th floor\n</history>\n</document>")
