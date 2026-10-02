#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Endpoint checks for the cerid-mlx server.

    check.py [PORT] [--heavy]

PORT defaults to 11434. The models come from the server's own GET /, so the
same checks run against any catalog. --heavy also loads each heavy row in turn,
which takes the heavy slot for a minute or more; skip it while someone is
using it.

When the embed row is nomic-embed-text-v1.5, embeddings are compared with
reference/nomic-embed-text-v1.5-cls.json, vectors quenchforge produced for the
same GGUF. Indexes built from quenchforge or from this server share that
space, so anything under 0.999 cosine is a different vector space, not noise.
"""

import base64
import json
import math
import re
import struct
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

PORT = int(next((a for a in sys.argv[1:] if a.isdigit()), "11434"))
REFERENCE = Path(__file__).resolve().parent / "reference" / "nomic-embed-text-v1.5-cls.json"
REFERENCE_MODEL = "nomic-embed-text-v1.5"
BASE = f"http://127.0.0.1:{PORT}"
failures = []


def call(path, body=None, timeout=300):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, raw
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def check(label, ok, detail=""):
    print(("PASS " if ok else "FAIL ") + label + (f"  [{detail}]" if detail else ""), flush=True)
    if not ok:
        failures.append(label)


def cos(a, b):
    return sum(x * y for x, y in zip(a, b)) / math.sqrt(sum(x * x for x in a) * sum(y * y for y in b))


# the catalog, as the server loaded it
code, raw = call("/")
status = json.loads(raw) if code == 200 else {}
check("GET / reports the catalog", code == 200 and not status.get("load_errors"), json.dumps(status.get("load_errors", {})))
if code != 200:
    print("FAILURES:", failures)
    sys.exit(1)
tiers = status["tiers"]
aliases = status.get("aliases", {})
tool_formats = status.get("tools", {})
SMALL = next(n for n, t in tiers.items() if t == "small")
DEFAULT = status["default"]
EMBED = next(n for n, t in tiers.items() if t == "embed")
HEAVY = [n for n, t in tiers.items() if t == "heavy"]

# tags
code, raw = call("/api/tags")
names = [m["name"] for m in json.loads(raw)["models"]]
check("GET /api/tags", code == 200, ", ".join(names))
check("aliases not listed", not any(n in names for n in aliases))
check("small listed as instruct", SMALL in names and "instruct" in SMALL, SMALL)

code, raw = call("/api/version")
revision = json.loads(raw).get("version", "") if code == 200 else ""
check("GET /api/version names a revision", bool(re.fullmatch(r"cerid-mlx-[0-9a-f]{12}", revision)), revision)

# chat, non-stream: each resident chat row and every alias
for model in (SMALL, DEFAULT, *aliases):
    t = time.perf_counter()
    code, raw = call("/api/chat", {"model": model, "stream": False, "messages": [{"role": "user", "content": "Reply with exactly the word: pong"}], "options": {"temperature": 0}})
    body = json.loads(raw)
    content = body.get("message", {}).get("content", "")
    check(f"/api/chat {model}", code == 200 and "pong" in content.lower() and body.get("model") == model,
          f"{content!r} {time.perf_counter() - t:.2f}s")

# chat, stream
code, raw = call("/api/chat", {"model": SMALL, "messages": [{"role": "user", "content": "Count from 1 to 5."}]})
lines = [json.loads(x) for x in raw.splitlines() if x.strip()]
check("/api/chat stream small", code == 200 and len(lines) > 2 and lines[-1]["done"] and "eval_count" in lines[-1],
      "".join(x["message"]["content"] for x in lines)[:60])
code, raw = call("/api/chat", {"model": DEFAULT, "stream": True, "messages": [{"role": "user", "content": "Count from 1 to 5."}]})
lines = [json.loads(x) for x in raw.splitlines() if x.strip()]
text = "".join(x["message"]["content"] for x in lines)
check("/api/chat stream default (thinking off)", code == 200 and lines[-1]["done"] and "thought" not in text and "<|" not in text and "<think>" not in text, text[:60])

# generate + json format
code, raw = call("/api/generate", {"model": SMALL, "prompt": "Say hi.", "stream": False})
check("/api/generate small", code == 200 and json.loads(raw).get("response"), json.loads(raw).get("response", "")[:40])
code, raw = call("/api/chat", {"model": SMALL, "stream": False, "format": "json", "messages": [{"role": "user", "content": "Classify 'rename a variable' as trivial|simple|complex in {\"class\": ...}"}]})
content = json.loads(raw)["message"]["content"]
try:
    json.loads(content)
    parsed = True
except json.JSONDecodeError:
    parsed = False
check("/api/chat format=json", code == 200 and parsed, content[:60])

# errors
code, _ = call("/api/chat", {"model": "nope", "messages": [{"role": "user", "content": "x"}]})
check("unknown model 404", code == 404)
code, _ = call("/api/chat", {"model": EMBED, "messages": [{"role": "user", "content": "x"}]})
check("chat on embedder 400", code == 400)
code, _ = call("/api/embed", {"model": DEFAULT, "input": "x"})
check("embed on chat model 400", code == 400)
code, _ = call("/v1/embeddings", {"model": "nomic-embed-text", "input": "x"})
check("unpinned embed name 404", code == 404)

# /v1/chat/completions and tools
WEATHER = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Current weather for a city.",
            "parameters": {"type": "object", "properties": {"location": {"type": "string"}}, "required": ["location"]},
        },
    }
]
ASK = {"role": "user", "content": "What's the weather in Paris right now?"}


def tool_round_trip(model):
    code, raw = call("/v1/chat/completions", {"model": model, "messages": [ASK], "tools": WEATHER, "max_tokens": 400})
    choice = json.loads(raw)["choices"][0] if code == 200 else {}
    calls = choice.get("message", {}).get("tool_calls") or []
    ok = code == 200 and choice["finish_reason"] == "tool_calls" and calls and calls[0]["function"]["name"] == "get_weather"
    check(f"/v1 tool call {model}", ok and "Paris" in calls[0]["function"]["arguments"], calls[0]["function"]["arguments"] if ok else raw[:160])
    if not ok:
        return
    assistant = {"role": "assistant", "content": choice["message"]["content"], "tool_calls": calls}
    if choice["message"].get("reasoning_content"):
        assistant["reasoning_content"] = choice["message"]["reasoning_content"]
    result = {"role": "tool", "tool_call_id": calls[0]["id"], "content": '{"temperature": 18, "condition": "light rain"}'}
    code, raw = call("/v1/chat/completions", {"model": model, "messages": [ASK, assistant, result], "tools": WEATHER, "max_tokens": 400})
    body = json.loads(raw)
    content = body["choices"][0]["message"]["content"] if code == 200 else ""
    cached = body.get("usage", {}).get("prompt_tokens_details", {}).get("cached_tokens", 0)
    check(f"/v1 tool result {model} (prefix reused)", code == 200 and "18" in content and cached > 0, f"cached={cached} {content[:60]!r}")


for model in (SMALL, DEFAULT):
    if tool_formats.get(model):
        tool_round_trip(model)
code, raw = call("/v1/chat/completions", {"model": DEFAULT, "stream": True, "messages": [{"role": "user", "content": "Count from 1 to 5."}]})
events = [x[len("data: "):] for x in raw.split("\n\n") if x.startswith("data: ")]
chunks = [json.loads(e) for e in events if e != "[DONE]"]
text = "".join(c["choices"][0]["delta"].get("content", "") for c in chunks if c["choices"])
check("/v1 stream default", code == 200 and events[-1] == "[DONE]" and "3" in text and "<|" not in text, text[:60])
code, _ = call("/v1/chat/completions", {"model": DEFAULT, "stream": True, "messages": [ASK], "tools": WEATHER})
check("/v1 stream with tools 400", code == 400)
if tool_formats.get(DEFAULT):
    code, raw = call("/api/chat", {"model": DEFAULT, "stream": False, "messages": [ASK], "tools": WEATHER})
    calls = json.loads(raw).get("message", {}).get("tool_calls") or []
    check("/api/chat tools (Ollama shape)", code == 200 and calls and calls[0]["function"]["arguments"].get("location") == "Paris", raw[:120])

# embeddings
ref = json.loads(REFERENCE.read_text())["items"]
texts = [r["text"] for r in ref]
want = [r["embedding"] for r in ref]
parity = EMBED == REFERENCE_MODEL
if not parity:
    print(f"NOTE embed row is {EMBED}, not {REFERENCE_MODEL}: shapes are checked, vector parity is not", flush=True)
t = time.perf_counter()
code, raw = call("/api/embed", {"model": EMBED, "input": texts})
dt = time.perf_counter() - t
got = json.loads(raw)["embeddings"]
if parity:
    cs = [cos(a, b) for a, b in zip(got, want)]
    check("/api/embed vs reference", code == 200 and len(got) == len(want) and min(cs) >= 0.999,
          f"n={len(cs)} min={min(cs):.6f} dim={len(got[0])} {dt:.2f}s")
    code, raw = call("/api/embed", {"model": EMBED, "input": texts, "truncate": True})
    counts = json.loads(raw)["prompt_eval_count"]
    check("token count matches reference", counts == sum(len(r["tokens"]) for r in ref), str(counts))
else:
    check("/api/embed batch", code == 200 and len(got) == len(texts), f"dim={len(got[0]) if got else 0} {dt:.2f}s")
dim = len(got[0]) if got else 0
code, raw = call("/api/embed", {"model": EMBED, "input": texts[0]})
body = json.loads(raw)
check("/api/embed single string", code == 200 and len(body["embeddings"]) == 1 and body["model"] == EMBED and "prompt_eval_count" in body)
code, raw = call("/api/embeddings", {"model": EMBED, "prompt": texts[1]})
body = json.loads(raw)
legacy_ok = code == 200 and len(body.get("embedding", [])) == dim
if parity and legacy_ok:
    legacy_ok = cos(body["embedding"], want[1]) >= 0.999
check("/api/embeddings legacy", legacy_ok)
code, raw = call("/v1/embeddings", {"model": EMBED, "input": texts[2:5]})
body = json.loads(raw)
ok = code == 200 and body["object"] == "list" and [d["index"] for d in body["data"]] == [0, 1, 2] and body["usage"]["prompt_tokens"] > 0
if parity and ok:
    ok = min(cos(d["embedding"], w) for d, w in zip(body["data"], want[2:5])) >= 0.999
check("/v1/embeddings float", ok)
code, raw = call("/v1/embeddings", {"model": EMBED, "input": texts[3], "encoding_format": "base64"})
vec = list(struct.unpack(f"<{dim}f", base64.b64decode(json.loads(raw)["data"][0]["embedding"]))) if code == 200 else []
check("/v1/embeddings base64", code == 200 and len(vec) == dim and (not parity or cos(vec, want[3]) >= 0.999))
long_text = " ".join(f"word{i}" for i in range(1500))
code, raw = call("/api/embed", {"model": EMBED, "input": long_text, "truncate": False})
check("over-context truncate=false 400", code == 400, raw[:80])
code, raw = call("/api/embed", {"model": EMBED, "input": long_text})
check("over-context truncates by default", code == 200)

# concurrency: a long default-model generation must not hold up small chat or embeddings
result = {}


def long_gen():
    t0 = time.perf_counter()
    c, r = call("/api/chat", {"model": DEFAULT, "stream": False, "messages": [{"role": "user", "content": "Write a 900-word essay about rivers."}], "options": {"num_predict": 1200}})
    result["long"] = (c, time.perf_counter() - t0, json.loads(r).get("eval_count"))


th = threading.Thread(target=long_gen)
th.start()
time.sleep(2.0)
t0 = time.perf_counter()
code_s, raw_s = call("/api/chat", {"model": SMALL, "stream": False, "messages": [{"role": "user", "content": "Reply with exactly: ok"}]})
small_s = time.perf_counter() - t0
t0 = time.perf_counter()
code_e, _ = call("/api/embed", {"model": EMBED, "input": texts})
embed_s = time.perf_counter() - t0
still_running = th.is_alive()
th.join()
check("small chat during long default gen", code_s == 200 and still_running and small_s < 5, f"{small_s:.2f}s")
check("embed during long default gen", code_e == 200 and embed_s < 5, f"{embed_s:.2f}s")
check("long default gen completed", result["long"][0] == 200, f"{result['long'][1]:.1f}s eval={result['long'][2]}")

if "--heavy" in sys.argv:
    if not HEAVY:
        print("NOTE --heavy: the catalog has no heavy rows", flush=True)
    resident = {SMALL, DEFAULT, EMBED}
    for i, model in enumerate(HEAVY):
        harmony = tool_formats.get(model) == "harmony"
        t0 = time.perf_counter()
        code, raw = call("/api/chat", {"model": model, "stream": False, "messages": [{"role": "user", "content": "Reply with exactly the word: pong"}], "options": {"num_predict": 400}}, timeout=900)
        content = json.loads(raw).get("message", {}).get("content", "")
        ok = code == 200 and "pong" in content.lower()
        if harmony:
            ok = ok and "<|" not in content and "analysis" not in content
        check(f"/api/chat {model}" + (" (Harmony final)" if harmony else ""), ok, f"{content!r} {time.perf_counter() - t0:.1f}s")
        if harmony:
            code, raw = call("/api/chat", {"model": model, "messages": [{"role": "user", "content": "Reply with exactly the word: pong"}], "options": {"num_predict": 400}}, timeout=900)
            lines = [json.loads(x) for x in raw.splitlines() if x.strip()]
            text = "".join(x["message"]["content"] for x in lines)
            check(f"/api/chat {model} stream (Harmony final)", code == 200 and "pong" in text.lower() and "<|" not in text, repr(text[:60]))
        if tool_formats.get(model):
            tool_round_trip(model)
        code, raw = call("/api/ps")
        ps = set(m["name"] for m in json.loads(raw)["models"])
        check(f"{model} co-resident with small+default+embed", {model} | resident <= ps, ", ".join(sorted(ps)))
        if i:
            check(f"swap to {model} evicted only the heavy slot", HEAVY[i - 1] not in ps, ", ".join(sorted(ps)))

print("FAILURES:", failures if failures else "none")
sys.exit(1 if failures else 0)
