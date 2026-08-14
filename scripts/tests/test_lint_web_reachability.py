# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Red/green probes for scripts/lint-web-reachability.py (audit Gate 1).

The load-bearing case is M1 from tasks/2026-08-11-consolidated-audit.md: a
module imported ONLY by a test must still be a violation — orphaning code
raises coverage and must produce a red signal here. The green cases pin the
resolver features the real tree depends on (alias, barrels, lazy(), workers).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_web_reachability", _ROOT / "scripts" / "lint-web-reachability.py"
)
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_web_reachability"] = lint
_SPEC.loader.exec_module(lint)


def _tree(tmp_path: Path, files: dict[str, str]) -> Path:
    web_root = tmp_path / "web" / "src"
    for rel, content in files.items():
        f = web_root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return web_root


def _run(web_root: Path, allow_lines: list[str] | None = None) -> int:
    allow_file = web_root.parent / "allow.txt"
    if allow_lines is not None:
        allow_file.write_text("\n".join(allow_lines) + "\n", encoding="utf-8")
    return lint.main(
        ["--check", "--web-root", str(web_root), "--allow-file", str(allow_file)]
    )


class TestRedCases:
    """Each must exit 1 — a gate never seen failing is not a gate."""

    def test_orphan_module_fails(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import { App } from "./App"\nApp()\n',
                "App.tsx": "export function App() { return null }\n",
                "components/orphan-pane.tsx": "export function OrphanPane() { return null }\n",
            },
        )
        assert _run(web_root) == 1

    def test_import_from_test_file_does_not_count(self, tmp_path):
        """M1: a test importing a module must not make it reachable."""
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import "./App"\n',
                "App.tsx": "export function App() { return null }\n",
                "hooks/use-dead.ts": "export function useDead() {}\n",
                "hooks/use-dead.test.ts": 'import { useDead } from "./use-dead"\n',
                "__tests__/helper.ts": 'import { useDead } from "../hooks/use-dead"\n',
            },
        )
        assert _run(web_root) == 1

    def test_transitive_orphan_fails(self, tmp_path):
        """A file imported only by another orphan is itself an orphan."""
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import "./App"\n',
                "App.tsx": "export function App() { return null }\n",
                "components/dead-pane.tsx": 'import { helper } from "../lib/dead-helper"\n',
                "lib/dead-helper.ts": "export const helper = 1\n",
            },
        )
        assert _run(web_root) == 1

    def test_allowlist_entry_without_reason_is_hard_error(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import "./App"\n',
                "App.tsx": "export function App() { return null }\n",
                "components/orphan.tsx": "export const x = 1\n",
            },
        )
        with pytest.raises(SystemExit) as exc:
            _run(web_root, allow_lines=["components/orphan.tsx"])
        assert exc.value.code == 2


class TestPhantomEdgeResistance:
    """The 2026-08-11 adversarial-review exploit class: import-shaped text in
    comments, strings, or templates must NOT create an edge. A phantom edge
    silences a real orphan — a false green, mechanism M1 by another door."""

    _ORPHAN = {"components/orphan-pane.tsx": "export const x = 1\n"}

    def test_line_comment_import_does_not_reach(self, tmp_path):
        """The exact exploit the review demonstrated on App.tsx."""
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import "./App"\n',
                "App.tsx": '// import "./components/orphan-pane"\n'
                "export function App() { return null }\n",
                **self._ORPHAN,
            },
        )
        assert _run(web_root) == 1

    def test_block_comment_reexport_does_not_reach(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import "./App"\n',
                "App.tsx": '/* export { x } from "./components/orphan-pane" */\n'
                "export function App() { return null }\n",
                **self._ORPHAN,
            },
        )
        assert _run(web_root) == 1

    def test_string_literal_import_text_does_not_reach(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import "./App"\n',
                "App.tsx": "export const doc = 'see import(\"./components/orphan-pane\")'\n",
                **self._ORPHAN,
            },
        )
        assert _run(web_root) == 1

    def test_template_literal_import_text_does_not_reach(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import "./App"\n',
                "App.tsx": 'export const msg = `import "./components/orphan-pane"`\n',
                **self._ORPHAN,
            },
        )
        assert _run(web_root) == 1

    def test_real_import_after_regex_literal_still_counts(self, tmp_path):
        """A regex containing a quote must not derail the string lexer."""
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'const re = str.match(/"[^"]*"/)\nimport "./App"\n',
                "App.tsx": "export function App() { return null }\n",
            },
        )
        assert _run(web_root) == 0

    def test_import_after_template_interpolation_still_counts(self, tmp_path):
        """Nested ${...} with braces must hand back to code mode cleanly."""
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": "const s = `x ${flag ? { a: 1 } : 2} y`\n"
                'import "./App"\n',
                "App.tsx": "export function App() { return null }\n",
            },
        )
        assert _run(web_root) == 0

    def test_jsx_closing_tags_do_not_swallow_lazy_import(self, tmp_path):
        """`</div>` must lex as code (not a regex open) so a later import
        edge in the same file survives."""
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import "./App"\n',
                "App.tsx": "export function App() {\n"
                "  return <div><span>hi</span></div>\n"
                "}\n"
                'export const P = lazy(() => import("./components/lazy-pane"))\n',
                "components/lazy-pane.tsx": "export default function LazyPane() { return null }\n",
            },
        )
        assert _run(web_root) == 0


class TestGreenCases:
    """Each must exit 0 — the resolver features the real tree depends on."""

    def test_static_and_alias_imports(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import { App } from "@/App"\n',
                "App.tsx": 'import { util } from "./lib/util"\nexport function App() { return util }\n',
                "lib/util.ts": "export const util = 1\n",
            },
        )
        assert _run(web_root) == 0

    def test_lazy_dynamic_import_counts(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'const Pane = lazy(() => import("@/components/lazy-pane"))\n',
                "components/lazy-pane.tsx": "export default function LazyPane() { return null }\n",
            },
        )
        assert _run(web_root) == 0

    def test_barrel_reexport_reaches_leaf(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import { Leaf } from "./components/index"\n',
                "components/index.ts": 'export { Leaf } from "./leaf"\n',
                "components/leaf.tsx": "export function Leaf() { return null }\n",
            },
        )
        assert _run(web_root) == 0

    def test_directory_import_resolves_index(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import { Leaf } from "@/components"\n',
                "components/index.ts": 'export { Leaf } from "./leaf"\n',
                "components/leaf.tsx": "export function Leaf() { return null }\n",
            },
        )
        assert _run(web_root) == 0

    def test_worker_new_url_counts(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'const w = new Worker(\n  new URL("./workers/calc.worker.ts", import.meta.url),\n)\n',
                "workers/calc.worker.ts": "export {}\n",
            },
        )
        assert _run(web_root) == 0

    def test_vite_query_suffix_stripped(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import LayoutWorker from "./workers/layout.worker.ts?worker"\n',
                "workers/layout.worker.ts": "export {}\n",
            },
        )
        assert _run(web_root) == 0

    def test_allowlisted_orphan_passes(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import "./App"\n',
                "App.tsx": "export function App() { return null }\n",
                "components/orphan.tsx": "export const x = 1\n",
            },
        )
        assert (
            _run(web_root, allow_lines=["components/orphan.tsx  # RA-99: probe orphan"]) == 0
        )

    def test_stale_allowlist_entry_warns_but_passes(self, tmp_path, capsys):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import { App } from "./App"\n',
                "App.tsx": "export function App() { return null }\n",
            },
        )
        assert _run(web_root, allow_lines=["App.tsx  # RA-99: now wired"]) == 0
        assert "[stale-allowlist]" in capsys.readouterr().out

    def test_test_files_are_not_violations(self, tmp_path):
        web_root = _tree(
            tmp_path,
            {
                "main.tsx": 'import "./App"\n',
                "App.tsx": "export function App() { return null }\n",
                "App.test.tsx": 'import { App } from "./App"\n',
                "__tests__/setup.ts": "export {}\n",
            },
        )
        assert _run(web_root) == 0
