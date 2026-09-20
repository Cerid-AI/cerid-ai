# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Plant-fault tests for scripts/lint-model-label-provenance.py (gate G-H)."""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location(
    "lint_model_label_provenance", _ROOT / "scripts" / "lint-model-label-provenance.py",
)
assert _SPEC is not None and _SPEC.loader is not None
lint = importlib.util.module_from_spec(_SPEC)
sys.modules["lint_model_label_provenance"] = lint
_SPEC.loader.exec_module(lint)


def _check(source: str, rel: str = "src/mcp/app/routers/fake.py") -> list["lint.Violation"]:
    return lint.check_source(source, rel)


class TestFrozenLabels:
    def test_string_literal_provider_flagged(self) -> None:
        src = (
            "@router.post('/ollama/enable')\n"
            "async def enable_ollama():\n"
            "    return {'status': 'enabled', 'provider': 'ollama'}\n"
        )
        v = _check(src)
        assert [(x.label, x.kind) for x in v] == [("provider", "literal")]

    def test_module_constant_model_flagged(self) -> None:
        src = (
            "DEFAULT_MODEL = 'llama3'\n"
            "\n"
            "@router.get('/answer')\n"
            "async def answer():\n"
            "    return {'text': out, 'model': DEFAULT_MODEL}\n"
        )
        assert [(x.label, x.kind) for x in _check(src)] == [("model", "module-constant")]

    def test_value_imported_constant_flagged(self) -> None:
        src = (
            "from config import DEFAULT_MODEL\n"
            "\n"
            "@router.get('/answer')\n"
            "async def answer():\n"
            "    return {'model': DEFAULT_MODEL}\n"
        )
        assert [x.kind for x in _check(src)] == ["module-constant"]

    def test_getenv_with_nonempty_default_flagged(self) -> None:
        src = (
            "@router.get('/settings')\n"
            "async def get_settings_endpoint():\n"
            "    return {'embeddings_provider': os.getenv('EMBEDDINGS_PROVIDER', 'sidecar')}\n"
        )
        assert [x.kind for x in _check(src)] == ["getenv-default"]

    def test_response_model_keyword_arg_flagged_when_frozen(self) -> None:
        """Constructor kwargs count too — `Resp(model=DEFAULT_MODEL)`."""
        src = (
            "DEFAULT_MODEL = 'llama3'\n"
            "\n"
            "@router.get('/answer')\n"
            "async def answer():\n"
            "    return AnswerResponse(text=out, model=DEFAULT_MODEL)\n"
        )
        assert [x.kind for x in _check(src)] == ["module-constant"]


class TestLiveLabelsAccepted:
    def test_label_from_the_call_result_not_flagged(self) -> None:
        src = (
            "@router.get('/answer')\n"
            "async def answer():\n"
            "    result = await call_llm(prompt)\n"
            "    return {'text': result.text, 'model': result.model}\n"
        )
        assert _check(src) == []

    def test_label_from_a_local_not_flagged(self) -> None:
        src = (
            "@router.get('/answer')\n"
            "async def answer():\n"
            "    model = resolve_model()\n"
            "    return {'model': model}\n"
        )
        assert _check(src) == []

    def test_getenv_with_empty_default_not_flagged(self) -> None:
        """`""` reads as "unset" downstream, not as a confident wrong answer."""
        src = (
            "@router.get('/settings')\n"
            "async def get_settings_endpoint():\n"
            "    return {'quenchforge_embed_model': os.getenv('QUENCHFORGE_EMBED_MODEL', '')}\n"
        )
        assert _check(src) == []

    def test_getattr_without_literal_default_not_flagged(self) -> None:
        src = (
            "@router.get('/settings')\n"
            "async def get_settings_endpoint():\n"
            "    return {'model': getattr(config, 'INTERNAL_LLM_MODEL')}\n"
        )
        assert _check(src) == []


class TestScope:
    def test_fastapi_response_model_kwarg_not_a_label(self) -> None:
        """`response_model=` is a schema declaration; flagging it would bury
        the gate under every decorated route in the repo."""
        src = (
            "@router.get('/x', response_model=Thing)\n"
            "async def x():\n"
            "    return {'ok': True}\n"
        )
        assert _check(src) == []

    def test_constant_used_to_choose_a_model_not_flagged(self) -> None:
        """Selecting a model is not a claim about what ran."""
        src = (
            "DEFAULT_MODEL = 'llama3'\n"
            "\n"
            "@router.get('/answer')\n"
            "async def answer():\n"
            "    out = await call_llm(prompt, model=DEFAULT_MODEL)\n"
            "    return {'text': out}\n"
        )
        assert _check(src) == []

    def test_undecorated_helper_not_flagged(self) -> None:
        src = (
            "DEFAULT_MODEL = 'llama3'\n"
            "\n"
            "def _describe():\n"
            "    return {'model': DEFAULT_MODEL}\n"
        )
        assert _check(src) == []

    def test_mcp_tool_handler_in_scope(self) -> None:
        src = (
            "DEFAULT_MODEL = 'llama3'\n"
            "\n"
            "@register_tool(name='pkb_answer')\n"
            "async def pkb_answer(params):\n"
            "    return {'model': DEFAULT_MODEL}\n"
        )
        assert [x.kind for x in _check(src, "src/mcp/app/mcp_tools/x.py")] == ["module-constant"]


class TestAllowlist:
    def test_every_repo_entry_has_a_reason(self) -> None:
        for key, reason in lint._load_allowlist().items():
            assert reason and not reason.startswith("TODO"), f"{key} has no reason"

    def test_live_hits_match_the_allowlist(self) -> None:
        live = {v.key() for v in lint.collect_all()}
        allow = set(lint._load_allowlist())
        assert live == allow, (
            f"unallowlisted: {sorted(live - allow)}; stale: {sorted(allow - live)}"
        )
