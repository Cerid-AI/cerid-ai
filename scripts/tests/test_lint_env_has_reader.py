# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Red/green probes for scripts/lint-env-has-reader.py (audit Gate 2).

The load-bearing case is M3 from tasks/2026-08-11-consolidated-audit.md: a
name declared in settings.py/features.py and read by NOTHING else must be a
violation, whether it is getenv-backed (would reach .env.example) or a bare
literal constant (AF-079's dead QUALITY_WEIGHT_* shape). Declaring it inside
one of the two generators, a test file, or the declaring module itself must
NOT count as a reader — that is exactly the phantom-reader class this gate
exists to reject.

These tests build a throwaway settings.py/features.py pair under tmp_path
and point the module's globals at them, rather than exercising the real repo
tree (which the module's own `main()` run against HEAD already covers as a
green-at-HEAD smoke check in test_real_tree_head_is_green below).
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_env_has_reader", _ROOT / "scripts" / "lint-env-has-reader.py"
)
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_env_has_reader"] = lint
_SPEC.loader.exec_module(lint)


def _tree(tmp_path: Path, settings_src: str, features_src: str, other: dict[str, str] | None = None) -> Path:
    root = tmp_path / "repo"
    (root / "src" / "mcp" / "config").mkdir(parents=True)
    (root / "src" / "mcp" / "config" / "settings.py").write_text(settings_src, encoding="utf-8")
    (root / "src" / "mcp" / "config" / "features.py").write_text(features_src, encoding="utf-8")
    (root / "scripts").mkdir(parents=True, exist_ok=True)
    for rel, content in (other or {}).items():
        f = root / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content, encoding="utf-8")
    return root


def _run(root: Path, allowlist: dict[str, str] | None = None) -> int:
    orig_root = lint.REPO_ROOT
    orig_settings = lint.SETTINGS_FILE
    orig_features = lint.FEATURES_FILE
    orig_env = lint.ENV_EXAMPLE_FILE
    orig_allow = dict(lint.ALLOWLIST)
    try:
        lint.REPO_ROOT = root
        lint.SETTINGS_FILE = root / "src" / "mcp" / "config" / "settings.py"
        lint.FEATURES_FILE = root / "src" / "mcp" / "config" / "features.py"
        lint.ENV_EXAMPLE_FILE = root / ".env.example"
        lint.ALLOWLIST.clear()
        if allowlist:
            lint.ALLOWLIST.update(allowlist)
        return lint.main()
    finally:
        lint.REPO_ROOT = orig_root
        lint.SETTINGS_FILE = orig_settings
        lint.FEATURES_FILE = orig_features
        lint.ENV_EXAMPLE_FILE = orig_env
        lint.ALLOWLIST.clear()
        lint.ALLOWLIST.update(orig_allow)


class TestRedCases:
    """Each must exit 1 — a gate never seen failing is not a gate."""

    def test_getenv_backed_orphan_fails(self, tmp_path):
        root = _tree(
            tmp_path,
            settings_src='import os\nDEAD_KNOB = os.getenv("DEAD_KNOB", "0")\n',
            features_src="",
        )
        assert _run(root) == 1

    def test_literal_scalar_orphan_fails(self, tmp_path):
        """The AF-079 shape: a bare constant, never wrapped in getenv, never read."""
        root = _tree(
            tmp_path,
            settings_src="QUALITY_WEIGHT_DEAD = 0.3\n",
            features_src="",
        )
        assert _run(root) == 1

    def test_declaration_in_own_file_is_not_a_reader(self, tmp_path):
        """Mentioning the name again inside settings.py itself (e.g. in a
        comment or an unrelated string) must not count — matches the audits'
        own methodology of excluding settings.py from the reader search."""
        root = _tree(
            tmp_path,
            settings_src=(
                'import os\n'
                'DEAD_KNOB = os.getenv("DEAD_KNOB", "0")\n'
                '# DEAD_KNOB is read by the scheduler\n'
            ),
            features_src="",
        )
        assert _run(root) == 1

    def test_generator_reference_is_not_a_reader(self, tmp_path):
        """A mention inside gen_env_example.py / gen_tier_matrix.py must not
        count as a reader — that would make every promoted name look used by
        construction, the exact loop M3 describes."""
        root = _tree(
            tmp_path,
            settings_src='import os\nDEAD_KNOB = os.getenv("DEAD_KNOB", "0")\n',
            features_src="",
            other={"scripts/gen_env_example.py": "# refers to DEAD_KNOB\n"},
        )
        assert _run(root) == 1

    def test_test_file_reference_is_not_a_reader(self, tmp_path):
        """A test importing/mocking the name must not count — the M1 sibling
        mechanism ('tests prove the module works and say nothing about
        whether anything reaches it') applies here too."""
        root = _tree(
            tmp_path,
            settings_src='import os\nDEAD_KNOB = os.getenv("DEAD_KNOB", "0")\n',
            features_src="",
            other={"src/mcp/tests/test_config.py": "DEAD_KNOB = 1\n"},
        )
        assert _run(root) == 1

    def test_env_example_mention_is_not_a_reader(self, tmp_path):
        """The generated .env.example line itself is the thing being gated,
        not evidence the name is read."""
        root = _tree(
            tmp_path,
            settings_src='import os\nDEAD_KNOB = os.getenv("DEAD_KNOB", "0")\n',
            features_src="",
            other={".env.example": "DEAD_KNOB=0\n"},
        )
        assert _run(root) == 1

    def test_unallowlisted_orphan_not_shadowed_by_unrelated_allowlist_entry(self, tmp_path):
        root = _tree(
            tmp_path,
            settings_src='import os\nDEAD_KNOB = os.getenv("DEAD_KNOB", "0")\n',
            features_src="",
        )
        assert _run(root, allowlist={"SOME_OTHER_NAME": "AF-999 — unrelated"}) == 1

    def test_comment_only_mention_in_other_file_is_not_a_reader(self, tmp_path):
        """The demonstrated bypass: a bare `#` comment mentioning the name in
        an otherwise-unrelated file, with zero real code relationship, must
        NOT launder the name past the gate. Regression guard for the exact
        plant an adversarial review used to refute an earlier version of
        this gate (a genuinely-orphaned name plus a one-line comment in an
        unrelated router file reported false-green)."""
        root = _tree(
            tmp_path,
            settings_src='import os\nDEAD_KNOB = os.getenv("DEAD_KNOB", "0")\n',
            features_src="",
            other={
                "src/mcp/app/routers/unrelated.py": (
                    "# TODO: wire DEAD_KNOB into the scheduler\n"
                    "def handler():\n"
                    "    return {}\n"
                ),
            },
        )
        assert _run(root) == 1

    def test_bare_unused_import_is_not_a_reader(self, tmp_path):
        """The first bypass an adversarial review demonstrated against a
        prior version of this gate: a throwaway file whose only content is
        `from config.settings import X`, never referenced again, is not
        evidence anything reads the value — a plain `ruff` pass would flag
        it as an unused import (F401). Import aliases are `ast.alias` nodes,
        never `ast.Name`, so an import with no subsequent reference now
        contributes nothing to the reader pool."""
        root = _tree(
            tmp_path,
            settings_src='import os\nDEAD_KNOB = os.getenv("DEAD_KNOB", "0")\n',
            features_src="",
            other={"src/mcp/app/routers/unrelated.py": "from config.settings import DEAD_KNOB\n"},
        )
        assert _run(root) == 1

    def test_docstring_only_mention_is_not_a_reader(self, tmp_path):
        """The second bypass an adversarial review demonstrated: a file whose
        only content is a docstring mentioning the name, with zero call-site
        reference, must not launder it past the gate. A docstring is an
        `ast.Expr(ast.Constant)` statement, not a call argument, so it never
        enters the string-literal reader pool."""
        root = _tree(
            tmp_path,
            settings_src='import os\nDEAD_KNOB = os.getenv("DEAD_KNOB", "0")\n',
            features_src="",
            other={
                "src/mcp/app/routers/unrelated.py": (
                    '"""DEAD_KNOB is read by the scheduler."""\n'
                    "def handler():\n"
                    "    return {}\n"
                ),
            },
        )
        assert _run(root) == 1


class TestGreenCases:
    """Each must exit 0."""

    def test_getenv_backed_name_with_reader_passes(self, tmp_path):
        root = _tree(
            tmp_path,
            settings_src='import os\nLIVE_KNOB = os.getenv("LIVE_KNOB", "0")\n',
            features_src="",
            other={
                "src/mcp/app/routers/thing.py": (
                    "from config.settings import LIVE_KNOB\n"
                    "value = LIVE_KNOB\n"
                ),
            },
        )
        assert _run(root) == 0

    def test_wrapped_getenv_call_still_detected_and_read(self, tmp_path):
        """int(os.getenv(...)) / list-comprehension-wrapped getenv must still
        be recognized as env-backed (SCAN_EXCLUDE_PATTERNS's shape)."""
        root = _tree(
            tmp_path,
            settings_src='import os\nMAX_ITEMS = int(os.getenv("MAX_ITEMS", "5"))\n',
            features_src="",
            other={"src/mcp/app/services/thing.py": "cap = config.MAX_ITEMS\n"},
        )
        assert _run(root) == 0

    def test_container_literal_is_out_of_scope(self, tmp_path):
        """Dict/list/set/tuple constants are internal lookup tables, not
        individually-consumed knobs — must not be flagged at all, even when
        it is the only otherwise-unread name in the file (a live scalar
        keeps the declared-name oracle non-empty)."""
        root = _tree(
            tmp_path,
            settings_src=(
                "LOOKUP_TABLE = {'a': 1, 'b': 2}\n"
                'import os\nLIVE_KNOB = os.getenv("LIVE_KNOB", "0")\n'
            ),
            features_src="",
            other={
                "src/mcp/app/routers/thing.py": (
                    "from config.settings import LIVE_KNOB\n"
                    "value = LIVE_KNOB\n"
                ),
            },
        )
        assert _run(root) == 0

    def test_allowlisted_orphan_passes(self, tmp_path):
        root = _tree(
            tmp_path,
            settings_src='import os\nDEAD_KNOB = os.getenv("DEAD_KNOB", "0")\n',
            features_src="",
        )
        assert _run(root, allowlist={"DEAD_KNOB": "AF-047 — zero readers"}) == 0

    def test_features_py_name_with_reader_passes(self, tmp_path):
        root = _tree(
            tmp_path,
            settings_src="",
            features_src='import os\nLIVE_FLAG = os.getenv("LIVE_FLAG", "false") == "true"\n',
            other={"src/mcp/app/routers/thing.py": "if config.LIVE_FLAG:\n    pass\n"},
        )
        assert _run(root) == 0

    def test_string_literal_reader_still_passes(self, tmp_path):
        """Regression guard: a name re-read by its own literal string key
        elsewhere (`os.getenv("LIVE_KNOB")` in a different module than the
        one that declared it) is a REAL reader and must keep passing. An
        earlier pass of this gate's comment-stripping fix switched Python
        extraction to NAME-token-only, which silently also excluded STRING
        token content and produced ~80 false positives (CORS_ORIGINS,
        TAVILY_API_KEY, ENABLE_SENTRY, ...) for exactly this pattern before
        the fix was caught and corrected to blank only COMMENT spans."""
        root = _tree(
            tmp_path,
            settings_src='import os\nLIVE_KNOB = os.getenv("LIVE_KNOB", "0")\n',
            features_src="",
            other={
                "src/mcp/app/main.py": 'value = os.getenv("LIVE_KNOB", "0")\n',
            },
        )
        assert _run(root) == 0

    def test_wrapper_accessor_with_external_caller_makes_wrapped_name_a_reader(self, tmp_path):
        """The EMBEDDING_MODEL_VERSION shape: a module-level constant read
        only inside an accessor FUNCTION defined in the declaring file
        itself, as that function's fallback default. If the accessor has a
        genuine external caller, the constant it wraps is real, wired
        production config — not an orphan — even though the constant's own
        name never appears as a bare token outside settings.py/features.py."""
        root = _tree(
            tmp_path,
            settings_src=(
                'import os\n'
                'WRAPPED_KNOB = os.getenv("WRAPPED_KNOB", "0")\n'
                'PER_DOMAIN_OVERRIDE: dict[str, str] = {}\n'
                '\n'
                'def accessor_for_domain(domain):\n'
                '    return PER_DOMAIN_OVERRIDE.get(domain, WRAPPED_KNOB)\n'
            ),
            features_src="",
            other={
                "src/mcp/app/routers/thing.py": "value = config.accessor_for_domain('x')\n",
            },
        )
        assert _run(root) == 0

    def test_wrapper_accessor_with_no_external_caller_does_not_launder(self, tmp_path):
        """Negative control for the wrapper-transitivity carve-out: an
        accessor function defined in settings.py that is NEVER called from
        outside must not make the constant it references look read — that
        would reopen a laundering vector (declare a dead helper function
        that mentions the orphan, get it for free). Same fixture as the
        positive case above, minus the external call site."""
        root = _tree(
            tmp_path,
            settings_src=(
                'import os\n'
                'WRAPPED_KNOB = os.getenv("WRAPPED_KNOB", "0")\n'
                'PER_DOMAIN_OVERRIDE: dict[str, str] = {}\n'
                '\n'
                'def accessor_for_domain(domain):\n'
                '    return PER_DOMAIN_OVERRIDE.get(domain, WRAPPED_KNOB)\n'
            ),
            features_src="",
        )
        assert _run(root) == 1

    def test_comment_stripping_does_not_corrupt_line_numbers_or_syntax(self, tmp_path):
        """Comment-blanking must not shift any other token's position or
        break re-tokenization of the already-stripped text — a multi-comment
        file with a live reader on the same line as a comment must still
        resolve correctly."""
        root = _tree(
            tmp_path,
            settings_src='import os\nLIVE_KNOB = os.getenv("LIVE_KNOB", "0")  # see docs\n',
            features_src="",
            other={
                "src/mcp/app/main.py": (
                    "# unrelated comment one\n"
                    "value = os.getenv('LIVE_KNOB', '0')  # unrelated comment two\n"
                ),
            },
        )
        assert _run(root) == 0


def test_real_tree_head_is_green():
    """Smoke check: running the gate's own main() against the real repo
    tree (module-level REPO_ROOT/SETTINGS_FILE/FEATURES_FILE, untouched)
    must exit 0 — the checked-in ALLOWLIST must cover every currently-
    existing orphan the consolidated audit documented."""
    assert lint.main() == 0
