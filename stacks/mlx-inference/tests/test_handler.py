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
        1.0,
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


def test_health_reports_tools_limits_and_cache(server):
    _, _, get = server
    code, body = get("/health")
    assert code == 200
    assert body["tools"] == {"small-q": "qwen3_coder", "default-g": "gemma4", "heavy-o": "harmony"}
    assert body["limits"]["default"]["max_active"] == 3
    assert body["prompt_cache"] == {"default-g": {"entries": 2, "bytes": 1024}}


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
