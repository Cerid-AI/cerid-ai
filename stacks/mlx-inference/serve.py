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
           heavy model swaps only that slot. A load that the host's memory
           cannot hold is refused with a 503, and an idle heavy model unloads
           after CERID_MLX_HEAVY_IDLE_S or at once under memory pressure.
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
import sys
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
MAX_STOPS = 16

# The heavy slot gives way to the rest of the host. On 2026-10-17 repeated
# gpt-oss-120b loads (about 65 GB) beside test suites and two VMs filled swap,
# and the server was killed and relaunched twice.
#   HEAVY_IDLE_S     seconds a loaded heavy model may sit unused; 0 keeps it
#   HEAVY_MARGIN_GB  memory a heavy load must leave free beyond its weights
#                    (KV caches, activations, everything else on the host)
HEAVY_IDLE_S = float(os.environ.get("CERID_MLX_HEAVY_IDLE_S", "600"))
HEAVY_MARGIN_GB = float(os.environ.get("CERID_MLX_HEAVY_MARGIN_GB", "8"))
PRESSURE_POLL_S = 10.0
GB = 1e9
# kern.memorystatus_vm_pressure_level: what memory_pressure(1) and Activity
# Monitor's pressure graph read.
PRESSURE_LEVELS = {1: "normal", 2: "warn", 4: "critical"}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


_LOG_LOCK = threading.Lock()


def _emit(line: str) -> None:
    """Write one log line whole. print() writes the text and the newline
    separately, so handler threads logging at once ran their lines together."""
    with _LOG_LOCK:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


# Reasoning effort a caller may ask for, as the three levels gpt-oss knows.
# OpenAI sends `reasoning_effort`, OpenRouter `reasoning: {effort}`, Ollama
# `think`. gpt-oss cannot turn reasoning off, so none/minimal mean low.
_EFFORTS = {"none": "low", "minimal": "low", "low": "low", "medium": "medium",
            "high": "high", "xhigh": "high", "max": "high"}


def effort_of(body: dict) -> str:
    raw = body.get("reasoning_effort")
    if raw is None and isinstance(body.get("reasoning"), dict):
        raw = body["reasoning"].get("effort")
    if raw is None and isinstance(body.get("think"), str):
        raw = body["think"]
    if raw is None:
        return "medium"
    effort = _EFFORTS.get(str(raw).lower())
    if effort is None:
        raise BadRequest(f"reasoning effort {raw!r} is not one of {sorted(_EFFORTS)}")
    return effort


def json_instruction(response_format) -> str | None:
    """The instruction for a response_format, or None when none is asked for.

    No grammar-constrained decoding here, so JSON mode is an instruction the
    model follows, as /api/chat's format=json already was."""
    if not isinstance(response_format, dict):
        return None
    kind = response_format.get("type")
    if kind == "json_object":
        return "Respond with one JSON object and no other text."
    if kind == "json_schema":
        schema = (response_format.get("json_schema") or {}).get("schema")
        text = "Respond with one JSON object and no other text."
        return text + (f" It must conform to this JSON Schema: {json.dumps(schema)}" if schema else "")
    if kind in (None, "text"):
        return None
    raise BadRequest(f"response_format type {kind!r} is not supported")


def with_instruction(messages: list[dict], instruction: str | None) -> list[dict]:
    if not instruction:
        return messages
    for i, m in enumerate(messages):
        if m.get("role") == "system":
            merged = dict(m, content=f"{m.get('content') or ''}\n\n{instruction}".strip())
            return [*messages[:i], merged, *messages[i + 1:]]
    return [{"role": "system", "content": instruction}, *messages]


def stop_strings(value) -> tuple[str, ...]:
    """Ollama's options.stop or OpenAI's stop: absent, a string, or a list of strings."""
    if value is None:
        return ()
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or len(value) > MAX_STOPS or not all(isinstance(s, str) and s for s in value):
        raise BadRequest(f"stop must be a non-empty string or a list of at most {MAX_STOPS} non-empty strings")
    return tuple(dict.fromkeys(value))


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


def _sampling_defaults(model_dir: Path) -> dict:
    """The truncation a model's generation_config.json samples it with.

    Applied to a sampled request that sets none of its own; the temperature
    stays the request's, greedy when absent. Gemma 4 ships top_k 64 and top_p
    0.95. Sampled at temperature 1 from all 262k tokens instead, about 1
    review reply in 100 took a token from outside that nucleus after its
    answer: a "<" that went on to spell <|file_separator|>, or a stray
    <channel|> (p 0.02) that visible() read as the end of a thought block,
    leaving the reply empty.
    """
    path = model_dir / "generation_config.json"
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as exc:
        raise SystemExit(f"unreadable {path}: {exc}") from None
    out: dict = {}
    top_k = raw.get("top_k")
    if top_k is not None:
        if isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 0:
            raise SystemExit(f"{path}: top_k must be a non-negative integer, got {top_k!r}")
        if top_k > 0:
            out["top_k"] = top_k
    top_p = raw.get("top_p")
    if top_p is not None:
        if isinstance(top_p, bool) or not isinstance(top_p, (int, float)) or not 0 < top_p <= 1:
            raise SystemExit(f"{path}: top_p must be in (0, 1], got {top_p!r}")
        if top_p < 1:
            out["top_p"] = float(top_p)
    return out


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
        add(f"sampling:{name}", json.dumps(models[name].get("sampling", {}), sort_keys=True).encode())
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


# -- host memory ----------------------------------------------------------------

@functools.cache
def _mach():
    """libc, this host's port, and vm_statistics64 from <mach/vm_statistics.h>."""
    import ctypes
    import ctypes.util

    u32, u64 = ctypes.c_uint32, ctypes.c_uint64
    counters = ("zero_fill_count", "reactivations", "pageins", "pageouts", "faults",
                "cow_faults", "lookups", "hits", "purges")
    fields = [("free_count", u32), ("active_count", u32), ("inactive_count", u32), ("wire_count", u32),
              *[(n, u64) for n in counters],
              ("purgeable_count", u32), ("speculative_count", u32),
              *[(n, u64) for n in ("decompressions", "compressions", "swapins", "swapouts")],
              ("compressor_page_count", u32), ("throttled_count", u32),
              ("external_page_count", u32), ("internal_page_count", u32),
              ("total_uncompressed_pages_in_compressor", u64)]
    vm_statistics64 = type("vm_statistics64", (ctypes.Structure,), {"_fields_": fields})
    libc = ctypes.CDLL(ctypes.util.find_library("c"))
    libc.mach_host_self.restype = u32
    # One send right for the life of the process: each call adds a reference.
    return libc, libc.mach_host_self(), vm_statistics64


def _sysctl_int(name: str) -> int | None:
    import ctypes

    libc = _mach()[0]
    value, size = ctypes.c_uint64(0), ctypes.c_size_t(8)
    if libc.sysctlbyname(name.encode(), ctypes.byref(value), ctypes.byref(size), None, ctypes.c_size_t(0)) != 0:
        return None
    return value.value


def memory_probe() -> dict:
    """The host's memory as the heavy slot judges it.

    available: bytes the kernel can hand out without swapping, free + inactive
      (host_statistics64's free count already includes speculative pages; this
      is vm_stat's free + speculative + inactive).
    pressure: "normal", "warn" or "critical".

    Either is None where it cannot be read (not macOS). In-process calls, no
    vm_stat fork: /api/ps and the idle check run it often.
    """
    available = pressure = None
    if sys.platform != "darwin":
        return {"available": available, "pressure": pressure}
    try:
        import ctypes

        libc, host, vm_statistics64 = _mach()
        stats = vm_statistics64()
        count = ctypes.c_uint32(ctypes.sizeof(stats) // 4)
        page = _sysctl_int("hw.pagesize")
        if page and libc.host_statistics64(host, 4, ctypes.byref(stats), ctypes.byref(count)) == 0:  # HOST_VM_INFO64
            available = (stats.free_count + stats.inactive_count) * page
        level = _sysctl_int("kern.memorystatus_vm_pressure_level")
        pressure = PRESSURE_LEVELS.get(level) if level is not None else None
    except (OSError, AttributeError):
        pass
    return {"available": available, "pressure": pressure}


def heavy_shortfall(size: int, probe: dict, margin_gb: float) -> dict | None:
    """Why a heavy model of `size` bytes may not load now, or None when it may.

    It may load when the kernel reports normal pressure and the available
    memory holds its weights plus margin_gb. A reading that could not be taken
    does not refuse: the guard steps aside where it cannot see.
    """
    need = size + margin_gb * GB
    available, pressure = probe.get("available"), probe.get("pressure")
    if pressure in ("warn", "critical") or (available is not None and available < need):
        return {
            "need_gb": round(need / GB, 1),
            "available_gb": None if available is None else round(available / GB, 1),
            "pressure": pressure,
        }
    return None


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
                "sampling": {} if tier == "embed" else _sampling_defaults(weights),
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
        self.effort = "medium"
        self.stops: tuple[str, ...] = ()
        self.submitted = time.perf_counter()
        self.started: float | None = None


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
        self.submitted = time.perf_counter()
        self.started: float | None = None


class BadRequest(ValueError):
    """The request cannot be served as sent. Returned to the caller as a 400."""


class HeavyDeferred(RuntimeError):
    """The heavy model does not fit in memory now. Returned as a 503, so the
    caller falls back to another model instead of the host swapping."""

    def __init__(self, name: str, shortfall: dict) -> None:
        self.payload = {"error": "heavy deferred: memory", "model": name, **shortfall}
        super().__init__(
            f"heavy deferred: memory ({name} needs {shortfall['need_gb']} GB,"
            f" {shortfall['available_gb']} GB available, pressure {shortfall['pressure']})"
        )


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
        self._heavy_used = 0.0  # monotonic time a heavy job last started or finished
        self._pressure_checked = 0.0
        threading.Thread(target=self._loop, name="mlx-gpu", daemon=True).start()

    # -- loading ------------------------------------------------------------

    def _load(self, name: str) -> None:
        spec = self.catalog.models[name]
        _emit(f"{_now()} loading {name}")
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
            _emit(f"{_now()} load failed {name}: {self.load_errors[name]}")
            raise
        with self._state:
            self.load_errors.pop(name, None)
        _emit(f"{_now()} loaded {name} in {time.perf_counter() - started:.1f}s")

    def _ensure(self, name: str) -> None:
        with self._state:
            ready = name in self.loaded or name in self.embedders
        if not ready:
            self._load(name)

    def _unload_heavy(self, why: str = "swap") -> None:
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
        _emit(f"{_now()} unloaded {name} ({why})")

    def _heavy_idle(self) -> bool:
        """A heavy model is loaded and nothing is using it or waiting for it."""
        return self.heavy is not None and not self._pending_heavy and not any(
            self.catalog.tier(j.name) == "heavy" for j in self._active
        )

    def _release_idle_heavy(self) -> None:
        """Unload an idle heavy model once its idle time is up, or at once when
        the kernel reports memory pressure. Pressure is read every
        PRESSURE_POLL_S, not on every step."""
        if not self._heavy_idle():
            return
        now = time.monotonic()
        idle = now - self._heavy_used
        if HEAVY_IDLE_S > 0 and idle >= HEAVY_IDLE_S:
            self._unload_heavy(f"idle {idle:.0f}s")
            return
        if now - self._pressure_checked < PRESSURE_POLL_S:
            return
        self._pressure_checked = now
        pressure = memory_probe()["pressure"]
        if pressure in ("warn", "critical"):
            self._unload_heavy(f"memory pressure {pressure}, idle {idle:.0f}s")

    def _wake_after(self) -> float | None:
        """How long the loop may block on its inbox: forever, unless an idle
        heavy model is due a pressure check or its idle unload."""
        if not self._heavy_idle():
            return None
        due = PRESSURE_POLL_S
        if HEAVY_IDLE_S > 0:
            due = min(due, HEAVY_IDLE_S - (time.monotonic() - self._heavy_used))
        return max(due, 0.01)

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
        # The embedders load first, before any request: every search needs one.
        # The chat models load one at a time between requests, so an embed is
        # not held behind them: after a restart, a search waited 9.4 s while
        # two chat models loaded on this thread.
        resident = [
            name for tier in RESIDENT_TIERS
            for name, spec in self.catalog.models.items() if spec["tier"] == tier
        ]
        for name in [n for n in resident if self.catalog.models[n]["tier"] == "embed"]:
            self._preload(name)
        resident = [n for n in resident if self.catalog.models[n]["tier"] != "embed"]
        while True:
            fresh = []
            # Block only when nothing can run. A job waiting for a slot that
            # the last step freed must start now, not when the next request
            # happens to arrive.
            idle = not (self._active or self._pending_heavy or any(self._waiting.values()))
            block = idle and not resident
            try:
                fresh.append(self._inbox.get(block=block, timeout=self._wake_after() if block else None))
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
            self._release_idle_heavy()
            if resident and not fresh and not self._active:
                self._preload(resident.pop(0))

    def _preload(self, name: str) -> None:
        try:
            self._ensure(name)
        except Exception:  # noqa: BLE001 — retried on the first request
            pass

    def _admit(self, job) -> None:
        if isinstance(job, EmbedJob):
            job.started = time.perf_counter()
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
                # Measured after the old model is gone, so its memory counts.
                short = heavy_shortfall(self.catalog.models[job.name]["size"], memory_probe(), HEAVY_MARGIN_GB)
                if short is not None:
                    self._pending_heavy.pop(0)
                    deferred = HeavyDeferred(job.name, short)
                    _emit(f"{_now()} {deferred}")
                    job.out.put(deferred)
                    job.out.put(None)
                    continue
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
        job.started = time.perf_counter()
        if self.catalog.tier(job.name) == "heavy":
            self._heavy_used = time.monotonic()
        job.gen = self._generate_chat(job) if isinstance(job, ChatJob) else self._generate(job)
        self._active.append(job)

    def _step(self, job: GenJob) -> None:
        done = False
        if job.cancelled.is_set():
            job.gen.close()
            _emit(f"cancelled {job.name}: client disconnected")
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
            if self.catalog.tier(job.name) == "heavy":
                self._heavy_used = time.monotonic()
            job.out.put(None)

    # -- generation -----------------------------------------------------------

    def _render(self, name: str, tokenizer, messages, prompt: str, raw: bool, effort: str = "medium") -> str:
        if raw:
            return prompt
        # mlx-lm turns thinking on unless the caller passes False. Gemma 4 then
        # spends the reply inside <|channel>thought, which Cerid reads as the answer.
        chat = messages or [{"role": "user", "content": prompt}]
        extra = {"enable_thinking": False}
        if self.catalog.models[name]["family"] == "gpt-oss":
            extra["reasoning_effort"] = effort
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

    def _sampler(self, job: GenJob):
        """The request's temperature, truncated as the model's own config asks.

        A request's top_p replaces the model's; top_k is always the model's.
        """
        from mlx_lm.sample_utils import make_sampler

        defaults = self.catalog.models[job.name]["sampling"]
        top_p = defaults.get("top_p", 1.0) if job.top_p is None else job.top_p
        return make_sampler(
            temp=job.temperature,
            top_p=top_p if 0 < top_p < 1 else 0.0,
            top_k=defaults.get("top_k", 0),
        )

    def _generate(self, job: GenJob):
        from mlx_lm import stream_generate

        with self._state:
            model, tokenizer = self.loaded[job.name]
        # Render with the tokenizer of the model that answers. A prompt built
        # for another family feeds Gemma a Harmony prompt and gpt-oss a Gemma one.
        prompt = self._render(job.name, tokenizer, job.messages, job.prompt, job.raw, job.effort)
        sampler = self._sampler(job)
        pieces: list[str] = []
        last = None
        stopped = None
        responses = stream_generate(model, tokenizer, prompt, max_tokens=job.max_tokens, sampler=sampler)
        for response in responses:
            last = response
            if response.text:
                pieces.append(response.text)
                stopped = stop_cut("".join(pieces), job.stops) if job.stops else None
                if stopped is not None:
                    break
                yield response.text, False, None
        responses.close()
        stats = _stats(last) | {"text": "".join(pieces)}
        if stopped is not None:
            stats |= {"done_reason": "stop", "content": stopped}
        yield "", True, stats

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

        with self._state:
            model, tokenizer = self.loaded[job.name]
            store = self.prompt_caches.get(job.name)
        spec = self.catalog.models[job.name]
        started = time.perf_counter()
        plan = chat_plan(spec["tool_format"], tokenizer, job.messages, job.tools, job.choice, job.effort)
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

        sampler = self._sampler(job)
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
        stopped = None
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
                    stopped = stop_cut(plan["opener_text"] + "".join(pieces), job.stops) if job.stops else None
                    if stopped is not None:
                        finish = "stop"
                        break
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
            **({} if stopped is None else {"content": stopped}),
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


def chat_plan(tool_format: str | None, tokenizer, messages: list[dict], tools, choice, effort: str = "medium") -> dict:
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
        return _harmony_plan(messages, tools, choice, effort)
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


def harmony_conversation(messages: list[dict], tools, effort: str = "medium"):
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
        .with_reasoning_effort(getattr(h.ReasoningEffort, effort.upper()))
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


def _harmony_plan(messages: list[dict], tools, choice, effort: str = "medium") -> dict:
    h, enc = _harmony()
    convo = harmony_conversation(messages, tools, effort)
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
    _emit(f"tool call not parsed ({finish}): {error}")
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


def chat_reply(tool_format: str | None, stats: dict, tools) -> dict:
    """parse_reply for a finished chat turn, with the answer a stop string cut short."""
    parsed = parse_reply(tool_format, stats["text"], stats["tokens"], tools, stats["finish"])
    if "content" in stats:
        parsed["content"] = stats["content"]
    return parsed


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
    # Gemma 4 opens a thought with <|channel> and closes it with <channel|>.
    # As the chat template's strip_thinking does, drop only what an opener
    # starts: a closer with no opener is stray, so its marker goes and the
    # answer before it stays.
    text = "".join(part.split("<|channel>", 1)[0] for part in raw.split("<channel|>"))
    for marker in ("<|end|>", "<|return|>", "<|start|>"):
        if marker in text:
            text = text.split(marker, 1)[0]
    return text.replace("<|message|>", "").replace("<|channel|>", "").strip()


def _shown(raw: str) -> str:
    """The answer the client is shown so far, unstripped, for stop matching.

    Harmony's analysis is never shown: the tool-less system prompt names the
    closing mark a stop is set on, so the analysis may quote it before any
    answer is written. Until the final channel opens, nothing is shown.
    """
    if "<|channel|>" in raw or "<|message|>" in raw:
        found = _FINAL_CHANNEL.findall(raw)
        return found[-1] if found else ""
    if "<|" in raw or "<channel|>" in raw:
        return visible(raw)
    return raw


def stop_cut(raw: str, stops: tuple[str, ...]) -> str | None:
    """The shown answer before the first stop string in it, or None if none appears."""
    shown = _shown(raw)
    hits = [i for i in (shown.find(s) for s in stops) if i >= 0]
    return shown[: min(hits)] if hits else None


class HoldBack:
    """Streamed text, less any tail that may yet turn out to begin a stop string."""

    def __init__(self, stops: tuple[str, ...]) -> None:
        self.stops = stops
        self.pending = ""

    def feed(self, text: str) -> str:
        self.pending += text
        keep = 0
        for stop in self.stops:
            for k in range(min(len(stop) - 1, len(self.pending)), keep, -1):
                if self.pending.endswith(stop[:k]):
                    keep = k
                    break
        ready = self.pending[: len(self.pending) - keep]
        self.pending = self.pending[len(ready) :]
        return ready


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


def _options(body: dict) -> tuple[int, float, float | None]:
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
    # None: the model's own top_p (Engine._sampler).
    try:
        top_p = None if options.get("top_p") is None else float(options["top_p"])
    except (TypeError, ValueError):
        top_p = None
    return num, temperature, top_p


def _v1_options(body: dict) -> tuple[int, float, float | None]:
    raw_n = body.get("max_completion_tokens", body.get("max_tokens"))
    if raw_n is None:
        num = V1_DEFAULT_MAX_TOKENS
    elif isinstance(raw_n, bool) or not isinstance(raw_n, int) or not 0 < raw_n <= V1_MAX_TOKENS:
        raise BadRequest(f"max_tokens must be an integer from 1 to {V1_MAX_TOKENS}")
    else:
        num = raw_n
    try:
        temperature = float(body.get("temperature") if body.get("temperature") is not None else 0)
        top_p = None if body.get("top_p") is None else float(body["top_p"])
    except (TypeError, ValueError):
        raise BadRequest("temperature and top_p must be numbers") from None
    return num, temperature, top_p


class Handler(BaseHTTPRequestHandler):
    engine: Engine
    catalog: Catalog
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        _emit(f"{_now()} {self.address_string()} {fmt % args}")

    # One line per response, written as its status goes out (for a stream:
    # time to first byte): when, who, what, which model, how long so far, how
    # long the job waited for the GPU loop, and how long the request waited
    # between accept and its handler thread. install.py reads the same line
    # for refused peers (REFUSED), so the peer and request line keep their place.
    def log_request(self, code="-", size="-") -> None:
        self._access_log(code)

    def setup(self) -> None:
        super().setup()
        accepted = self.server.accepted.pop(id(self.request), None)
        self._accept_ms = (time.perf_counter() - accepted) * 1000 if accepted else None

    def parse_request(self) -> bool:
        # The request line has just been read: time from here, not from the
        # wait for it on an idle keep-alive connection.
        self._t0, self._job = time.perf_counter(), None
        return super().parse_request()

    def _submit(self, job) -> None:
        self._job = job
        self.engine.submit(job)

    def _access_log(self, code) -> None:
        job = getattr(self, "_job", None)
        t0 = getattr(self, "_t0", None)
        accept = getattr(self, "_accept_ms", None)
        wait = ((job.started or time.perf_counter()) - job.submitted) * 1000 if job else None
        fields = [
            _now(), self.client_address[0], f'"{getattr(self, "requestline", "")}"', str(code),
            f"model={job.name if job else '-'}",
            f"ms={(time.perf_counter() - t0) * 1000:.0f}" if t0 else "ms=-",
            f"wait_ms={wait:.0f}" if wait is not None else "wait_ms=-",
            f"accept_ms={accept:.0f}" if accept is not None else "accept_ms=-",
        ]
        if job is not None and hasattr(job, "effort"):
            fields.append(f"effort={job.effort}")
        _emit(" ".join(fields))
        self._accept_ms = None

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

    def _json(self, code: int, payload: dict, headers: dict | None = None) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(body)

    def _deferred(self, exc: HeavyDeferred, openai: bool = False) -> None:
        """503 for a heavy load the host's memory cannot hold. The body says
        so in its own words, so a caller can tell it from a failure."""
        payload = dict(exc.payload)
        if openai:
            payload["error"] = {"message": payload["error"], "type": "server_error", "param": None, "code": "heavy_deferred"}
        self._json(503, payload, {"Retry-After": "60"})

    def _memory(self) -> dict:
        probe = memory_probe()
        with self.engine._state:
            heavy = self.engine.heavy
        available = probe["available"]
        return {
            "pressure": probe["pressure"],
            "available_gb": None if available is None else round(available / GB, 1),
            "heavy_loaded": heavy is not None,
            "heavy": heavy,
            "heavy_idle_unload_s": HEAVY_IDLE_S,
            "heavy_margin_gb": HEAVY_MARGIN_GB,
        }

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
            self._json(200, {"models": [self._tag(n, True) for n in resident], "memory": self._memory()})
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
                    "memory": self._memory(),
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
        try:
            stops = stop_strings((body.get("options") or {}).get("stop"))
        except BadRequest as exc:
            self._json(400, {"error": str(exc)})
            return
        if path == "/api/chat" and body.get("tools"):
            self._chat_ollama_tools(requested, name, body, messages, stream, num, temperature, top_p, stops)
            return
        job = GenJob(name, messages, prompt, raw, num, temperature, top_p)
        job.stops = stops
        try:
            job.effort = effort_of(body)
        except BadRequest as exc:
            self._json(400, {"error": str(exc)})
            return
        self._submit(job)
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
        self._submit(job)
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
            # The status waits for the first item, as Ollama's does for the
            # model to load, so a deferred heavy load is a 503, not a 200.
            first = out.get()
            if isinstance(first, HeavyDeferred):
                self._deferred(first)
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/x-ndjson")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Transfer-Encoding", "chunked")
            self.end_headers()
            held_control = False
            hold = HoldBack(job.stops)
            sent = ""
            pending = [first]
            while True:
                item = pending.pop() if pending else out.get()
                if item is None:
                    break
                if isinstance(item, Exception):
                    self._chunk({"error": f"{type(item).__name__}: {item}", "done": True})
                    break
                text, final, stats = item
                if not final and (held_control or "<|" in text or "<channel|>" in text):
                    held_control = True
                    continue
                if final and (held_control or hold.pending or "content" in (stats or {})):
                    # Send what the answer adds to the text already sent.
                    rest = (stats or {}).get("content", visible((stats or {}).get("text") or ""))
                    text = rest[len(sent) :] if rest.startswith(sent) else ""
                elif not final:
                    text = hold.feed(text)
                    if not text:
                        continue
                sent += text
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
                    payload.update({k: v for k, v in stats.items() if k not in ("text", "content")})
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
        if isinstance(error, HeavyDeferred):
            self._deferred(error)
            return
        if error is not None:
            self._json(500, {"error": f"{type(error).__name__}: {error}"})
            return
        content = (stats or {}).get("content", visible((stats or {}).get("text") or "".join(text_parts)))
        timing = {k: v for k, v in (stats or {}).items() if k not in ("text", "content")}
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
            stops = stop_strings(body.get("stop"))
            if tools and stops:
                raise BadRequest("stop is not supported with tools")
            num, temperature, top_p = _v1_options(body)
            effort = effort_of(body)
            messages = with_instruction(messages, json_instruction(body.get("response_format")))
        except BadRequest as exc:
            self._v1_error(400, str(exc))
            return
        job = ChatJob(name, messages, tools, choice, num, temperature, top_p)
        job.effort = effort
        job.stops = stops
        rid = f"chatcmpl-{uuid.uuid4().hex}"
        created = int(time.time())
        self._submit(job)
        try:
            if stream:
                self._stream_v1(requested, job, tool_format, rid, created, bool((body.get("stream_options") or {}).get("include_usage")))
                return
            stats, error = self._finish_chat(job)
            if isinstance(error, BadRequest):
                self._v1_error(400, str(error))
                return
            if isinstance(error, HeavyDeferred):
                self._deferred(error, openai=True)
                return
            if error is not None or stats is None:
                self._v1_error(500, f"{type(error).__name__}: {error}", "server_error")
                return
            parsed = chat_reply(tool_format, stats, tools)
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
        if isinstance(first, HeavyDeferred):
            self._deferred(first, openai=True)
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
        hold = HoldBack(job.stops)
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
                # Send what the non-streamed answer would add, if it still
                # extends what was sent: the answer a stop string cut short,
                # the answer around control text, or the text held back.
                if "content" in info:
                    rest = info["content"]
                elif parser is None and held:
                    rest = visible(info["text"])
                else:
                    rest = sent + hold.pending
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
                    text = hold.feed(parser.last_content_delta)
                    if text:
                        sent += text
                        send({"content": text})
                continue
            if held or "<|" in text or "<channel|>" in text:
                held = True
                continue
            text = hold.feed(text)
            if text:
                sent += text
                send({"content": text})
        self._raw_chunk(b"data: [DONE]\n\n")
        self.wfile.write(b"0\r\n\r\n")

    def _chat_ollama_tools(self, requested, name, body, messages, stream, num, temperature, top_p, stops) -> None:
        tool_format = self.catalog.models[name]["tool_format"]
        try:
            if tool_format is None:
                raise BadRequest(f"'{requested}' has no tool format; it cannot take tools")
            if stream:
                raise BadRequest("stream is not supported with tools; send \"stream\": false")
            checked = chat_messages(messages)
            tools, choice = chat_tools({"tools": body.get("tools")})
            if tools and stops:
                raise BadRequest("stop is not supported with tools")
        except BadRequest as exc:
            self._json(400, {"error": str(exc)})
            return
        job = ChatJob(name, checked, tools, choice, num, temperature, top_p)
        job.stops = stops
        try:
            job.effort = effort_of(body)
        except BadRequest as exc:
            self._json(400, {"error": str(exc)})
            return
        started = time.perf_counter()
        self._submit(job)
        try:
            stats, error = self._finish_chat(job)
            if isinstance(error, BadRequest):
                self._json(400, {"error": str(error)})
                return
            if isinstance(error, HeavyDeferred):
                self._deferred(error)
                return
            if error is not None or stats is None:
                self._json(500, {"error": f"{type(error).__name__}: {error}"})
                return
            parsed = chat_reply(tool_format, stats, tools)
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

    def __init__(self, *a, **k) -> None:
        super().__init__(*a, **k)
        self.accepted: dict[int, float] = {}

    def process_request(self, request, client_address) -> None:
        # Stamped at accept, read by the handler thread: the gap is time spent
        # waiting for a thread to run, which the access log reports.
        self.accepted[id(request)] = time.perf_counter()
        super().process_request(request, client_address)


def main() -> None:
    catalog = Catalog(CONFIG_PATH)
    Handler.engine = Engine(catalog)
    Handler.catalog = catalog
    servers = []
    for host in dict.fromkeys(BIND_HOSTS):
        try:
            httpd = Server((host, PORT), Handler)
        except OSError as exc:
            _emit(f"skip bind {host}:{PORT}: {exc}")
            continue
        servers.append(httpd)
        threading.Thread(target=httpd.serve_forever, name=f"http-{host}", daemon=True).start()
        _emit(f"listening {host}:{PORT}")
    if not servers:
        raise SystemExit("no bind succeeded")
    threading.Event().wait()


if __name__ == "__main__":
    main()
