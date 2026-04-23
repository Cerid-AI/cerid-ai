# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for scripts/lint-no-silent-catch.py.

Covers Phase 2.4 of the 2026-04-22 ship audit: broad-except-without-helper
detection runs as warn-only by default, supports inline opt-out tokens,
and is promotable to a hard failure with ``--strict-broad``.

Existing strict patterns (bare-except / exception-pass / debug-only) keep
their fail-on-new behavior gated by ``silent_catch_allowlist.txt``.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_no_silent_catch", _ROOT / "scripts" / "lint-no-silent-catch.py",
)
assert _SPEC is not None and _SPEC.loader is not None
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_no_silent_catch"] = lint
_SPEC.loader.exec_module(lint)


def _check(source: str) -> tuple[list[lint.Violation], list[lint.Violation]]:
    """Parse ``source`` and return ``(strict_violations, warnings)``."""
    import ast
    tree = ast.parse(source)
    visitor = lint.SilentCatchVisitor("<test>", source.splitlines())
    visitor.visit(tree)
    return visitor.violations, visitor.warnings


# ---------------------------------------------------------------------------
# Strict patterns — regression coverage so warn-only addition didn't regress
# ---------------------------------------------------------------------------


class TestStrictPatterns:
    def test_bare_except_flagged(self) -> None:
        v, w = _check("try:\n    x = 1\nexcept:\n    pass\n")
        assert len(v) == 1
        assert v[0].kind == "bare-except"
        assert w == []

    def test_exception_pass_flagged(self) -> None:
        v, w = _check("try:\n    x = 1\nexcept Exception:\n    pass\n")
        assert len(v) == 1
        assert v[0].kind == "exception-pass"
        assert w == []

    def test_exception_as_pass_flagged(self) -> None:
        v, w = _check("try:\n    x = 1\nexcept Exception as e:\n    pass\n")
        assert len(v) == 1
        assert v[0].kind == "exception-as-pass"
        assert w == []

    def test_debug_only_flagged(self) -> None:
        v, w = _check(
            "try:\n    x = 1\nexcept Exception:\n    logger.debug('oops')\n",
        )
        assert len(v) == 1
        assert v[0].kind == "debug-only"
        assert w == []


# ---------------------------------------------------------------------------
# Phase 2.4 — broad-no-helper warnings
# ---------------------------------------------------------------------------


class TestBroadNoHelperWarnings:
    def test_broad_except_without_helper_warns(self) -> None:
        src = (
            "try:\n"
            "    x = parse()\n"
            "except Exception as e:\n"
            "    logger.warning('parse failed: %s', e)\n"
            "    return None\n"
        )
        v, w = _check(src)
        assert v == []
        assert len(w) == 1
        assert w[0].kind == "broad-no-helper"

    def test_log_swallowed_error_call_silences_warning(self) -> None:
        src = (
            "try:\n"
            "    x = parse()\n"
            "except Exception as e:\n"
            "    log_swallowed_error('parsers.html', e)\n"
            "    return None\n"
        )
        _, w = _check(src)
        assert w == []

    def test_logger_exception_silences_warning(self) -> None:
        src = (
            "try:\n"
            "    x = parse()\n"
            "except Exception:\n"
            "    logger.exception('parse failed')\n"
        )
        _, w = _check(src)
        assert w == []

    def test_sentry_capture_exception_silences_warning(self) -> None:
        src = (
            "try:\n"
            "    x = parse()\n"
            "except Exception:\n"
            "    sentry_sdk.capture_exception()\n"
        )
        _, w = _check(src)
        assert w == []

    def test_reraise_silences_warning(self) -> None:
        src = (
            "try:\n"
            "    x = parse()\n"
            "except Exception as e:\n"
            "    logger.warning('failed: %s', e)\n"
            "    raise\n"
        )
        _, w = _check(src)
        assert w == []

    def test_reraise_other_silences_warning(self) -> None:
        src = (
            "try:\n"
            "    x = parse()\n"
            "except Exception as e:\n"
            "    raise RuntimeError('wrap') from e\n"
        )
        _, w = _check(src)
        assert w == []

    def test_tuple_with_exception_warns(self) -> None:
        src = (
            "try:\n"
            "    x = parse()\n"
            "except (ValueError, Exception):\n"
            "    return None\n"
        )
        _, w = _check(src)
        assert len(w) == 1
        assert w[0].kind == "broad-no-helper"

    def test_narrow_except_does_not_warn(self) -> None:
        src = (
            "try:\n"
            "    x = parse()\n"
            "except ValueError:\n"
            "    return None\n"
        )
        v, w = _check(src)
        assert v == []
        assert w == []

    def test_pass_only_body_is_strict_not_warning(self) -> None:
        """`except Exception: pass` lands under strict ``exception-pass``,
        never double-counted as broad-no-helper."""
        v, w = _check("try:\n    x = 1\nexcept Exception:\n    pass\n")
        assert [vi.kind for vi in v] == ["exception-pass"]
        assert w == []


class TestOptOutTokens:
    def test_noqa_ble001_silences_warning(self) -> None:
        src = (
            "try:\n"
            "    x = parse()\n"
            "except Exception:  # noqa: BLE001\n"
            "    return None\n"
        )
        _, w = _check(src)
        assert w == []

    def test_silent_catch_allowed_silences_warning(self) -> None:
        src = (
            "try:\n"
            "    x = parse()\n"
            "except Exception:  # silent-catch-allowed: observability never raises\n"
            "    return None\n"
        )
        _, w = _check(src)
        assert w == []

    def test_unrelated_comment_does_not_silence(self) -> None:
        src = (
            "try:\n"
            "    x = parse()\n"
            "except Exception:  # we should investigate\n"
            "    return None\n"
        )
        _, w = _check(src)
        assert len(w) == 1


# ---------------------------------------------------------------------------
# CLI behavior — warn-only by default, fails with --strict-broad
# ---------------------------------------------------------------------------


class TestCliExitCode:
    def test_warn_only_exits_zero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "sample.py"
        target.write_text(
            "try:\n    x = 1\nexcept Exception:\n    return None\n",
        )
        empty_allow = tmp_path / "empty.txt"
        empty_allow.write_text("")
        rc = lint.main([str(target), "--allowlist", str(empty_allow)])
        out = capsys.readouterr()
        assert rc == 0
        assert "broad-no-helper" in out.out
        assert "WARN" in out.out

    def test_strict_broad_exits_nonzero(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "sample.py"
        target.write_text(
            "try:\n    x = 1\nexcept Exception:\n    return None\n",
        )
        empty_allow = tmp_path / "empty.txt"
        empty_allow.write_text("")
        rc = lint.main([
            str(target),
            "--allowlist", str(empty_allow),
            "--strict-broad",
        ])
        out = capsys.readouterr()
        assert rc == 1
        assert "broad-no-helper" in out.err
        assert "FAIL" in out.err

    def test_strict_violation_still_fails(
        self,
        tmp_path: Path,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        target = tmp_path / "sample.py"
        target.write_text("try:\n    x = 1\nexcept:\n    pass\n")
        empty_allow = tmp_path / "empty.txt"
        empty_allow.write_text("")
        rc = lint.main([str(target), "--allowlist", str(empty_allow)])
        out = capsys.readouterr()
        assert rc == 1
        assert "bare-except" in out.err
