# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Request checks and prompt rendering, with a fake tokenizer where a model is needed."""

from __future__ import annotations

import json

import pytest
import serve

TOOL = {"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {}}}}


# -- tools and tool_choice ------------------------------------------------------


@pytest.mark.parametrize(
    ("body", "expected"),
    [
        ({}, (None, None)),
        ({"tools": []}, (None, None)),
        ({"tools": [TOOL]}, ([TOOL], "auto")),
        ({"tools": [TOOL], "tool_choice": "auto"}, ([TOOL], "auto")),
        ({"tools": [TOOL], "tool_choice": "none"}, (None, None)),
        ({"tools": [TOOL], "tool_choice": "required"}, ([TOOL], "required")),
        ({"tools": [TOOL], "tool_choice": {"type": "function", "function": {"name": "get_weather"}}}, ([TOOL], "get_weather")),
    ],
)
def test_tool_choice(body, expected):
    assert serve.chat_tools(body) == expected


@pytest.mark.parametrize(
    "body",
    [
        {"tools": {"get_weather": {}}},
        {"tools": [{"type": "retrieval"}]},
        {"tools": [{"type": "function", "function": {"name": "has.dot"}}]},
        {"tools": [{"type": "function", "function": {"name": "x" * 65}}]},
        {"tools": [{"type": "function", "function": {"name": "f", "parameters": "{}"}}]},
        {"tools": [TOOL, TOOL]},
        {"tools": [TOOL], "tool_choice": "any"},
        {"tools": [TOOL], "tool_choice": {"type": "function", "function": {"name": "other"}}},
        {"tool_choice": "required"},
    ],
)
def test_bad_tools_are_rejected(body):
    with pytest.raises(serve.BadRequest):
        serve.chat_tools(body)


# -- messages ---------------------------------------------------------------------


def test_openai_tool_history_is_normalised_for_templates():
    messages = serve.chat_messages(
        [
            {"role": "developer", "content": [{"type": "text", "text": "Be brief."}]},
            {"role": "user", "content": "Weather in Oslo?"},
            {
                "role": "assistant",
                "content": None,
                "reasoning_content": "Need a lookup.",
                "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": '{"location": "Oslo"}'}}],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "7C"},
        ]
    )
    assert messages == [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Weather in Oslo?"},
        {
            "role": "assistant",
            "content": "",
            "reasoning_content": "Need a lookup.",
            "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": {"location": "Oslo"}}}],
        },
        {"role": "tool", "content": "7C", "tool_call_id": "c1"},
    ]


def test_ollama_tool_history_is_accepted():
    messages = serve.chat_messages(
        [
            {"role": "user", "content": "Weather in Oslo?"},
            {"role": "assistant", "content": "", "thinking": "Lookup.", "tool_calls": [{"function": {"name": "get_weather", "arguments": {"location": "Oslo"}}}]},
            {"role": "tool", "tool_name": "get_weather", "content": "7C"},
        ]
    )
    assert messages[1]["tool_calls"][0]["function"]["arguments"] == {"location": "Oslo"}
    assert messages[1]["tool_calls"][0]["id"] == "call_1_0"
    assert messages[1]["reasoning_content"] == "Lookup."
    assert messages[2]["name"] == "get_weather"


@pytest.mark.parametrize(
    "messages",
    [
        [],
        "hello",
        [{"role": "robot", "content": "x"}],
        [{"role": "user", "content": [{"type": "image_url", "image_url": {"url": "x"}}]}],
        [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "f", "arguments": "{not json"}}]}],
        [{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "f", "arguments": "[1]"}}]}],
        [{"role": "assistant", "content": "", "tool_calls": [{"function": {"arguments": "{}"}}]}],
    ],
)
def test_bad_messages_are_rejected(messages):
    with pytest.raises(serve.BadRequest):
        serve.chat_messages(messages)


# -- rendering with a chat template --------------------------------------------


class FakeTokenizer:
    """Byte-per-token encoding with a toy ChatML template."""

    bos_token = None

    def __init__(self) -> None:
        self.calls = []

    def apply_chat_template(self, messages, add_generation_prompt, tokenize, **kwargs):
        self.calls.append(kwargs)
        text = ""
        if kwargs.get("tools"):
            text += "<sys>tools:" + json.dumps([t["function"]["name"] for t in kwargs["tools"]]) + "</sys>"
        for m in messages:
            text += f"<{m['role']}>{m['content']}"
            for c in m.get("tool_calls", []):
                text += f"<call>{c['function']['name']}:{json.dumps(c['function']['arguments'])}</call>"
            text += "</end>"
        if add_generation_prompt:
            text += "<assistant>"
        return text

    def encode(self, text, add_special_tokens=True):
        return list(text.encode())


USER = [{"role": "user", "content": "hi"}]


def test_plan_passes_tools_and_keeps_thinking_off():
    tok = FakeTokenizer()
    plan = serve.chat_plan("qwen3_coder", tok, USER, [TOOL], "auto")
    assert tok.calls == [{"enable_thinking": False, "tools": [TOOL]}] * 2
    assert bytes(plan["prompt"]).decode().endswith("<user>hi</end><assistant>")
    assert plan["opener_text"] == "" and plan["opener_tokens"] == []


def test_plan_without_tools_renders_no_tools():
    tok = FakeTokenizer()
    serve.chat_plan("qwen3_coder", tok, USER, None, None)
    assert tok.calls == [{"enable_thinking": False}] * 2


def test_checkpoint_is_the_conversation_before_the_generation_prompt():
    tok = FakeTokenizer()
    plan = serve.chat_plan("gemma4", tok, USER, None, None)
    history = tok.apply_chat_template(USER, add_generation_prompt=False, tokenize=False)
    assert plan["checkpoint"] == len(history.encode())
    assert plan["prompt"][: plan["checkpoint"]] == list(history.encode())


def test_checkpoint_never_reaches_the_last_prompt_token():
    class NoGenerationPrompt(FakeTokenizer):
        def apply_chat_template(self, messages, add_generation_prompt, tokenize, **kwargs):
            return super().apply_chat_template(messages, False, tokenize, **kwargs)

    plan = serve.chat_plan("gemma4", NoGenerationPrompt(), USER, None, None)
    assert plan["checkpoint"] == len(plan["prompt"]) - 1


def test_checkpoint_is_off_when_history_is_not_a_prefix():
    class Rewrites(FakeTokenizer):
        def apply_chat_template(self, messages, add_generation_prompt, tokenize, **kwargs):
            text = super().apply_chat_template(messages, add_generation_prompt, tokenize, **kwargs)
            return text if add_generation_prompt else "<x>" + text

    assert serve.chat_plan("gemma4", Rewrites(), USER, None, None)["checkpoint"] == 0


@pytest.mark.parametrize(
    ("fmt", "choice", "opener"),
    [
        ("qwen3_coder", "required", "<tool_call>\n<function="),
        ("qwen3_coder", "get_weather", "<tool_call>\n<function=get_weather>\n"),
        ("gemma4", "required", "<|tool_call>call:"),
        ("gemma4", "get_weather", "<|tool_call>call:get_weather{"),
    ],
)
def test_forced_tool_choice_opens_the_reply_with_a_call(fmt, choice, opener):
    plan = serve.chat_plan(fmt, FakeTokenizer(), USER, [TOOL], choice)
    assert plan["opener_text"] == opener
    assert bytes(plan["prompt"]).decode().endswith("<assistant>" + opener)
    assert bytes(plan["opener_tokens"]).decode() == opener


def test_template_rejection_is_a_bad_request():
    import jinja2

    class Raises(FakeTokenizer):
        def apply_chat_template(self, *a, **k):
            raise jinja2.TemplateError("No user query found in messages.")

    with pytest.raises(serve.BadRequest, match="No user query"):
        serve.chat_plan("qwen3_coder", Raises(), USER, None, None)


# -- harmony rendering ------------------------------------------------------------

HISTORY = [
    {"role": "system", "content": "Be brief."},
    {"role": "user", "content": "Weather in Oslo?"},
    {
        "role": "assistant",
        "content": "",
        "reasoning_content": "Need the weather tool.",
        "tool_calls": [{"id": "c1", "type": "function", "function": {"name": "get_weather", "arguments": {"location": "Oslo"}}}],
    },
    {"role": "tool", "tool_call_id": "c1", "content": "7C"},
]


def test_harmony_keeps_the_analysis_of_a_turn_that_is_still_calling_tools(harmony):
    _, enc = serve._harmony()
    plan = serve.chat_plan("harmony", None, HISTORY, [TOOL], "auto")
    text = enc.decode(plan["prompt"])
    assert "# Instructions\n\nBe brief." in text
    assert "type get_weather" in text
    assert "<|channel|>analysis<|message|>Need the weather tool.<|end|>" in text
    assert '<|start|>assistant to=functions.get_weather<|channel|>commentary <|constrain|>json<|message|>{"location": "Oslo"}<|call|>' in text
    assert "<|start|>functions.get_weather to=assistant<|channel|>commentary<|message|>7C<|end|>" in text
    assert text.endswith("<|start|>assistant")
    assert plan["stops"] == set(enc.encode("<|call|><|return|>", allowed_special="all"))
    assert plan["prompt"][: plan["checkpoint"]] == enc.render_conversation(serve.harmony_conversation(HISTORY, [TOOL]))


def test_harmony_drops_analysis_once_a_turn_has_answered(harmony):
    _, enc = serve._harmony()
    answered = [
        *HISTORY,
        {"role": "assistant", "content": "It is 7C.", "reasoning_content": "Report it."},
        {"role": "user", "content": "Thanks"},
    ]
    text = enc.decode(serve.chat_plan("harmony", None, answered, [TOOL], "auto")["prompt"])
    assert "Need the weather tool." not in text and "Report it." not in text
    assert "<|channel|>final<|message|>It is 7C.<|end|>" in text


def test_harmony_tool_result_needs_a_name(harmony):
    with pytest.raises(serve.BadRequest):
        serve.chat_plan("harmony", None, [{"role": "user", "content": "x"}, {"role": "tool", "tool_call_id": "zz", "content": "1"}], [TOOL], "auto")


def test_harmony_forced_call_comes_after_the_analysis(harmony):
    _, enc = serve._harmony()
    plan = serve.chat_plan("harmony", None, HISTORY[:2], [TOOL], "get_weather")
    assert enc.decode(plan["prompt"]).endswith("<|start|>assistant<|channel|>analysis<|message|>")
    assert plan["end"] == enc.encode("<|end|>", allowed_special="all")[0]
    assert enc.decode(plan["then"]) == "<|start|>assistant<|channel|>commentary to=functions.get_weather <|constrain|>json<|message|>"
    required = serve.chat_plan("harmony", None, HISTORY[:2], [TOOL], "required")
    assert enc.decode(required["then"]) == "<|start|>assistant<|channel|>commentary to=functions."


# -- options ------------------------------------------------------------------------


def test_v1_options():
    assert serve._v1_options({}) == (serve.V1_DEFAULT_MAX_TOKENS, 0.0, 1.0)
    assert serve._v1_options({"max_tokens": 10, "temperature": 0.7, "top_p": 0.9}) == (10, 0.7, 0.9)
    assert serve._v1_options({"max_completion_tokens": 12, "max_tokens": 10})[0] == 12
    for bad in ({"max_tokens": 0}, {"max_tokens": serve.V1_MAX_TOKENS + 1}, {"max_tokens": "10"}, {"temperature": "hot"}):
        with pytest.raises(serve.BadRequest):
            serve._v1_options(bad)
