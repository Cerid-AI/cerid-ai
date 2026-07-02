# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Phase 2b slice 6 — external-fetch boundary guard.

Locks the slice-1 hardening so a future edit can't silently revert it: the two
attacker/user-influenceable fetches must stay on the SSRF-guarded ``guarded_get``,
and the SourceConnector RSS parser must stay on the defusedxml wrapper.

This is a regression fence (fails CI on revert). A broader *preventive* gate —
an AST lint forbidding any NEW raw external ``httpx`` fetch outside the hardened
layer, with an allowlist for curated fixed-endpoint sites — is tracked as a
Phase 2b follow-on (needs the ~13 follow_redirects=True sites triaged +
gates.yaml/Makefile/ci.yml wiring under the gates-parity contract).
"""
import ast
import pathlib

_MCP = pathlib.Path(__file__).resolve().parents[1]  # src/mcp


def _find_function(tree: ast.AST, name: str) -> ast.AST | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef | ast.FunctionDef) and node.name == name:
            return node
    return None


def _instantiates_raw_httpx_client(fn: ast.AST) -> bool:
    """True if the function body constructs httpx.AsyncClient/Client or calls a
    top-level httpx.get/post/stream/request (i.e. bypasses guarded_get)."""
    raw_ctors = {"AsyncClient", "Client"}
    raw_methods = {"get", "post", "stream", "request", "put", "delete"}
    for node in ast.walk(fn):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            val = node.func.value
            if isinstance(val, ast.Name) and val.id == "httpx" and (
                node.func.attr in raw_ctors or node.func.attr in raw_methods
            ):
                return True
    return False


def _calls_name(fn: ast.AST, name: str) -> bool:
    for node in ast.walk(fn):
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == name:
                return True
            if isinstance(f, ast.Attribute) and f.attr == name:
                return True
    return False


def _parse(rel: str) -> ast.AST:
    return ast.parse((_MCP / rel).read_text())


class TestCitedUrlFetchGuarded:
    _REL = "core/agents/hallucination/verification.py"

    def test_uses_guarded_get_not_raw_httpx(self):
        fn = _find_function(_parse(self._REL), "_verify_against_cited_url")
        assert fn is not None, "_verify_against_cited_url not found"
        assert _calls_name(fn, "guarded_get"), "cited-URL fetch must use guarded_get"
        assert not _instantiates_raw_httpx_client(fn), (
            "cited-URL fetch must NOT construct a raw httpx client (SSRF regression)"
        )


class TestPkbIngestUrlGuarded:
    _REL = "app/mcp_tools/batch.py"

    def test_uses_guarded_get_not_raw_httpx(self):
        fn = _find_function(_parse(self._REL), "pkb_ingest_url")
        assert fn is not None, "pkb_ingest_url not found"
        assert _calls_name(fn, "guarded_get"), "pkb_ingest_url must use guarded_get"
        assert not _instantiates_raw_httpx_client(fn), (
            "pkb_ingest_url must NOT construct a raw httpx client (SSRF regression)"
        )


class TestRssConnectorSafeParse:
    _REL = "core/ingest/sources/connectors/rss.py"

    def test_uses_safe_fromstring(self):
        fn = _find_function(_parse(self._REL), "_parse_feed")
        assert fn is not None
        assert _calls_name(fn, "safe_fromstring"), "_parse_feed must use safe_fromstring"

    def test_no_raw_et_fromstring(self):
        src = (_MCP / self._REL).read_text()
        assert "ET.fromstring(" not in src, (
            "connector RSS must parse via defusedxml safe_fromstring, not raw ET.fromstring"
        )


class TestHtmlScrapeFetchGuarded:
    _REL = "core/knowledge/adapter_html_scrape.py"

    def test_uses_guarded_sync_not_raw_httpx(self):
        fn = _find_function(_parse(self._REL), "_httpx_text_get")
        assert fn is not None, "_httpx_text_get not found"
        assert _calls_name(fn, "guarded_get_sync"), (
            "operator-URL scrape fetch must use guarded_get_sync"
        )
        assert not _instantiates_raw_httpx_client(fn), (
            "html_scrape fetch must NOT construct a raw httpx client (SSRF regression)"
        )


class TestClipboardDaemonFetchGuarded:
    _REL = "scripts/clipboard_daemon.py"

    def test_uses_guarded_sync_not_raw_httpx(self):
        fn = _find_function(_parse(self._REL), "_detect_content_type")
        assert fn is not None, "_detect_content_type not found"
        assert _calls_name(fn, "guarded_get_sync"), (
            "clipboard URL fetch must use guarded_get_sync"
        )
        assert not _instantiates_raw_httpx_client(fn), (
            "clipboard fetch must NOT construct a raw httpx client (SSRF regression)"
        )


class TestGuardedGetIsHardened:
    def test_guarded_get_disables_autoredirect_and_validates(self):
        src = (_MCP / "core/ingest/sources/safe_fetch.py").read_text()
        # The single hardened entry: no auto-redirect + per-hop SSRF revalidation.
        assert "follow_redirects=False" in src
        assert "assert_fetchable" in src

    def test_sync_variant_also_hardened(self):
        fn = _find_function(
            _parse("core/ingest/sources/safe_fetch.py"), "guarded_get_sync"
        )
        assert fn is not None, "guarded_get_sync not found"
        assert _calls_name(fn, "assert_fetchable"), (
            "guarded_get_sync must re-validate each hop via assert_fetchable"
        )
