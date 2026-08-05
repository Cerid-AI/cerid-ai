# Copyright (c) 2026 Justin Michaels / Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Tests for the design-drift linter (scripts/lint-no-design-drift.py).

All tests use synthetic in-memory fixtures written to ``tmp_path``.
No dependency on the real ``src/web/src/`` source tree — the gate passes
even when the live codebase has pre-existing violations.

Test organisation:
  - One class per check type (TestHex, TestInlineStyle, etc.)
  - Shared ``run_linter`` fixture dynamically imports the script so the
    tests stay in sync with the live implementation without an install step.
  - ``TestAllowlist`` covers the cross-cutting suppression mechanism.
  - ``TestReportOnly`` covers the ``--report-only`` exit-code contract.
  - ``TestExcludeDirs`` covers the ``--exclude-dir`` skip mechanism.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from ._helpers import scripts_dir

# ---------------------------------------------------------------------------
# Module-level skip guard — mirrors test_lint_no_silent_catch.py
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    scripts_dir() is None,
    reason="scripts/ dir not reachable from test env (repo-root not mounted)",
)


# ---------------------------------------------------------------------------
# Shared fixture
# ---------------------------------------------------------------------------


def _load_drift_module() -> Any:
    sd = scripts_dir()
    assert sd is not None
    script = sd / "lint-no-design-drift.py"
    spec = importlib.util.spec_from_file_location("lint_no_design_drift", script)
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    sys.modules["lint_no_design_drift"] = mod
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def drift_module() -> Any:
    return _load_drift_module()


@pytest.fixture
def scan(drift_module: Any, tmp_path: Path):
    """Return a helper that:
    1. Writes a .tsx (or .ts) file with the given source into tmp_path.
    2. Calls check_file() on it with the provided flags.
    3. Returns (exit_code, violations_list).
    """
    def _scan(
        source: str,
        *,
        suffix: str = ".tsx",
        check_hex: bool = True,
        check_inline_style: bool = True,
        check_arbitrary_tailwind: bool = True,
        check_icons: bool = True,
        check_motion: bool = True,
        check_settings_controls: bool = True,
    ):
        f = tmp_path / f"comp{suffix}"
        f.write_text(source, encoding="utf-8")
        violations = drift_module.check_file(
            f,
            check_hex=check_hex,
            check_inline_style=check_inline_style,
            check_arbitrary_tailwind=check_arbitrary_tailwind,
            check_icons=check_icons,
            check_motion=check_motion,
            check_settings_controls=check_settings_controls,
        )
        return violations

    return _scan


@pytest.fixture
def run_main(drift_module: Any, tmp_path: Path):
    """Call main() with a synthetic directory."""
    def _run(files: dict[str, str], extra_args: list[str] | None = None) -> int:
        src_dir = tmp_path / "src"
        src_dir.mkdir(exist_ok=True)
        for name, content in files.items():
            p = src_dir / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
        args = ["--root", str(src_dir)] + (extra_args or [])
        return drift_module.main(args)

    return _run


# ---------------------------------------------------------------------------
# TestHex
# ---------------------------------------------------------------------------


class TestHex:
    def test_raw_hex_flagged(self, scan):
        viols = scan('const color = "#ff0000";', check_hex=True)
        assert any(v.check == "hex" for v in viols)

    def test_short_hex_flagged(self, scan):
        viols = scan('const c = "#abc";', check_hex=True)
        assert any(v.check == "hex" for v in viols)

    def test_8char_hex_flagged(self, scan):
        viols = scan('const c = "#ff000080";', check_hex=True)
        assert any(v.check == "hex" for v in viols)

    def test_comment_line_ignored(self, scan):
        viols = scan("// the colour #ff0000 is red\n", check_hex=True)
        assert not any(v.check == "hex" for v in viols)

    def test_jsdoc_comment_ignored(self, scan):
        viols = scan(" * Hex value: #aabbcc\n", check_hex=True)
        assert not any(v.check == "hex" for v in viols)

    def test_css_var_context_ignored(self, scan):
        # Should be ignored — var(--my-color: #ff0000) is a CSS fallback, not a drift violation
        viols = scan("  background: var(--brand-color, #ff0000);\n", check_hex=True)
        assert not any(v.check == "hex" for v in viols)

    def test_html_entity_ignored(self, scan):
        viols = scan("  <div>&#9888;&#65039;</div>\n", check_hex=True)
        assert not any(v.check == "hex" for v in viols)

    def test_drift_allowed_suppression(self, scan):
        viols = scan('const c = "#ff0000";  // drift-allowed: legacy chart colour\n', check_hex=True)
        assert not any(v.check == "hex" for v in viols)

    def test_hex_check_disabled(self, scan):
        viols = scan('const c = "#ff0000";', check_hex=False)
        assert not any(v.check == "hex" for v in viols)

    def test_hex_in_ts_file_flagged(self, scan):
        viols = scan('export const RED = "#ff0000";', suffix=".ts", check_hex=True)
        assert any(v.check == "hex" for v in viols)


# ---------------------------------------------------------------------------
# TestInlineStyle
# ---------------------------------------------------------------------------


class TestInlineStyle:
    def test_style_prop_flagged(self, scan):
        viols = scan('<div style={{ width: "100%" }} />\n', check_inline_style=True)
        assert any(v.check == "inline-style" for v in viols)

    def test_style_with_spaces_flagged(self, scan):
        viols = scan('<div style = {{ color: "red" }} />\n', check_inline_style=True)
        assert any(v.check == "inline-style" for v in viols)

    def test_class_name_not_flagged(self, scan):
        viols = scan('<div className="bg-primary" />\n', check_inline_style=True)
        assert not any(v.check == "inline-style" for v in viols)

    def test_drift_allowed_suppression(self, scan):
        viols = scan(
            '<ScrollArea style={{ maxHeight }}>  // drift-allowed: dynamic height for chart\n',
            check_inline_style=True,
        )
        assert not any(v.check == "inline-style" for v in viols)

    def test_ts_file_not_scanned_for_style(self, scan):
        # .ts files don't have JSX — inline-style check is tsx-only
        viols = scan('const x = { style: {{ width: 1 }} };\n', suffix=".ts", check_inline_style=True)
        # Should not flag .ts files (check skipped)
        assert not any(v.check == "inline-style" for v in viols)

    def test_check_disabled(self, scan):
        viols = scan('<div style={{ width: "100%" }} />\n', check_inline_style=False)
        assert not any(v.check == "inline-style" for v in viols)


# ---------------------------------------------------------------------------
# TestArbitraryTailwind
# ---------------------------------------------------------------------------


class TestArbitraryTailwind:
    def test_px_value_flagged(self, scan):
        viols = scan('<p className="text-[11px]">', check_arbitrary_tailwind=True)
        assert any(v.check == "arbitrary-tailwind" for v in viols)

    def test_rem_value_flagged(self, scan):
        viols = scan('<div className="max-w-[10rem]">', check_arbitrary_tailwind=True)
        assert any(v.check == "arbitrary-tailwind" for v in viols)

    def test_hex_in_tailwind_flagged(self, scan):
        viols = scan('<p className="bg-[#ff0000]">', check_arbitrary_tailwind=True)
        assert any(v.check == "arbitrary-tailwind" for v in viols)

    def test_canonical_tailwind_not_flagged(self, scan):
        viols = scan('<p className="text-sm font-medium space-y-4">', check_arbitrary_tailwind=True)
        assert not any(v.check == "arbitrary-tailwind" for v in viols)

    def test_tailwind_fraction_not_flagged(self, scan):
        # w-1/2, h-3/4 etc — fractions use slashes, not px/rem
        viols = scan('<div className="w-1/2 h-3/4">', check_arbitrary_tailwind=True)
        assert not any(v.check == "arbitrary-tailwind" for v in viols)

    def test_drift_allowed_suppression(self, scan):
        viols = scan(
            '<p className="text-[10px]">  // drift-allowed: brand spec locked size\n',
            check_arbitrary_tailwind=True,
        )
        assert not any(v.check == "arbitrary-tailwind" for v in viols)

    def test_check_disabled(self, scan):
        viols = scan('<p className="text-[11px]">', check_arbitrary_tailwind=False)
        assert not any(v.check == "arbitrary-tailwind" for v in viols)

    def test_p_value_flagged(self, scan):
        viols = scan('<div className="p-[3px]">', check_arbitrary_tailwind=True)
        assert any(v.check == "arbitrary-tailwind" for v in viols)

    def test_ring_value_flagged(self, scan):
        viols = scan('<div className="ring-[3px]">', check_arbitrary_tailwind=True)
        assert any(v.check == "arbitrary-tailwind" for v in viols)


# ---------------------------------------------------------------------------
# TestIcons
# ---------------------------------------------------------------------------


class TestIcons:
    def test_heroicons_flagged(self, scan):
        viols = scan("import { XMarkIcon } from '@heroicons/react/24/outline';\n", check_icons=True)
        assert any(v.check == "icons" for v in viols)

    def test_react_icons_flagged(self, scan):
        viols = scan("import { FaCheck } from 'react-icons/fa';\n", check_icons=True)
        assert any(v.check == "icons" for v in viols)

    def test_material_ui_icons_flagged(self, scan):
        viols = scan("import CheckIcon from '@material-ui/icons/Check';\n", check_icons=True)
        assert any(v.check == "icons" for v in viols)

    def test_lucide_allowed(self, scan):
        viols = scan("import { Check, X } from 'lucide-react';\n", check_icons=True)
        assert not any(v.check == "icons" for v in viols)

    def test_drift_allowed_suppression(self, scan):
        viols = scan(
            "import { XMarkIcon } from '@heroicons/react/24/outline';  // drift-allowed: legacy\n",
            check_icons=True,
        )
        assert not any(v.check == "icons" for v in viols)

    def test_check_disabled(self, scan):
        viols = scan("import { FaCheck } from 'react-icons/fa';\n", check_icons=False)
        assert not any(v.check == "icons" for v in viols)


# ---------------------------------------------------------------------------
# TestMotion
# ---------------------------------------------------------------------------


class TestMotion:
    def test_framer_motion_flagged(self, scan):
        viols = scan("import { motion } from 'framer-motion';\n", check_motion=True)
        assert any(v.check == "motion" for v in viols)

    def test_gsap_flagged(self, scan):
        viols = scan("import gsap from 'gsap';\n", check_motion=True)
        assert any(v.check == "motion" for v in viols)

    def test_react_spring_flagged(self, scan):
        viols = scan("import { useSpring } from 'react-spring';\n", check_motion=True)
        assert any(v.check == "motion" for v in viols)

    def test_react_spring_scoped_flagged(self, scan):
        viols = scan("import { useSpring } from '@react-spring/web';\n", check_motion=True)
        assert any(v.check == "motion" for v in viols)

    def test_tailwind_animate_not_flagged(self, scan):
        viols = scan('<div className="animate-bounce">\n', check_motion=True)
        assert not any(v.check == "motion" for v in viols)

    def test_drift_allowed_suppression(self, scan):
        viols = scan(
            "import { motion } from 'framer-motion';  // drift-allowed: pre-shadcn legacy\n",
            check_motion=True,
        )
        assert not any(v.check == "motion" for v in viols)

    def test_check_disabled(self, scan):
        viols = scan("import { motion } from 'framer-motion';\n", check_motion=False)
        assert not any(v.check == "motion" for v in viols)


# ---------------------------------------------------------------------------
# TestAllowlist — the path:lineno allowlist mechanism
# ---------------------------------------------------------------------------


class TestAllowlist:
    def test_allowlisted_line_suppressed(self, drift_module: Any, tmp_path: Path):
        f = tmp_path / "comp.tsx"
        f.write_text('const c = "#ff0000";\nconst d = "#00ff00";\n', encoding="utf-8")

        all_viols = drift_module.check_file(
            f,
            check_hex=True,
            check_inline_style=False,
            check_arbitrary_tailwind=False,
            check_icons=False,
            check_motion=False,
            check_settings_controls=False,
        )
        assert len(all_viols) == 2

        # Allowlist only line 1
        allow = drift_module.load_allow_files([])
        allow[str(f)] = {1}
        filtered = drift_module.filter_allowlisted(all_viols, allow)
        assert len(filtered) == 1
        assert filtered[0].lineno == 2

    def test_allow_file_loaded_correctly(self, drift_module: Any, tmp_path: Path):
        allow_file = tmp_path / "allow.txt"
        allow_file.write_text(
            "# comment\n"
            "some/path.tsx:10\n"
            "other/path.tsx:20\n"
            "\n"
            "bad-line-no-colon\n",
            encoding="utf-8",
        )
        allow = drift_module.load_allow_files([allow_file])
        assert allow.get("some/path.tsx") == {10}
        assert allow.get("other/path.tsx") == {20}
        # bad line silently ignored
        assert "bad-line-no-colon" not in allow


# ---------------------------------------------------------------------------
# TestReportOnly — --report-only exit-code contract
# ---------------------------------------------------------------------------


class TestReportOnly:
    def test_report_only_exits_zero_on_violations(self, run_main: Any):
        rc = run_main(
            {"comp.tsx": '<p className="text-[11px]">text</p>'},
            extra_args=["--report-only"],
        )
        assert rc == 0

    def test_blocking_exits_one_on_violations(self, run_main: Any):
        rc = run_main({"comp.tsx": '<p className="text-[11px]">text</p>'})
        assert rc == 1

    def test_clean_codebase_exits_zero(self, run_main: Any):
        rc = run_main({"comp.tsx": '<p className="text-sm">hello</p>'})
        assert rc == 0

    def test_report_only_clean_also_exits_zero(self, run_main: Any):
        rc = run_main(
            {"comp.tsx": '<p className="text-sm">hello</p>'},
            extra_args=["--report-only"],
        )
        assert rc == 0


# ---------------------------------------------------------------------------
# TestExcludeDirs — --exclude-dir skip mechanism
# ---------------------------------------------------------------------------


class TestExcludeDirs:
    def test_default_ui_dir_excluded(self, run_main: Any):
        # ui/ shadcn components have expected arbitrary values — they should not be flagged
        rc = run_main({"ui/button.tsx": '<button className="ring-[3px]">click</button>'})
        assert rc == 0

    def test_custom_excluded_dir_skipped(self, run_main: Any):
        rc = run_main(
            {"legacy/chart.tsx": '<div style={{ width: 100 }}>chart</div>'},
            extra_args=["--exclude-dir", "legacy"],
        )
        assert rc == 0

    def test_non_excluded_dir_scanned(self, run_main: Any):
        rc = run_main({"settings/MyComp.tsx": '<p className="text-[11px]">text</p>'})
        assert rc == 1


# ---------------------------------------------------------------------------
# TestBadRoot — exit code 2 on missing root
# ---------------------------------------------------------------------------


class TestBadRoot:
    def test_missing_root_exits_two(self, drift_module: Any):
        rc = drift_module.main(["--root", "/no/such/path/D1-test"])
        assert rc == 2
