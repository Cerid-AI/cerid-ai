#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Capture raw tool-call completions from a running cerid-mlx server.

    capture_corpus.py MODEL [MODEL ...] [--url URL] [--catalog FILE] [--tokenizer MODEL=DIR] [--out DIR]

Each case is rendered here, through serve.py's own message checks and the
model's chat template (or openai-harmony for gpt-oss), then posted to
/api/generate with raw=true at temperature 0. The reply is what the model
wrote, so a model or quant refresh that changes the format fails the replay
tests instead of silently dropping arguments. Review `expect` in the output
before committing it: it is the parser's reading at capture time.

/api/generate returns visible(text), not the raw text:
  - qwen3_coder replies carry no "<|" and come back unchanged;
  - gemma4 replies come back with outer whitespace stripped;
  - harmony replies lose their control tokens, so each gpt-oss case takes two
    requests. The first returns the answer (a final channel) or the analysis
    (a function call). The second stops inside the header that follows the
    analysis, so the analysis comes back whole (final case), or continues after
    the analysis so the call header comes back with its markers removed
    (call case). The completion is rebuilt from those pieces and marked so.

Needs transformers and openai-harmony. On the server's host, run it with the
server's venv and pass --catalog if the server was started with one; elsewhere,
pass --tokenizer with a directory holding each model's
tokenizer and chat template files.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import serve  # noqa: E402

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "location": {"type": "string", "description": "City name, e.g. Paris"},
                    "unit": {"type": "string", "enum": ["celsius", "fahrenheit"]},
                },
                "required": ["location"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_files",
            "description": "Find files in the repository whose names match a glob pattern.",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {"type": "string", "description": "Glob pattern, e.g. *.md"},
                    "max_results": {"type": "integer", "description": "Upper bound on results"},
                    "include_hidden": {"type": "boolean", "description": "Also search dot-directories"},
                },
                "required": ["pattern"],
            },
        },
    },
]

WEATHER_CALL = {
    "role": "assistant",
    "content": "",
    "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "get_weather", "arguments": '{"location": "Paris"}'}}
    ],
}
WEATHER_RESULT = {
    "role": "tool",
    "tool_call_id": "call_1",
    "content": '{"temperature": 18, "unit": "celsius", "condition": "light rain"}',
}
ASK_WEATHER = {"role": "user", "content": "What's the weather in Paris right now?"}

CASES = {
    "single_call": {"messages": [ASK_WEATHER], "tools": TOOLS},
    "parallel_calls": {
        "messages": [{"role": "user", "content": "Get the current weather in Paris and in Tokyo."}],
        "tools": TOOLS,
    },
    "plain_text_with_tools": {
        "messages": [{"role": "user", "content": "What is 17 + 25? Reply with just the number."}],
        "tools": TOOLS,
    },
    "tool_result_followup": {"messages": [ASK_WEATHER, WEATHER_CALL, WEATHER_RESULT], "tools": TOOLS},
    "typed_arguments": {
        "messages": [
            {
                "role": "user",
                "content": "Find at most 5 files matching test_*.py, including hidden directories.",
            }
        ],
        "tools": TOOLS,
    },
    "named_choice": {
        "messages": [{"role": "user", "content": "Tell me something about Tokyo."}],
        "tools": TOOLS,
        "tool_choice": {"type": "function", "function": {"name": "get_weather"}},
    },
}
# Harmony writes one call per completion, so it has no parallel case, and the
# forced header of a named choice is not captured to stay within the heavy
# model's request budget.
HARMONY_CASES = ("single_call", "plain_text_with_tools", "tool_result_followup")
MAX_TOKENS = 400


def generate(url: str, model: str, prompt: str, num_predict: int) -> dict:
    body = json.dumps(
        {
            "model": model,
            "prompt": prompt,
            "raw": True,
            "stream": False,
            "options": {"temperature": 0, "num_predict": num_predict},
        }
    ).encode()
    req = urllib.request.Request(url + "/api/generate", data=body, headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())


def capture_template(url, model, tool_format, tokenizer, case) -> dict:
    messages = serve.chat_messages(case["messages"])
    tools, choice = serve.chat_tools(case)
    plan = serve.chat_plan(tool_format, tokenizer, messages, tools, choice)
    prompt = tokenizer.decode(plan["prompt"])
    reply = generate(url, model, prompt, MAX_TOKENS)
    return {
        "prompt_tokens": len(plan["prompt"]),
        "completion": plan["opener_text"] + reply["response"],
        "done_reason": reply.get("done_reason"),
        "capture": "one request" + ("; forced opener prepended" if plan["opener_text"] else ""),
    }


def capture_harmony(url, model, case) -> dict:
    h, enc = serve._harmony()
    messages = serve.chat_messages(case["messages"])
    tools, choice = serve.chat_tools(case)
    plan = serve.chat_plan("harmony", None, messages, tools, choice)
    prompt = enc.decode(plan["prompt"])
    first = generate(url, model, prompt, MAX_TOKENS)
    header = "<|end|><|start|>assistant"
    if first.get("done_reason") != "stop":
        raise SystemExit(f"{model}: reply hit max_tokens; raise MAX_TOKENS for this case")
    answer = first["response"]
    if not answer.startswith("analysis"):
        # A final answer: stop again three tokens into the next header, so
        # visible() cuts at <|end|> and returns the whole analysis.
        # The reply is: 3 opener tokens, the analysis, <|end|><|start|>assistant
        # <|channel|>final<|message|> (6), the answer, <|return|>. Stop 3 tokens
        # past the analysis; +-2 still lands between <|end|> and <|message|>.
        answer_tokens = len(enc.encode(answer, allowed_special="all"))
        analysis_tokens = first["eval_count"] - answer_tokens - 10
        second = generate(url, model, prompt, 3 + analysis_tokens + 3)
        analysis = second["response"].removeprefix("analysis")
        completion = (
            f"<|channel|>analysis<|message|>{analysis}{header}<|channel|>final<|message|>{answer}<|return|>"
        )
        return _check_count(enc, {
            "prompt_tokens": len(plan["prompt"]),
            "completion": completion,
            "done_reason": "stop",
            "capture": "two requests; control tokens restored around the analysis and the final answer",
        }, first["eval_count"])
    analysis = answer.removeprefix("analysis")
    rendered = f"{prompt}<|channel|>analysis<|message|>{analysis}{header}"
    second = generate(url, model, rendered, MAX_TOKENS)
    call = second["response"]
    m = re.fullmatch(r"(commentary|analysis) to=(functions\.[\w-]+) ?(<\|constrain\|>json)?(.*)", call, re.S)
    if not m:
        raise SystemExit(f"{model}: unrecognised call header {call[:80]!r}; capture it by hand")
    channel, recipient, constrain, args = m.groups()
    rebuilt = f"<|channel|>{channel} to={recipient}{' ' + constrain if constrain else ''}<|message|>{args}<|call|>"
    return _check_count(enc, {
        "prompt_tokens": len(plan["prompt"]),
        "completion": f"<|channel|>analysis<|message|>{analysis}{header}{rebuilt}",
        "done_reason": second.get("done_reason"),
        "capture": "two requests; control tokens restored around the analysis and the call header",
    }, first["eval_count"])


def _check_count(enc, got: dict, eval_count: int) -> dict:
    # The first request generated the whole reply, so the rebuilt completion
    # must tokenize to exactly what it counted. A stripped space or a cut
    # analysis shows up here instead of in the corpus.
    n = len(enc.encode(got["completion"], allowed_special="all"))
    if n != eval_count:
        raise SystemExit(f"rebuilt completion is {n} tokens, the server generated {eval_count}: {got['completion'][:120]!r}")
    return got


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--url", default="http://127.0.0.1:11434")
    ap.add_argument("--tokenizer", action="append", default=[], help="MODEL=DIR")
    ap.add_argument("--out", default=str(HERE / "corpus"))
    ap.add_argument("--server", default="", help="what served the capture: revision and mlx/mlx-lm versions")
    ap.add_argument("--catalog", default=str(serve.CONFIG_PATH), help="the catalog the server was started with")
    args = ap.parse_args()
    catalog_path = Path(args.catalog)
    catalog = json.loads(catalog_path.read_text())["models"]
    dirs = dict(item.split("=", 1) for item in args.tokenizer)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for model in args.models:
        tool_format = catalog[model]["tool_format"]
        tokenizer = None
        if tool_format != "harmony":
            from transformers import AutoTokenizer

            weights = Path(catalog[model]["path"]).expanduser()
            if not weights.is_absolute():
                weights = catalog_path.parent / weights
            tokenizer = AutoTokenizer.from_pretrained(dirs.get(model, str(weights)))
        names = HARMONY_CASES if tool_format == "harmony" else tuple(CASES)
        cases = []
        for name in names:
            case = CASES[name]
            if tool_format == "harmony":
                got = capture_harmony(args.url, model, case)
            else:
                got = capture_template(args.url, model, tool_format, tokenizer, case)
            tools, _ = serve.chat_tools(case)
            if tool_format == "harmony":
                _, enc = serve._harmony()
                tokens = enc.encode(got["completion"], allowed_special="all")
            else:
                tokens = []
            parsed = serve.parse_reply(tool_format, got["completion"], tokens, tools, got["done_reason"] or "stop")
            expect = {k: parsed[k] for k in ("content", "calls", "finish")}
            cases.append({"name": name, "request": case, **got, "expect": expect})
            print(f"{model} {name}: {parsed['finish']} {parsed['calls'] or repr((parsed['content'] or '')[:60])}", flush=True)
        path = out / f"{model}.json"
        path.write_text(
            json.dumps(
                {
                    "model": model,
                    "tool_format": tool_format,
                    "captured": time.strftime("%Y-%m-%d"),
                    "server": args.server,
                    "sampling": "temperature 0, raw /api/generate",
                    "cases": cases,
                },
                indent=2,
                ensure_ascii=False,
            )
            + "\n"
        )
        print(f"wrote {path}", flush=True)


if __name__ == "__main__":
    main()
