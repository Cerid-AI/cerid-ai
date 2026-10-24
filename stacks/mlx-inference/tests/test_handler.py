# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""HTTP handling against a fake engine: shapes, errors, and the untouched Ollama path."""

from __future__ import annotations

import http.client
import json
import socket
import threading

import pytest
import serve

TOOLS = [
    {
        "type": "function",
        "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}}},
    }
]
QWEN_CALL = "<tool_call>\n<function=get_weather>\n<parameter=location>\nParis\n</parameter>\n</function>\n</tool_call>"


def write_catalog(tmp_path, tiers=None, **extra_rows):
    rows = {
        "small-q": {"tier": "small", "family": "qwen3_5", "tool_format": "qwen3_coder"},
        "default-g": {"tier": "default", "family": "gemma4", "tool_format": "gemma4"},
        "heavy-o": {"tier": "heavy", "family": "gpt-oss", "tool_format": "harmony"},
        "plain": {"tier": "heavy", "family": "llama"},
        **extra_rows,
    }
    models = {}
    for name, spec in rows.items():
        d = tmp_path / name
        d.mkdir(exist_ok=True)
        models[name] = {"path": str(d), **spec}
    raw = {"models": models, "aliases": {"old-small": "small"}}
    if tiers is not None:
        raw["tiers"] = tiers
    path = tmp_path / "models.json"
    path.write_text(json.dumps(raw))
    return path


def stats(text="", tokens=(), finish="stop", prompt=100, cached=40, completion=7):
    return {
        "prompt_tokens": prompt,
        "cached_tokens": cached,
        "completion_tokens": completion,
        "prompt_eval_duration": 5,
        "eval_duration": 6,
        "finish": finish,
        "text": text,
        "tokens": list(tokens),
    }


class FakeEngine:
    def __init__(self) -> None:
        self._state = threading.Lock()
        self.load_errors = {}
        self.heavy = None
        self.jobs = []
        self.items = []

    def resident(self):
        return ["small-q", "default-g"]

    def cache_stats(self):
        return {"default-g": {"entries": 2, "bytes": 1024}}

    def submit(self, job):
        self.jobs.append(job)
        for item in self.items:
            job.out.put(item)
        job.out.put(None)


@pytest.fixture
def server(tmp_path, monkeypatch):
    catalog = serve.Catalog(write_catalog(tmp_path))
    engine = FakeEngine()
    monkeypatch.setattr(serve.Handler, "engine", engine, raising=False)
    monkeypatch.setattr(serve.Handler, "catalog", catalog, raising=False)
    httpd = serve.Server(("127.0.0.1", 0), serve.Handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def post(path, body):
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, resp.getheader("Content-Type"), resp.read()

    def get(path):
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())

    yield engine, post, get
    httpd.shutdown()


def v1(model, **body):
    return {"model": model, "messages": [{"role": "user", "content": "Weather in Paris?"}], **body}


# -- /v1/chat/completions --------------------------------------------------------


def test_v1_tool_call(server, parsers):
    engine, post, _ = server
    engine.items = [("", True, stats(QWEN_CALL))]
    code, ctype, raw = post("/v1/chat/completions", v1("small-q", tools=TOOLS, max_tokens=64))
    assert code == 200 and ctype == "application/json"
    body = json.loads(raw)
    job = engine.jobs[0]
    assert isinstance(job, serve.ChatJob) and job.tools == TOOLS and job.choice == "auto" and job.max_tokens == 64
    assert body["object"] == "chat.completion" and body["model"] == "small-q" and body["id"].startswith("chatcmpl-")
    choice = body["choices"][0]
    assert choice["finish_reason"] == "tool_calls"
    call = choice["message"]["tool_calls"][0]
    assert call["type"] == "function" and call["function"] == {"name": "get_weather", "arguments": '{"location": "Paris"}'}
    assert choice["message"]["content"] is None
    assert body["usage"] == {
        "prompt_tokens": 100,
        "completion_tokens": 7,
        "total_tokens": 107,
        "prompt_tokens_details": {"cached_tokens": 40},
    }


def test_v1_unparseable_call_comes_back_as_content(server, parsers):
    engine, post, _ = server
    broken = QWEN_CALL.replace("<function=get_weather>", "<function get_weather")
    engine.items = [("", True, stats(broken))]
    body = json.loads(post("/v1/chat/completions", v1("small-q", tools=TOOLS))[2])
    assert body["choices"][0]["finish_reason"] == "invalid_tool_call"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": broken}


def test_v1_plain_chat(server):
    engine, post, _ = server
    engine.items = [("", True, stats("Hello there."))]
    body = json.loads(post("/v1/chat/completions", v1("default-g"))[2])
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Hello there."}
    assert body["choices"][0]["finish_reason"] == "stop"
    assert engine.jobs[0].tools is None


def test_v1_no_model_is_the_default(server):
    engine, post, _ = server
    engine.items = [("", True, stats("x"))]
    post("/v1/chat/completions", {"messages": [{"role": "user", "content": "x"}]})
    assert engine.jobs[0].name == "default-g"


def test_v1_tool_choice_none_offers_no_tools(server):
    engine, post, _ = server
    engine.items = [("", True, stats(QWEN_CALL))]
    body = json.loads(post("/v1/chat/completions", v1("small-q", tools=TOOLS, tool_choice="none"))[2])
    assert engine.jobs[0].tools is None
    assert body["choices"][0]["finish_reason"] == "stop" and "tool_calls" not in body["choices"][0]["message"]


@pytest.mark.parametrize(
    ("body", "code", "needle"),
    [
        (v1("small-q", tools=TOOLS, stream=True), 400, "stream is not supported with tools"),
        (v1("plain", tools=TOOLS), 400, "has no tool format"),
        (v1("nope"), 404, "not found"),
        (v1("small-q", tools=TOOLS, tool_choice="sometimes"), 400, "tool_choice"),
        (v1("small-q", max_tokens=0), 400, "max_tokens"),
        ({"model": "small-q", "messages": []}, 400, "messages"),
    ],
)
def test_v1_request_errors(server, body, code, needle):
    engine, post, _ = server
    got, _, raw = post("/v1/chat/completions", body)
    error = json.loads(raw)["error"]
    assert got == code and needle in error["message"] and "type" in error
    assert engine.jobs == []


def test_v1_error_raised_while_rendering_is_a_400(server):
    engine, post, _ = server
    engine.items = [serve.BadRequest("the model's chat template rejected the messages")]
    code, _, raw = post("/v1/chat/completions", v1("small-q"))
    assert code == 400 and "chat template" in json.loads(raw)["error"]["message"]


def test_v1_stream_render_error_is_still_a_400(server):
    engine, post, _ = server
    engine.items = [serve.BadRequest("No user query found in messages.")]
    code, ctype, raw = post("/v1/chat/completions", v1("small-q", stream=True))
    assert code == 400 and ctype == "application/json" and "No user query" in json.loads(raw)["error"]["message"]


def test_v1_engine_failure_is_a_500(server):
    engine, post, _ = server
    engine.items = [RuntimeError("metal went away")]
    code, _, raw = post("/v1/chat/completions", v1("small-q"))
    assert code == 500 and "metal went away" in json.loads(raw)["error"]["message"]


def events(raw: bytes) -> list:
    out = []
    for block in raw.decode().split("\n\n"):
        if block.startswith("data: "):
            data = block[len("data: ") :]
            out.append(data if data == "[DONE]" else json.loads(data))
    return out


def test_v1_stream_without_tools(server):
    engine, post, _ = server
    engine.items = [("Hel", False, 1), ("lo", False, 2), ("", True, stats("Hello"))]
    code, ctype, raw = post("/v1/chat/completions", v1("default-g", stream=True, stream_options={"include_usage": True}))
    assert code == 200 and ctype == "text/event-stream"
    got = events(raw)
    assert got[-1] == "[DONE]"
    chunks = got[:-1]
    assert all(c["object"] == "chat.completion.chunk" and c["id"] == chunks[0]["id"] for c in chunks)
    assert chunks[0]["choices"][0]["delta"] == {"role": "assistant", "content": ""}
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"]) == "Hello"
    assert chunks[-2]["choices"][0]["finish_reason"] == "stop"
    assert chunks[-1]["choices"] == [] and chunks[-1]["usage"]["prompt_tokens"] == 100


def test_v1_stream_holds_control_text(server):
    engine, post, _ = server
    engine.items = [("Hi", False, 1), ("<|channel>thought", False, 2), ("<channel|> there", False, 3), ("", True, stats("Hi<|channel>thought<channel|> there"))]
    got = events(post("/v1/chat/completions", v1("default-g", stream=True))[2])
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in got[:-1] if c["choices"])
    assert "<|" not in text and "<channel|>" not in text


STRAY_CLOSER = [("5: Wrong", False, 1), (" sign.", False, 2), ("<channel|>", False, 3), ("\n", False, 4), ("", True, stats("5: Wrong sign.<channel|>\n"))]


def test_v1_stream_keeps_the_answer_before_a_stray_closer(server):
    engine, post, _ = server
    engine.items = STRAY_CLOSER
    got = events(post("/v1/chat/completions", v1("default-g", stream=True))[2])
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in got[:-1] if c["choices"]) == "5: Wrong sign."


def test_api_chat_stream_keeps_the_answer_before_a_stray_closer(server):
    engine, post, _ = server
    engine.items = [*STRAY_CLOSER[:-1], ("", True, {**stats("5: Wrong sign.<channel|>\n"), "done_reason": "stop"})]
    _, _, raw = post("/api/chat", {"model": "default-g", "messages": [{"role": "user", "content": "hi"}]})
    lines = [json.loads(line) for line in raw.decode().splitlines() if line]
    assert "".join(x["message"]["content"] for x in lines) == "5: Wrong sign."


def test_v1_stream_harmony_sends_only_the_final_channel(server, harmony):
    engine, post, _ = server
    _, enc = serve._harmony()
    tokens = enc.encode("<|channel|>analysis<|message|>Think.<|end|><|start|>assistant<|channel|>final<|message|>Hello world<|return|>", allowed_special="all")
    engine.items = [*[("?", False, t) for t in tokens], ("", True, stats("", tokens))]
    got = events(post("/v1/chat/completions", v1("heavy-o", stream=True))[2])
    text = "".join(c["choices"][0]["delta"].get("content", "") for c in got[:-1] if c["choices"])
    assert text == "Hello world"


def test_v1_harmony_reply_has_reasoning_content(server, harmony):
    engine, post, _ = server
    _, enc = serve._harmony()
    tokens = enc.encode(
        "<|channel|>analysis<|message|>Use the tool.<|end|><|start|>assistant<|channel|>commentary to=functions.get_weather <|constrain|>json<|message|>"
        '{"location":"Paris"}<|call|>',
        allowed_special="all",
    )
    engine.items = [("", True, stats("", tokens))]
    body = json.loads(post("/v1/chat/completions", v1("heavy-o", tools=TOOLS))[2])
    message = body["choices"][0]["message"]
    assert message["reasoning_content"] == "Use the tool."
    assert json.loads(message["tool_calls"][0]["function"]["arguments"]) == {"location": "Paris"}


# -- /api/chat --------------------------------------------------------------------

LEGACY_ITEMS = [
    ("Hel", False, None),
    ("lo", False, None),
    ("", True, {"prompt_eval_count": 3, "prompt_eval_duration": 1, "eval_count": 2, "eval_duration": 2, "done_reason": "stop", "text": "Hello"}),
]


def test_api_chat_without_tools_is_the_unchanged_path(server):
    engine, post, _ = server
    engine.items = LEGACY_ITEMS
    code, _, raw = post("/api/chat", {"model": "old-small", "stream": False, "messages": [{"role": "user", "content": "hi"}]})
    body = json.loads(raw)
    job = engine.jobs[0]
    assert type(job) is serve.GenJob
    assert (job.name, job.messages, job.prompt, job.raw, job.max_tokens, job.temperature, job.top_p) == (
        "small-q",
        [{"role": "user", "content": "hi"}],
        "",
        False,
        256,
        0.0,
        None,
    )
    assert code == 200
    assert list(body) == [
        "model",
        "created_at",
        "message",
        "done",
        "prompt_eval_count",
        "prompt_eval_duration",
        "eval_count",
        "eval_duration",
        "done_reason",
        "total_duration",
    ]
    assert body["model"] == "old-small" and body["message"] == {"role": "assistant", "content": "Hello"} and body["done"] is True


def test_api_chat_stream_without_tools_is_the_unchanged_path(server):
    engine, post, _ = server
    engine.items = LEGACY_ITEMS
    code, ctype, raw = post("/api/chat", {"model": "small-q", "messages": [{"role": "user", "content": "hi"}]})
    lines = [json.loads(line) for line in raw.decode().splitlines() if line]
    assert code == 200 and ctype == "application/x-ndjson"
    assert [x["message"]["content"] for x in lines] == ["Hel", "lo", ""]
    assert [x["done"] for x in lines] == [False, False, True]
    assert list(lines[0]) == ["model", "created_at", "message", "done"]
    assert "text" not in lines[-1] and lines[-1]["eval_count"] == 2


def test_api_chat_empty_tools_is_the_unchanged_path(server):
    engine, post, _ = server
    engine.items = LEGACY_ITEMS
    post("/api/chat", {"model": "small-q", "stream": False, "tools": [], "messages": [{"role": "user", "content": "hi"}]})
    assert type(engine.jobs[0]) is serve.GenJob


def test_api_chat_tools_need_stream_false(server):
    engine, post, _ = server
    code, _, raw = post("/api/chat", {"model": "small-q", "tools": TOOLS, "messages": [{"role": "user", "content": "hi"}]})
    assert code == 400 and "stream" in json.loads(raw)["error"] and engine.jobs == []


def test_api_chat_tools_ollama_shape(server, parsers):
    engine, post, _ = server
    engine.items = [("", True, stats(QWEN_CALL))]
    code, _, raw = post(
        "/api/chat",
        {"model": "small-q", "stream": False, "tools": TOOLS, "messages": [{"role": "user", "content": "hi"}], "options": {"num_predict": 99}},
    )
    body = json.loads(raw)
    assert code == 200
    assert body["message"] == {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "get_weather", "arguments": {"location": "Paris"}}}]}
    assert body["done"] is True and body["done_reason"] == "tool_calls"
    assert body["prompt_eval_count"] == 100 and body["eval_count"] == 7
    assert engine.jobs[0].max_tokens == 99


def test_api_generate_is_the_unchanged_path(server):
    engine, post, _ = server
    engine.items = LEGACY_ITEMS
    body = json.loads(post("/api/generate", {"model": "small-q", "prompt": "hi", "raw": True})[2])
    job = engine.jobs[0]
    assert type(job) is serve.GenJob and job.raw is True and job.prompt == "hi"
    assert body["response"] == "Hello"


# -- stop strings -----------------------------------------------------------------

MARK = "<<anneal:"
# What the engine yields when the reply runs into a stop: the tokens before the
# one that completed it (a split "<<anneal"), then stats with the answer cut short.
STOPPED = [
    ("Done.", False, None),
    ("\n\n<", False, None),
    ("<anneal", False, None),
    ("", True, {"prompt_eval_count": 3, "prompt_eval_duration": 1, "eval_count": 4, "eval_duration": 2,
                "done_reason": "stop", "text": "Done.\n\n<<anneal:", "content": "Done.\n\n"}),
]


def ndjson(raw: bytes) -> list:
    return [json.loads(line) for line in raw.decode().splitlines() if line]


def test_api_chat_stop_cuts_the_answer(server):
    engine, post, _ = server
    engine.items = STOPPED
    code, _, raw = post("/api/chat", {"model": "default-g", "stream": False, "messages": USER, "options": {"stop": [MARK]}})
    body = json.loads(raw)
    assert code == 200 and engine.jobs[0].stops == (MARK,)
    assert body["message"]["content"] == "Done.\n\n" and body["done_reason"] == "stop"
    assert "content" not in body and "text" not in body


def test_api_chat_stream_holds_back_a_stop_split_over_tokens(server):
    engine, post, _ = server
    engine.items = STOPPED
    lines = ndjson(post("/api/chat", {"model": "default-g", "messages": USER, "options": {"stop": MARK}})[2])
    sent = [x["message"]["content"] for x in lines]
    assert "".join(sent) == "Done.\n\n" and not any("<" in s for s in sent)
    assert lines[-1]["done"] is True and lines[-1]["done_reason"] == "stop" and "content" not in lines[-1]


def test_api_generate_stop_cuts_the_response(server):
    engine, post, _ = server
    engine.items = STOPPED
    body = json.loads(post("/api/generate", {"model": "default-g", "prompt": "hi", "stream": False, "options": {"stop": [MARK]}})[2])
    assert body["response"] == "Done.\n\n" and body["done_reason"] == "stop"


def test_api_chat_stream_sends_a_held_prefix_that_was_not_a_stop(server):
    engine, post, _ = server
    engine.items = [
        ("a <", False, None),
        ("<an", False, None),
        ("swer", False, None),
        ("", True, {**LEGACY_ITEMS[-1][2], "text": "a <<answer"}),
    ]
    sent = [x["message"]["content"] for x in ndjson(post("/api/chat", {"model": "default-g", "messages": USER, "options": {"stop": [MARK]}})[2])]
    assert sent == ["a ", "<<answer", ""]


def test_api_chat_stream_sends_a_held_prefix_when_the_reply_ends_inside_it(server):
    engine, post, _ = server
    engine.items = [("x <", False, None), ("", True, {**LEGACY_ITEMS[-1][2], "text": "x <"})]
    lines = ndjson(post("/api/chat", {"model": "default-g", "messages": USER, "options": {"stop": [MARK]}})[2])
    assert [x["message"]["content"] for x in lines] == ["x ", "<"] and lines[-1]["done_reason"] == "stop"


def test_v1_stop_cuts_the_answer(server):
    engine, post, _ = server
    engine.items = [("", True, {**stats("Done.\n\n<<anneal:"), "content": "Done.\n\n"})]
    body = json.loads(post("/v1/chat/completions", v1("default-g", stop=[MARK]))[2])
    assert engine.jobs[0].stops == (MARK,)
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Done.\n\n"}
    assert body["choices"][0]["finish_reason"] == "stop"


def test_v1_stream_holds_back_a_stop_split_over_tokens(server):
    engine, post, _ = server
    engine.items = [("Done.", False, 1), ("\n\n<", False, 2), ("<anneal", False, 3), ("", True, {**stats("Done.\n\n<<anneal:"), "content": "Done.\n\n"})]
    got = events(post("/v1/chat/completions", v1("default-g", stream=True, stop=MARK))[2])
    deltas = [c["choices"][0]["delta"].get("content", "") for c in got[:-1] if c["choices"]]
    assert "".join(deltas) == "Done.\n\n" and not any("<" in d for d in deltas)
    assert got[-2]["choices"][0]["finish_reason"] == "stop"


def test_v1_stream_sends_a_held_prefix_that_was_not_a_stop(server):
    engine, post, _ = server
    engine.items = [("x <", False, 1), ("<b", False, 2), ("", True, stats("x <<b"))]
    got = events(post("/v1/chat/completions", v1("default-g", stream=True, stop=[MARK]))[2])
    assert "".join(c["choices"][0]["delta"].get("content", "") for c in got[:-1] if c["choices"]) == "x <<b"


def test_v1_stream_sends_a_held_prefix_when_the_reply_ends_inside_it(server):
    engine, post, _ = server
    engine.items = [("x <", False, 1), ("", True, stats("x <"))]
    got = events(post("/v1/chat/completions", v1("default-g", stream=True, stop=[MARK]))[2])
    assert [c["choices"][0]["delta"].get("content") for c in got[1:-1] if c["choices"]] == ["x ", "<", None]


def test_v1_stream_harmony_stop_applies_to_the_final_channel(server, harmony):
    engine, post, _ = server
    _, enc = serve._harmony()
    # The analysis quotes the mark and does not stop the reply; the final
    # channel runs into it, and the token that completed it is never yielded.
    tokens = enc.encode(
        "<|channel|>analysis<|message|>It ends at <<anneal: here.<|end|><|start|>assistant<|channel|>final<|message|>Hello <<anneal",
        allowed_special="all",
    )
    engine.items = [*[("?", False, t) for t in tokens], ("", True, {**stats("", tokens), "content": "Hello "})]
    got = events(post("/v1/chat/completions", v1("heavy-o", stream=True, stop=[MARK]))[2])
    deltas = [c["choices"][0]["delta"].get("content", "") for c in got[:-1] if c["choices"]]
    assert "".join(deltas) == "Hello " and not any("<" in d for d in deltas)


def test_no_stop_leaves_the_job_without_one(server):
    engine, post, _ = server
    engine.items = LEGACY_ITEMS
    post("/api/chat", {"model": "default-g", "stream": False, "messages": USER})
    engine.items = [("", True, stats("x"))]
    post("/v1/chat/completions", v1("default-g"))
    assert [job.stops for job in engine.jobs] == [(), ()]


BAD_STOPS = [5, [1], [""], [MARK, None], {"a": 1}, ["s"] * (serve.MAX_STOPS + 1)]


@pytest.mark.parametrize("stop", BAD_STOPS)
def test_api_bad_stop_is_a_400(server, stop):
    engine, post, _ = server
    code, _, raw = post("/api/chat", {"model": "default-g", "messages": USER, "options": {"stop": stop}})
    assert code == 400 and "stop" in json.loads(raw)["error"] and engine.jobs == []


@pytest.mark.parametrize("stop", BAD_STOPS)
def test_v1_bad_stop_is_a_400(server, stop):
    engine, post, _ = server
    code, _, raw = post("/v1/chat/completions", v1("default-g", stop=stop))
    assert code == 400 and "stop" in json.loads(raw)["error"]["message"] and engine.jobs == []


def test_stop_with_tools_is_a_400(server):
    engine, post, _ = server
    code, _, raw = post("/v1/chat/completions", v1("small-q", tools=TOOLS, stop=[MARK]))
    assert code == 400 and "stop is not supported with tools" in json.loads(raw)["error"]["message"]
    code, _, raw = post("/api/chat", {"model": "small-q", "stream": False, "tools": TOOLS, "messages": USER, "options": {"stop": [MARK]}})
    assert code == 400 and "stop is not supported with tools" in json.loads(raw)["error"]
    assert engine.jobs == []


def test_health_reports_tools_limits_and_cache(server):
    _, _, get = server
    code, body = get("/health")
    assert code == 200
    assert body["tools"] == {"small-q": "qwen3_coder", "default-g": "gemma4", "heavy-o": "harmony"}
    assert body["limits"]["default"]["max_active"] == 3
    assert body["prompt_cache"] == {"default-g": {"entries": 2, "bytes": 1024}}


# -- the heavy slot and host memory ------------------------------------------------

DEFERRED = {"need_gb": 73.0, "available_gb": 41.2, "pressure": "warn"}
USER = [{"role": "user", "content": "hi"}]


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/api/chat", {"model": "heavy-o", "stream": False, "messages": USER}),
        ("/api/chat", {"model": "heavy-o", "messages": USER}),  # streams by default
        ("/api/generate", {"model": "heavy-o", "prompt": "hi"}),
        ("/api/chat", {"model": "heavy-o", "stream": False, "tools": TOOLS, "messages": USER}),
    ],
)
def test_deferred_heavy_load_is_a_503_on_the_ollama_routes(server, path, body):
    engine, post, _ = server
    engine.items = [serve.HeavyDeferred("heavy-o", DEFERRED)]
    code, ctype, raw = post(path, body)
    assert code == 503 and ctype == "application/json"
    assert json.loads(raw) == {"error": "heavy deferred: memory", "model": "heavy-o", **DEFERRED}


@pytest.mark.parametrize("extra", [{}, {"stream": True}, {"tools": TOOLS}])
def test_deferred_heavy_load_is_a_503_on_v1(server, extra):
    engine, post, _ = server
    engine.items = [serve.HeavyDeferred("heavy-o", DEFERRED)]
    code, ctype, raw = post("/v1/chat/completions", v1("heavy-o", **extra))
    body = json.loads(raw)
    assert code == 503 and ctype == "application/json"
    assert body["error"]["message"] == "heavy deferred: memory" and body["error"]["code"] == "heavy_deferred"
    assert (body["need_gb"], body["available_gb"], body["pressure"]) == (73.0, 41.2, "warn")


def test_other_engine_failures_are_still_500(server):
    engine, post, _ = server
    engine.items = [RuntimeError("metal went away")]
    code, _, raw = post("/api/chat", {"model": "heavy-o", "stream": False, "messages": USER})
    assert code == 500 and "metal went away" in json.loads(raw)["error"]


@pytest.mark.parametrize("route", ["/api/ps", "/health"])
def test_status_reports_memory_and_the_heavy_slot(server, monkeypatch, route):
    engine, _, get = server
    monkeypatch.setattr(serve, "memory_probe", lambda: {"available": 41_234_567_890, "pressure": "warn"})
    engine.heavy = "heavy-o"
    code, body = get(route)
    assert code == 200
    assert body["memory"] == {
        "pressure": "warn",
        "available_gb": 41.2,
        "heavy_loaded": True,
        "heavy": "heavy-o",
        "heavy_idle_unload_s": serve.HEAVY_IDLE_S,
        "heavy_margin_gb": serve.HEAVY_MARGIN_GB,
    }


def test_status_without_a_memory_reading(server, monkeypatch):
    _, _, get = server
    monkeypatch.setattr(serve, "memory_probe", lambda: {"available": None, "pressure": None})
    _, body = get("/api/ps")
    assert body["memory"]["available_gb"] is None and body["memory"]["heavy_loaded"] is False
    assert [m["name"] for m in body["models"]] == ["small-q", "default-g"]


# -- catalog ---------------------------------------------------------------------


def test_catalog_defaults_when_no_tiers_block(tmp_path):
    assert serve.Catalog(write_catalog(tmp_path)).limits == serve.TIER_LIMITS


def test_catalog_tiers_override(tmp_path):
    limits = serve.Catalog(write_catalog(tmp_path, tiers={"default": {"max_active": 1}, "small": {"prompt_cache_gib": 0}})).limits
    assert limits["default"] == {**serve.TIER_LIMITS["default"], "max_active": 1}
    assert limits["small"]["prompt_cache_gib"] == 0 and limits["small"]["max_active"] is None


@pytest.mark.parametrize(
    "tiers",
    [
        {"embed": {"max_active": 1}},
        {"default": {"max_live": 1}},
        {"default": {"max_active": 0}},
        {"default": {"max_active": 1.5}},
        {"default": {"prefill_chunk": True}},
        {"heavy": {"prompt_cache_gib": -1}},
        {"heavy": []},
    ],
)
def test_catalog_rejects_bad_tiers(tmp_path, tiers):
    with pytest.raises(SystemExit):
        serve.Catalog(write_catalog(tmp_path, tiers=tiers))


def test_catalog_rejects_unknown_tool_format(tmp_path):
    with pytest.raises(SystemExit, match="tool_format"):
        serve.Catalog(write_catalog(tmp_path, other={"tier": "heavy", "tool_format": "hermes"}))


def _relative_catalog(tmp_path, small_path):
    (tmp_path / "models" / "s").mkdir(parents=True)
    (tmp_path / "models" / "d").mkdir()
    (tmp_path / "models" / "e.gguf").write_bytes(b"x")
    path = tmp_path / "models.json"
    path.write_text(json.dumps({"models": {
        "s": {"tier": "small", "path": small_path},
        "d": {"tier": "default", "path": "models/d"},
        "e": {"tier": "embed", "path": "models/e.gguf"},
    }}))
    return path


def test_catalog_paths_resolve_against_the_catalog_file(tmp_path, monkeypatch):
    monkeypatch.chdir("/")
    catalog = serve.Catalog(_relative_catalog(tmp_path, "models/s"))
    assert catalog.models["s"]["path"] == tmp_path / "models" / "s"
    assert catalog.models["e"]["path"] == tmp_path / "models" / "e.gguf"


def test_catalog_paths_expand_home(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    catalog = serve.Catalog(_relative_catalog(tmp_path, "~/models/s"))
    assert catalog.models["s"]["path"] == tmp_path / "models" / "s"


def test_a_missing_relative_path_names_where_it_looked(tmp_path):
    with pytest.raises(SystemExit, match=str(tmp_path / "models" / "absent")):
        serve.Catalog(_relative_catalog(tmp_path, "models/absent"))


# -- /api/version ----------------------------------------------------------------


def test_version_reports_the_catalog_revision(server, tmp_path):
    _, _, get = server
    code, body = get("/api/version")
    assert code == 200
    assert body == {"version": serve.Catalog(tmp_path / "models.json").revision}
    assert serve.REVISION.fullmatch(body["version"])


def test_revision_is_stable_for_the_same_build(tmp_path):
    path = write_catalog(tmp_path)
    assert serve.Catalog(path).revision == serve.Catalog(path).revision


def test_revision_moves_with_the_catalog(tmp_path):
    before = serve.Catalog(write_catalog(tmp_path)).revision
    after = serve.Catalog(write_catalog(tmp_path, tiers={"default": {"max_active": 1}})).revision
    assert before != after


def test_revision_moves_with_a_models_weights(tmp_path):
    path = write_catalog(tmp_path)
    before = serve.Catalog(path).revision
    (tmp_path / "default-g" / "model.safetensors").write_bytes(b"\0" * 64)
    assert serve.Catalog(path).revision != before


def test_catalog_reads_the_models_sampling_truncation(tmp_path):
    (tmp_path / "default-g").mkdir()
    (tmp_path / "default-g" / "generation_config.json").write_text(
        json.dumps({"eos_token_id": [1, 106, 50], "temperature": 1.0, "top_k": 64, "top_p": 0.95})
    )
    models = serve.Catalog(write_catalog(tmp_path)).models
    assert models["default-g"]["sampling"] == {"top_k": 64, "top_p": 0.95}
    assert models["small-q"]["sampling"] == {}


@pytest.mark.parametrize("config", ['{"top_k": -1}', '{"top_k": 1.5}', '{"top_p": 0}', '{"top_p": 1.5}', '{"top_p": true}', "{not json"])
def test_catalog_rejects_a_bad_generation_config(tmp_path, config):
    (tmp_path / "default-g").mkdir()
    (tmp_path / "default-g" / "generation_config.json").write_text(config)
    with pytest.raises(SystemExit, match="generation_config.json"):
        serve.Catalog(write_catalog(tmp_path))


def test_revision_moves_with_a_models_sampling(tmp_path):
    path = write_catalog(tmp_path)
    config = tmp_path / "default-g" / "generation_config.json"
    config.write_text('{"top_p": 0.95}')
    before = serve.Catalog(path).revision
    config.write_text('{"top_p": 0.90}')
    assert serve.Catalog(path).revision != before


def test_revision_moves_with_a_package_version(tmp_path, monkeypatch):
    path = write_catalog(tmp_path)
    before = serve.Catalog(path).revision
    real = serve._package_version
    monkeypatch.setattr(serve, "_package_version", lambda name: "9.9.9" if name == "mlx-lm" else real(name))
    assert serve.Catalog(path).revision != before


def test_revision_moves_with_the_server_source(tmp_path, monkeypatch):
    path = write_catalog(tmp_path)
    before = serve.Catalog(path).revision
    source = tmp_path / "serve.py"
    source.write_text("# a different build\n")
    monkeypatch.setattr(serve, "REVISION_SOURCES", (source,))
    assert serve.Catalog(path).revision != before


def test_revision_survives_an_absent_package_and_source(tmp_path, monkeypatch):
    path = write_catalog(tmp_path)
    monkeypatch.setattr(serve, "REVISION_SOURCES", (tmp_path / "missing.py",))
    monkeypatch.setattr(serve, "_package_version", lambda name: None)
    assert serve.REVISION.fullmatch(serve.Catalog(path).revision)


# -- listener --------------------------------------------------------------------


def test_a_burst_of_new_connections_is_served(server):
    # The client pool opens up to 10 connections at once after it idles, and
    # other host callers share the port. On macOS a full listen queue resets
    # queued connections, which httpx reports as ConnectError.
    httpd = serve.Server(("127.0.0.1", 0), serve.Handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()
    port = httpd.server_address[1]
    n = 40
    barrier = threading.Barrier(n)
    outcomes = [None] * n

    def one(i):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(10)
        barrier.wait()
        try:
            s.connect(("127.0.0.1", port))
            s.sendall(b"GET /api/version HTTP/1.1\r\nHost: x\r\nConnection: close\r\n\r\n")
            data = b""
            while chunk := s.recv(65536):
                data += chunk
            outcomes[i] = data.split(b" ", 2)[1].decode() if data else "empty"
        except OSError as exc:
            outcomes[i] = type(exc).__name__
        finally:
            s.close()

    threads = [threading.Thread(target=one, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    httpd.shutdown()
    httpd.server_close()
    assert outcomes == ["200"] * n


# -- reasoning effort, JSON mode, access log (audit 2026-10-10, F15 + F18) -----------


@pytest.mark.parametrize("extra,effort", [
    ({}, "medium"),
    ({"reasoning_effort": "high"}, "high"),
    ({"reasoning": {"effort": "low"}}, "low"),      # OpenRouter's shape
    ({"reasoning_effort": "none"}, "low"),          # gpt-oss cannot switch reasoning off
    ({"reasoning_effort": "xhigh"}, "high"),
])
def test_v1_reasoning_effort_reaches_the_job(server, extra, effort):
    engine, post, _ = server
    engine.items = [("", True, stats("ok"))]
    status, _, _ = post("/v1/chat/completions", v1("default-g", **extra))
    assert status == 200 and engine.jobs[-1].effort == effort


def test_v1_unknown_reasoning_effort_is_a_400(server):
    _, post, _ = server
    status, _, body = post("/v1/chat/completions", v1("default-g", reasoning_effort="extreme"))
    assert status == 400 and b"reasoning effort" in body


def test_v1_json_mode_instructs_the_model(server):
    engine, post, _ = server
    engine.items = [("", True, stats("{}"))]
    post("/v1/chat/completions", v1("default-g", response_format={"type": "json_object"}))
    assert engine.jobs[-1].messages[0] == {"role": "system", "content": "Respond with one JSON object and no other text."}


def test_v1_json_schema_joins_an_existing_system_message(server):
    engine, post, _ = server
    engine.items = [("", True, stats("{}"))]
    body = v1("default-g", response_format={"type": "json_schema", "json_schema": {"schema": {"type": "object"}}})
    body["messages"] = [{"role": "system", "content": "Be brief."}, *body["messages"]]
    post("/v1/chat/completions", body)
    system = engine.jobs[-1].messages[0]["content"]
    assert system.startswith("Be brief.") and '"type": "object"' in system


def test_v1_unsupported_response_format_is_a_400(server):
    _, post, _ = server
    status, _, _ = post("/v1/chat/completions", v1("default-g", response_format={"type": "grammar"}))
    assert status == 400


def test_every_request_leaves_one_timed_access_line(server, capsys):
    engine, post, _ = server
    engine.items = [("", True, stats("Hello there."))]
    post("/v1/chat/completions", v1("default-g"))
    lines = [ln for ln in capsys.readouterr().out.splitlines() if "/v1/chat/completions" in ln]
    assert len(lines) == 1
    line = lines[0]
    assert line[:4].isdigit() and "Z " in line  # ISO timestamp first
    for field in ("200", "model=default-g", "ms=", "wait_ms=", "accept_ms=", "effort=medium"):
        assert field in line


def test_harmony_system_message_carries_the_effort(harmony):
    _, enc = serve._harmony()
    text = enc.decode(enc.render_conversation(serve.harmony_conversation([{"role": "user", "content": "hi"}], None, "high")))
    assert "Reasoning: high" in text


def test_log_lines_from_many_threads_stay_whole(capsys):
    """print() wrote the text and the newline separately, so two handler threads
    logging at once ran their access-log lines together ('accept_ms=1' followed
    straight by the next timestamp)."""
    import threading

    def write(n):
        for i in range(200):
            serve._emit(f"thread={n} line={i} " + "x" * 64)

    threads = [threading.Thread(target=write, args=(n,)) for n in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    lines = capsys.readouterr().out.splitlines()
    assert len(lines) == 1600
    assert all(line.startswith("thread=") and line.endswith("x" * 64) and line.count("thread=") == 1 for line in lines)
