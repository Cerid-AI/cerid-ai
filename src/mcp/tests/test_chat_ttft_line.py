# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""The TTFT clock starts at generated text, not the routing frame.

``tests/test_latency_slo.py`` carries ``benchmark_slo`` and is excluded
from the default run, so this contract lives here.
"""

from tests.helpers.chat_ttft import sse_data_is_generated_token


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
