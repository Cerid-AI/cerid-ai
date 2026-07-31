# Native Ollama.app removal (Mac Pro) — record + reverse

> **Date:** 2026-07-21  
> **Host:** Justin's Mac Pro (`MacPro7,1`)  
> **Audience:** operators / agents restoring a stock Ollama install or diagnosing Squirrel crash history  
> **Status:** applied (Ollama.app removed; Quenchforge remains canonical on `:11434`)

## Why this was done

Recurring crash notifications for process **Squirrel**
(`/Applications/Ollama.app/Contents/Frameworks/Squirrel.framework/.../Squirrel`)
with:

- exception: `SIGKILL (Code Signature Invalid)` at `_dyld_start`
- coalition: `com.ollama.ollama`
- timing: ~30–40 s after login (RunAtLoad)

**Root cause:** Ollama’s login LaunchAgent (`com.ollama.ollama`) starts
**Squirrel `background`**, not the main `Ollama` binary. At the same time,
Quenchforge’s prestart guard boots out `com.ollama.ollama` to reclaim
`127.0.0.1:11434`. That race produces crash dialogs every reboot even though
the on-disk codesign was valid.

Cerid AI on this machine does **not** require the native Ollama.app. It
requires an **Ollama-API-compatible** server on `:11434`, which is
**Quenchforge** (see `CLAUDE.md` “Inference backend on this machine”).

Related: WindowServer / GPU restart gotchas when overlapping Quenchforge
loads — separate issue; see quenchforge `CLAUDE.md` operational gotcha #0.

## What was removed (2026-07-21)

| Item | Action |
|---|---|
| `com.ollama.ollama` launchd job | `launchctl bootout` + `launchctl disable gui/$(id -u)/com.ollama.ollama` |
| `/Applications/Ollama.app` | deleted (~559 MB; had been 0.24.0, briefly updated toward 0.32.1) |
| `/usr/local/bin/ollama` | deleted (symlink into the app bundle) |
| `~/Library/Caches/ollama` | deleted (update zips / webkit) |
| `~/Library/Caches/com.electron.ollama` | deleted if present |
| `~/Library/Application Support/Ollama` | deleted (sqlite + pid only) |

## What was intentionally preserved

| Item | Why |
|---|---|
| `~/.ollama/` (~6.5 GB) | Model blobs + manifests historically used by Ollama |
| `~/.quenchforge/models/` | Active GGUFs + migrate-from-ollama symlinks into `~/.ollama/models/blobs` |
| `com.cerid.quenchforge` LaunchAgent | Canonical local inference |
| Quenchforge binary / brew keg | Still listening on `127.0.0.1:11434` |

**Do not delete `~/.ollama` when “cleaning Ollama”** unless you also re-pull
or re-copy every GGUF Quenchforge still resolves via symlink.

## Post-removal verify (expected)

```bash
test ! -e /Applications/Ollama.app
test ! -e /usr/local/bin/ollama
launchctl print-disabled "gui/$(id -u)" | grep ollama
# expect: "com.ollama.ollama" => disabled

lsof -nP -iTCP:11434 -sTCP:LISTEN   # quenchfor only
curl -sS http://127.0.0.1:11434/health
```

Squirrel crash reports under `~/Library/Logs/DiagnosticReports/Squirrel-*.ips`
should stop accumulating after the next few reboots.

## How to reverse (restore native Ollama)

Only do this if you **intentionally** want stock Ollama again (e.g. compare
upstream behavior, non-Cerid tools). On this Mac Pro, **prefer Quenchforge**
for Cerid workloads.

### 1. Reinstall Ollama.app

```bash
# Official installer (preferred)
open "https://ollama.com/download"

# Or CLI install script (pulls current stable):
# curl -fsSL https://ollama.com/install.sh | sh
```

After install, confirm:

```bash
ls /Applications/Ollama.app
/usr/local/bin/ollama --version   # or path from the installer
```

### 2. Decide port ownership (critical)

**Do not** let both own `:11434`.

| Goal | Action |
|---|---|
| **Cerid / Quenchforge remains default** (recommended) | Keep Quenchforge LaunchAgent. Disable Ollama login/background again (step 3). Only open Ollama.app manually if needed, and point it at a **non-11434** host if the app supports it — stock `ollama serve` defaults to 11434 and will fight Quenchforge. |
| **Stock Ollama owns :11434** | Stop Quenchforge first, then start Ollama: |

```bash
# Make Quenchforge cede the port (Cerid local LLM will break until reverse):
launchctl bootout "gui/$(id -u)/com.cerid.quenchforge"

# Enable + start Ollama login agent (after reinstall):
launchctl enable "gui/$(id -u)/com.ollama.ollama"
# Open Ollama.app once so SMAppService re-registers the agent if needed.
open -a Ollama
```

Point Cerid at Ollama only if you accept AMD Metal correctness limits that
Quenchforge exists to fix:

```bash
# Example .env overrides (not recommended on Mac Pro Vega II for production evals)
INTERNAL_LLM_PROVIDER=ollama
OLLAMA_ENABLED=true
OLLAMA_URL=http://host.docker.internal:11434   # docker stack
# or http://127.0.0.1:11434 for host-native clients
```

### 3. Keep Ollama installed but silent (no Squirrel login crashes)

If you reinstall for the CLI/models but still run Quenchforge on 11434:

```bash
launchctl bootout "gui/$(id -u)/com.ollama.ollama" 2>/dev/null || true
launchctl disable "gui/$(id -u)/com.ollama.ollama" 2>/dev/null || true
```

Also turn **off** Ollama under:

**System Settings → General → Login Items & Extensions → Allow in the Background**

Quenchforge’s `~/.config/quenchforge/prestart-guard.sh` will continue to boot
out `com.ollama.ollama` whenever the LaunchAgent is present and loaded.

### 4. Models

Blobs under `~/.ollama/models` were **not** deleted. After reinstall, stock
Ollama should see existing models. Quenchforge continues to use
`~/.quenchforge/models` (including symlinks into `~/.ollama/models/blobs`
created by `quenchforge migrate-from-ollama`).

If models go missing:

```bash
quenchforge migrate-from-ollama   # re-link from ~/.ollama if needed
# or: quenchforge pull <alias>
```

### 5. Re-enable Cerid’s preferred stack

```bash
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.cerid.quenchforge.plist 2>/dev/null \
  || launchctl kickstart -k "gui/$(id -u)/com.cerid.quenchforge"
lsof -nP -iTCP:11434 -sTCP:LISTEN   # must be quenchfor
curl -sS http://127.0.0.1:11434/health
```

## Cerid config notes (unchanged by this removal)

- Docker optional profile `ollama` (`docker compose --profile ollama`) is
  independent of the native Mac app; default remains `OLLAMA_ENABLED=false`.
- Env names like `OLLAMA_URL` / routes under `/ollama/*` still mean
  “Ollama-compatible API”; with Quenchforge they hit `:11434` on this host.
- `INTERNAL_LLM_PROVIDER=quenchforge` (or ollama-compatible URL pointing at
  Quenchforge) is the Mac Pro production path.

## References

- `CLAUDE.md` — inference backend = quenchforge on this machine  
- `docs/RUNBOOK_PRODUCTION.md` — quenchforge restart playbook  
- `docs/LOCAL_INSTANCES.md` — personal vs public sandbox ports  
- `~/Develop/quenchforge/CLAUDE.md` — AMD / WindowServer gotchas  
- `~/.config/quenchforge/prestart-guard.sh` — boots out `com.ollama.ollama`  
- Crash samples (pre-removal): `~/Library/Logs/DiagnosticReports/Squirrel-2026-07-21-*.ips`
