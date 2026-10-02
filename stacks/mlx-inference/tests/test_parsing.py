# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Tool-call parsing edge cases the corpus does not reach."""

from __future__ import annotations

import pytest
import serve

WEATHER = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "parameters": {
                "type": "object",
                "properties": {"location": {"type": "string"}, "days": {"type": "integer"}},
            },
        },
    }
]


def parse(fmt, text, finish="stop", tools=WEATHER):
    return serve.parse_reply(fmt, text, [], tools, finish)


# -- gemma4 ---------------------------------------------------------------------


def test_gemma_plain_text_with_tools_is_content_not_an_error(parsers):
    # mlx-lm #1125: the gemma4 parser raises "No function provided." on a reply
    # with no call. A plain reply must never reach it.
    got = parse("gemma4", "Sure, I will do that.")
    assert got == {"content": "Sure, I will do that.", "reasoning": None, "calls": [], "finish": "stop", "error": None}


def test_gemma_marker_without_a_parseable_call_is_invalid_not_raised(parsers):
    text = "<|tool_call>get_weather(location='Paris')<tool_call|>"
    got = parse("gemma4", text)
    assert got["finish"] == "invalid_tool_call"
    assert got["content"] == text and got["calls"] == []


def test_gemma_argument_json_that_does_not_parse_is_returned_raw(parsers):
    text = '<|tool_call>call:get_weather{location:<|"|>Paris<|"|>,days:three}<tool_call|>'
    got = parse("gemma4", text)
    assert got["finish"] == "invalid_tool_call"
    assert got["content"] == text
    assert "JSONDecodeError" in got["error"]


def test_gemma_prose_before_a_call_is_content(parsers):
    text = 'Checking.<|tool_call>call:get_weather{location:<|"|>Oslo<|"|>}<tool_call|>'
    got = parse("gemma4", text)
    assert got["content"] == "Checking."
    assert got["calls"] == [{"name": "get_weather", "arguments": {"location": "Oslo"}}]
    assert got["finish"] == "tool_calls"


def test_gemma_unterminated_call_at_max_tokens_keeps_length(parsers):
    text = '<|tool_call>call:get_weather{location:<|"|>Os'
    assert parse("gemma4", text, finish="length")["finish"] == "length"
    assert parse("gemma4", text, finish="stop")["finish"] == "invalid_tool_call"


# -- qwen3_coder ----------------------------------------------------------------


def test_qwen_arguments_follow_the_schema_types(parsers):
    text = (
        "<tool_call>\n<function=get_weather>\n<parameter=location>\nOslo\n</parameter>\n"
        "<parameter=days>\n3\n</parameter>\n</function>\n</tool_call>"
    )
    assert parse("qwen3_coder", text)["calls"] == [{"name": "get_weather", "arguments": {"location": "Oslo", "days": 3}}]


def test_qwen_argument_that_does_not_fit_its_type_is_invalid(parsers):
    text = (
        "<tool_call>\n<function=get_weather>\n<parameter=days>\nthree\n</parameter>\n</function>\n</tool_call>"
    )
    got = parse("qwen3_coder", text)
    assert got["finish"] == "invalid_tool_call" and got["content"] == text


def test_qwen_one_bad_call_among_several_fails_the_whole_reply(parsers):
    good = "<tool_call>\n<function=get_weather>\n<parameter=location>\nOslo\n</parameter>\n</function>\n</tool_call>"
    bad = "<tool_call>\n<function=get_weather\n</tool_call>"
    got = parse("qwen3_coder", good + "\n" + bad)
    assert got["finish"] == "invalid_tool_call" and got["calls"] == []


def test_no_tools_offered_means_no_parsing(parsers):
    text = "<tool_call>\n<function=get_weather>\n</function>\n</tool_call>"
    got = parse("qwen3_coder", text, tools=None)
    assert got["calls"] == [] and got["content"] == text and got["finish"] == "stop"


# -- harmony --------------------------------------------------------------------


def hparse(text, finish="stop", tools=WEATHER):
    _, enc = serve._harmony()
    return serve.parse_reply("harmony", "", enc.encode(text, allowed_special="all"), tools, finish)


CALL = (
    "<|channel|>analysis<|message|>Need weather.<|end|><|start|>assistant"
    "<|channel|>commentary to=functions.get_weather <|constrain|>json<|message|>{ARGS}<|call|>"
)


def test_harmony_call_keeps_the_analysis_as_reasoning(harmony):
    got = hparse(CALL.replace("{ARGS}", '{"location":"Oslo"}'))
    assert got["calls"] == [{"name": "get_weather", "arguments": {"location": "Oslo"}}]
    assert got["reasoning"] == "Need weather."
    assert got["content"] is None and got["finish"] == "tool_calls"


def test_harmony_recipient_before_channel_is_the_same_call(harmony):
    text = (
        "<|channel|>analysis<|message|>Need weather.<|end|>"
        '<|start|>assistant to=functions.get_weather<|channel|>commentary <|constrain|>json<|message|>{"location":"Oslo"}<|call|>'
    )
    assert hparse(text)["calls"] == [{"name": "get_weather", "arguments": {"location": "Oslo"}}]


def test_harmony_arguments_that_are_not_json_are_returned_raw(harmony):
    got = hparse(CALL.replace("{ARGS}", "{location: Oslo}"))
    assert got["finish"] == "invalid_tool_call" and got["calls"] == []
    assert "{location: Oslo}" in got["content"]


@pytest.mark.parametrize("args", ['["Oslo"]', '"Oslo"'])
def test_harmony_arguments_must_be_an_object(harmony, args):
    assert hparse(CALL.replace("{ARGS}", args))["finish"] == "invalid_tool_call"


def test_harmony_call_to_a_tool_not_offered_is_invalid(harmony):
    text = CALL.replace("functions.get_weather", "browser.search").replace("{ARGS}", '{"query":"x"}')
    assert hparse(text)["finish"] == "invalid_tool_call"


def test_harmony_call_cut_off_by_max_tokens_is_not_a_call(harmony):
    text = CALL.replace("{ARGS}", '{"location":"Os').removesuffix("<|call|>")
    got = hparse(text, finish="length")
    assert got["finish"] == "length" and got["calls"] == []


def test_harmony_final_answer_with_tools_offered(harmony):
    got = hparse("<|channel|>analysis<|message|>Easy.<|end|><|start|>assistant<|channel|>final<|message|>42<|return|>")
    assert got == {"content": "42", "reasoning": "Easy.", "calls": [], "finish": "stop", "error": None}


def test_harmony_without_tools_returns_the_final_channel(harmony):
    got = hparse("<|channel|>analysis<|message|>Hm.<|end|><|start|>assistant<|channel|>final<|message|>Hi<|return|>", tools=None)
    assert got["content"] == "Hi" and got["calls"] == []


# -- response shapes ------------------------------------------------------------


def test_openai_message_arguments_are_a_json_string_with_ids():
    parsed = {"content": None, "reasoning": "r", "calls": [{"name": "f", "arguments": {"a": "é"}}], "finish": "tool_calls"}
    message = serve.openai_message(parsed)
    call = message["tool_calls"][0]
    assert call["type"] == "function" and call["id"].startswith("call_")
    assert call["function"] == {"name": "f", "arguments": '{"a": "é"}'}
    assert message["content"] is None and message["reasoning_content"] == "r"


def test_openai_message_without_calls_has_string_content():
    message = serve.openai_message({"content": None, "reasoning": None, "calls": [], "finish": "stop"})
    assert message == {"role": "assistant", "content": ""}


def test_ollama_message_arguments_are_an_object():
    parsed = {"content": None, "reasoning": "r", "calls": [{"name": "f", "arguments": {"a": 1}}], "finish": "tool_calls"}
    assert serve.ollama_message(parsed) == {
        "role": "assistant",
        "content": "",
        "thinking": "r",
        "tool_calls": [{"function": {"name": "f", "arguments": {"a": 1}}}],
    }
