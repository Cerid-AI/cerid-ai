# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Test setup for the cerid-mlx server.

serve.py imports MLX lazily, so request handling and parsing run anywhere.
Three things are only sometimes installable:

  - mlx: Apple silicon, or Linux with mlx[cpu]. Not Intel macOS.
  - mlx-lm's tool parsers: pure Python, but `import mlx_lm` imports MLX. When
    MLX is missing and mlx-lm's files are present, `mlx_lm` is registered as a
    bare package so `mlx_lm.tool_parsers.*` load without its __init__.
  - openai-harmony: no wheel for Intel macOS.

A test that needs one of them skips with the reason. CI sets
MLX_STACK_TESTS_STRICT=1, which turns every such skip into a failure, so a
dependency that silently failed to install cannot pass as green.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

STACK = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(STACK))

_mlx_lm = importlib.util.find_spec("mlx_lm")
HAVE_PARSERS = _mlx_lm is not None
try:
    import mlx.core  # noqa: F401

    HAVE_MLX = True
except ImportError:
    HAVE_MLX = False
    if _mlx_lm is not None and _mlx_lm.submodule_search_locations:
        _pkg = types.ModuleType("mlx_lm")
        _pkg.__path__ = list(_mlx_lm.submodule_search_locations)
        sys.modules["mlx_lm"] = _pkg
HAVE_HARMONY = importlib.util.find_spec("openai_harmony") is not None


def need(available: bool, what: str) -> None:
    if available:
        return
    if os.environ.get("MLX_STACK_TESTS_STRICT") == "1":
        pytest.fail(f"{what} is not installed (MLX_STACK_TESTS_STRICT=1)")
    pytest.skip(f"{what} is not installed; see stacks/mlx-inference/requirements-test.txt")


@pytest.fixture
def parsers():
    need(HAVE_PARSERS, "mlx-lm")


@pytest.fixture
def harmony():
    need(HAVE_HARMONY, "openai-harmony")


@pytest.fixture
def mlx():
    need(HAVE_MLX, "mlx")
