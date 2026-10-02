#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Ollama- and OpenAI-compatible model server for Apple silicon, on MLX.

Chat: GET /api/tags, /api/ps, /api/version; POST /api/chat, /api/generate, /api/show,
and POST /v1/chat/completions (OpenAI).
Embeddings: POST /api/embed, /api/embeddings (Ollama) and /v1/embeddings (OpenAI).
Tools: /v1/chat/completions and /api/chat accept `tools`. Each model row names its
tool format; the model's own output is parsed into tool calls.

models.json beside this file (or CERID_MLX_CONFIG) is the one place models are
declared. A row's path is absolute, ~-relative, or relative to the catalog
file. Each row has a tier:

  small    utility chat model. Loaded at start, never unloaded.
  default  answers requests that name no model. Loaded at start, never unloaded.
  heavy    loaded when a request names it. One heavy slot; naming a different
           heavy model swaps only that slot.
  embed    embedding model. Loaded at start, never unloaded.

"aliases" maps legacy names onto a tier (small, default) or a model name.
Aliases resolve on request and are not listed in /api/tags, so a client that
ranks the listed names never sees a name that does not match its weights.

All MLX work runs on one worker thread. Requests in flight are interleaved one
token at a time, so a small-model call is not queued behind a long generation.
Loading a heavy model blocks that thread until the load finishes. Each tier caps
how many generations run at once; the rest wait in order.

Requests on /v1/chat/completions, and /api/chat requests with tools, reuse the
KV cache of an earlier prompt that shares their prefix, and prefill in chunks
so other generations keep stepping in between.

Names have no slash, so Cerid treats them as local. Loopback and the peers in
CERID_MLX_ALLOW (by default the Colima VM) are the only peers accepted.
"""

from __future__ import annotations

import base64
import copy
import functools
import gc
import hashlib
import json
import os
import queue
import re
import struct
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG_PATH = Path(
    os.environ.get(
        "CERID_MLX_CONFIG",
        str(HERE / "models.json"),
    )
)
PORT = int(os.environ.get("CERID_MLX_PORT", "11434"))
# 0.0.0.0 is required under Colima: it reaches the host at 192.168.5.2, a
# userspace forward onto every host listener, not onto 127.0.0.1. Peers are
# filtered below, so a LAN or tailnet address does not get a model.
BIND_HOSTS = [
    h.strip()
    for h in os.environ.get("CERID_MLX_BIND", "0.0.0.0").split(",")
    if h.strip()
]
# 192.168.5.1 is the Colima VM. Another container runtime may connect from a
# different address; install.sh probes from a container and names it. Anything
# else on 0.0.0.0 is refused before a model call.
ALLOWED_PEERS = {
    h.strip()
    for h in os.environ.get(
        "CERID_MLX_ALLOW", "127.0.0.1,::1,192.168.5.1"
    ).split(",")
    if h.strip()
}

# What /api/version reports. A client that certified a model on one revision
# (an agent runtime's local worker, say) treats any other revision as uncertified, so this
# moves with everything that can change how a model answers: this file, the
# embedder, the catalog, each model's weights and the packages that run them.
REVISION = re.compile(r"cerid-mlx-[0-9a-f]{12}")
REVISION_SOURCES = (HERE / "serve.py", HERE / "nomic_bert.py")
REVISION_PACKAGES = ("mlx", "mlx-lm", "openai-harmony")

RESIDENT_TIERS = ("embed", "small", "default")
CHAT_TIERS = ("small", "default", "heavy")
TIERS = (*CHAT_TIERS, "embed")

# How each model family writes tool calls. qwen3_coder and gemma4 are parsed by
# mlx-lm's parsers; harmony (gpt-oss) is rendered and parsed by openai-harmony.
TOOL_FORMATS = ("qwen3_coder", "gemma4", "harmony")
TOOL_MARKERS = {
    "qwen3_coder": ("<tool_call>", "</tool_call>"),
    "gemma4": ("<|tool_call>", "<tool_call|>"),
}
# The forced start of a reply for tool_choice "required" (first) or a named
# function (second). The model continues from there, so the reply is a call.
TOOL_OPENERS = {
    "qwen3_coder": ("<tool_call>\n<function=", "<tool_call>\n<function={name}>\n"),
    "gemma4": ("<|tool_call>call:", "<|tool_call>call:{name}{{"),
}
# OpenAI's rule for function names. The gemma4 parser only reads [\w-] names,
# so a looser name could be offered but never parsed back.
TOOL_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}")

# Per-tier limits. The "tiers" block in models.json overrides any of them.
#   max_active        generations running at once on the tier; null = no cap
#   prompt_cache_gib  memory for reusable prompt KV caches; 0 turns reuse off
#   prefill_chunk     prompt tokens per step of the worker thread
TIER_LIMITS = {
    "small": {"max_active": None, "prompt_cache_gib": 2, "prefill_chunk": 512},
    "default": {"max_active": 3, "prompt_cache_gib": 8, "prefill_chunk": 512},
    "heavy": {"max_active": 1, "prompt_cache_gib": 16, "prefill_chunk": 512},
}
PROMPT_CACHE_ENTRIES = 16
V1_MAX_TOKENS = 8192
V1_DEFAULT_MAX_TOKENS = 4096


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _size(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += (Path(root) / name).stat().st_size
            except OSError:
                continue
    return total


def _package_version(name: str) -> str | None:
    from importlib import metadata

    try:
        return metadata.version(name)
    except metadata.PackageNotFoundError:
        return None


def _revision(config: bytes, models: dict[str, dict]) -> str:
    digest = hashlib.sha256()

    def add(label: str, value: bytes) -> None:
        digest.update(f"{label}\0{len(value)}\0".encode())
        digest.update(value)

    for source in REVISION_SOURCES:
        try:
            add(f"source:{source.name}", source.read_bytes())
        except OSError:
            add(f"source:{source.name}", b"absent")
    add("config", config)
    for name in sorted(models):
        add(f"weights:{name}", str(models[name]["size"]).encode())
    for package in REVISION_PACKAGES:
        add(f"package:{package}", (_package_version(package) or "absent").encode())
    return f"cerid-mlx-{digest.hexdigest()[:12]}"


def _tier_limits(raw: dict) -> dict[str, dict]:
    limits = {tier: dict(values) for tier, values in TIER_LIMITS.items()}
    for tier, values in (raw or {}).items():
        if tier not in limits or not isinstance(values, dict):
            raise SystemExit(f"tiers: {tier!r} must be one of {CHAT_TIERS} with an object value")
        for key, value in values.items():
            if key not in limits[tier]:
                raise SystemExit(f"tiers.{tier}: unknown key {key!r}")
            if key == "max_active" and value is None:
                limits[tier][key] = None
                continue
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise SystemExit(f"tiers.{tier}.{key} must be a non-negative number")
            if key in ("max_active", "prefill_chunk") and (int(value) != value or value < 1):
                raise SystemExit(f"tiers.{tier}.{key} must be a positive integer")
            limits[tier][key] = value
    return limits


class Catalog:
    def __init__(self, path: Path) -> None:
        config = path.read_bytes()
        raw = json.loads(config)
        self.models: dict[str, dict] = {}
        for name, spec in raw["models"].items():
            if "/" in name:
                raise SystemExit(f"model name {name!r} contains a slash")
            tier = spec.get("tier")
            if tier not in TIERS:
                raise SystemExit(f"{name}: tier must be one of {TIERS}, got {tier!r}")
            tool_format = spec.get("tool_format")
            if tool_format is not None and (tier == "embed" or tool_format not in TOOL_FORMATS):
                raise SystemExit(f"{name}: tool_format must be one of {TOOL_FORMATS} on a chat model")
            weights = Path(spec["path"]).expanduser()
            if not weights.is_absolute():
                weights = path.parent / weights
            if tier == "embed" and not weights.is_file():
                raise SystemExit(f"missing embedding weights for {name}: {weights}")
            if tier != "embed" and not weights.is_dir():
                raise SystemExit(f"missing model directory for {name}: {weights}")
            self.models[name] = {
                "tier": tier,
                "path": weights,
                "family": spec.get("family", ""),
                "parameter_size": spec.get("parameter_size", ""),
                "quantization": spec.get("quantization", ""),
                "pooling": spec.get("pooling", "cls"),
                "tool_format": tool_format,
                "size": _size(weights),
                "modified_at": datetime.fromtimestamp(
                    weights.stat().st_mtime, timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
        self.by_tier: dict[str, str] = {}
        for tier in ("small", "default"):
            rows = [n for n, s in self.models.items() if s["tier"] == tier]
            if len(rows) != 1:
                raise SystemExit(f"exactly one {tier!r} model is required, found {rows}")
            self.by_tier[tier] = rows[0]
        self.default = self.by_tier["default"]
        self.aliases: dict[str, str] = {}
        for alias, target in (raw.get("aliases") or {}).items():
            resolved = self.by_tier.get(target, target)
            if resolved not in self.models:
                raise SystemExit(f"alias {alias!r} targets unknown {target!r}")
            if alias in self.models:
                raise SystemExit(f"alias {alias!r} shadows a model name")
            self.aliases[alias] = resolved
        self.limits = _tier_limits(raw.get("tiers"))
        self.revision = _revision(config, self.models)

    def resolve(self, name: str) -> str | None:
        name = name.strip()
        if name in self.models:
            return name
        return self.aliases.get(name)

    def tier(self, name: str) -> str:
        return self.models[name]["tier"]


class GenJob:
    def __init__(self, name, messages, prompt, raw, max_tokens, temperature, top_p) -> None:
        self.name = name
        self.messages = messages
        self.prompt = prompt
        self.raw = raw
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.top_p = top_p
        self.out: queue.Queue = queue.Queue()
        self.cancelled = threading.Event()
        self.gen = None


class ChatJob(GenJob):
    """A chat turn rendered to tokens here, with tools and prefix-cache reuse.

    `messages` are the client's messages, already checked by chat_messages().
    `tools` is None when the turn offers none (including tool_choice "none").
    `choice` is "auto", "required", or a function name.
    """

    def __init__(self, name, messages, tools, choice, max_tokens, temperature, top_p) -> None:
        super().__init__(name, messages, "", False, max_tokens, temperature, top_p)
        self.tools = tools
        self.choice = choice


class EmbedJob:
    def __init__(self, name: str, texts: list[str], truncate: bool) -> None:
        self.name = name
        self.texts = texts
        self.truncate = truncate
        self.out: queue.Queue = queue.Queue()


class BadRequest(ValueError):
    """The request cannot be served as sent. Returned to the caller as a 400."""


class Engine:
    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog
        self.loaded: dict[str, tuple] = {}  # chat name -> (model, tokenizer)
        self.embedders: dict = {}
        self.prompt_caches: dict = {}  # chat name -> mlx_lm LRUPromptCache
        self.heavy: str | None = None
        self.load_errors: dict[str, str] = {}
        self._state = threading.Lock()
        self._inbox: queue.Queue = queue.Queue()
        self._active: list[GenJob] = []
        self._pending_heavy: list[GenJob] = []
        self._waiting: dict[str, list[GenJob]] = {"small": [], "default": []}
        threading.Thread(target=self._loop, name="mlx-gpu", daemon=True).start()

    # -- loading ------------------------------------------------------------

    def _load(self, name: str) -> None:
        spec = self.catalog.models[name]
        print(f"loading {name}", flush=True)
        started = time.perf_counter()
        try:
            if spec["tier"] == "embed":
                from nomic_bert import NomicBertEmbedder

                embedder = NomicBertEmbedder(spec["path"], pooling=spec["pooling"])
                with self._state:
                    self.embedders[name] = embedder
            else:
                from mlx_lm import load
                from mlx_lm.models.cache import LRUPromptCache

                model, tokenizer = load(str(spec["path"]))
                budget = self.catalog.limits[spec["tier"]]["prompt_cache_gib"]
                with self._state:
                    self.loaded[name] = (model, tokenizer)
                    if budget > 0:
                        self.prompt_caches[name] = LRUPromptCache(
                            PROMPT_CACHE_ENTRIES, int(budget * (1 << 30))
                        )
                    if spec["tier"] == "heavy":
                        self.heavy = name
        except Exception as exc:
            with self._state:
                self.load_errors[name] = f"{type(exc).__name__}: {exc}"
            print(f"load failed {name}: {self.load_errors[name]}", flush=True)
            raise
        with self._state:
            self.load_errors.pop(name, None)
        print(f"loaded {name} in {time.perf_counter() - started:.1f}s", flush=True)

    def _ensure(self, name: str) -> None:
        with self._state:
            ready = name in self.loaded or name in self.embedders
        if not ready:
            self._load(name)

    def _unload_heavy(self) -> None:
        import mlx.core as mx

        with self._state:
            name = self.heavy
            if name is None:
                return
            self.loaded.pop(name, None)
            # Cached prompts hold that model's KV arrays; they go with it.
            self.prompt_caches.pop(name, None)
            self.heavy = None
        gc.collect()
        mx.clear_cache()
        print(f"unloaded {name}", flush=True)

    def resident(self) -> list[str]:
        with self._state:
            return [*self.embedders, *self.loaded]

    def cache_stats(self) -> dict:
        with self._state:
            caches = dict(self.prompt_caches)
        return {name: {"entries": len(c), "bytes": c.nbytes} for name, c in caches.items()}

    # -- scheduling -----------------------------------------------------------

    def submit(self, job) -> None:
        self._inbox.put(job)

    def _loop(self) -> None:
        import mlx.core as mx

        # stream_generate sets the wired limit on entry and restores the old
        # value on exit. Interleaved generations would restore each other's
        # values, so pin it once to the value they all set.
        if mx.metal.is_available():
            mx.set_wired_limit(mx.device_info()["max_recommended_working_set_size"])
        for tier in RESIDENT_TIERS:
            for name, spec in self.catalog.models.items():
                if spec["tier"] == tier:
                    try:
                        self._load(name)
                    except Exception:  # noqa: BLE001 — retried on the first request
                        pass
        while True:
            fresh = []
            # Block only when nothing can run. A job waiting for a slot that
            # the last step freed must start now, not when the next request
            # happens to arrive.
            idle = not (self._active or self._pending_heavy or any(self._waiting.values()))
            try:
                fresh.append(self._inbox.get(block=idle))
                while True:
                    fresh.append(self._inbox.get_nowait())
            except queue.Empty:
                pass
            for job in fresh:
                self._admit(job)
            self._admit_pending_heavy()
            self._admit_waiting()
            for job in list(self._active):
                self._step(job)

    def _admit(self, job) -> None:
        if isinstance(job, EmbedJob):
            try:
                self._ensure(job.name)
                with self._state:
                    embedder = self.embedders[job.name]
                job.out.put(embedder.embed(job.texts, truncate=job.truncate))
            except Exception as exc:  # noqa: BLE001 — returned to the caller
                job.out.put(exc)
            return
        tier = self.catalog.tier(job.name)
        if tier == "heavy":
            self._pending_heavy.append(job)
            return
        self._waiting[tier].append(job)

    def _has_slot(self, tier: str) -> bool:
        cap = self.catalog.limits[tier]["max_active"]
        if cap is None:
            return True
        return sum(1 for j in self._active if self.catalog.tier(j.name) == tier) < cap

    def _admit_waiting(self) -> None:
        for tier, waiting in self._waiting.items():
            while waiting:
                job = waiting[0]
                if job.cancelled.is_set():
                    waiting.pop(0)
                    job.out.put(None)
                    continue
                if not self._has_slot(tier):
                    break
                waiting.pop(0)
                self._start(job)

    def _admit_pending_heavy(self) -> None:
        while self._pending_heavy:
            job = self._pending_heavy[0]
            if job.cancelled.is_set():
                self._pending_heavy.pop(0)
                job.out.put(None)
                continue
            if self.heavy != job.name:
                if any(j.name == self.heavy for j in self._active):
                    return  # the heavy slot is busy; swap once it drains
                self._unload_heavy()
            elif not self._has_slot("heavy"):
                return
            self._pending_heavy.pop(0)
            self._start(job)

    def _start(self, job: GenJob) -> None:
        try:
            self._ensure(job.name)
        except Exception as exc:  # noqa: BLE001 — returned to the caller
            job.out.put(exc)
            job.out.put(None)
            return
        job.gen = self._generate_chat(job) if isinstance(job, ChatJob) else self._generate(job)
        self._active.append(job)

    def _step(self, job: GenJob) -> None:
        done = False
        if job.cancelled.is_set():
            job.gen.close()
            print(f"cancelled {job.name}: client disconnected", flush=True)
            done = True
        else:
            try:
                item = next(job.gen)
                # None is a prefill step: it produced no output, but it gave
                # the other generations their turn.
                if item is not None:
                    job.out.put(item)
                    done = item[1]
            except StopIteration:
                done = True
            except Exception as exc:  # noqa: BLE001 — returned as an Ollama error
                job.out.put(exc)
                done = True
        if done:
            self._active.remove(job)
            job.out.put(None)

    # -- generation -----------------------------------------------------------

    def _render(self, name: str, tokenizer, messages, prompt: str, raw: bool) -> str:
        if raw:
            return prompt
        # mlx-lm turns thinking on unless the caller passes False. Gemma 4 then
        # spends the reply inside <|channel>thought, which Cerid reads as the answer.
        chat = messages or [{"role": "user", "content": prompt}]
        extra = {"enable_thinking": False}
        if self.catalog.models[name]["family"] == "gpt-oss":
            extra["reasoning_effort"] = "medium"
        try:
            rendered = tokenizer.apply_chat_template(
                chat, add_generation_prompt=True, tokenize=False, **extra
            )
        except TypeError:
            rendered = tokenizer.apply_chat_template(
                chat, add_generation_prompt=True, tokenize=False
            )
        if isinstance(rendered, list):
            return tokenizer.decode(rendered)
        return rendered

    def _generate(self, job: GenJob):
        from mlx_lm import stream_generate
        from mlx_lm.sample_utils import make_sampler

        with self._state:
            model, tokenizer = self.loaded[job.name]
        # Render with the tokenizer of the model that answers. A prompt built
        # for another family feeds Gemma a Harmony prompt and gpt-oss a Gemma one.
        prompt = self._render(job.name, tokenizer, job.messages, job.prompt, job.raw)
        top_p = job.top_p if 0 < job.top_p < 1 else 0.0
        sampler = make_sampler(temp=job.temperature, top_p=top_p)
        pieces: list[str] = []
        last = None
        for response in stream_generate(
            model, tokenizer, prompt, max_tokens=job.max_tokens, sampler=sampler
        ):
            last = response
            if response.text:
                pieces.append(response.text)
                yield response.text, False, None
        yield "", True, _stats(last) | {"text": "".join(pieces)}

    def _generate_chat(self, job: ChatJob):
        """Generate one chat turn from tokens, reusing a cached prompt prefix.

        Yields None after each prefill chunk, (text, False, token) per
        generated token, then ("", True, stats). stats carries the completion
        text and tokens for the parsers.
        """
        import mlx.core as mx
        from mlx_lm import stream_generate
        from mlx_lm.generate import generation_stream
        from mlx_lm.models.cache import (
            can_trim_prompt_cache,
            make_prompt_cache,
            trim_prompt_cache,
        )
        from mlx_lm.sample_utils import make_sampler

        with self._state:
            model, tokenizer = self.loaded[job.name]
            store = self.prompt_caches.get(job.name)
        spec = self.catalog.models[job.name]
        started = time.perf_counter()
        plan = chat_plan(spec["tool_format"], tokenizer, job.messages, job.tools, job.choice)
        prompt = plan["prompt"]

        cache, rest = (None, prompt) if store is None else store.fetch_nearest_cache(job.name, prompt)
        if cache is not None and not rest:
            # An exact hit leaves nothing to feed. Step back one token so the
            # first generated token comes from a real forward pass.
            if can_trim_prompt_cache(cache):
                trim_prompt_cache(cache, 1)
                rest = prompt[-1:]
            else:
                cache, rest = None, prompt
        cached = len(prompt) - len(rest)
        if cache is None:
            cache = make_prompt_cache(model)

        # Prefill all but the last prompt token in chunks, yielding between
        # them. Stop exactly at the checkpoint (the conversation before the
        # generation prompt) and keep a copy: the next turn of an agent loop
        # extends that prefix even when the template re-renders this turn's
        # reply differently from the tokens the model produced.
        pos = cached
        checkpoint = plan["checkpoint"]
        chunk = int(self.catalog.limits[spec["tier"]]["prefill_chunk"])
        while len(prompt) - pos > 1:
            end = min(pos + chunk, len(prompt) - 1)
            if pos < checkpoint < end:
                end = checkpoint
            with mx.stream(generation_stream):
                model(mx.array(prompt[pos:end])[None], cache=cache)
                mx.eval([c.state for c in cache])
            pos = end
            if store is not None and pos == checkpoint:
                store.insert_cache(job.name, prompt[:pos], copy.deepcopy(cache))
            mx.clear_cache()
            yield None

        top_p = job.top_p if 0 < job.top_p < 1 else 0.0
        sampler = make_sampler(temp=job.temperature, top_p=top_p)
        fed = list(prompt)  # every token the cache has seen, for the post-turn entry
        completion = list(plan["opener_tokens"])
        pieces: list[str] = []
        feed = prompt[pos:]
        stops = plan["stops"]
        then = plan["then"]
        remaining = job.max_tokens
        generated = 0
        finish = "length"
        first_token_at = None
        while True:
            phase_stops = stops | {plan["end"]} if then else stops
            ended = None
            count = 0
            responses = stream_generate(
                model, tokenizer, feed, max_tokens=remaining, sampler=sampler, prompt_cache=cache
            )
            for response in responses:
                if first_token_at is None:
                    first_token_at = time.perf_counter()
                count += 1
                fed.append(response.token)
                completion.append(response.token)
                if response.text:
                    pieces.append(response.text)
                yield response.text, False, response.token
                if response.finish_reason is not None:
                    finish = response.finish_reason
                    ended = "eos" if finish == "stop" else "length"
                    break
                if response.token in phase_stops:
                    finish = "stop"
                    ended = response.token
                    break
            responses.close()
            remaining -= count
            generated += count
            if then and ended == plan["end"]:
                if remaining <= 0:
                    finish = "length"
                    break
                # Harmony, tool_choice required: the analysis ended. Force the
                # header of a function call and let the model write the rest.
                feed = then
                fed.extend(then)
                completion.extend(then)
                pieces.append(plan["then_text"])
                then = None
                continue
            break

        if store is not None:
            store.insert_cache(job.name, fed, cache)
        done_at = time.perf_counter()
        yield "", True, {
            "prompt_tokens": len(prompt),
            "cached_tokens": cached,
            "completion_tokens": generated,
            "prompt_eval_duration": int(1e9 * ((first_token_at or done_at) - started)),
            "eval_duration": int(1e9 * (done_at - (first_token_at or done_at))),
            "finish": finish,
            "text": plan["opener_text"] + "".join(pieces),
            "tokens": completion,
        }


# -- chat turns: messages, tools, rendering -----------------------------------


def _text(content, where: str) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for part in content:
            if not isinstance(part, dict) or part.get("type") != "text" or not isinstance(part.get("text"), str):
                raise BadRequest(f"{where}: only text content parts are supported")
            parts.append(part["text"])
        return "".join(parts)
    raise BadRequest(f"{where}: content must be a string or a list of text parts")


def chat_tools(body: dict) -> tuple[list | None, str | None]:
    """Check `tools` and `tool_choice`.

    Returns (tools, choice). tools is None when none are offered, including
    tool_choice "none", which renders the turn without them so the model has
    nothing to call. choice is "auto", "required", or a function name.
    """
    tools = body.get("tools")
    raw_choice = body.get("tool_choice")
    if tools is None or tools == []:
        if raw_choice not in (None, "none", "auto"):
            raise BadRequest("tool_choice needs tools")
        return None, None
    if not isinstance(tools, list):
        raise BadRequest("tools must be a list")
    names = []
    for i, tool in enumerate(tools):
        fn = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(tool, dict) or tool.get("type", "function") != "function" or not isinstance(fn, dict):
            raise BadRequest(f"tools[{i}] must be {{'type': 'function', 'function': {{...}}}}")
        name = fn.get("name")
        if not isinstance(name, str) or not TOOL_NAME.fullmatch(name):
            raise BadRequest(f"tools[{i}].function.name must match [A-Za-z0-9_-]{{1,64}}")
        if not isinstance(fn.get("description", ""), str):
            raise BadRequest(f"tools[{i}].function.description must be a string")
        if not isinstance(fn.get("parameters", {}), dict):
            raise BadRequest(f"tools[{i}].function.parameters must be a JSON schema object")
        names.append(name)
    if len(set(names)) != len(names):
        raise BadRequest("tool names must be unique")
    if raw_choice in (None, "auto"):
        return tools, "auto"
    if raw_choice == "none":
        return None, None
    if raw_choice == "required":
        return tools, "required"
    if isinstance(raw_choice, dict) and raw_choice.get("type", "function") == "function":
        name = (raw_choice.get("function") or {}).get("name")
        if name not in names:
            raise BadRequest(f"tool_choice names {name!r}, which is not in tools")
        return tools, name
    raise BadRequest("tool_choice must be auto, none, required, or {'type': 'function', 'function': {'name': ...}}")


def chat_messages(messages) -> list[dict]:
    """Check a client's messages and put them in the shape chat templates read.

    Assistant tool calls carry `arguments` as a JSON object string (OpenAI) or
    an object (Ollama); templates iterate them, so the result always holds the
    object. `reasoning_content` (or Ollama's `thinking`) on an assistant turn
    is kept: gpt-oss renders it as the analysis that led to the call.
    """
    if not isinstance(messages, list) or not messages:
        raise BadRequest("messages must be a non-empty list")
    out = []
    for i, message in enumerate(messages):
        where = f"messages[{i}]"
        if not isinstance(message, dict):
            raise BadRequest(f"{where} must be an object")
        role = message.get("role")
        if role not in ("system", "developer", "user", "assistant", "tool"):
            raise BadRequest(f"{where}.role {role!r} is not supported")
        item = {"role": "system" if role == "developer" else role, "content": _text(message.get("content"), where)}
        if role == "assistant":
            reasoning = message.get("reasoning_content") or message.get("reasoning") or message.get("thinking")
            if reasoning:
                if not isinstance(reasoning, str):
                    raise BadRequest(f"{where}.reasoning_content must be a string")
                item["reasoning_content"] = reasoning
            calls = message.get("tool_calls") or []
            if not isinstance(calls, list):
                raise BadRequest(f"{where}.tool_calls must be a list")
            normalised = []
            for j, call in enumerate(calls):
                fn = call.get("function") if isinstance(call, dict) else None
                if not isinstance(fn, dict) or not isinstance(fn.get("name"), str):
                    raise BadRequest(f"{where}.tool_calls[{j}].function.name is required")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    try:
                        args = json.loads(args) if args.strip() else {}
                    except json.JSONDecodeError as exc:
                        raise BadRequest(f"{where}.tool_calls[{j}].function.arguments is not valid JSON: {exc}") from None
                if not isinstance(args, dict):
                    raise BadRequest(f"{where}.tool_calls[{j}].function.arguments must be a JSON object")
                normalised.append(
                    {
                        "id": call.get("id") or f"call_{i}_{j}",
                        "type": "function",
                        "function": {"name": fn["name"], "arguments": args},
                    }
                )
            if normalised:
                item["tool_calls"] = normalised
        if role == "tool":
            for key in ("tool_call_id", "name"):
                if isinstance(message.get(key), str):
                    item[key] = message[key]
            if "name" not in item and isinstance(message.get("tool_name"), str):
                item["name"] = message["tool_name"]  # Ollama
        out.append(item)
    return out


def _encode(tokenizer, text: str) -> list[int]:
    # The same rule stream_generate applies to a string prompt: do not add a
    # BOS the chat template already wrote.
    bos = tokenizer.bos_token
    return list(tokenizer.encode(text, add_special_tokens=bos is None or not text.startswith(bos)))


def chat_plan(tool_format: str | None, tokenizer, messages: list[dict], tools, choice) -> dict:
    """Render a turn to prompt tokens and describe how to generate it.

      prompt         tokens to feed, ending in any forced opener
      checkpoint     prompt length before the generation prompt, 0 if unknown
      opener_text    forced start of the reply (tool_choice required/named)
      opener_tokens  the same, as tokens
      stops          token ids that end the reply besides the tokenizer's EOS
      end, then      harmony only: after the analysis ends at `end`, feed
                     `then` (the forced header of a function call)
    """
    if tool_format == "harmony":
        return _harmony_plan(messages, tools, choice)
    if tools and choice != "auto" and tool_format not in TOOL_OPENERS:
        raise BadRequest(f"tool_choice {choice!r} is not supported for this model")
    opener = ""
    if tools and choice == "required":
        opener = TOOL_OPENERS[tool_format][0]
    elif tools and choice not in (None, "auto"):
        opener = TOOL_OPENERS[tool_format][1].format(name=choice)
    extra: dict = {"enable_thinking": False}
    if tools:
        extra["tools"] = tools
    import jinja2

    try:
        full = tokenizer.apply_chat_template(messages, add_generation_prompt=True, tokenize=False, **extra)
        history = tokenizer.apply_chat_template(messages, add_generation_prompt=False, tokenize=False, **extra)
    except jinja2.TemplateError as exc:
        raise BadRequest(f"the model's chat template rejected the messages: {exc}") from None
    prompt = _encode(tokenizer, full + opener)
    checkpoint = 0
    if full.startswith(history):
        head = _encode(tokenizer, history)
        if prompt[: len(head)] == head:
            checkpoint = min(len(head), len(prompt) - 1)
    opener_tokens = prompt[len(_encode(tokenizer, full)):] if opener else []
    return {
        "prompt": prompt,
        "checkpoint": checkpoint,
        "opener_text": opener,
        "opener_tokens": opener_tokens,
        "stops": set(),
        "end": None,
        "then": None,
        "then_text": "",
    }


@functools.lru_cache(maxsize=1)
def _harmony():
    # openai-harmony loads the o200k vocabulary on first use. A tiktoken/
    # directory beside this file is used when present, so the server does not
    # need the network; otherwise harmony downloads and caches it.
    vocab = HERE / "tiktoken"
    if vocab.is_dir():
        os.environ.setdefault("TIKTOKEN_ENCODINGS_BASE", str(vocab))
    import openai_harmony as h

    return h, h.load_harmony_encoding(h.HarmonyEncodingName.HARMONY_GPT_OSS)


def harmony_conversation(messages: list[dict], tools):
    """OpenAI-shaped messages as a harmony Conversation.

    System and developer text become the developer instructions, as the
    gpt-oss chat template does. An assistant turn's reasoning_content is its
    analysis; with tool_calls, its text is commentary, otherwise the final
    answer. Harmony drops analysis before a final answer when it renders, so
    the analysis is kept only while a turn is still calling tools.
    """
    h, _ = _harmony()
    system = (
        h.SystemContent.new()
        .with_reasoning_effort(h.ReasoningEffort.MEDIUM)
        .with_conversation_start_date(time.strftime("%Y-%m-%d"))
    )
    convo = [h.Message.from_role_and_content(h.Role.SYSTEM, system)]
    instructions = "\n\n".join(m["content"] for m in messages if m["role"] == "system" and m["content"])
    if instructions or tools:
        developer = h.DeveloperContent.new()
        if instructions:
            developer = developer.with_instructions(instructions)
        if tools:
            developer = developer.with_function_tools(
                [
                    h.ToolDescription.new(
                        t["function"]["name"],
                        t["function"].get("description", ""),
                        parameters=t["function"].get("parameters") or None,
                    )
                    for t in tools
                ]
            )
        convo.append(h.Message.from_role_and_content(h.Role.DEVELOPER, developer))
    names: dict[str, str] = {}
    for i, m in enumerate(messages):
        if m["role"] == "system":
            continue
        if m["role"] == "user":
            convo.append(h.Message.from_role_and_content(h.Role.USER, m["content"]))
        elif m["role"] == "assistant":
            calls = m.get("tool_calls") or []
            if m.get("reasoning_content"):
                convo.append(h.Message.from_role_and_content(h.Role.ASSISTANT, m["reasoning_content"]).with_channel("analysis"))
            if m["content"]:
                channel = "commentary" if calls else "final"
                convo.append(h.Message.from_role_and_content(h.Role.ASSISTANT, m["content"]).with_channel(channel))
            for call in calls:
                name = call["function"]["name"]
                names[call["id"]] = name
                convo.append(
                    h.Message.from_role_and_content(h.Role.ASSISTANT, json.dumps(call["function"]["arguments"], ensure_ascii=False))
                    .with_channel("commentary")
                    .with_recipient(f"functions.{name}")
                    .with_content_type("<|constrain|>json")
                )
        else:
            name = m.get("name") or names.get(m.get("tool_call_id", ""))
            if not name:
                raise BadRequest(f"messages[{i}]: a tool result needs tool_call_id of an earlier call, or name")
            convo.append(
                h.Message.from_author_and_content(h.Author.new(h.Role.TOOL, f"functions.{name}"), m["content"])
                .with_channel("commentary")
                .with_recipient("assistant")
            )
    return h.Conversation.from_messages(convo)


def _harmony_plan(messages: list[dict], tools, choice) -> dict:
    h, enc = _harmony()
    convo = harmony_conversation(messages, tools)
    prompt = list(enc.render_conversation_for_completion(convo, h.Role.ASSISTANT))
    history = list(enc.render_conversation(convo))
    checkpoint = len(history) if prompt[: len(history)] == history else 0
    special = functools.partial(enc.encode, allowed_special="all")
    plan = {
        "prompt": prompt,
        "checkpoint": min(checkpoint, len(prompt) - 1),
        "opener_text": "",
        "opener_tokens": [],
        # <|call|> and <|return|>. The tokenizer's EOS list carries them too;
        # naming them here keeps the stop independent of generation_config.
        "stops": set(enc.stop_tokens_for_assistant_actions()),
        "end": None,
        "then": None,
        "then_text": "",
    }
    if tools and choice != "auto":
        # gpt-oss reasons before it acts, so the call is forced after its
        # analysis rather than in place of it.
        opener = "<|channel|>analysis<|message|>"
        header = "<|start|>assistant<|channel|>commentary to=functions."
        if choice != "required":
            header += f"{choice} <|constrain|>json<|message|>"
        plan["opener_text"] = opener
        plan["opener_tokens"] = special(opener)
        plan["prompt"] = prompt + plan["opener_tokens"]
        plan["end"] = special("<|end|>")[0]
        plan["then"] = special(header)
        plan["then_text"] = header
    return plan


# -- chat turns: parsing the reply ---------------------------------------------


@functools.lru_cache(maxsize=None)
def _tool_parser(tool_format: str):
    from importlib import import_module

    return import_module(f"mlx_lm.tool_parsers.{tool_format}").parse_tool_call


def _unparsed(text: str, finish: str, error: str) -> dict:
    print(f"tool call not parsed ({finish}): {error}", flush=True)
    return {"content": text, "reasoning": None, "calls": [], "finish": finish, "error": error}


def parse_reply(tool_format: str | None, text: str, tokens: list[int], tools, finish: str) -> dict:
    """Split a completion into content, reasoning and tool calls.

    Returns {content, reasoning, calls: [{name, arguments}], finish, error}.
    finish is the generation's reason, "tool_calls" when calls were parsed,
    or "invalid_tool_call" when the reply started a call that does not parse;
    the raw completion is then the content, never a repaired guess.
    """
    if tool_format == "harmony":
        return _parse_harmony(tokens, tools, finish)
    if not tools or tool_format not in TOOL_MARKERS:
        return {"content": visible(text), "reasoning": None, "calls": [], "finish": finish, "error": None}
    start, end = TOOL_MARKERS[tool_format]
    if start not in text:
        # A plain-text reply. It never reaches the parser, so the gemma4
        # parser's raise on "no call here" (mlx-lm #1125) cannot fire.
        return {"content": visible(text), "reasoning": None, "calls": [], "finish": finish, "error": None}
    parser = _tool_parser(tool_format)
    prose, calls, pos = [], [], 0
    while True:
        i = text.find(start, pos)
        if i < 0:
            prose.append(text[pos:])
            break
        prose.append(text[pos:i])
        j = text.find(end, i + len(start))
        if j < 0:
            return _unparsed(text, "length" if finish == "length" else "invalid_tool_call", "tool call is not closed")
        inner = text[i + len(start) : j].strip()
        try:
            parsed = parser(inner, tools)
        except (ValueError, SyntaxError, TypeError, IndexError, KeyError) as exc:
            return _unparsed(text, "invalid_tool_call", f"{type(exc).__name__}: {exc}")
        parsed = parsed if isinstance(parsed, list) else [parsed]
        if not parsed:
            # gemma4 with the #1142 fix returns [] where 0.31.3 raises.
            return _unparsed(text, "invalid_tool_call", "no function in the tool call")
        for call in parsed:
            if not isinstance(call.get("name"), str) or not isinstance(call.get("arguments"), dict):
                return _unparsed(text, "invalid_tool_call", "tool call arguments are not an object")
            calls.append({"name": call["name"], "arguments": call["arguments"]})
        pos = j + len(end)
    content = visible("".join(prose)).strip() or None
    return {"content": content, "reasoning": None, "calls": calls, "finish": "tool_calls", "error": None}


def _parse_harmony(tokens: list[int], tools, finish: str) -> dict:
    h, enc = _harmony()
    text = enc.decode(tokens)
    try:
        messages = enc.parse_messages_from_completion_tokens(tokens, h.Role.ASSISTANT, strict=False)
    except Exception as exc:  # noqa: BLE001 — HarmonyError and friends; the reply is returned raw
        if tools:
            return _unparsed(text, "invalid_tool_call", f"{type(exc).__name__}: {exc}")
        return {"content": visible(text), "reasoning": None, "calls": [], "finish": finish, "error": None}
    analysis, final, preamble, calls = [], None, [], []
    for message in messages:
        body = "".join(c.text for c in message.content if hasattr(c, "text"))
        recipient = message.recipient or ""
        if recipient and recipient != "assistant":
            if not tools or not recipient.startswith("functions."):
                return _unparsed(text, "invalid_tool_call", f"call to {recipient!r}, which was not offered")
            if finish == "length" and message is messages[-1]:
                return _unparsed(text, "length", "tool call cut off at max_tokens")
            try:
                arguments = json.loads(body) if body.strip() else {}
            except json.JSONDecodeError as exc:
                return _unparsed(text, "invalid_tool_call", f"arguments are not JSON: {exc}")
            if not isinstance(arguments, dict):
                return _unparsed(text, "invalid_tool_call", "tool call arguments are not an object")
            calls.append({"name": recipient[len("functions.") :], "arguments": arguments})
        elif message.channel == "analysis":
            analysis.append(body)
        elif message.channel == "commentary":
            preamble.append(body)
        else:
            final = body
    content = final if final is not None else ("\n".join(preamble) or None)
    if calls:
        return {"content": content, "reasoning": "\n".join(analysis) or None, "calls": calls, "finish": "tool_calls", "error": None}
    return {"content": content or "", "reasoning": "\n".join(analysis) or None, "calls": [], "finish": finish, "error": None}


def openai_message(parsed: dict) -> dict:
    message: dict = {"role": "assistant", "content": parsed["content"]}
    if parsed["calls"]:
        message["tool_calls"] = [
            {
                "id": f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {"name": c["name"], "arguments": json.dumps(c["arguments"], ensure_ascii=False)},
            }
            for c in parsed["calls"]
        ]
    elif message["content"] is None:
        message["content"] = ""
    if parsed["reasoning"]:
        message["reasoning_content"] = parsed["reasoning"]
    return message


def ollama_message(parsed: dict) -> dict:
    message: dict = {"role": "assistant", "content": parsed["content"] or ""}
    if parsed["reasoning"]:
        message["thinking"] = parsed["reasoning"]
    if parsed["calls"]:
        message["tool_calls"] = [{"function": {"name": c["name"], "arguments": c["arguments"]}} for c in parsed["calls"]]
    return message


_FINAL_CHANNEL = re.compile(
    r"<\|channel\|>(?:final|commentary)<\|message\|>(.*?)(?:<\|end\|>|<\|return\|>|<\|start\|>|$)",
    re.S,
)


def visible(raw: str) -> str:
    """User-visible answer. Harmony keeps analysis in a separate channel."""
    if not raw:
        return ""
    found = _FINAL_CHANNEL.findall(raw)
    if found:
        return found[-1].strip()
    if "<|" not in raw and "<channel|>" not in raw:
        return raw
    text = raw.split("<channel|>")[-1]
    for marker in ("<|end|>", "<|return|>", "<|start|>"):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text.replace("<|message|>", "").replace("<|channel|>", "").strip()


def _stats(response) -> dict:
    if response is None:
        return {
            "prompt_eval_count": 0,
            "prompt_eval_duration": 0,
            "eval_count": 0,
            "eval_duration": 0,
        }
    prompt_tps = response.prompt_tps or 0
    gen_tps = response.generation_tps or 0
    prompt_n = int(response.prompt_tokens or 0)
    gen_n = int(response.generation_tokens or 0)
    return {
        "prompt_eval_count": prompt_n,
        "prompt_eval_duration": int(1e9 * prompt_n / prompt_tps) if prompt_tps else 0,
        "eval_count": gen_n,
        "eval_duration": int(1e9 * gen_n / gen_tps) if gen_tps else 0,
        "done_reason": response.finish_reason or "stop",
    }


def _options(body: dict) -> tuple[int, float, float]:
    options = body.get("options") or {}
    raw_n = options.get("num_predict", body.get("num_predict", 256))
    try:
        num = int(raw_n)
    except (TypeError, ValueError):
        num = 256
    if num <= 0 or num > 8192:
        num = 256
    try:
        temperature = float(options.get("temperature", body.get("temperature", 0)))
    except (TypeError, ValueError):
        temperature = 0.0
    try:
        top_p = float(options.get("top_p", 1.0))
    except (TypeError, ValueError):
        top_p = 1.0
    return num, temperature, top_p


def _v1_options(body: dict) -> tuple[int, float, float]:
    raw_n = body.get("max_completion_tokens", body.get("max_tokens"))
    if raw_n is None:
        num = V1_DEFAULT_MAX_TOKENS
    elif isinstance(raw_n, bool) or not isinstance(raw_n, int) or not 0 < raw_n <= V1_MAX_TOKENS:
        raise BadRequest(f"max_tokens must be an integer from 1 to {V1_MAX_TOKENS}")
    else:
        num = raw_n
    try:
        temperature = float(body.get("temperature") if body.get("temperature") is not None else 0)
        top_p = float(body.get("top_p") if body.get("top_p") is not None else 1.0)
    except (TypeError, ValueError):
        raise BadRequest("temperature and top_p must be numbers") from None
    return num, temperature, top_p


class Handler(BaseHTTPRequestHandler):
    engine: Engine
    catalog: Catalog
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} {fmt % args}", flush=True)

    def _reject(self) -> bool:
        peer = self.client_address[0]
        if peer in ALLOWED_PEERS:
            return False
        body = json.dumps({"error": "refused"}).encode()
        self.send_response(403)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        return True

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length", "0") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length))

    def _tag(self, name: str, loaded: bool) -> dict:
        spec = self.catalog.models[name]
        return {
            "name": name,
            "model": name,
            "modified_at": spec["modified_at"],
            "size": spec["size"],
            "digest": "",
            "details": _details(spec),
            "loaded": loaded,
        }

    def do_GET(self) -> None:  # noqa: N802
        if self._reject():
            return
        path = self.path.split("?", 1)[0]
        resident = self.engine.resident()
        if path == "/api/tags":
            self._json(200, {"models": [self._tag(n, n in resident) for n in self.catalog.models]})
            return
        if path == "/api/ps":
            self._json(200, {"models": [self._tag(n, True) for n in resident]})
            return
        if path == "/api/version":
            self._json(200, {"version": self.catalog.revision})
            return
        if path in ("/", "/health"):
            with self.engine._state:
                errors = dict(self.engine.load_errors)
                heavy = self.engine.heavy
            self._json(
                200,
                {
                    "status": "ok",
                    "default": self.catalog.default,
                    "tiers": {n: s["tier"] for n, s in self.catalog.models.items()},
                    "aliases": self.catalog.aliases,
                    "resident": resident,
                    "heavy": heavy,
                    "load_errors": errors,
                    "tools": {n: s["tool_format"] for n, s in self.catalog.models.items() if s["tool_format"]},
                    "limits": self.catalog.limits,
                    "prompt_cache": self.engine.cache_stats(),
                },
            )
            return
        self._json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self._reject():
            return
        path = self.path.split("?", 1)[0]
        try:
            body = self._read_json()
        except json.JSONDecodeError:
            self._json(400, {"error": "invalid json"})
            return
        if path == "/v1/chat/completions":
            self._chat_v1(body)
            return
        if path == "/api/show":
            name = body.get("name") or body.get("model") or ""
            resolved = self.catalog.resolve(name)
            if resolved is None:
                self._json(404, {"error": f"model '{name}' not found"})
                return
            spec = self.catalog.models[resolved]
            self._json(
                200,
                {
                    "modelfile": "",
                    "details": _details(spec),
                    "model_info": {"path": str(spec["path"]), "tier": spec["tier"], "serves": resolved},
                },
            )
            return
        if path in ("/api/embed", "/api/embeddings", "/v1/embeddings"):
            self._embed(path, body)
            return
        if path not in ("/api/chat", "/api/generate"):
            self._json(404, {"error": "not found"})
            return
        requested = (body.get("model") or self.catalog.default).strip()
        name = self.catalog.resolve(requested)
        if name is None:
            self._json(404, {"error": f"model '{requested}' not found"})
            return
        if self.catalog.tier(name) == "embed":
            self._json(400, {"error": f"'{requested}' is an embedding model"})
            return
        num, temperature, top_p = _options(body)
        messages = body.get("messages") if path == "/api/chat" else None
        if path == "/api/chat" and isinstance(messages, list) and body.get("format") == "json":
            if not any(m.get("role") == "system" for m in messages if isinstance(m, dict)):
                messages = [{"role": "system", "content": "Respond with one JSON object and no other text."}, *messages]
        prompt = body.get("prompt") or ""
        raw = bool(body.get("raw")) and path == "/api/generate" and not messages
        stream = bool(body.get("stream", path == "/api/chat"))
        if path == "/api/chat" and body.get("tools"):
            self._chat_ollama_tools(requested, name, body, messages, stream, num, temperature, top_p)
            return
        job = GenJob(name, messages, prompt, raw, num, temperature, top_p)
        self.engine.submit(job)
        try:
            self._respond(path, requested, job, stream)
        except (BrokenPipeError, ConnectionResetError):
            # The client left. Stop generating for it and drop the connection.
            job.cancelled.set()
            self.close_connection = True

    # -- embeddings -----------------------------------------------------------

    def _embed(self, path: str, body: dict) -> None:
        openai = path == "/v1/embeddings"
        requested = (body.get("model") or "").strip()
        name = self.catalog.resolve(requested) if requested else None
        if name is None:
            self._json(404, {"error": f"model '{requested}' not found"})
            return
        if self.catalog.tier(name) != "embed":
            self._json(400, {"error": f"'{requested}' is not an embedding model"})
            return
        if path == "/api/embeddings":
            raw_input = body.get("prompt")
            if raw_input is None:
                raw_input = body.get("input")
        else:
            raw_input = body.get("input")
        texts = [raw_input] if isinstance(raw_input, str) else raw_input
        if not isinstance(texts, list) or not texts or not all(isinstance(t, str) for t in texts):
            self._json(400, {"error": "input must be a string or a non-empty list of strings"})
            return
        if path == "/api/embeddings" and len(texts) != 1:
            self._json(400, {"error": "/api/embeddings takes one prompt; use /api/embed for a batch"})
            return
        job = EmbedJob(name, texts, truncate=body.get("truncate", True) is not False)
        started = time.perf_counter()
        self.engine.submit(job)
        result = job.out.get()
        if isinstance(result, ValueError):
            self._json(400, {"error": str(result)})
            return
        if isinstance(result, Exception):
            self._json(500, {"error": f"{type(result).__name__}: {result}"})
            return
        vectors, n_tokens = result
        elapsed = int((time.perf_counter() - started) * 1e9)
        if openai:
            b64 = body.get("encoding_format") == "base64"
            data = [
                {
                    "object": "embedding",
                    "index": i,
                    "embedding": base64.b64encode(struct.pack(f"<{len(v)}f", *v)).decode() if b64 else v,
                }
                for i, v in enumerate(vectors)
            ]
            self._json(
                200,
                {
                    "object": "list",
                    "data": data,
                    "model": requested,
                    "usage": {"prompt_tokens": n_tokens, "total_tokens": n_tokens},
                },
            )
        elif path == "/api/embeddings":
            self._json(200, {"embedding": vectors[0]})
        else:
            self._json(
                200,
                {
                    "model": requested,
                    "embeddings": vectors,
                    "total_duration": elapsed,
                    "load_duration": 0,
                    "prompt_eval_count": n_tokens,
                },
            )

    # -- chat -----------------------------------------------------------------

    def _respond(self, path: str, name: str, job: GenJob, stream: bool) -> None:
        out = job.out
        started = time.perf_counter()
        if stream:
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            held_control = False
            while True:
                item = out.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    self._chunk({"error": f"{type(item).__name__}: {item}", "done": True})
                    break
                text, final, stats = item
                if not final and (held_control or "<|" in text):
                    held_control = True
                    continue
                if final and held_control:
                    text = visible((stats or {}).get("text") or "")
                if path == "/api/chat":
                    payload = {
                        "model": name,
                        "created_at": _now(),
                        "message": {"role": "assistant", "content": text},
                        "done": final,
                    }
                else:
                    payload = {"model": name, "created_at": _now(), "response": text, "done": final}
                if final and stats:
                    payload.update({k: v for k, v in stats.items() if k != "text"})
                    payload["total_duration"] = int((time.perf_counter() - started) * 1e9)
                self._chunk(payload)
            self.wfile.write(b"0\r\n\r\n")
            return

        text_parts: list[str] = []
        stats = None
        error = None
        while True:
            item = out.get()
            if item is None:
                break
            if isinstance(item, Exception):
                error = item
                continue
            text, final, piece_stats = item
            if text:
                text_parts.append(text)
            if final:
                stats = piece_stats
        if error is not None:
            self._json(500, {"error": f"{type(error).__name__}: {error}"})
            return
        content = visible((stats or {}).get("text") or "".join(text_parts))
        timing = {k: v for k, v in (stats or {}).items() if k != "text"}
        timing["total_duration"] = int((time.perf_counter() - started) * 1e9)
        if path == "/api/chat":
            reply = {"message": {"role": "assistant", "content": content}}
        else:
            reply = {"response": content}
        self._json(200, {"model": name, "created_at": _now(), **reply, "done": True, **timing})

    def _chunk(self, payload: dict) -> None:
        data = json.dumps(payload).encode() + b"\n"
        self._raw_chunk(data)

    def _raw_chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):X}\r\n".encode())
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()

    # -- chat with tools and /v1 ------------------------------------------------

    def _v1_error(self, code: int, message: str, kind: str = "invalid_request_error") -> None:
        self._json(code, {"error": {"message": message, "type": kind, "param": None, "code": None}})

    def _finish_chat(self, job: ChatJob) -> tuple[dict | None, Exception | None]:
        """Wait for a non-streamed chat turn. Returns (stats, error)."""
        stats = None
        error = None
        while True:
            item = job.out.get()
            if item is None:
                return stats, error
            if isinstance(item, Exception):
                error = item
            elif item[1]:
                stats = item[2]

    def _chat_v1(self, body: dict) -> None:
        requested = (body.get("model") or self.catalog.default).strip()
        name = self.catalog.resolve(requested)
        if name is None:
            self._v1_error(404, f"model '{requested}' not found", "not_found_error")
            return
        if self.catalog.tier(name) == "embed":
            self._v1_error(400, f"'{requested}' is an embedding model")
            return
        tool_format = self.catalog.models[name]["tool_format"]
        stream = body.get("stream") is True
        try:
            messages = chat_messages(body.get("messages"))
            tools, choice = chat_tools(body)
            if tools and tool_format is None:
                raise BadRequest(f"'{requested}' has no tool format; it cannot take tools")
            if tools and stream:
                # A call is only usable once its arguments are complete, and a
                # second, incremental parser per format could drift from this
                # one. Agent loops read whole turns; the latency lever is the
                # prefix cache, not streaming.
                raise BadRequest("stream is not supported with tools; send stream: false")
            num, temperature, top_p = _v1_options(body)
        except BadRequest as exc:
            self._v1_error(400, str(exc))
            return
        job = ChatJob(name, messages, tools, choice, num, temperature, top_p)
        rid = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        self.engine.submit(job)
        try:
            if stream:
                self._stream_v1(requested, job, tool_format, rid, created, bool((body.get("stream_options") or {}).get("include_usage")))
                return
            stats, error = self._finish_chat(job)
            if isinstance(error, BadRequest):
                self._v1_error(400, str(error))
                return
            if error is not None or stats is None:
                self._v1_error(500, f"{type(error).__name__}: {error}", "server_error")
                return
            parsed = parse_reply(tool_format, stats["text"], stats["tokens"], tools, stats["finish"])
            self._json(
                200,
                {
                    "id": rid,
                    "object": "chat.completion",
                    "created": created,
                    "model": requested,
                    "choices": [
                        {"index": 0, "message": openai_message(parsed), "logprobs": None, "finish_reason": parsed["finish"]}
                    ],
                    "usage": _v1_usage(stats),
                },
            )
        except (BrokenPipeError, ConnectionResetError):
            job.cancelled.set()
            self.close_connection = True

    def _stream_v1(self, requested: str, job: ChatJob, tool_format, rid: str, created: int, include_usage: bool) -> None:
        # The prompt is rendered on the worker thread. Wait for its first
        # item, so a request the template rejects still gets a 400 rather than
        # an error inside a 200 stream.
        first = job.out.get()
        if isinstance(first, BadRequest):
            self._v1_error(400, str(first))
            return
        if isinstance(first, Exception) or first is None:
            self._v1_error(500, f"{type(first).__name__}: {first}", "server_error")
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        def send(delta: dict, finish=None, usage=None) -> None:
            chunk = {
                "id": rid,
                "object": "chat.completion.chunk",
                "created": created,
                "model": requested,
                "choices": [{"index": 0, "delta": delta, "logprobs": None, "finish_reason": finish}] if usage is None else [],
            }
            if usage is not None:
                chunk["usage"] = usage
            self._raw_chunk(b"data: " + json.dumps(chunk).encode() + b"\n\n")

        send({"role": "assistant", "content": ""})
        parser = None
        if tool_format == "harmony":
            h, enc = _harmony()
            parser = h.StreamableParser(enc, h.Role.ASSISTANT)
        sent = ""
        held = False
        pending = [first]
        while True:
            item = pending.pop() if pending else job.out.get()
            if item is None:
                break
            if isinstance(item, Exception):
                kind = "invalid_request_error" if isinstance(item, BadRequest) else "server_error"
                self._raw_chunk(b"data: " + json.dumps({"error": {"message": str(item), "type": kind}}).encode() + b"\n\n")
                break
            text, final, info = item
            if final:
                if parser is None and held:
                    # Control text appeared; send what the non-streamed answer
                    # would add, if it still extends what was sent.
                    rest = visible(info["text"])
                    if rest.startswith(sent) and rest != sent:
                        send({"content": rest[len(sent) :]})
                send({}, info["finish"])
                if include_usage:
                    send({}, usage=_v1_usage(info))
                continue
            if parser is not None:
                try:
                    parser.process(info)
                except Exception:  # noqa: BLE001 — malformed harmony; stop streaming deltas
                    parser = None
                    held = True
                    continue
                if parser.current_channel == "final" and parser.last_content_delta:
                    send({"content": parser.last_content_delta})
                continue
            if held or "<|" in text or "<channel|>" in text:
                held = True
                continue
            if text:
                sent += text
                send({"content": text})
        self._raw_chunk(b"data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")

    def _chat_ollama_tools(self, requested, name, body, messages, stream, num, temperature, top_p) -> None:
        tool_format = self.catalog.models[name]["tool_format"]
        try:
            if tool_format is None:
                raise BadRequest(f"'{requested}' has no tool format; it cannot take tools")
            if stream:
                raise BadRequest("stream is not supported with tools; send \"stream\": false")
            checked = chat_messages(messages)
            tools, choice = chat_tools({"tools": body.get("tools")})
        except BadRequest as exc:
            self._json(400, {"error": str(exc)})
            return
        job = ChatJob(name, checked, tools, choice, num, temperature, top_p)
        started = time.perf_counter()
        self.engine.submit(job)
        try:
            stats, error = self._finish_chat(job)
            if isinstance(error, BadRequest):
                self._json(400, {"error": str(error)})
                return
            if error is not None or stats is None:
                self._json(500, {"error": f"{type(error).__name__}: {error}"})
                return
            parsed = parse_reply(tool_format, stats["text"], stats["tokens"], tools, stats["finish"])
            self._json(
                200,
                {
                    "model": requested,
                    "created_at": _now(),
                    "message": ollama_message(parsed),
                    "done": True,
                    "done_reason": parsed["finish"],
                    "prompt_eval_count": stats["prompt_tokens"],
                    "prompt_eval_duration": stats["prompt_eval_duration"],
                    "eval_count": stats["completion_tokens"],
                    "eval_duration": stats["eval_duration"],
                    "total_duration": int((time.perf_counter() - started) * 1e9),
                },
            )
        except (BrokenPipeError, ConnectionResetError):
            job.cancelled.set()
            self.close_connection = True


def _v1_usage(stats: dict) -> dict:
    return {
        "prompt_tokens": stats["prompt_tokens"],
        "completion_tokens": stats["completion_tokens"],
        "total_tokens": stats["prompt_tokens"] + stats["completion_tokens"],
        "prompt_tokens_details": {"cached_tokens": stats["cached_tokens"]},
    }


def _details(spec: dict) -> dict:
    return {
        "family": spec["family"],
        "parameter_size": spec["parameter_size"],
        "quantization_level": spec["quantization"],
    }


class Server(ThreadingHTTPServer):
    # socketserver's default listen backlog is 5. On macOS a connection that
    # arrives at a full queue resets queued ones, so a burst of ten new
    # connections (one client pool after its keep-alive expires) lost about
    # half of them as ConnectError. 128 is kern.ipc.somaxconn's default.
    request_queue_size = 128


def main() -> None:
    catalog = Catalog(CONFIG_PATH)
    Handler.engine = Engine(catalog)
    Handler.catalog = catalog
    servers = []
    for host in dict.fromkeys(BIND_HOSTS):
        try:
            httpd = Server((host, PORT), Handler)
        except OSError as exc:
            print(f"skip bind {host}:{PORT}: {exc}", flush=True)
            continue
        servers.append(httpd)
        threading.Thread(target=httpd.serve_forever, name=f"http-{host}", daemon=True).start()
        print(f"listening {host}:{PORT}", flush=True)
    if not servers:
        raise SystemExit("no bind succeeded")
    threading.Event().wait()


if __name__ == "__main__":
    main()
