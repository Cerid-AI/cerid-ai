# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The TTFT clock starts at generated text, not the routing frame.

``tests/test_latency_slo.py`` carries ``benchmark_slo`` and is excluded
from the default run, so this contract lives here.
"""

from tests.helpers.chat_ttft import served_local_chat_model, sse_data_is_generated_token


def test_a_routing_frame_is_not_a_token():
    meta = 'data: {"cerid_meta": {"requested_model": "a", "resolved_model": "b"}}'
    update = 'data: {"cerid_meta_update": {"actual_model": "b"}}'
    assert sse_data_is_generated_token(meta) is False
    assert sse_data_is_generated_token(update) is False


def test_a_role_only_delta_is_not_a_token():
    line = 'data: {"choices": [{"delta": {"role": "assistant"}}]}'
    assert sse_data_is_generated_token(line) is False


def test_an_error_frame_is_not_a_token():
    line = 'data: {"error": {"message": "upstream"}}'
    assert sse_data_is_generated_token(line) is False


def test_done_and_non_data_lines_are_not_tokens():
    assert sse_data_is_generated_token("data: [DONE]") is False
    assert sse_data_is_generated_token(": keep-alive") is False
    assert sse_data_is_generated_token("") is False


def test_delta_content_is_a_token():
    line = 'data: {"choices": [{"delta": {"content": "hi"}}]}'
    assert sse_data_is_generated_token(line) is True


def test_message_content_is_a_token():
    line = 'data: {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}'
    assert sse_data_is_generated_token(line) is True


# The local TTFT case takes its model from GET /providers/routing, the snapshot
# that lists what the local server actually serves, and skips when it serves
# nothing — a name guessed from config would measure a 404 or a cloud fallback.


def test_the_first_served_local_chat_model_is_the_benchmark_model():
    routing = {"ollama_available": True, "ollama_models": ["gemma-4-26b-a4b", "qwen3.5-4b-instruct"]}
    assert served_local_chat_model(routing) == "gemma-4-26b-a4b"


def test_an_unavailable_local_server_serves_no_model():
    assert served_local_chat_model({"ollama_available": False, "ollama_models": ["gemma-4-26b-a4b"]}) is None


def test_an_empty_catalog_serves_no_model():
    assert served_local_chat_model({"ollama_available": True, "ollama_models": []}) is None
    assert served_local_chat_model({}) is None
