# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Plant-fault tests for scripts/lint-env-example-orphans.py (gate G-F).

The symbol/env-var indirection is the whole reason the drift survived two
gates, so it gets its own test: CLASSIFICATION_ENABLED is what one gate
records and CERID_CLASSIFICATION is what the other publishes, and a
same-string comparison sees nothing.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_env_example_orphans", _ROOT / "scripts" / "lint-env-example-orphans.py",
)
assert _SPEC is not None and _SPEC.loader is not None
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_env_example_orphans"] = lint
_SPEC.loader.exec_module(lint)


class TestSymbolToEnvVarMapping:
    def test_symbol_differing_from_env_var_is_resolved(self) -> None:
        decls = {
            "settings.py": 'CLASSIFICATION_ENABLED = os.getenv("CERID_CLASSIFICATION", "false")\n'
        }
        assert lint.symbol_to_env_vars(decls) == {
            "CLASSIFICATION_ENABLED": {"CERID_CLASSIFICATION"}
        }

    def test_environ_get_form_is_resolved(self) -> None:
        decls = {"settings.py": 'X = os.environ.get("X_ENV", "1")\n'}
        assert lint.symbol_to_env_vars(decls) == {"X": {"X_ENV"}}

    def test_declaration_with_no_env_read_is_ignored(self) -> None:
        decls = {"settings.py": "SYNC_CRDT_ENABLED = True\n"}
        assert lint.symbol_to_env_vars(decls) == {}


class TestPublishedKeys:
    def test_commented_out_computed_default_still_counts_as_published(self) -> None:
        """gen_env_example emits computed defaults commented out; the operator
        still reads the name as a documented lever."""
        assert lint.published_keys("# CERID_MACHINE_ID=\nFOO=1\n") == {
            "CERID_MACHINE_ID",
            "FOO",
        }

    def test_prose_is_not_a_key(self) -> None:
        assert lint.published_keys("# Set this to enable things\n") == set()


class TestAnalyse:
    def test_orphan_published_under_a_different_name_is_flagged(self) -> None:
        orphans = {"CLASSIFICATION_ENABLED": "GATE2-SELF-001 — zero readers"}
        decls = {"s.py": 'CLASSIFICATION_ENABLED = os.getenv("CERID_CLASSIFICATION", "false")\n'}
        v = lint.analyse(orphans, decls, "CERID_CLASSIFICATION=false\n")
        assert len(v) == 1
        assert v[0].env_var == "CERID_CLASSIFICATION"
        assert v[0].symbol == "CLASSIFICATION_ENABLED"
        assert "GATE2-SELF-001" in v[0].reason

    def test_orphan_not_published_is_not_flagged(self) -> None:
        """Half the reader gate's allowlist is already absent from
        .env.example; those are not this gate's business."""
        orphans = {"ENABLE_DEGRADATION_TIERS": "GATE2-SELF-003"}
        decls = {"f.py": 'ENABLE_DEGRADATION_TIERS = os.getenv("ENABLE_DEGRADATION_TIERS", "0")\n'}
        assert lint.analyse(orphans, decls, "OTHER=1\n") == []

    def test_published_name_with_a_reader_is_not_flagged(self) -> None:
        """The control: the overwhelming majority of .env.example is fine."""
        decls = {"s.py": 'CHROMA_URL = os.getenv("CHROMA_URL", "http://localhost:8000")\n'}
        assert lint.analyse({}, decls, "CHROMA_URL=http://localhost:8000\n") == []

    def test_removing_the_reader_gate_entry_clears_this_gate(self) -> None:
        """The join is what makes the two gates agree: fixing it upstream —
        wiring a reader, so the entry leaves the reader gate's ALLOWLIST —
        is what removes the hit here. There is no way to satisfy both while
        shipping a dead knob."""
        decls = {"s.py": 'CLASSIFICATION_ENABLED = os.getenv("CERID_CLASSIFICATION", "false")\n'}
        env = "CERID_CLASSIFICATION=false\n"
        assert lint.analyse({"CLASSIFICATION_ENABLED": "orphan"}, decls, env)
        assert lint.analyse({}, decls, env) == []


class TestLiveWiring:
    def test_reader_gate_allowlist_is_actually_loaded(self) -> None:
        """If the import seam breaks, this gate silently passes forever."""
        orphans = lint.orphan_symbols()
        assert orphans, "lint-env-has-reader.py ALLOWLIST did not load"
        assert "CLASSIFICATION_ENABLED" in orphans

    def test_live_hits_match_the_allowlist(self) -> None:
        live = {v.key() for v in lint.collect_all()}
        allow = set(lint._load_allowlist())
        assert live == allow, (
            f"unallowlisted: {sorted(live - allow)}; stale: {sorted(allow - live)}"
        )

    def test_every_entry_has_a_reason(self) -> None:
        for key, reason in lint._load_allowlist().items():
            assert reason and not reason.startswith("TODO"), f"{key} has no reason"
