# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the Phase 2.5 preventive linters.

* ``scripts/lint-no-module-env-captures.py`` — module-level
  ``os.getenv`` capture detector.
* ``scripts/lint-http-singleton-thread-guard.py`` — module-level
  ``httpx.AsyncClient/Client`` singleton detector.

Each linter runs warn-only by default and exits non-zero with
``--strict`` once existing call sites are remediated.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


env_lint = _load(
    "lint_no_module_env_captures",
    _ROOT / "scripts" / "lint-no-module-env-captures.py",
)
httpx_lint = _load(
    "lint_http_singleton_thread_guard",
    _ROOT / "scripts" / "lint-http-singleton-thread-guard.py",
)


# ---------------------------------------------------------------------------
# lint-no-module-env-captures
# ---------------------------------------------------------------------------


class TestModuleEnvCapture:
    def test_module_level_getenv_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text(
            "import os\n"
            "API_KEY = os.getenv('OPENROUTER_API_KEY', '')\n",
        )
        caps = env_lint.check_file(f)
        assert len(caps) == 1
        assert caps[0].name == "API_KEY"

    def test_annotated_module_assignment_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text(
            "import os\n"
            "API_KEY: str = os.getenv('K', '')\n",
        )
        caps = env_lint.check_file(f)
        assert len(caps) == 1

    def test_function_local_getenv_not_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text(
            "import os\n"
            "def get_key():\n"
            "    return os.getenv('K', '')\n",
        )
        assert env_lint.check_file(f) == []

    def test_class_attribute_getenv_not_flagged(self, tmp_path: Path) -> None:
        """Class bodies are not module-level; settings classes use this pattern."""
        f = tmp_path / "code.py"
        f.write_text(
            "import os\n"
            "class S:\n"
            "    API_KEY = os.getenv('K', '')\n",
        )
        assert env_lint.check_file(f) == []

    def test_opt_out_token_silences(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text(
            "import os\n"
            "API_KEY = os.getenv('K', '')  # env-capture-allowed: bootstrap default\n",
        )
        assert env_lint.check_file(f) == []

    def test_unrelated_call_not_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text(
            "import os\n"
            "PATH = os.path.expanduser('~')\n"
            "X = os.environ.get('K', '')\n",
        )
        # Only os.getenv is the bug shape per the lesson; os.environ.get is
        # still a capture but not what we lint here. (Could extend later.)
        assert env_lint.check_file(f) == []

    def test_config_dir_skipped_in_walk(self, tmp_path: Path) -> None:
        cfg = tmp_path / "config"
        cfg.mkdir()
        (cfg / "settings.py").write_text(
            "import os\nK = os.getenv('K', '')\n",
        )
        files = list(env_lint.iter_py_files(tmp_path))
        assert files == []

    def test_warn_mode_exits_zero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        f = tmp_path / "code.py"
        f.write_text("import os\nK = os.getenv('K', '')\n")
        rc = env_lint.main([str(f)])
        out = capsys.readouterr()
        assert rc == 0
        assert "module-env-capture" in out.out
        assert "WARN" in out.out

    def test_strict_mode_exits_nonzero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        f = tmp_path / "code.py"
        f.write_text("import os\nK = os.getenv('K', '')\n")
        rc = env_lint.main([str(f), "--strict"])
        out = capsys.readouterr()
        assert rc == 1
        assert "module-env-capture" in out.err


# ---------------------------------------------------------------------------
# lint-http-singleton-thread-guard
# ---------------------------------------------------------------------------


class TestHttpxSingleton:
    def test_module_async_client_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text(
            "import httpx\n"
            "_client = httpx.AsyncClient(timeout=5.0)\n",
        )
        out = httpx_lint.check_file(f)
        assert len(out) == 1
        assert out[0].name == "_client"

    def test_module_sync_client_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text(
            "import httpx\n"
            "_client = httpx.Client()\n",
        )
        out = httpx_lint.check_file(f)
        assert len(out) == 1

    def test_type_only_declaration_not_flagged(self, tmp_path: Path) -> None:
        """`_client: httpx.AsyncClient | None = None` is a typing decl, not a singleton."""
        f = tmp_path / "code.py"
        f.write_text(
            "import httpx\n"
            "_client: 'httpx.AsyncClient | None' = None\n",
        )
        assert httpx_lint.check_file(f) == []

    def test_function_local_client_not_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text(
            "import httpx\n"
            "def get_client():\n"
            "    return httpx.AsyncClient()\n",
        )
        assert httpx_lint.check_file(f) == []

    def test_opt_out_token_silences(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text(
            "import httpx\n"
            "_client = httpx.AsyncClient()  # httpx-singleton-allowed: test fixture\n",
        )
        assert httpx_lint.check_file(f) == []

    def test_unrelated_constructor_not_flagged(self, tmp_path: Path) -> None:
        f = tmp_path / "code.py"
        f.write_text(
            "import httpx\n"
            "_x = httpx.URL('https://x')\n",
        )
        assert httpx_lint.check_file(f) == []

    def test_warn_mode_exits_zero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        f = tmp_path / "code.py"
        f.write_text("import httpx\n_c = httpx.AsyncClient()\n")
        rc = httpx_lint.main([str(f)])
        out = capsys.readouterr()
        assert rc == 0
        assert "httpx-module-singleton" in out.out

    def test_strict_mode_exits_nonzero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        f = tmp_path / "code.py"
        f.write_text("import httpx\n_c = httpx.AsyncClient()\n")
        rc = httpx_lint.main([str(f), "--strict"])
        out = capsys.readouterr()
        assert rc == 1
        assert "httpx-module-singleton" in out.err


# ---------------------------------------------------------------------------
# Repo state assertions — keeps the existing baselines visible
# ---------------------------------------------------------------------------


class TestRepoStateBaselines:
    """Lock in the *current* state of each linter against the real repo so a
    regression — say, a new module-level httpx singleton — fails this test
    before it lands. The baselines tighten as cleanup PRs land.
    """

    def test_no_module_level_httpx_singletons_in_src_mcp(self) -> None:
        target = _ROOT / "src" / "mcp"
        all_findings = [
            s
            for py in httpx_lint.iter_py_files(target)
            for s in httpx_lint.check_file(py)
        ]
        assert all_findings == [], (
            "New module-level httpx singleton(s) found — wrap behind a "
            "thread-aware _get_client() accessor instead:\n  "
            + "\n  ".join(httpx_lint.format_singleton(s) for s in all_findings)
        )
