# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The plugin docs must enumerate what the loader actually accepts (F216/F309).

docs/PLUGIN_DEVELOPMENT.md published four manifest types — parser, agent,
sync, middleware — while the validator accepts six. The two it omitted are
the two that matter most: ``connector`` is the type of every in-tree Pro
plugin, and ``tool`` is the mechanism (RA-63) that puts a plugin's tools in
``tools/list``. A third-party developer following the docs could not build
either.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

DOC = Path(__file__).resolve().parents[3] / "docs" / "PLUGIN_DEVELOPMENT.md"


def _manifest_schema_block() -> str:
    text = DOC.read_text(encoding="utf-8")
    match = re.search(r"## Manifest Schema\n+```json\n(.*?)```", text, re.S)
    assert match, "manifest schema block not found in PLUGIN_DEVELOPMENT.md"
    return match.group(1)


def test_the_schema_block_lists_every_accepted_type():
    from plugins import VALID_PLUGIN_TYPES

    block = _manifest_schema_block()
    declared = re.search(r'"type": "([^"]+)"', block).group(1)
    documented = {
        t.replace("(required)", "").strip() for t in declared.split("|")
    }

    assert documented == set(VALID_PLUGIN_TYPES), (
        f"documented={sorted(documented)} accepted={sorted(VALID_PLUGIN_TYPES)}"
    )


@pytest.mark.parametrize("base_class", ["ToolPlugin", "ConnectorPlugin"])
def test_each_concrete_base_class_has_a_section(base_class):
    text = DOC.read_text(encoding="utf-8")
    assert f"### {base_class}" in text, f"{base_class} has no section in the docs"


def test_the_documented_base_classes_are_the_ones_that_exist():
    """A section for a class that was renamed away is drift in the other
    direction, so pin both ends."""
    import inspect

    from plugins import base as base_mod
    from plugins.base import CeridPlugin

    concrete = {
        name
        for name, obj in vars(base_mod).items()
        if inspect.isclass(obj)
        and issubclass(obj, CeridPlugin)
        and obj is not CeridPlugin
    }
    text = DOC.read_text(encoding="utf-8")
    documented = set(re.findall(r"^### (\w+Plugin)$", text, re.M))

    assert documented == concrete, (
        f"documented={sorted(documented)} defined={sorted(concrete)}"
    )
