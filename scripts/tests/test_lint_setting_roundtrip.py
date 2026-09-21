# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Plant-fault tests for scripts/lint-setting-roundtrip.py (gate G-C).

Each test plants a synthetic ``SettingsUpdateRequest`` + PATCH handler and a
synthetic set of reader modules, then runs the real analyser. The
value-import case is the one that matters most: it is the shape the gate
exists to catch, and the shape a naive "does the symbol appear anywhere"
check reports as healthy.

The last class pins the repo's live count as non-increasing, so a new knob
cannot be added to the model and quietly join the placebo pile.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_setting_roundtrip", _ROOT / "scripts" / "lint-setting-roundtrip.py",
)
assert _SPEC is not None and _SPEC.loader is not None
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_setting_roundtrip"] = lint
_SPEC.loader.exec_module(lint)


def _settings(model_body: str, handler_body: str) -> str:
    return (
        "class SettingsUpdateRequest(BaseModel):\n"
        f"{model_body}"
        "\n\n"
        "@router.patch('/settings')\n"
        "async def update_settings_endpoint(req: SettingsUpdateRequest):\n"
        "    updated = {}\n"
        f"{handler_body}"
        "    return {'updated': updated}\n"
    )


class TestFieldEnumeration:
    def test_fields_come_from_the_model_class_body(self) -> None:
        """Derived, never typed out — that is what stops the gate going stale."""
        import ast

        src = _settings(
            "    alpha: int | None = Field(None)\n    beta: bool | None = Field(None)\n",
            "    pass\n",
        )
        assert lint.model_fields(ast.parse(src)) == ["alpha", "beta"]


class TestNotWritten:
    def test_field_accepted_and_never_assigned_is_flagged(self) -> None:
        src = _settings(
            "    alpha: int | None = Field(None)\n",
            "    if req.alpha is not None:\n        updated['alpha'] = req.alpha\n",
        )
        v = lint.analyse(src, ["def read():\n    return config.ALPHA\n"])
        assert [(x.field, x.kind) for x in v] == [("alpha", "not-written")]

    def test_field_with_no_branch_at_all_is_flagged(self) -> None:
        src = _settings("    alpha: int | None = Field(None)\n", "    pass\n")
        assert [x.kind for x in lint.analyse(src, [])] == ["not-written"]


class TestReadbackAccepted:
    def test_attribute_read_inside_a_function_counts(self) -> None:
        src = _settings(
            "    alpha: int | None = Field(None)\n",
            "    if req.alpha is not None:\n        config.ALPHA = req.alpha\n",
        )
        assert lint.analyse(src, ["def serve():\n    return config.ALPHA\n"]) == []

    def test_getattr_string_read_counts(self) -> None:
        """The shape query_agent uses: getattr(config, "HYBRID_FUSION_MODE", ...)."""
        src = _settings(
            "    alpha: int | None = Field(None)\n",
            "    if req.alpha is not None:\n        config.ALPHA = req.alpha\n",
        )
        assert lint.analyse(src, ['def serve():\n    return getattr(config, "ALPHA", 1)\n']) == []

    def test_env_write_read_back_via_getenv_counts(self) -> None:
        src = _settings(
            "    alpha: str | None = Field(None)\n",
            "    if req.alpha is not None:\n        os.environ['ALPHA_MODE'] = req.alpha\n",
        )
        assert lint.analyse(src, ['def serve():\n    return os.getenv("ALPHA_MODE")\n']) == []

    def test_set_toggle_read_back_via_registry_counts(self) -> None:
        src = _settings(
            "    enable_x: bool | None = Field(None)\n",
            "    if req.enable_x is not None:\n        set_toggle('enable_x', req.enable_x)\n",
        )
        readers = ['def serve():\n    return FEATURE_TOGGLES["enable_x"]\n']
        assert lint.analyse(src, readers) == []


class TestReadbackRejected:
    def test_value_import_does_not_count_as_a_readback(self) -> None:
        """The MMR_LAMBDA shape, and the whole reason this gate exists.

        `from config.features import ALPHA` copies the value at import time.
        `features_mod.ALPHA = v` rebinds a different name, so the reading
        module serves its boot-time value forever. A symbol-appears-anywhere
        check calls this healthy.
        """
        src = _settings(
            "    alpha: float | None = Field(None)\n",
            "    if req.alpha is not None:\n        features_mod.ALPHA = req.alpha\n",
        )
        frozen_reader = (
            "from config.features import ALPHA\n"
            "\n"
            "def serve(x=None):\n"
            "    return x if x is not None else ALPHA\n"
        )
        v = lint.analyse(src, [frozen_reader])
        assert [(x.field, x.kind) for x in v] == [("alpha", "no-readback")]

    def test_same_module_re_read_through_the_module_object_does_count(self) -> None:
        """`features_mod.ALPHA` inside a function IS live — the fix shape."""
        src = _settings(
            "    alpha: float | None = Field(None)\n",
            "    if req.alpha is not None:\n        features_mod.ALPHA = req.alpha\n",
        )
        live_reader = (
            "import config.features as features_mod\n"
            "\n"
            "def serve():\n"
            "    return features_mod.ALPHA\n"
        )
        assert lint.analyse(src, [live_reader]) == []

    def test_module_scope_read_does_not_count(self) -> None:
        """Runs once at import, so it cannot observe a later PATCH."""
        src = _settings(
            "    alpha: int | None = Field(None)\n",
            "    if req.alpha is not None:\n        config.ALPHA = req.alpha\n",
        )
        v = lint.analyse(src, ["TOP = config.ALPHA\n"])
        assert [(x.field, x.kind) for x in v] == [("alpha", "no-readback")]


class TestRepoBaseline:
    """The count the audit asked to be pinned: it may fall, never rise."""

    def test_live_hits_match_the_allowlist_exactly(self) -> None:
        live = {v.key() for v in lint.collect_all()}
        allow = set(lint._load_allowlist())
        assert live == allow, (
            f"unallowlisted: {sorted(live - allow)}; stale: {sorted(allow - live)}"
        )

    def test_allowlist_is_non_increasing(self) -> None:
        """Seeded at 10 on 2026-09-03. Lower this constant when one drains;
        never raise it — a new placebo knob has to fail here first."""
        assert len(lint._load_allowlist()) <= 10

    def test_every_allowlist_entry_has_a_reason(self) -> None:
        for key, reason in lint._load_allowlist().items():
            assert reason and not reason.startswith("TODO"), f"{key} has no reason"
