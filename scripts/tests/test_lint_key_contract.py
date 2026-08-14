# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Red/green probes for scripts/lint-key-contract.py (Gate 5, audit §3).

A gate never seen red is not a gate: every failure mode this gate exists to
catch (AF-015 reads-unwritten, AF-064 written-unread key drift, WB-60
non-string values under a dict[str,str] contract) is planted here against a
synthetic tree and asserted to exit 1. The green cases pin the shapes it must
NOT flag — including the allowlist doing its one job of keeping HEAD green
while the audited defects are still in the tree.

TestLaundering pins the 2026-08-11 adversarial-review bypasses closed: a
metadata object built in a local variable, a helper call, a shorthand
property, and a spread must all be either resolved and classified or
reported as an unresolvable ERROR — never silently skipped.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_key_contract", _ROOT / "scripts" / "lint-key-contract.py"
)
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_key_contract"] = lint
_SPEC.loader.exec_module(lint)


GREEN_CONNECTOR = """\
export interface Item {
  id: string
  title: string
  sizes: number[]
}

declare function postStructured(b: string, k: string, body: unknown): Promise<{ ok: boolean }>

export async function ingestItem(item: Item, base: string): Promise<void> {
  await postStructured(base, 'probe', {
    content: item.title,
    metadata: {
      source: 'probe',
      title: item.title,
      joined: item.sizes.join(','),
      stringified: String(item.id),
    },
  })
}
"""

ROUTER_STRICT = """\
from pydantic import BaseModel, Field


class StructuredIngestRequest(BaseModel):
    content: str
    metadata: dict[str, str] = Field(default_factory=dict)


class Other(BaseModel):
    pass
"""


def make_tree(
    tmp_path: Path,
    ingestion: str = 'base_meta = {"domain": d, "artifact_id": a}\n'
    'result = {"status": "success", "artifact_id": a}\n',
    query_agent: str = 'x = metadata.get("artifact_id", "")\n',
    folder_scanner: str = 'aid = result.get("artifact_id", "")\n',
    router: str = ROUTER_STRICT,
    connector: str = GREEN_CONNECTOR,
    artifacts: str = "",
    verified_memory: str = "",
    knowledge_packs: str = "",
    triage: str = "",
) -> Path:
    files = {
        "src/mcp/app/services/ingestion.py": ingestion,
        "src/mcp/core/agents/query_agent.py": query_agent,
        "src/mcp/app/services/folder_scanner.py": folder_scanner,
        "src/mcp/app/routers/ingestion.py": router,
        "src/mcp/app/routers/artifacts.py": artifacts,
        "src/mcp/core/agents/verified_memory.py": verified_memory,
        "src/mcp/app/services/knowledge_packs.py": knowledge_packs,
        "src/mcp/app/agents/triage.py": triage,
        "packages/desktop/src/main/connectors/probe.ts": connector,
    }
    for rel, content in files.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
    return tmp_path


def run(root: Path) -> int:
    return lint.main(["--check", "--root", str(root)])


class TestGreen:
    def test_matched_contract_passes(self, tmp_path):
        assert run(make_tree(tmp_path)) == 0

    def test_relaxed_server_contract_skips_shape_check(self, tmp_path, capsys):
        """If metadata is no longer dict[str, str], non-string values are legal."""
        bad_connector = GREEN_CONNECTOR.replace(
            "stringified: String(item.id),", "sizes: item.sizes,"
        )
        relaxed = ROUTER_STRICT.replace("dict[str, str]", "dict[str, object]")
        root = make_tree(tmp_path, router=relaxed, connector=bad_connector)
        assert run(root) == 0
        assert "no longer dict[str, str]" in capsys.readouterr().out

    def test_drained_af015_keys_now_fail(self, tmp_path):
        """AF-015 was fixed and its allowlist entries drained (2026-08-12), so
        its reads-unwritten shape must now fail like any other new violation —
        the drain is load-bearing: re-introducing the defect goes red."""
        root = make_tree(
            tmp_path,
            folder_scanner='q = result.get("quality_score", 0.0)\n'
            'd = result.get("duplicate")\n',
        )
        assert run(root) == 1

    def test_written_unread_is_warn_not_fail(self, tmp_path, capsys):
        root = make_tree(
            tmp_path,
            ingestion='base_meta = {"domain": d, "artifact_id": a,'
            ' "gate5_unread_probe": 1}\n'
            'result = {"status": "success", "artifact_id": a}\n',
        )
        assert run(root) == 0
        out = capsys.readouterr().out
        assert "WARN" in out and "gate5_unread_probe" in out


class TestRed:
    """Each must exit 1 — the planted fault classes the audits documented."""

    def test_consumer_reads_unwritten_chroma_key(self, tmp_path):
        root = make_tree(
            tmp_path,
            query_agent='x = metadata.get("artifact_id", "")\n'
            'y = metadata.get("gate5_never_written", "")\n',
        )
        assert run(root) == 1

    def test_scanner_reads_unwritten_result_key(self, tmp_path):
        """AF-015's shape with a non-allowlisted key name."""
        root = make_tree(
            tmp_path,
            folder_scanner='v = result.get("gate5_phantom_key", 0.0)\n',
        )
        assert run(root) == 1

    def test_connector_sends_numeric_literal(self, tmp_path):
        bad = GREEN_CONNECTOR.replace("source: 'probe',", "source: 'probe',\n      count: 42,")
        assert run(make_tree(tmp_path, connector=bad)) == 1

    def test_connector_sends_boolean_literal(self, tmp_path):
        bad = GREEN_CONNECTOR.replace("source: 'probe',", "source: 'probe',\n      flagged: true,")
        assert run(make_tree(tmp_path, connector=bad)) == 1

    def test_connector_sends_array_typed_member(self, tmp_path):
        """WB-60's exact shape: a member whose exported interface type is an
        array (Calendar's attendees: string[])."""
        bad = GREEN_CONNECTOR.replace(
            "joined: item.sizes.join(','),", "sizes: item.sizes,"
        )
        assert run(make_tree(tmp_path, connector=bad)) == 1

    def test_connector_in_subdirectory_is_scanned(self, tmp_path):
        """The scan is recursive: a bad connector one directory deeper must
        still go red (the reviewer's planted glob-bypass, 2026-08-11)."""
        root = make_tree(tmp_path)
        bad = GREEN_CONNECTOR.replace(
            "source: 'probe',", "source: 'probe',\n      count: 42,"
        )
        nested = root / "packages/desktop/src/main/connectors/subdir/nested.ts"
        nested.parent.mkdir(parents=True)
        nested.write_text(bad, encoding="utf-8")
        assert run(root) == 1

    def test_success_dict_only_not_all_return_paths(self, tmp_path):
        """A key present only in a non-success return dict must NOT count as
        produced — that union-masking is how AF-015 hid from a naive check."""
        root = make_tree(
            tmp_path,
            ingestion='base_meta = {"domain": d, "artifact_id": a}\n'
            'result = {"status": "success", "artifact_id": a}\n'
            'dropped = {"status": "dropped", "gate5_dropped_only": 1.0}\n',
            folder_scanner='v = result.get("gate5_dropped_only", 0.0)\n',
        )
        assert run(root) == 1


LAUNDER_BASE = """\
export interface Item {
  id: string
  title: string
  sizes: number[]
}

declare function postStructured(b: string, k: string, body: unknown): Promise<{ ok: boolean }>

export async function ingestItem(item: Item, base: string): Promise<void> {
%s
  await postStructured(base, 'probe', {
    content: item.title,
%s
  })
}
"""


class TestLaundering:
    """The 2026-08-11 adversarial review reproduced a live bypass: building
    the metadata object outside the literal ``metadata: {`` shape evaded the
    scanner entirely. Every indirection form must now be red when it hides a
    non-string, green when it does not, and red-unresolvable when the gate
    cannot see the object at all."""

    def test_local_variable_non_string_is_red(self, tmp_path):
        """The reviewer's exact reproduced bypass (plantfault_launder.ts)."""
        bad = LAUNDER_BASE % (
            "  const md = {\n    source: 'probe',\n    count: 42,\n  }",
            "    metadata: md,",
        )
        assert run(make_tree(tmp_path, connector=bad)) == 1

    def test_local_variable_all_strings_is_green(self, tmp_path):
        ok = LAUNDER_BASE % (
            "  const md = {\n    source: 'probe',\n    title: item.title,\n  }",
            "    metadata: md,",
        )
        assert run(make_tree(tmp_path, connector=ok)) == 0

    def test_inline_local_variable_non_string_is_red(self, tmp_path):
        bad = LAUNDER_BASE % (
            "  const md = { source: 'probe', count: 42 }",
            "    metadata: md,",
        )
        assert run(make_tree(tmp_path, connector=bad)) == 1

    def test_augmented_local_variable_is_red(self, tmp_path):
        """Empty literal then ``md.count = 42`` — augmentation counts."""
        bad = LAUNDER_BASE % (
            "  const md = { source: 'probe' }\n  md.count = 42",
            "    metadata: md,",
        )
        assert run(make_tree(tmp_path, connector=bad)) == 1

    def test_helper_call_is_unresolvable_red(self, tmp_path, capsys):
        """A same-file helper building the object is the 'more natural'
        bypass the review called out — must be an ERROR, not a skip."""
        bad = LAUNDER_BASE % (
            "  function buildMeta(i: Item) {\n"
            "    return { source: 'probe', count: i.sizes.length }\n  }",
            "    metadata: buildMeta(item),",
        )
        assert run(make_tree(tmp_path, connector=bad)) == 1
        assert "unresolvable" in capsys.readouterr().out

    def test_shorthand_property_is_red(self, tmp_path):
        bad = LAUNDER_BASE % (
            "  const metadata = { source: 'probe', count: 42 }",
            "    metadata,",
        )
        assert run(make_tree(tmp_path, connector=bad)) == 1

    def test_spread_of_local_non_string_is_red(self, tmp_path):
        bad = LAUNDER_BASE % (
            "  const extra = { count: 42 }",
            "    metadata: {\n      source: 'probe',\n      ...extra,\n    },",
        )
        assert run(make_tree(tmp_path, connector=bad)) == 1

    def test_spread_of_unresolvable_is_red(self, tmp_path, capsys):
        bad = LAUNDER_BASE % (
            "",
            "    metadata: {\n      source: 'probe',\n      ...item.title,\n    },",
        )
        assert run(make_tree(tmp_path, connector=bad)) == 1
        assert "unresolvable" in capsys.readouterr().out

    def test_string_const_chain_value_is_green(self, tmp_path):
        """imessage's displayName shape: a const whose initializer chain ends
        in string literals classifies as string — no allowlist entry needed."""
        ok = LAUNDER_BASE % (
            "  const label =\n"
            "    item.title ?? item.sizes.join(', ') ?? `Item ${item.id}`",
            "    metadata: {\n      source: 'probe',\n      label: label,\n    },",
        )
        assert run(make_tree(tmp_path, connector=ok)) == 0


class TestAllowlistIntegrity:
    def test_every_entry_cites_a_finding_id(self):
        """The adversarial review found 26 entries carrying narrative instead
        of a finding id. The bar: AF-nnn / WB-nn (audit ledgers) or KC-nnn
        (declared gate-discovered findings)."""
        import re as _re

        for code, reason in lint.ALLOWLIST.items():
            m = _re.search(r"\b(AF-\d+|WB-\d+|KC-\d+)\b", reason)
            assert m, f"allowlist entry {code!r} cites no finding id"
            if m.group(1).startswith("KC-"):
                assert m.group(1) in lint.GATE_FINDINGS, (
                    f"{code!r} cites undeclared {m.group(1)}"
                )

    def test_gate_findings_carry_evidence(self):
        for fid, text in lint.GATE_FINDINGS.items():
            assert ".ts:" in text or ".py:" in text, (
                f"{fid} lacks file:line evidence"
            )

    def test_missing_id_fails_the_gate(self, tmp_path, monkeypatch):
        monkeypatch.setitem(
            lint.ALLOWLIST, "chroma-metadata|written-unread|bogus", "no id here"
        )
        assert run(make_tree(tmp_path)) == 1


class TestClassifier:
    def test_string_shapes(self):
        types = {"title": "string", "when": "string | null"}
        assert lint.classify_ts_value("'lit'", types) == "string"
        assert lint.classify_ts_value("`tpl ${x}`", types) == "string"
        assert lint.classify_ts_value("String(n)", types) == "string"
        assert lint.classify_ts_value("xs.join(',')", types) == "string"
        assert lint.classify_ts_value("e.title", types) == "string"
        assert lint.classify_ts_value("e.when ?? ''", types) == "string"
        assert lint.classify_ts_value("x ? '1' : '0'", types) == "string"

    def test_non_string_shapes(self):
        types = {"n": "number", "b": "boolean", "xs": "string[]", "lat": "number | null"}
        assert lint.classify_ts_value("42", types) == "non-string"
        assert lint.classify_ts_value("true", types) == "non-string"
        assert lint.classify_ts_value("null", types) == "non-string"
        assert lint.classify_ts_value("[1, 2]", types) == "non-string"
        assert lint.classify_ts_value("e.n", types) == "non-string"
        assert lint.classify_ts_value("e.b", types) == "non-string"
        assert lint.classify_ts_value("e.xs", types) == "non-string"
        assert lint.classify_ts_value("e.lat", types) == "non-string"

    def test_unknown_is_not_an_error_class(self):
        assert lint.classify_ts_value("displayName", {}) == "unknown"

    def test_terminal_numeric_properties_are_non_string(self):
        """A chained member ending in a well-known numeric builtin classifies
        as non-string even though the property itself (`length`, `size`,
        `byteLength`, `count`) is not in the interface's field-type map —
        only the receiver (`sizes`) is. Before this fix these degraded to
        unknown->WARNING, so `count: item.sizes.length` reached the
        dict[str, str] wire silently."""
        types = {"sizes": "number[]"}
        assert lint.classify_ts_value("item.sizes.length", types) == "non-string"
        assert lint.classify_ts_value("buf.byteLength", types) == "non-string"
        assert lint.classify_ts_value("xs.size", types) == "non-string"
        assert lint.classify_ts_value("arr.count", types) == "non-string"
        # not a false positive on a receiver that merely ends in the word
        assert lint.classify_ts_value("item.sizes", types) == "non-string"  # via type map, not the terminal-prop path
        assert lint.classify_ts_value("item.title", types) == "unknown"

    def test_arithmetic_binary_expressions_are_non_string(self):
        """`-`, `*`, `/`, `%` coerce to number unconditionally; `+` only
        counts when a bare numeric literal is on one side, so genuine string
        concatenation (`'x-' + item.id`) is left as unknown, not misfired
        into non-string."""
        types = {"n": "number"}
        assert lint.classify_ts_value("item.sizes.length + 1", types) == "non-string"
        assert lint.classify_ts_value("n - 1", types) == "non-string"
        assert lint.classify_ts_value("a * b", types) == "non-string"
        assert lint.classify_ts_value("total / count", types) == "non-string"
        assert lint.classify_ts_value("a % b", types) == "non-string"
        assert lint.classify_ts_value("'x-' + item.id", types) == "unknown"


class TestHead:
    def test_real_repo_is_green(self, capsys):
        """The seeded allowlist must keep HEAD green; if a seeded finding got
        fixed, its entry goes stale (warn) — still green by design."""
        assert lint.main(["--check", "--root", str(_ROOT)]) == 0
