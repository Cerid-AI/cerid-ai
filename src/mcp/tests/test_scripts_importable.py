# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Import smoke gate for the operator scripts under ``src/mcp/scripts/``.

These are host processes an operator copy-pastes out of the docs. Nothing else
imports them, mypy excludes ``scripts/`` (pyproject.toml), and no test touched
them — so ``watch_obsidian.py`` shipped for months importing a ``parse_markdown``
that ``parsers.structured`` has never exported. The failure only appears at the
moment a user runs the documented command.

Every module here is imported in a clean subprocess: a bad top-level import in
one script must not be masked by another test having already populated
``sys.modules``, and the scripts mutate ``sys.path`` at import time.
"""
from __future__ import annotations

import importlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

MCP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MCP_ROOT.parents[1]
SCRIPTS_DIR = MCP_ROOT / "scripts"

SCRIPT_MODULES = sorted(
    p.stem for p in SCRIPTS_DIR.glob("*.py") if not p.stem.startswith("_")
)

# Documented copy-paste invocations — path relative to the repo root, exactly as
# README.md / docs/API_REFERENCE.md spell them.
DOCUMENTED_ENTRYPOINTS = [
    "src/mcp/scripts/watch_obsidian.py",
]


def _run(args: list[str], cwd: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        timeout=120,
    )


def test_scripts_dir_is_not_empty() -> None:
    """Guard the parametrisation itself: an empty glob would make the gate vacuous."""
    assert len(SCRIPT_MODULES) >= 10, f"only found {SCRIPT_MODULES!r} under {SCRIPTS_DIR}"


@pytest.mark.parametrize("module", SCRIPT_MODULES)
def test_script_module_imports(module: str) -> None:
    """``python -m scripts.<name>`` must at least get past its imports."""
    proc = _run(["-c", f"import scripts.{module}"], cwd=MCP_ROOT)
    assert proc.returncode == 0, (
        f"src/mcp/scripts/{module}.py cannot be imported:\n{proc.stderr.strip()}"
    )


@pytest.mark.parametrize("entrypoint", DOCUMENTED_ENTRYPOINTS)
def test_documented_entrypoint_runs(entrypoint: str) -> None:
    """The docs' own command, run from the repo root, must reach argparse.

    Scripts invoked by file path get ``src/mcp/scripts`` on ``sys.path[0]``, not
    ``src/mcp`` — so every in-tree import has to sit below the script's own
    ``sys.path`` bootstrap.
    """
    proc = _run([entrypoint, "--help"], cwd=REPO_ROOT)
    assert proc.returncode == 0, f"`python {entrypoint} --help` failed:\n{proc.stderr.strip()}"
    assert "usage:" in proc.stdout


class TestObsidianNoteParsing:
    """``watch_obsidian.ingest_note`` end-to-end over a real vault note.

    Importing is necessary but not sufficient: the parser it now calls returns a
    list of sections with the frontmatter attached to the first one under
    ``frontmatter_json``, not a single dict under ``frontmatter``.
    """

    @pytest.fixture
    def watcher(self, monkeypatch: pytest.MonkeyPatch):
        # Deliberately not importorskip: a script that cannot import is the
        # defect this file exists to catch, and a skip would hide it again.
        watcher = importlib.import_module("scripts.watch_obsidian")
        monkeypatch.setattr(watcher, "STABILITY_INTERVAL", 0.0)
        monkeypatch.setattr(watcher, "DEBOUNCE_SECONDS", 0.0)
        watcher._recent.clear()
        watcher._retry_queue.clear()
        return watcher

    @staticmethod
    def _note(tmp_path: Path) -> str:
        path = tmp_path / "Reading Notes.md"
        path.write_text(
            "---\n"
            "tags: [reading, ml]\n"
            "aliases:\n"
            "  - Paper Notes\n"
            'created: "2026-08-01"\n'
            "cssclass: wide\n"
            "---\n"
            "\n"
            "# Attention\n"
            "\n"
            "See [[Transformers|the transformer note]] and [[Scaling Laws]].\n",
            encoding="utf-8",
        )
        return str(path)

    def test_posts_body_and_mapped_frontmatter(self, watcher, tmp_path, monkeypatch) -> None:
        posted: dict = {}

        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"status": "success", "chunks": 2}

        def _fake_post(url, json=None, timeout=None):  # noqa: A002 — httpx kwarg name
            posted["url"] = url
            posted["payload"] = json
            return _Resp()

        monkeypatch.setattr(watcher.httpx, "post", _fake_post)
        watcher.ingest_note(self._note(tmp_path), "personal", "manual")

        assert posted, "ingest_note never reached the /ingest call"
        content = posted["payload"]["content"]
        assert "the transformer note" in content, "wikilink display text was not unwrapped"
        assert "[[" not in content, f"raw wikilink survived: {content!r}"
        assert "tags:" not in content, "frontmatter fence leaked into the ingested body"

    def test_frontmatter_reaches_map_frontmatter(self, watcher, tmp_path, monkeypatch) -> None:
        """The parser's frontmatter must arrive as a dict, under the key it uses."""
        seen: list[dict] = []
        mapped: list[tuple[str, dict]] = []
        real_map = watcher._map_frontmatter

        def _spy(frontmatter: dict, domain: str):
            seen.append(frontmatter)
            result = real_map(frontmatter, domain)
            mapped.append(result)
            return result

        class _Resp:
            status_code = 200

            @staticmethod
            def json() -> dict:
                return {"status": "success", "chunks": 2}

        monkeypatch.setattr(watcher.httpx, "post", lambda *a, **k: _Resp())
        monkeypatch.setattr(watcher, "_map_frontmatter", _spy)
        watcher.ingest_note(self._note(tmp_path), "personal", "manual")

        assert seen, "_map_frontmatter never ran — the frontmatter key is wrong"
        assert seen[0]["tags"] == ["reading", "ml"]
        assert seen[0]["aliases"] == ["Paper Notes"]
        assert "cssclass" not in seen[0], "Obsidian-only keys must be filtered out"
        assert json.dumps(seen[0])  # JSON-serialisable, as the payload requires

        _, metadata = mapped[0]
        assert json.loads(metadata["tags_json"]) == ["reading", "ml"]
        assert json.loads(metadata["alternate_titles"]) == ["Paper Notes"]
        assert metadata["source_date"] == "2026-08-01"
