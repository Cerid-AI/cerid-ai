# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""The engine on real MLX: prefix-cache reuse, chunked prefill, tier caps, swaps.

The models are tiny and randomly initialised (MLX on CPU runs them in CI), and
the tokenizer is byte-level, so no weights are downloaded. Where a test needs a
particular reply, a scripted sampler picks the tokens; everything else, from
the forward passes to stream_generate and the KV caches, is the real code.
"""

from __future__ import annotations

import json
import time

import pytest
import serve

pytestmark = pytest.mark.usefixtures("mlx")

TEMPLATE = (
    "{%- if tools %}<|im_start|>system\n{% for t in tools %}{{ t.function.name }}\n{% endfor %}<|im_end|>\n{% endif %}"
    "{%- for m in messages %}<|im_start|>{{ m.role }}\n{{ m.content }}"
    "{%- for c in m.tool_calls or [] %}<tool_call>\n<function={{ c.function.name }}>\n"
    "{%- for k, v in c.function.arguments.items() %}<parameter={{ k }}>\n{{ v }}\n</parameter>\n{% endfor %}"
    "</function>\n</tool_call>{% endfor %}<|im_end|>\n{% endfor %}"
    "{%- if add_generation_prompt %}<|im_start|>assistant\n{% endif %}"
)
SPECIALS = ["<|im_start|>", "<|im_end|>", "<tool_call>", "</tool_call>"]
TOOLS = [{"type": "function", "function": {"name": "get_weather", "parameters": {"type": "object", "properties": {"location": {"type": "string"}}}}}]


def byte_tokenizer():
    from mlx_lm.tokenizer_utils import TokenizerWrapper
    from tokenizers import Tokenizer, decoders, models, pre_tokenizers
    from transformers import PreTrainedTokenizerFast

    alphabet = sorted(pre_tokenizers.ByteLevel.alphabet())
    tok = Tokenizer(models.BPE(vocab={c: i for i, c in enumerate(alphabet)}, merges=[]))
    tok.pre_tokenizer = pre_tokenizers.ByteLevel(add_prefix_space=False, use_regex=False)
    tok.decoder = decoders.ByteLevel()
    tok.add_special_tokens(SPECIALS)
    hf = PreTrainedTokenizerFast(tokenizer_object=tok, eos_token="<|im_end|>")
    hf.chat_template = TEMPLATE
    return TokenizerWrapper(hf, eos_token_ids=[hf.convert_tokens_to_ids("<|im_end|>")])


class HarmonyTokenizer:
    """What stream_generate needs from a tokenizer, over the harmony vocabulary."""

    bos_token = None
    chat_template = None
    clean_up_tokenization_spaces = False

    def __init__(self) -> None:
        _, self.enc = serve._harmony()
        self.eos_token_id = self.enc.encode("<|return|>", allowed_special="all")[0]

    def get_vocab(self):
        return {}

    def encode(self, text, add_special_tokens=True):
        return self.enc.encode(text, allowed_special="all")

    def decode(self, tokens, **_):
        return self.enc.decode(list(tokens))

    # mlx-lm >= 0.32 inspects this method's signature when it wraps a tokenizer.
    # Harmony prompts are rendered by openai-harmony, so it is never called.
    def apply_chat_template(self, messages, **_):
        raise NotImplementedError("harmony prompts are rendered by openai-harmony")


def tiny_model(model_type: str, vocab_size: int, seed: int):
    import mlx.core as mx
    from mlx_lm.utils import _get_classes

    config = {
        "qwen3": {
            "model_type": "qwen3",
            "hidden_size": 64,
            "num_hidden_layers": 2,
            "intermediate_size": 128,
            "num_attention_heads": 4,
            "num_key_value_heads": 2,
            "head_dim": 16,
            "rms_norm_eps": 1e-6,
            "max_position_embeddings": 4096,
            "rope_theta": 10000.0,
            "tie_word_embeddings": True,
        },
        # Recurrent state (ArraysCache): a cache that cannot be trimmed.
        "mamba": {
            "model_type": "mamba",
            "hidden_size": 64,
            "intermediate_size": 128,
            "state_size": 8,
            "num_hidden_layers": 2,
            "conv_kernel": 4,
            "use_bias": False,
            "use_conv_bias": True,
            "time_step_rank": 8,
            "tie_word_embeddings": True,
        },
    }[model_type]
    model_cls, args_cls = _get_classes(config)
    mx.random.seed(seed)
    model = model_cls(args_cls.from_dict({**config, "vocab_size": vocab_size}))
    mx.eval(model.parameters())
    return model


ROWS = {
    "small-m": {"tier": "small", "arch": "mamba", "tool_format": "qwen3_coder"},
    "default-q": {"tier": "default", "arch": "qwen3", "tool_format": "qwen3_coder"},
    "heavy-a": {"tier": "heavy", "arch": "qwen3", "tool_format": "qwen3_coder"},
    "heavy-b": {"tier": "heavy", "arch": "qwen3", "tool_format": "qwen3_coder"},
    "heavy-h": {"tier": "heavy", "arch": "qwen3", "tool_format": "harmony"},
}


HOST = {"available": 200 * serve.GB, "pressure": "normal"}


@pytest.fixture
def engine_for(tmp_path, monkeypatch):
    import mlx_lm

    engines = []

    def make(tiers=None):
        models = {}
        for name, row in ROWS.items():
            d = tmp_path / name
            d.mkdir(exist_ok=True)
            models[name] = {"path": str(d), "tier": row["tier"], "family": "test", "tool_format": row["tool_format"]}
        path = tmp_path / f"models-{len(engines)}.json"
        path.write_text(json.dumps({"models": models, "tiers": tiers or {}}))

        def load(model_path):
            name = model_path.rsplit("/", 1)[-1]
            row = ROWS[name]
            if row["tool_format"] == "harmony":
                return tiny_model(row["arch"], 201088, seed=7), HarmonyTokenizer()
            return tiny_model(row["arch"], 256 + len(SPECIALS), seed=sorted(ROWS).index(name)), byte_tokenizer()

        monkeypatch.setattr(mlx_lm, "load", load)
        # The host running the tests is not the host being judged: a heavy row
        # here is a tiny model, so the real memory reading has no say.
        monkeypatch.setattr(serve, "memory_probe", lambda: dict(HOST))
        engine = serve.Engine(serve.Catalog(path))
        deadline = time.time() + 60
        while len(engine.resident()) < 2 and time.time() < deadline:
            time.sleep(0.05)
        assert set(engine.resident()) == {"small-m", "default-q"}
        engines.append(engine)
        return engine

    return make


def run(engine, job, timeout=120):
    engine.submit(job)
    items = []
    deadline = time.time() + timeout
    while True:
        item = job.out.get(timeout=max(0.1, deadline - time.time()))
        if item is None:
            break
        if isinstance(item, Exception):
            raise item
        items.append(item)
    return items[-1][2], items


def scripted(monkeypatch, tokens):
    import mlx.core as mx
    import mlx_lm.sample_utils

    script = iter(tokens)

    def make_sampler(**_):
        # Past the script, token 0: the lookahead after a stop, or a later turn.
        return lambda logprobs: mx.array([next(script, 0)])

    monkeypatch.setattr(mlx_lm.sample_utils, "make_sampler", make_sampler)


def chat(name, messages, tools=None, choice=None, max_tokens=8):
    return serve.ChatJob(name, serve.chat_messages(messages), tools, choice, max_tokens, 0.0, 1.0)


FIRST = [{"role": "system", "content": "You answer about the weather. " * 8}, {"role": "user", "content": "Weather in Oslo?"}]


def second_turn(first_text):
    return [*FIRST, {"role": "assistant", "content": first_text}, {"role": "user", "content": "And tomorrow?"}]


@pytest.mark.parametrize("name", ["default-q", "small-m"])
def test_next_turn_reuses_the_whole_previous_turn(engine_for, monkeypatch, name):
    engine = engine_for()
    tok = engine.loaded[name][1]
    scripted(monkeypatch, [*tok.encode("Sunny.", add_special_tokens=False), tok.eos_token_id, 0])
    first, _ = run(engine, chat(name, FIRST))
    assert first["cached_tokens"] == 0 and first["text"] == "Sunny."
    second, _ = run(engine, chat(name, second_turn("Sunny.")))
    # The reply came back verbatim, so the prompt, the reply and its end
    # token are all reused.
    assert second["cached_tokens"] == first["prompt_tokens"] + first["completion_tokens"]
    assert engine.cache_stats()[name]["bytes"] > 0


@pytest.mark.parametrize("name", ["default-q", "small-m"])
def test_a_rerendered_reply_still_reuses_the_conversation_before_it(engine_for, name):
    # Templates re-render the model's reply (Gemma drops the empty thought
    # block, harmony moves the recipient), so the next prompt diverges inside
    # it. A trimmable cache can cut back to the divergence; a recurrent one
    # cannot, and only the checkpoint before the generation prompt serves it.
    engine = engine_for()
    run(engine, chat(name, FIRST))
    tok = engine.loaded[name][1]
    history = len(tok.encode(tok.apply_chat_template(serve.chat_messages(FIRST), add_generation_prompt=False, tokenize=False)))
    second, _ = run(engine, chat(name, second_turn("A reply the model never wrote.")))
    if name == "small-m":
        assert second["cached_tokens"] == history
    else:
        assert second["cached_tokens"] > history


@pytest.mark.parametrize("name", ["default-q", "small-m"])
def test_a_cached_prefix_generates_what_a_cold_prompt_does(engine_for, name):
    warm_engine = engine_for()
    first, _ = run(warm_engine, chat(name, FIRST))
    warm, _ = run(warm_engine, chat(name, second_turn(first["text"]), max_tokens=12))
    cold_engine = engine_for(tiers={"small": {"prompt_cache_gib": 0}, "default": {"prompt_cache_gib": 0}})
    cold, _ = run(cold_engine, chat(name, second_turn(first["text"]), max_tokens=12))
    assert warm["cached_tokens"] > 0 and cold["cached_tokens"] == 0
    assert warm["tokens"] == cold["tokens"]


def test_a_sampled_turn_keeps_to_the_models_own_truncation(engine_for, tmp_path):
    # Gemma 4's generation_config.json asks for top_k 64 and top_p 0.95. A
    # request with only temperature 1 sampled all 262k tokens instead and drew
    # control tokens from the tail (<|file_separator|>, a stray <channel|>).
    # top_k 1 makes the truncation visible: a sampled turn is the greedy one.
    (tmp_path / "default-q").mkdir()
    (tmp_path / "default-q" / "generation_config.json").write_text(json.dumps({"temperature": 1.0, "top_k": 1}))
    engine = engine_for()
    greedy, _ = run(engine, chat("default-q", FIRST, max_tokens=16))
    max_tokens, temperature, top_p = serve._v1_options({"max_tokens": 16, "temperature": 1})
    for _ in range(3):
        job = serve.ChatJob("default-q", serve.chat_messages(FIRST), None, None, max_tokens, temperature, top_p)
        sampled, _ = run(engine, job)
        assert sampled["tokens"] == greedy["tokens"]


def test_prefill_runs_in_chunks(engine_for):
    # Driven directly: through the engine, prefill steps yield nothing visible.
    engine = engine_for(tiers={"default": {"prefill_chunk": 16}})
    steps = list(engine._generate_chat(chat("default-q", FIRST, max_tokens=1)))
    stats = steps[-1][2]
    prefill = [s for s in steps if s is None]
    assert stats["cached_tokens"] == 0
    # 16-token chunks up to the checkpoint, then up to the last prompt token.
    assert len(prefill) >= (stats["prompt_tokens"] - 1) // 16


def test_forced_opener_is_part_of_the_completion(engine_for):
    engine = engine_for()
    stats, _ = run(engine, chat("default-q", FIRST, tools=TOOLS, choice="get_weather", max_tokens=4))
    assert stats["text"].startswith("<tool_call>\n<function=get_weather>\n")


def test_scripted_qwen_call_comes_back_parsed(engine_for, monkeypatch, parsers):
    engine = engine_for()
    tok = engine.loaded["default-q"][1]
    reply = "<tool_call>\n<function=get_weather>\n<parameter=location>\nOslo\n</parameter>\n</function>\n</tool_call>"
    scripted(monkeypatch, [*tok.encode(reply, add_special_tokens=False), tok.eos_token_id, 0, 0])
    stats, _ = run(engine, chat("default-q", FIRST, tools=TOOLS, choice="auto", max_tokens=200))
    assert stats["finish"] == "stop"
    parsed = serve.parse_reply("qwen3_coder", stats["text"], stats["tokens"], TOOLS, stats["finish"])
    assert parsed["calls"] == [{"name": "get_weather", "arguments": {"location": "Oslo"}}]


def test_harmony_required_forces_the_call_after_the_analysis(engine_for, monkeypatch, harmony):
    engine = engine_for()
    _, enc = serve._harmony()
    special = lambda s: enc.encode(s, allowed_special="all")  # noqa: E731
    # The model writes some analysis and ends it; the server then feeds the
    # call header, and the model names the function and writes arguments.
    # A sampled token after each stop is the lookahead that never gets used.
    scripted(
        monkeypatch,
        [
            *special("Need weather."),
            *special("<|end|>"),
            0,
            *special('get_weather <|constrain|>json<|message|>{"location":"Oslo"}'),
            *special("<|call|>"),
            0,
        ],
    )
    stats, _ = run(engine, chat("heavy-h", FIRST, tools=TOOLS, choice="required", max_tokens=100))
    parsed = serve.parse_reply("harmony", stats["text"], stats["tokens"], TOOLS, stats["finish"])
    assert parsed["calls"] == [{"name": "get_weather", "arguments": {"location": "Oslo"}}]
    assert parsed["reasoning"] == "Need weather."
    assert enc.decode(stats["tokens"]).startswith("<|channel|>analysis<|message|>Need weather.<|end|><|start|>assistant<|channel|>commentary to=functions.")


def test_harmony_stops_at_call(engine_for, monkeypatch, harmony):
    engine = engine_for()
    _, enc = serve._harmony()
    reply = '<|channel|>commentary to=functions.get_weather <|constrain|>json<|message|>{"location":"Oslo"}<|call|>'
    # Tokens after <|call|> must never be generated.
    scripted(monkeypatch, [*enc.encode(reply, allowed_special="all"), *enc.encode("MORE TEXT"), 0])
    stats, _ = run(engine, chat("heavy-h", FIRST, tools=TOOLS, choice="auto", max_tokens=100))
    assert enc.decode(stats["tokens"]) == reply


def test_heavy_swap_drops_the_old_models_prompt_cache(engine_for):
    engine = engine_for()
    run(engine, chat("heavy-a", FIRST))
    assert "heavy-a" in engine.cache_stats()
    run(engine, chat("heavy-b", FIRST))
    assert engine.heavy == "heavy-b"
    assert "heavy-a" not in engine.cache_stats() and "heavy-a" not in engine.resident()


def wait_for(condition, timeout=10):
    deadline = time.time() + timeout
    while not condition() and time.time() < deadline:
        time.sleep(0.02)
    return condition()


def test_heavy_load_that_does_not_fit_is_deferred(engine_for, monkeypatch):
    monkeypatch.setattr(serve, "HEAVY_MARGIN_GB", 8)
    engine = engine_for()
    monkeypatch.setattr(serve, "memory_probe", lambda: {"available": 3 * serve.GB, "pressure": "normal"})
    with pytest.raises(serve.HeavyDeferred) as caught:
        run(engine, chat("heavy-a", FIRST))
    assert caught.value.payload == {
        "error": "heavy deferred: memory", "model": "heavy-a", "need_gb": 8.0, "available_gb": 3.0, "pressure": "normal",
    }
    assert engine.heavy is None and "heavy-a" not in engine.resident()
    # The resident tiers are untouched and still answer.
    run(engine, chat("default-q", FIRST))


def test_heavy_swap_measures_after_the_old_model_is_gone(engine_for, monkeypatch):
    engine = engine_for()
    run(engine, chat("heavy-a", FIRST))
    monkeypatch.setattr(serve, "memory_probe", lambda: {"available": None, "pressure": "critical"})
    with pytest.raises(serve.HeavyDeferred):
        run(engine, chat("heavy-b", FIRST))
    # The swap freed the slot before the reading refused the new model.
    assert engine.heavy is None and "heavy-a" not in engine.resident()


def test_idle_heavy_unloads_under_memory_pressure(engine_for, monkeypatch):
    monkeypatch.setattr(serve, "PRESSURE_POLL_S", 0.05)
    engine = engine_for()
    run(engine, chat("heavy-a", FIRST))
    assert engine.heavy == "heavy-a"
    time.sleep(0.2)
    assert engine.heavy == "heavy-a"  # normal pressure keeps it
    monkeypatch.setattr(serve, "memory_probe", lambda: {"available": 100 * serve.GB, "pressure": "warn"})
    assert wait_for(lambda: engine.heavy is None)
    assert "heavy-a" not in engine.resident() and "heavy-a" not in engine.cache_stats()
    assert set(engine.resident()) >= {"small-m", "default-q"}


def test_idle_heavy_unloads_after_the_idle_timeout(engine_for, monkeypatch):
    monkeypatch.setattr(serve, "HEAVY_IDLE_S", 0.3)
    engine = engine_for()
    run(engine, chat("heavy-a", FIRST))
    assert engine.heavy == "heavy-a"
    assert wait_for(lambda: engine.heavy is None)


def test_idle_timeout_zero_keeps_the_heavy_model(engine_for, monkeypatch):
    monkeypatch.setattr(serve, "HEAVY_IDLE_S", 0)
    monkeypatch.setattr(serve, "PRESSURE_POLL_S", 0.05)
    engine = engine_for()
    run(engine, chat("heavy-a", FIRST))
    time.sleep(0.3)
    assert engine.heavy == "heavy-a"


def test_tier_cap_queues_the_extra_generation_and_leaves_other_tiers_alone(engine_for):
    engine = engine_for(tiers={"default": {"max_active": 1}})
    long_job = chat("default-q", FIRST, max_tokens=1000)
    queued = chat("default-q", FIRST, max_tokens=2)
    small = chat("small-m", FIRST, max_tokens=2)
    done = {}

    def watch(tag, job):
        def wait():
            while job.out.get() is not None:
                pass
            done[tag] = time.perf_counter()

        import threading

        threading.Thread(target=wait, daemon=True).start()

    for tag, job in (("long", long_job), ("queued", queued)):
        engine.submit(job)
        watch(tag, job)
    deadline = time.time() + 30
    while queued not in engine._waiting["default"] and time.time() < deadline:
        time.sleep(0.01)
    assert queued in engine._waiting["default"] and long_job in engine._active
    engine.submit(small)
    watch("small", small)
    while len(done) < 3 and time.time() < deadline + 90:
        time.sleep(0.02)
    assert done["small"] < done["long"] < done["queued"]


def test_legacy_generation_is_untouched(engine_for):
    engine = engine_for()
    job = serve.GenJob("default-q", [{"role": "user", "content": "hi"}], "", False, 4, 0.0, 1.0)
    _, items = run(engine, job)
    final = items[-1][2]
    assert set(final) == {"prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration", "done_reason", "text"}
    assert all(item[2] is None for item in items[:-1])


MARK = "<<anneal:"
ECHO = "Done.\n\n<<anneal: end of the user’s message>> You are an anneal worker"


def test_legacy_generation_halts_at_a_stop_string(engine_for, monkeypatch):
    # The byte tokenizer spells the mark over nine tokens. The token that
    # completes it ends the generation and is never yielded.
    engine = engine_for()
    tok = engine.loaded["default-q"][1]
    scripted(monkeypatch, tok.encode(ECHO, add_special_tokens=False))
    job = serve.GenJob("default-q", [{"role": "user", "content": "hi"}], "", False, 200, 0.0, 1.0)
    job.stops = (MARK,)
    final, items = run(engine, job)
    assert final["content"] == "Done.\n\n" and final["done_reason"] == "stop"
    assert "".join(item[0] for item in items) == "Done.\n\n<<anneal"
    assert final["eval_count"] == len("Done.\n\n<<anneal:")


def test_legacy_generation_without_a_stop_runs_on(engine_for, monkeypatch):
    engine = engine_for()
    tok = engine.loaded["default-q"][1]
    scripted(monkeypatch, tok.encode(ECHO, add_special_tokens=False))
    final, _ = run(engine, serve.GenJob("default-q", [{"role": "user", "content": "hi"}], "", False, 30, 0.0, 1.0))
    assert "content" not in final and final["done_reason"] == "length" and final["text"] == ECHO[:30]


def test_chat_turn_halts_at_a_stop_string(engine_for, monkeypatch):
    engine = engine_for()
    tok = engine.loaded["default-q"][1]
    scripted(monkeypatch, tok.encode(ECHO, add_special_tokens=False))
    job = chat("default-q", FIRST, max_tokens=200)
    job.stops = (MARK,)
    stats, items = run(engine, job)
    assert stats["finish"] == "stop" and stats["content"] == "Done.\n\n"
    assert stats["completion_tokens"] == len("Done.\n\n<<anneal:")
    assert "".join(item[0] for item in items) == "Done.\n\n<<anneal"
    assert serve.chat_reply("qwen3_coder", stats, None)["content"] == "Done.\n\n"


def test_harmony_turn_stops_in_the_final_channel_not_the_analysis(engine_for, monkeypatch, harmony):
    engine = engine_for()
    _, enc = serve._harmony()
    reply = (
        "<|channel|>analysis<|message|>The message ends at <<anneal: end of the user’s message>>.<|end|>"
        "<|start|>assistant<|channel|>final<|message|>Answer.\n\n<<anneal: end of the user’s message>> again"
    )
    scripted(monkeypatch, enc.encode(reply, allowed_special="all"))
    job = chat("heavy-h", FIRST, max_tokens=200)
    job.stops = (MARK,)
    stats, _ = run(engine, job)
    assert stats["finish"] == "stop" and stats["content"] == "Answer.\n\n"
    parsed = serve.chat_reply("harmony", stats, None)
    assert parsed["content"] == "Answer.\n\n" and "<<anneal:" in parsed["reasoning"]


def test_http_tool_loop_end_to_end(engine_for, monkeypatch, parsers):
    import http.client
    import threading

    engine = engine_for()
    tok = engine.loaded["default-q"][1]
    call = "<tool_call>\n<function=get_weather>\n<parameter=location>\nOslo\n</parameter>\n</function>\n</tool_call>"
    scripted(
        monkeypatch,
        [
            *tok.encode(call, add_special_tokens=False), tok.eos_token_id, 0,
            *tok.encode("It is 7C in Oslo.", add_special_tokens=False), tok.eos_token_id, 0,
        ],
    )
    monkeypatch.setattr(serve.Handler, "engine", engine, raising=False)
    monkeypatch.setattr(serve.Handler, "catalog", engine.catalog, raising=False)
    httpd = serve.Server(("127.0.0.1", 0), serve.Handler)
    threading.Thread(target=httpd.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True).start()

    def post(body):
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=60)
        conn.request("POST", "/v1/chat/completions", json.dumps(body), {"Content-Type": "application/json"})
        resp = conn.getresponse()
        return resp.status, json.loads(resp.read())

    try:
        ask = {"role": "user", "content": "Weather in Oslo?"}
        code, first = post({"model": "default-q", "messages": [ask], "tools": TOOLS, "max_tokens": 200})
        assert code == 200 and first["choices"][0]["finish_reason"] == "tool_calls"
        calls = first["choices"][0]["message"]["tool_calls"]
        assert [(c["function"]["name"], json.loads(c["function"]["arguments"])) for c in calls] == [("get_weather", {"location": "Oslo"})]
        history = [
            ask,
            {"role": "assistant", "content": None, "tool_calls": calls},
            {"role": "tool", "tool_call_id": calls[0]["id"], "content": "7C"},
        ]
        code, second = post({"model": "default-q", "messages": history, "tools": TOOLS, "max_tokens": 200})
        assert code == 200
        assert second["choices"][0]["message"] == {"role": "assistant", "content": "It is 7C in Oslo."}
        assert second["usage"]["prompt_tokens_details"]["cached_tokens"] >= first["usage"]["prompt_tokens"]
    finally:
        httpd.shutdown()


def test_an_embed_is_answered_between_the_chat_models_loads(tmp_path, monkeypatch):
    """After a restart a search waited 9.4 s while two chat models loaded on the
    GPU thread. The embedder loads first; the chat models load one at a time
    between requests, so an embed waits at most for the load in progress."""
    import threading

    (tmp_path / "embed.safetensors").write_bytes(b"")
    for name in ("small-m", "default-q"):
        (tmp_path / name).mkdir()
    catalog = tmp_path / "models.json"
    catalog.write_text(json.dumps({"models": {
        "embed-e": {"path": str(tmp_path / "embed.safetensors"), "tier": "embed", "family": "test"},
        "small-m": {"path": str(tmp_path / "small-m"), "tier": "small", "family": "test", "tool_format": "qwen3_coder"},
        "default-q": {"path": str(tmp_path / "default-q"), "tier": "default", "family": "test", "tool_format": "qwen3_coder"},
    }}))
    started = {name: threading.Event() for name in ("small-m", "default-q")}
    finish = {name: threading.Event() for name in ("small-m", "default-q")}

    embedded_during: list[bool] = []

    class Embedder:
        def embed(self, texts, truncate=True):
            embedded_during.append(started["default-q"].is_set())
            return [[0.5] for _ in texts]

    def load(self, name):
        if name == "embed-e":
            with self._state:
                self.embedders[name] = Embedder()
            return
        started[name].set()
        finish[name].wait(30)
        with self._state:
            self.loaded[name] = (object(), object())

    monkeypatch.setattr(serve.Engine, "_load", load)
    engine = serve.Engine(serve.Catalog(catalog))
    try:
        assert started["small-m"].wait(10)
        job = serve.EmbedJob("embed-e", ["q"], truncate=True)
        engine.submit(job)
        finish["small-m"].set()
        assert job.out.get(timeout=10) == [[0.5]]
        assert embedded_during == [False]  # served before the next chat model began loading
    finally:
        for event in finish.values():
            event.set()
