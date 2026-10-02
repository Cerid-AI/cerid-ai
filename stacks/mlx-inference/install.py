#!/usr/bin/env python3
# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Install or update the cerid-mlx model server as a launchd agent.

Run through install.sh, which checks the platform, builds the venv and copies
the server files into the install directory, then hands its arguments here.
Steps, each idempotent:

  1. Pick a catalog from catalogs/ by memory (or --profile), plus heavy rows
     with --heavy.
  2. Download each row's weights from Hugging Face. Files with a sha256 in the
     catalog are verified; a mismatch stops the install.
  3. Write <home>/models.json and the launchd agent, and (re)start it.
  4. Wait for /api/version, run check.py, and probe the server from a
     container the way Cerid reaches it.
  5. Print the Cerid settings, or append the unset ones to a .env with
     --write-env. Embeddings are never switched: that changes the vector space
     of an existing knowledge base.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from xml.sax.saxutils import escape

STACK = Path(__file__).resolve().parent
GIB = 1 << 30
# Below this, the default tier moves to the compact catalog.
STANDARD_MIN_GIB = 48
COMPACT_MIN_GIB = 16
HEAVY_MIN_GIB = 128
DEFAULT_ALLOW = ("127.0.0.1", "::1", "192.168.5.1")
O200K_URL = "https://openaipublic.blob.core.windows.net/encodings/o200k_base.tiktoken"
O200K_SHA256 = "446a9538cb6c348e3516120d7c08b09f57c36495e2acfffe59a5bf8b0cfb1a2d"  # pragma: allowlist secret
REFUSED = re.compile(r'^(\S+) "[A-Z]+ [^"]*" 403\b', re.M)


def say(msg: str = "") -> None:
    print(msg, flush=True)


def fail(msg: str) -> None:
    raise SystemExit(f"install: {msg}")


# -- catalog -------------------------------------------------------------------


def choose_profile(memory_gib: float, requested: str) -> str:
    if requested != "auto":
        return requested
    if memory_gib < COMPACT_MIN_GIB:
        fail(f"{memory_gib:.0f} GiB of memory is below the {COMPACT_MIN_GIB} GiB the compact catalog needs")
    return "standard" if memory_gib >= STANDARD_MIN_GIB else "compact"


def build_catalog(profile: dict, heavy: dict | None, models_dir: Path | None) -> dict:
    """The catalog to install: the profile, heavy rows merged in, and paths moved
    under models_dir when one is given (otherwise they stay relative to the
    installed catalog, which puts weights in <home>/models)."""
    models = dict(profile["models"])
    tiers = dict(profile.get("tiers", {}))
    if heavy:
        for name in heavy["models"]:
            if name in models:
                fail(f"heavy row {name!r} is already in the profile")
        models.update(heavy["models"])
        tiers.update(heavy.get("tiers", {}))
    rows = {}
    for name, spec in models.items():
        spec = dict(spec)
        if models_dir is not None:
            spec["path"] = str(models_dir / Path(spec["path"]).relative_to("models"))
        rows[name] = spec
    out = {"models": rows, "aliases": dict(profile.get("aliases", {}))}
    if tiers:
        out["tiers"] = tiers
    return out


def row_path(catalog_file: Path, spec: dict) -> Path:
    path = Path(spec["path"]).expanduser()
    return path if path.is_absolute() else catalog_file.parent / path


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def download(catalog: dict, catalog_file: Path) -> None:
    from huggingface_hub import hf_hub_download, snapshot_download

    for name, spec in catalog["models"].items():
        source = spec.get("source")
        dest = row_path(catalog_file, spec)
        if not source:
            if not dest.exists():
                fail(f"{name}: no source to download from, and {dest} does not exist")
            continue
        say(f"  {name}: {source['repo']}{'/' + source['file'] if 'file' in source else ''}")
        if "file" in source:
            dest.parent.mkdir(parents=True, exist_ok=True)
            got = Path(hf_hub_download(source["repo"], source["file"], local_dir=dest.parent))
            if got != dest:
                got.replace(dest)
            want = source.get("sha256")
            if want and sha256(dest) != want:
                dest.unlink()
                fail(f"{name}: {dest.name} does not match sha256 {want}; removed it")
        else:
            snapshot_download(source["repo"], local_dir=dest)


def install_o200k(home: Path) -> None:
    vocab = home / "tiktoken" / "o200k_base.tiktoken"
    if vocab.is_file() and sha256(vocab) == O200K_SHA256:
        return
    vocab.parent.mkdir(parents=True, exist_ok=True)
    tmp = vocab.with_suffix(".part")
    with urllib.request.urlopen(O200K_URL, timeout=120) as r, tmp.open("wb") as f:
        shutil.copyfileobj(r, f)
    if sha256(tmp) != O200K_SHA256:
        tmp.unlink()
        fail("o200k_base.tiktoken does not match its published sha256")
    tmp.replace(vocab)


# -- launchd -------------------------------------------------------------------


def render_plist(template: str, values: dict[str, str]) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{{" + key + "}}", escape(value))
    left = re.findall(r"\{\{[A-Z_]+\}\}", out)
    if left:
        fail(f"plist template placeholders not filled: {left}")
    return out


def launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["launchctl", *args], capture_output=True, text=True, check=check)


def get(url: str, timeout: float = 3) -> tuple[int, str]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except (urllib.error.URLError, OSError):
        return 0, ""


def label_pid(label: str) -> int | None:
    out = launchctl("print", f"gui/{os.getuid()}/{label}", check=False)
    m = re.search(r"^\s*pid = (\d+)", out.stdout, re.M) if out.returncode == 0 else None
    return int(m.group(1)) if m else None


def listener_pids(port: int) -> set[int]:
    out = subprocess.run(["lsof", "-nP", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"], capture_output=True, text=True)
    return {int(pid) for pid in out.stdout.split()}


def port_owner(port: int, label: str) -> str | None:
    """None when the port is free or held by this label's own process;
    otherwise a description of what holds it. A loaded label is not enough:
    it may be serving a different port."""
    pids = listener_pids(port)
    if not pids or label_pid(label) in pids:
        return None
    code, body = get(f"http://127.0.0.1:{port}/api/version")
    try:
        version = json.loads(body).get("version", "") if code == 200 else ""
    except json.JSONDecodeError:
        version = ""
    if version.startswith("cerid-mlx-"):
        return f"another cerid-mlx server ({version}, pid {min(pids)}) not started by {label}"
    return f"pid {min(pids)}, answering {version or code or 'no HTTP'} (Ollama is the usual one)"


def restart_agent(domain: str, label: str, plist: Path) -> None:
    launchctl("bootout", f"{domain}/{label}", check=False)
    # bootout returns before launchd has finished removing the job; bootstrap
    # fails with error 5 until it has.
    for _ in range(20):
        out = launchctl("bootstrap", domain, str(plist), check=False)
        if out.returncode == 0:
            return
        time.sleep(1)
    fail(f"launchctl bootstrap {plist} failed: {out.stderr.strip() or out.returncode}")


def wait_ready(port: int, log: Path, minutes: float) -> str:
    deadline = time.time() + minutes * 60
    while time.time() < deadline:
        code, body = get(f"http://127.0.0.1:{port}/api/version")
        if code == 200:
            return json.loads(body)["version"]
        time.sleep(2)
    tail = log.read_text(errors="replace").splitlines()[-20:] if log.exists() else []
    fail("the server did not answer within the time limit. Last log lines:\n" + "\n".join(tail))
    return ""


# -- reaching it from a container ----------------------------------------------


def refused_peers(log_text: str) -> list[str]:
    return sorted(set(REFUSED.findall(log_text)))


def probe_from_container(port: int, log: Path) -> str:
    """What a container sees at host.docker.internal: 'ok', 'skipped: …', or
    'refused: <peer>' when the server's allowlist turned it away."""
    if not shutil.which("docker"):
        return "skipped: docker is not installed"
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        return "skipped: docker is not running"
    offset = log.stat().st_size if log.exists() else 0
    run = subprocess.run(
        ["docker", "run", "--rm", "busybox:stable", "wget", "-q", "-O", "-", f"http://host.docker.internal:{port}/api/version"],
        capture_output=True, text=True, timeout=300,
    )
    if run.returncode == 0 and "cerid-mlx-" in run.stdout:
        return "ok"
    time.sleep(1)
    with log.open(errors="replace") as f:
        f.seek(offset)
        peers = refused_peers(f.read())
    if peers:
        return "refused: " + ",".join(peers)
    return "failed: " + (run.stderr.strip().splitlines() or ["no output"])[-1]


# -- Cerid settings ------------------------------------------------------------


def cerid_settings(catalog: dict, port: int) -> dict[str, str]:
    tiers = {name: spec["tier"] for name, spec in catalog["models"].items()}
    return {
        # Gates Cerid's Ollama proxy, the local rows of its model list, and
        # local chat dispatch; without it they 503 or are left out.
        "OLLAMA_ENABLED": "true",
        "INTERNAL_LLM_PROVIDER": "ollama",
        "OLLAMA_URL": f"http://host.docker.internal:{port}",
        "INTERNAL_LLM_MODEL": next(n for n, t in tiers.items() if t == "default"),
        "OLLAMA_DEFAULT_MODEL": next(n for n, t in tiers.items() if t == "small"),
    }


def env_keys(text: str) -> dict[str, str]:
    keys = {}
    for line in text.splitlines():
        m = re.match(r"\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)", line)
        if m:
            keys[m.group(1)] = m.group(2).strip().strip("\"'")
    return keys


def env_plan(existing: str, wanted: dict[str, str]) -> tuple[dict[str, str], dict[str, str]]:
    """(keys to append, keys already set to something else). A key that is
    already set is never changed."""
    have = env_keys(existing)
    add = {k: v for k, v in wanted.items() if k not in have}
    differ = {k: have[k] for k, v in wanted.items() if k in have and have[k] != v}
    return add, differ


def append_env(path: Path, add: dict[str, str]) -> None:
    if not add:
        return
    text = path.read_text() if path.exists() else ""
    block = "" if not text or text.endswith("\n") else "\n"
    block += "\n# cerid-mlx (stacks/mlx-inference/install.py)\n"
    block += "".join(f"{k}={v}\n" for k, v in add.items())
    with path.open("a") as f:
        f.write(block)


# -- main ----------------------------------------------------------------------


def memory_gib() -> float:
    out = subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True)
    return int(out.stdout.strip()) / GIB


def uninstall(args) -> None:
    plist = Path.home() / "Library" / "LaunchAgents" / f"{args.label}.plist"
    launchctl("bootout", f"gui/{os.getuid()}/{args.label}", check=False)
    if plist.exists():
        plist.unlink()
    say(f"Stopped and removed {args.label}.")
    if args.purge:
        shutil.rmtree(args.home, ignore_errors=True)
        say(f"Removed {args.home}" + (f". Weights under {args.models_dir} were kept." if args.models_dir else ", weights included."))
    else:
        say(f"Kept {args.home} (venv, catalog and weights). Pass --purge to remove it.")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="install.sh", description=__doc__.split("\n\n")[0])
    ap.add_argument("--home", type=Path, default=Path(os.environ.get("CERID_MLX_HOME", Path.home() / ".local/share/cerid-mlx")))
    ap.add_argument("--profile", choices=("auto", "compact", "standard"), default="auto")
    ap.add_argument("--heavy", action="store_true", help=f"add catalogs/heavy.json (gpt-oss-120b; {HEAVY_MIN_GIB} GiB of memory)")
    ap.add_argument("--models-dir", type=Path, help="keep weights here instead of <home>/models")
    ap.add_argument("--port", type=int, default=11434)
    ap.add_argument("--allow", action="append", default=[], help="another peer address to accept (repeatable)")
    ap.add_argument("--label", default="com.cerid.mlx")
    ap.add_argument("--write-env", type=Path, metavar="ENV_FILE", help="append the unset Cerid settings to this .env")
    ap.add_argument("--no-start", action="store_true", help="download and write files, but leave the agent alone")
    ap.add_argument("--no-check", action="store_true", help="skip check.py after start")
    ap.add_argument("--force", action="store_true", help="install a catalog larger than this machine's memory suggests")
    ap.add_argument("--uninstall", action="store_true")
    ap.add_argument("--purge", action="store_true", help="with --uninstall, also remove the install directory")
    ap.add_argument("--wait-minutes", type=float, default=15)
    args = ap.parse_args(argv)
    args.home = args.home.expanduser().resolve()
    if args.models_dir:
        args.models_dir = args.models_dir.expanduser().resolve()

    if args.uninstall:
        uninstall(args)
        return

    mem = memory_gib()
    profile_name = choose_profile(mem, args.profile)
    if profile_name == "standard" and mem < STANDARD_MIN_GIB and not args.force:
        fail(f"the standard catalog wants {STANDARD_MIN_GIB} GiB of memory and this Mac has {mem:.0f}; use --profile compact, or --force")
    if args.heavy and mem < HEAVY_MIN_GIB and not args.force:
        fail(f"--heavy wants {HEAVY_MIN_GIB} GiB of memory and this Mac has {mem:.0f}; pass --force to install it anyway")
    profile = json.loads((STACK / "catalogs" / f"{profile_name}.json").read_text())
    heavy = json.loads((STACK / "catalogs" / "heavy.json").read_text()) if args.heavy else None
    catalog = build_catalog(profile, heavy, args.models_dir)
    catalog_file = args.home / "models.json"
    say(f"Profile: {profile_name} ({mem:.0f} GiB of memory). {profile['description']}")

    owner = port_owner(args.port, args.label)
    if owner and not args.no_start:
        fail(f"port {args.port} is taken by {owner}. Stop it, or install on another port with --port.")

    say("Downloading weights (resumes where a previous run stopped):")
    download(catalog, catalog_file)
    if any(spec.get("tool_format") == "harmony" for spec in catalog["models"].values()):
        install_o200k(args.home)

    text = json.dumps(catalog, indent=2) + "\n"
    if catalog_file.exists() and catalog_file.read_text() != text:
        backup = catalog_file.with_name(f"models.json.{time.strftime('%Y%m%d-%H%M%S')}")
        catalog_file.replace(backup)
        say(f"Previous catalog kept as {backup.name}")
    catalog_file.write_text(text)

    settings = cerid_settings(catalog, args.port)
    if not args.no_start:
        logs = Path.home() / "Library" / "Logs"
        logs.mkdir(parents=True, exist_ok=True)
        log = logs / f"{args.label}.log"
        plist = Path.home() / "Library" / "LaunchAgents" / f"{args.label}.plist"
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text(render_plist(
            (STACK / "launchd" / "cerid-mlx.plist.template").read_text(),
            {
                "LABEL": args.label,
                "PYTHON": str(args.home / ".venv" / "bin" / "python"),
                "HOME_DIR": str(args.home),
                "LOG": str(log),
                "ERR_LOG": str(logs / f"{args.label}.err.log"),
                "PORT": str(args.port),
                "ALLOW": ",".join(dict.fromkeys((*DEFAULT_ALLOW, *args.allow))),
            },
        ))
        restart_agent(f"gui/{os.getuid()}", args.label, plist)
        say(f"Started {args.label}; loading models (log: {log}).")
        version = wait_ready(args.port, log, args.wait_minutes)
        say(f"Serving {version} on port {args.port}.")
        if not args.no_check:
            check = subprocess.run([str(args.home / ".venv" / "bin" / "python"), str(args.home / "check.py"), str(args.port)])
            if check.returncode != 0:
                fail(f"check.py reported failures; the log is {log}")
        probe = probe_from_container(args.port, log)
        say(f"From a container: {probe}")
        if probe.startswith("refused: "):
            peers = probe.split(": ", 1)[1]
            say(f"  The server turned away {peers}. Accept it by running install.sh again with --allow {peers.split(',')[0]}")

    say("")
    say("Cerid settings for this server:")
    for k, v in settings.items():
        say(f"  {k}={v}")
    say("Embeddings stay as they are. Pointing Cerid at nomic-embed-text-v1.5 here")
    say("(EMBEDDINGS_PROVIDER=quenchforge, QUENCHFORGE_EMBED_MODEL=nomic-embed-text-v1.5,")
    say("RERANK_PROVIDER=in-process) changes the vector space: only do it on a new")
    say("knowledge base, or re-embed the existing one.")
    if args.write_env:
        env = args.write_env.expanduser()
        add, differ = env_plan(env.read_text() if env.exists() else "", settings)
        append_env(env, add)
        say(f"{env}: added {', '.join(add) or 'nothing'}.")
        for k, v in differ.items():
            say(f"  {k} is already {v!r}; left unchanged (this server suggests {settings[k]!r}).")
        if add:
            say("Restart Cerid to pick them up: ./scripts/start-cerid.sh")


if __name__ == "__main__":
    main(sys.argv[1:])
