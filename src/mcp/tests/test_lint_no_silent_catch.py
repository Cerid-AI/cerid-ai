# src/mcp/tests/test_lint_no_silent_catch.py
"""Tests for the silent-catch linter."""
from pathlib import Path

import pytest


@pytest.fixture
def run_linter(tmp_path, monkeypatch):
    """Fixture that invokes the linter with an isolated allowlist."""
    import importlib.util
    import sys

    # Dynamic import of the script (not a package)
    script = Path(__file__).resolve().parents[3] / "scripts" / "lint-no-silent-catch.py"
    spec = importlib.util.spec_from_file_location("lint_silent_catch", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules["lint_silent_catch"] = module
    spec.loader.exec_module(module)

    def _run(source: str, allowlist: str = "") -> tuple[int, list[str]]:
        target = tmp_path / "code.py"
        target.write_text(source)
        al = tmp_path / "allowlist.txt"
        al.write_text(allowlist)
        violations = module.check_file(target)
        allowlist_map = module.load_allowlist(al)
        new = [v for v in violations if v.lineno not in allowlist_map.get(str(target), set())]
        return (1 if new else 0, [f"{v.kind}:{v.lineno}" for v in new])

    return _run


def test_bare_except_flagged(run_linter):
    rc, violations = run_linter(
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "try:\n"
        "    pass\n"
        "except:\n"
        "    pass\n"
    )
    assert rc == 1
    assert any(v.startswith("bare-except:") for v in violations)


def test_exception_pass_flagged(run_linter):
    rc, violations = run_linter(
        "try:\n"
        "    pass\n"
        "except Exception:\n"
        "    pass\n"
    )
    assert rc == 1
    assert any(v.startswith("exception-pass:") for v in violations)


def test_exception_as_pass_flagged(run_linter):
    rc, violations = run_linter(
        "try:\n"
        "    pass\n"
        "except Exception as e:\n"
        "    pass\n"
    )
    assert rc == 1
    assert any(v.startswith("exception-as-pass:") for v in violations)


def test_debug_only_flagged(run_linter):
    rc, violations = run_linter(
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "try:\n"
        "    pass\n"
        "except Exception as e:\n"
        "    logger.debug('silent: %s', e)\n"
    )
    assert rc == 1
    assert any(v.startswith("debug-only:") for v in violations)


def test_narrow_exception_with_real_handler_ok(run_linter):
    """`except (ValueError,): logger.exception(...)` is the GOOD pattern — not flagged."""
    rc, violations = run_linter(
        "import logging\n"
        "logger = logging.getLogger(__name__)\n"
        "try:\n"
        "    pass\n"
        "except (ValueError, OSError) as e:\n"
        "    logger.exception('real handler: %s', e)\n"
    )
    assert rc == 0
    assert violations == []


def test_allowlist_entry_suppresses_violation(tmp_path):
    import importlib.util
    import sys

    # Load the linter module
    script = Path(__file__).resolve().parents[3] / "scripts" / "lint-no-silent-catch.py"
    spec = importlib.util.spec_from_file_location("lint_silent_catch", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules["lint_silent_catch"] = module
    spec.loader.exec_module(module)

    target = tmp_path / "code.py"
    target.write_text("try:\n    pass\nexcept Exception:\n    pass\n")

    # Baseline: no allowlist → violation detected
    violations = module.check_file(target)
    assert len(violations) == 1
    assert violations[0].kind == "exception-pass"
    offending_line = violations[0].lineno

    # With matching allowlist entry → violation suppressed
    allowlist_path = tmp_path / "allowlist.txt"
    allowlist_path.write_text(f"{target}:{offending_line}\n")

    allowlist_map = module.load_allowlist(allowlist_path)
    filtered = [v for v in violations if v.lineno not in allowlist_map.get(v.file, set())]
    assert filtered == [], "allowlist did not suppress the grandfathered violation"


def test_allowlist_unmatched_lineno_does_not_suppress(tmp_path):
    """An allowlist entry for the WRONG line number must not suppress the real violation."""
    import importlib.util
    import sys

    script = Path(__file__).resolve().parents[3] / "scripts" / "lint-no-silent-catch.py"
    spec = importlib.util.spec_from_file_location("lint_silent_catch", script)
    module = importlib.util.module_from_spec(spec)
    sys.modules["lint_silent_catch"] = module
    spec.loader.exec_module(module)

    target = tmp_path / "code.py"
    target.write_text("try:\n    pass\nexcept Exception:\n    pass\n")

    allowlist_path = tmp_path / "allowlist.txt"
    # Allowlist a DIFFERENT line number (999) — should not match
    allowlist_path.write_text(f"{target}:999\n")

    violations = module.check_file(target)
    allowlist_map = module.load_allowlist(allowlist_path)
    filtered = [v for v in violations if v.lineno not in allowlist_map.get(v.file, set())]
    assert len(filtered) == 1, "wrong-line allowlist should not suppress the real violation"


def test_try_except_else_finally_ok(run_linter):
    """try/except with substantive handler body passes."""
    rc, violations = run_linter(
        "try:\n"
        "    pass\n"
        "except ValueError as e:\n"
        "    print(e)\n"
        "    raise\n"
    )
    assert rc == 0
    assert violations == []
