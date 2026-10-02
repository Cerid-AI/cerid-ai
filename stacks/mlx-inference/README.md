# MLX inference server

`serve.py` is a local model server for Apple-silicon Macs. It runs models on
the GPU through [MLX](https://github.com/ml-explore/mlx) and answers on
`0.0.0.0:11434` with the Ollama API, plus the OpenAI chat-completions and
embeddings endpoints. Cerid AI talks to it exactly as it talks to Ollama. It
runs on the host, not in a container, because containers on macOS cannot
reach the GPU.

What it adds over a stock Ollama install:

- Tool calling on `/v1/chat/completions` and `/api/chat`, parsed from each
  model's own output and tested against captured replies.
- A small model that answers in about 0.2 s while a long generation runs on
  the default model: all generations interleave one token at a time.
- Prompt-cache reuse for agent loops: each turn prefills only its new
  messages.
- `nomic-embed-text-v1.5` embeddings that match llama.cpp's CLS vectors at
  0.9998 cosine or better, so an index built through quenchforge or llama.cpp
  keeps working.

It does not rerank: there is no `/v1/rerank`. A Cerid stack that uses it
leaves `RERANK_PROVIDER` at its default (`sidecar`, falling back to
in-process); never set it to `quenchforge` for this server.

## Install

On an Apple-silicon Mac with 16 GiB of memory or more, from the repo root:

```bash
stacks/mlx-inference/install.sh --write-env .env
./scripts/start-cerid.sh            # restart Cerid so it reads the new settings
```

install.sh needs [uv](https://docs.astral.sh/uv/) or `python3.12` on the
path. It:

1. builds a venv in `~/.local/share/cerid-mlx` from the pinned
   `requirements.txt`, and copies the server there;
2. picks a catalog by memory and downloads its weights from Hugging Face into
   `~/.local/share/cerid-mlx/models` (resumable; files with a published hash
   are verified);
3. installs and starts the launchd agent `com.cerid.mlx`, which restarts the
   server on crash and at login;
4. waits for it to load, runs `check.py` against it, and checks it can be
   reached from a container the way Cerid reaches it;
5. with `--write-env`, appends the Cerid settings below to `.env`. A setting
   that is already in the file is never changed; the installer prints it
   with the value it would have used.

Run it again to update: it is idempotent, keeps finished downloads, and keeps
the previous catalog as `models.json.<timestamp>` when it changes.

| Option | |
|---|---|
| `--profile compact\|standard` | override the choice by memory |
| `--heavy` | add gpt-oss-120b (59 GiB of weights; 128 GiB of memory) |
| `--models-dir DIR` | keep weights in `DIR/<org>/<repo>` instead |
| `--port N` | serve on another port (default 11434) |
| `--allow ADDR` | accept another peer address (see [Network](#network)) |
| `--write-env FILE` | append the unset Cerid settings to `FILE` |
| `--no-start`, `--no-check` | download only; skip `check.py` |
| `--uninstall [--purge]` | stop and remove the agent; `--purge` also removes the install directory |

If another server already holds the port, install.sh stops and says which.
Ollama is the usual one: quit it, or install on another port and point
`OLLAMA_URL` there.

### Catalogs

| Profile | For | Small | Default | Weights |
|---|---|---|---|---|
| `compact` | 16 to 47 GiB | `qwen3.5-4b-instruct` | `qwen3.5-9b-instruct` | about 9 GiB |
| `standard` | 48 GiB and up | `qwen3.5-4b-instruct` | `gemma-4-26b-a4b` | about 19 GiB |
| `heavy.json` (`--heavy`) | 128 GiB and up | | `gpt-oss-120b`, loaded when named | 59 GiB |

Both profiles add `nomic-embed-text-v1.5` (146 MB) and alias Cerid's default
local model name, `llama3.2:3b`, to the small model.

### Cerid settings

```bash
OLLAMA_ENABLED=true
INTERNAL_LLM_PROVIDER=ollama
OLLAMA_URL=http://host.docker.internal:11434
INTERNAL_LLM_MODEL=<the catalog's default row>
OLLAMA_DEFAULT_MODEL=<the catalog's small row>
```

`OLLAMA_ENABLED` turns on Cerid's local-model proxy and lists the server's
models in the GUI. `INTERNAL_LLM_MODEL` matters too: the server lists several
chat models on `/api/tags`, and Cerid only picks one it was told about or
recognises.

Cerid's setup wizard finds the server on its own: it answers the same
`/api/tags` probe Ollama does. Its "pull a model" step does not apply, since
the catalog decides what is loaded; pick from the models it lists.

**Embeddings are left alone.** Cerid embeds with its own embedder by default
(`EMBEDDINGS_PROVIDER=sidecar`, falling back to in-process; Snowflake
arctic-embed). To embed through this server instead, set
`EMBEDDINGS_PROVIDER=quenchforge` and
`QUENCHFORGE_EMBED_MODEL=nomic-embed-text-v1.5` (and `RERANK_PROVIDER=in-process`
if it was `quenchforge`). That is a different vector
space: do it on a new knowledge base, or re-embed an existing one, never by
flipping the setting under stored vectors.

## Network

The server binds `0.0.0.0` and accepts only `127.0.0.1`, `::1`, and the
Colima VM `192.168.5.1`; every other peer gets HTTP 403 before a model call.
`0.0.0.0` is required under Colima, which reaches the host at `192.168.5.2`
through a forward onto every host listener rather than onto loopback. The
allowlist keeps the LAN and any VPN address out.

A container runtime that connects from another address shows up in the
install's container check as `refused: <address>`, and in the log as a 403
line from that address. Accept it with `install.sh --allow <address>`.
`CERID_MLX_BIND` and `CERID_MLX_ALLOW` set the same things for a server run by
hand.

## Model tiers

The catalog (`models.json` in the install directory, or `CERID_MLX_CONFIG`)
is the only place models are declared. A row's `path` is absolute,
`~`-relative, or relative to the catalog file. Every row has a tier:

- `small`, `default` and `embed` load at start and never unload.
- `heavy` rows share one slot. Naming a heavy model loads it. Naming a
  different heavy model swaps only that slot, after the requests using the
  current one finish. The other tiers are never evicted.
- A request with no `model` goes to the `default` row.
- Exactly one `small` and one `default` row are required.

`aliases` map other names onto a tier (`small`, `default`) or a row. They
resolve on request and are **not** listed in `/api/tags`, so a client that
ranks the listed names never sees a name that does not match its weights.

### Adding a model

1. Download the MLX weights, for example
   `hf download mlx-community/<repo> --local-dir ~/.local/share/cerid-mlx/models/mlx-community/<repo>`.
2. Add one row to the catalog with `"tier": "heavy"`, a name without a slash,
   and `path`, `family`, `parameter_size`, `quantization`. Add `tool_format`
   only if its chat template writes calls in one of the three formats below,
   and capture a corpus for it (see [Tests](#tests)); a row without one
   answers requests with `tools` with 400.
3. Restart: `launchctl kickstart -k gui/$(id -u)/com.cerid.mlx`.

To make it the default instead, move `"tier": "default"` onto the new row and
set the old default row to `heavy`. Callers that name the old model keep
working. To replace the small model, change the `small` row's `path`. If you
rename a row, add the old name as an alias. The family name matters for two
families: `gpt-oss` gets `reasoning_effort` and the Harmony final channel,
and every family gets `enable_thinking=False` in the chat template. On
`/v1/chat/completions` and tool requests, `tool_format: "harmony"` is what
routes a row through openai-harmony instead of its chat template.

A catalog edited in place is replaced by the next install.sh run (the old one
is kept beside it). To keep your own, run the server with `CERID_MLX_CONFIG`
pointing elsewhere, or edit `catalogs/` in your checkout.

### gpt-oss and its vocabulary

`openai-harmony` renders and parses gpt-oss turns. It needs OpenAI's o200k
vocabulary. install.sh puts it in `tiktoken/` beside `serve.py` when the
catalog has a harmony row, checking its sha256
(`446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d`), so the
server never fetches it at run time.

## Chat completions and tools

`POST /v1/chat/completions` takes OpenAI's request shape:

| Field | Accepted |
|---|---|
| `model` | a row or alias; missing means the `default` row |
| `messages` | `system`, `developer` (read as system), `user`, `assistant`, `tool`. Content is a string or a list of text parts |
| `max_tokens` / `max_completion_tokens` | 1 to 8192; default 4096 |
| `temperature`, `top_p` | default 0 and 1 |
| `stream`, `stream_options.include_usage` | SSE chunks ending in `data: [DONE]`; not with tools |
| `tools`, `tool_choice` | below |

The reply is a `chat.completion`: `choices[0].message` holds `content`, and
`tool_calls` (`id`, `type: "function"`, `function.name`, `function.arguments`
as a JSON string) when the model called something. `usage` carries
`prompt_tokens_details.cached_tokens`, the part of the prompt that came from
the prompt cache. Errors are `{"error": {"message", "type"}}` with 400 for a
request the server or the model's chat template cannot take, 404 for an
unknown model, 500 otherwise.

`finish_reason` is `stop`, `length`, `tool_calls`, or `invalid_tool_call`.
`invalid_tool_call` means the reply started a call that does not parse: an
unclosed marker, arguments that are not JSON or not an object, a value the
tool's schema types reject, or a call to a tool that was not offered. The
model's raw reply is then the `content`; the server never repairs a call. A
call cut off by `max_tokens` comes back the same way, with `length`.

| `tool_choice` | Effect |
|---|---|
| `auto` (default with tools) | the model decides |
| `none` | the turn is rendered without the tools |
| `required` | the reply is started with the model's call opener, so it is a call |
| `{"type": "function", "function": {"name": X}}` | the reply is started with a call to X |

gpt-oss reasons before it acts, so for it a forced call comes after the
analysis: generation stops at `<|end|>`, the server feeds the call header, and
the model writes the rest.

**Formats.** Each chat row's `tool_format` names how its template writes
calls. A row without one answers `tools` with 400.

| `tool_format` | Models | A call as the model writes it | Parsed by |
|---|---|---|---|
| `qwen3_coder` | Qwen3.5 | `<tool_call>` `<function=NAME>` `<parameter=K>` value `</parameter>` `</function>` `</tool_call>`, repeated for parallel calls | `mlx_lm.tool_parsers.qwen3_coder`; values are typed from the tool's JSON schema |
| `gemma4` | Gemma 4 | `<\|tool_call>call:NAME{k:<\|"\|>v<\|"\|>}<tool_call\|>`, repeated for parallel calls | `mlx_lm.tool_parsers.gemma4` |
| `harmony` | gpt-oss | `<\|channel\|>commentary to=functions.NAME <\|constrain\|>json<\|message\|>{...}<\|call\|>`, one per reply | `openai-harmony`, which also renders the prompt; generation stops at `<\|call\|>` |

Tool names must match `[A-Za-z0-9_-]{1,64}`. The gemma4 parser in mlx-lm
0.31.3 raises on text that holds no call (mlx-lm #1125; the fix in #1142 was
closed unmerged). Here the parsers only ever see the text between a call's
markers, so a plain reply is plain content.

**gpt-oss and its analysis.** The analysis channel comes back as
`message.reasoning_content`. Send it back on that assistant message (with its
`tool_calls`) in the next request: harmony renders it while the turn is still
calling tools, and drops it once the turn has a final answer, which is how the
model was trained. The final channel is `content`.

**Streaming with tools is refused (400).** A call can only be acted on once its
arguments are complete, and incremental parsers per format would be a second
implementation that can drift from the one tested against the corpus. Agent
loops read whole turns; the latency that matters is prefill, which the prompt
cache addresses.

**Ollama.** `/api/chat` with a non-empty `tools` takes the same path and answers
in Ollama's shape: `message.tool_calls: [{function: {name, arguments}}]` with
`arguments` an object, `message.thinking` for gpt-oss, and `done_reason`. It
needs `"stream": false`, since Ollama streams by default. Without `tools`,
`/api/chat` and `/api/generate` run exactly as before: same rendering, no
prompt cache, same bytes.

## Embeddings

`nomic-embed-text-v1.5` runs on MLX in `nomic_bert.py`. It reads the GGUF
that llama.cpp and quenchforge serve: `nomic-embed-text-v1.5.Q8_0.gguf` from
`nomic-ai/nomic-embed-text-v1.5-GGUF`, sha256
`3e24342164b3d94991ba9692fdc0dd08e3fd7362e0aacc396a9a5c54a544c3b7`. MLX loads
Q8_0 as 8-bit groups of 32, so the weights are the same values llama.cpp
uses. The tokenizer is a port of llama.cpp's WPM tokenizer. It keeps that
tokenizer's quirks. For example, NFD keeps only the first codepoint, so `é`
becomes `e`.

**Pooling is CLS, not mean.** quenchforge starts its embed slot with
`--pooling cls` and ignores the GGUF's own `pooling_type` (mean); this server
matches it. Mean pooling scores 0.81 to 0.94 cosine against CLS vectors, so
it is a different space. No `search_query:` or `search_document:` prefix is
added. Callers send raw text.

Against llama.cpp's CLS vectors for the same GGUF, token ids matched for 128
of 128 test texts (code, docs, CJK, RTL scripts, accents, emoji, control
characters), and cosine over 133 texts of up to 561 tokens was at least
0.99988. `check.py`
re-checks a sample of those vectors (`reference/`) on every run.

| Endpoint | Request | Response |
|---|---|---|
| `POST /api/embed` | `input`: string or list; `truncate` (default true) | `{model, embeddings, total_duration, load_duration, prompt_eval_count}` |
| `POST /api/embeddings` | `prompt`: string | `{embedding}` |
| `POST /v1/embeddings` | `input`: string or list; `encoding_format` float or base64 | `{object: "list", data: [{object, index, embedding}], model, usage}` |

Vectors are L2-normalised on every endpoint, including the legacy one. That
matches quenchforge. Ollama itself returns unnormalised vectors on
`/api/embeddings`. The context is 2048 tokens. A longer input is truncated
unless the request sets `truncate: false`, which returns 400. An unknown
embedding name is 404. There is no catch-all, because quietly serving a
different model would mix vector spaces.

## Scheduling

All MLX work runs on one thread. Generations in flight advance one token at a
time in turn, and embeddings run between steps. A small-model call or an
embed during a long default-model generation returns in about 0.2 s and
0.02 s.

The `tiers` block in the catalog sets three knobs per chat tier:

| Knob | Meaning |
|---|---|
| `max_active` | generations stepping at once on the tier; the rest wait in order. `null` is no cap |
| `prompt_cache_gib` | memory for cached prompt KV, per loaded model of the tier; 0 turns reuse off |
| `prefill_chunk` | prompt tokens per step, for `/v1` and tool requests |

| Tier | `standard` | `compact` | Built-in default |
|---|---|---|---|
| small | no cap, 2 GiB, 512 | no cap, 1 GiB, 512 | no cap, 2 GiB, 512 |
| default | 3, 8 GiB, 512 | 2, 2 GiB, 512 | 3, 8 GiB, 512 |
| heavy | 1, 16 GiB, 512 (`heavy.json`) | | 1, 16 GiB, 512 |

The cap counts every generation on its tier, `/api` included. A burst of agent
loops on the default model waits for default slots and never takes the small
model's turns, so utility calls keep stepping; a waiting request costs the
thread nothing.

**Prompt cache.** `/v1` and tool requests reuse the KV cache of an earlier
prompt with the same token prefix (mlx-lm's `LRUPromptCache`, one per loaded
model). Each turn stores two entries: the conversation before the generation
prompt, and the whole prompt plus the reply. The next turn of an agent loop
extends one of them, so only the new messages are prefilled. The first entry
is the one that usually serves: templates re-render the model's reply
differently from the tokens it wrote (Gemma drops the empty thought block,
harmony moves the recipient), and caches with recurrent or sliding-window
layers (Qwen3.5, Gemma 4, gpt-oss) cannot be trimmed back to where the prompts
diverge. `usage.prompt_tokens_details.cached_tokens` shows the reuse, and `GET
/` shows each cache's entries and bytes. Swapping the heavy slot drops the old
model's cache. The cache is memory on top of the weights.

What still blocks the thread:

- **Loading a heavy model**: gpt-oss-120b takes about 13 s from a fast disk.
- **The prefill of an `/api` prompt**, in one step.
- **One prefill chunk** of a `/v1` or tool request: up to `prefill_chunk`
  tokens, after which every other generation takes its step.
- **Copying a cache entry**: once when the checkpoint is stored, once when an
  entry is reused.
- **An embedding batch.**

A client that disconnects mid-stream cancels its generation.

## Revision

`GET /api/version` answers `{"version": "cerid-mlx-<12 hex>"}`. The value is a
hash of `serve.py`, `nomic_bert.py`, the catalog, each model's weight size,
and the installed `mlx`, `mlx-lm` and `openai-harmony` versions. A client that
validated a model on one revision can treat any other revision as
unvalidated: anything that can change how a model answers moves it.

## Behaviour kept from Ollama clients' expectations

- Gemma and Qwen are rendered with `enable_thinking=False`.
- gpt-oss replies are the Harmony final channel, in both streaming and
  non-streaming responses.
- `/api/chat` streams by default. `/api/generate` does not.
- `format: "json"` adds a JSON-only system message when the caller sent none.
- Names have no slash, so Cerid treats them as local models.

## Operations

```bash
launchctl print gui/$(id -u)/com.cerid.mlx | grep -E 'state|pid ='
launchctl kickstart -k gui/$(id -u)/com.cerid.mlx                 # restart
tail -f ~/Library/Logs/com.cerid.mlx.log
curl -s http://127.0.0.1:11434/ | python3 -m json.tool             # tiers, resident, heavy, load errors
~/.local/share/cerid-mlx/.venv/bin/python ~/.local/share/cerid-mlx/check.py          # endpoint checks
~/.local/share/cerid-mlx/.venv/bin/python ~/.local/share/cerid-mlx/check.py --heavy  # also loads each heavy row
```

If the weights live on an external volume and the agent hangs at start
while the same command works from a terminal, macOS is blocking the agent's
access to the volume. Grant Full Disk Access (System Settings, Privacy &
Security) to the Python that `.venv/bin/python` resolves to (`readlink -f`
shows it), then restart the agent.

To try a change without touching a running server, start a second copy on a
side port and point `check.py` at it:

```bash
CERID_MLX_CONFIG=~/.local/share/cerid-mlx/models.json CERID_MLX_PORT=11436 \
  ~/.local/share/cerid-mlx/.venv/bin/python -u stacks/mlx-inference/serve.py
~/.local/share/cerid-mlx/.venv/bin/python stacks/mlx-inference/check.py 11436
```

## Tests

```bash
uv venv -p 3.12 /tmp/mlx-test && uv pip install --python /tmp/mlx-test/bin/python -r stacks/mlx-inference/requirements-test.txt
MLX_STACK_TESTS_STRICT=1 /tmp/mlx-test/bin/python -m pytest stacks/mlx-inference/tests/ -q
```

| File | Covers | Needs |
|---|---|---|
| `test_corpus.py` | captured replies from each catalog model, replayed through the parsers | mlx-lm, openai-harmony |
| `test_parsing.py` | parse edge cases: unclosed calls, bad arguments, #1125 | mlx-lm, openai-harmony |
| `test_requests.py` | request checks, template kwargs, forced openers, harmony rendering | openai-harmony for some |
| `test_handler.py` | HTTP shapes and errors on a fake engine; catalog loading; `/api` without tools unchanged | mlx-lm for some |
| `test_engine.py` | the engine on MLX with tiny random models: cache reuse, chunked prefill, caps, swap, stop at `<\|call\|>` | MLX |
| `test_install.py` | install.py, and every shipped catalog loading through the server's own parser | |

A test whose dependency is missing skips with the reason: Intel macOS has
neither MLX nor an openai-harmony wheel, so there the rest run. CI installs
`requirements-test.txt` (MLX runs on Linux CPUs) and sets
`MLX_STACK_TESTS_STRICT=1`, which turns every such skip into a failure.

`tests/corpus/<model>.json` holds what each model wrote for the same requests:
a single call, parallel calls, a plain reply with tools offered, the answer
after a tool result, typed arguments and a named choice. Every model with a
`tool_format` in a catalog must have one. Capture it again after changing a
model's weights or quant, or upgrading mlx-lm, and review the diff:

```bash
~/.local/share/cerid-mlx/.venv/bin/python stacks/mlx-inference/tests/capture_corpus.py \
  qwen3.5-4b-instruct qwen3.5-9b-instruct --catalog ~/.local/share/cerid-mlx/models.json \
  --server "<rev>, mlx <v>, mlx-lm <v>"
```

It posts to `/api/generate` with `raw`, which returns `visible()` of the reply.
Qwen replies come back unchanged and Gemma's lose outer whitespace. gpt-oss
replies lose their control tokens, so each gpt-oss case takes two requests and
the reply is rebuilt; the script checks the rebuilt reply has exactly the
token count the server generated. Naming a heavy model loads it.
