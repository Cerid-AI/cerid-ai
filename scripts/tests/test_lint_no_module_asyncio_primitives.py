# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The asyncio-primitive gate fails on each form it exists to catch, and passes the fix."""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "lint-no-module-asyncio-primitives.py"
_spec = importlib.util.spec_from_file_location("lint_asyncio_primitives", SCRIPT)
lint = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lint)


@pytest.mark.parametrize(
    "source",
    [
        "import asyncio\n_sem = asyncio.Semaphore(3)\n",
        "import asyncio\n_lock: asyncio.Lock = asyncio.Lock()\n",
        "from asyncio import Event\n_ready = Event()\n",
        "import asyncio\ntry:\n    _sem = asyncio.BoundedSemaphore(1)\nexcept Exception:\n    pass\n",
        "import asyncio\nclass Pool:\n    gate = asyncio.Condition()\n",
    ],
)
def test_module_or_class_scope_primitive_fails(source):
    assert lint.check_source(source, "x.py")


@pytest.mark.parametrize(
    "source",
    [
        "import asyncio\nfrom core.utils.loop_local import LoopLocal\n_sem = LoopLocal(lambda: asyncio.Semaphore(3))\n",
        "import asyncio\nasync def f():\n    sem = asyncio.Semaphore(3)\n",
        "import asyncio\nclass Pool:\n    def __init__(self):\n        self.lock = asyncio.Lock()\n",
    ],
)
def test_function_scope_and_loop_local_pass(source):
    assert lint.check_source(source, "x.py") == []


def test_the_server_tree_is_clean():
    result = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
