# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2

"""Host-side probes must survive the LAN gate they now run behind.

`/health` and `/health/*` stopped being unauthenticated once the server binds
off loopback — they carry the pack-registry path, the KB domain names, the
swallowed-error counters and the internal inference URL. The probes that ship
with the product were written against the old, open behaviour: the startup
script, the macOS integration installer and the beta harness all fetch
`/health` with no credential, so on exactly the deployment the gate was written
for (LAN mode) they report the stack as down.

Two grades of caller, two fixes:
  * pure liveness ("is it up?") targets `/health/ping`, which is
    unconditionally exempt because the Docker HEALTHCHECK cannot present a
    header either;
  * payload consumers (the beta assertions, the Pro-feature CI gate) resolve
    `CERID_API_KEY` — from the environment, falling back to the operator .env
    the way the startup script already reads single keys — and send it.

The first two tests drive the REAL app under a real uvicorn in LAN mode, so the
exempt/gated split is a property of the shipped server rather than of a stub.
"""

from __future__ import annotations

import re
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

MCP_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = MCP_ROOT.parents[1]

LAN_API_KEY = "lan-probe-key-abc123"  # pragma: allowlist secret

# Every host-side caller of the health surface that ships in the repo.
LIVENESS_CALLERS = (
    "scripts/start-cerid.sh",
    "scripts/install-macos-integration.sh",
    "tests/beta/run.sh",
)
PAYLOAD_CALLERS = (
    "tests/beta/smoke.sh",
    "tests/beta/security.sh",
    "tests/beta/performance.sh",
    "scripts/lint-pro-feature-health.py",
)

# The health path a caller actually requests, in any of the shapes these
# callers build: a literal URL, an interpolated one ("${CERID_API}/health"),
# or a variable holding either.
_HEALTH_PATH = re.compile(r"(?<![A-Za-z_.])(/health[A-Za-z0-9/_-]*)")
_PROBE_MARKERS = ("curl", "urlopen", "wait_for_service", "check_http", "HEALTH_URL", "health_url")


def _health_paths(rel: str) -> list[str]:
    text = (REPO_ROOT / rel).read_text()
    return [
        m.group(1)
        for line in text.splitlines()
        if not line.lstrip().startswith(("#", "//"))
        and any(marker in line for marker in _PROBE_MARKERS)
        for m in _HEALTH_PATH.finditer(line)
    ]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def lan_server():
    """The real app, real uvicorn, LAN-declared bind, key enforced."""
    port = _free_port()
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "app.main:app",
            "--host", "127.0.0.1", "--port", str(port), "--lifespan", "on",
        ],
        cwd=MCP_ROOT,
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(Path.home()),
            "PYTHONUNBUFFERED": "1",
            "CERID_BIND_ADDR": "0.0.0.0",
            "CERID_API_KEY": LAN_API_KEY,
        },
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(0.25)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                break
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            pytest.fail(f"LAN server exited before binding:\n{out[-3000:]}")
        time.sleep(0.25)
    else:  # pragma: no cover - CI slowness
        proc.kill()
        pytest.fail("LAN server did not bind within the budget")
    try:
        yield f"http://127.0.0.1:{port}"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:  # pragma: no cover - defensive
            proc.kill()


def _get(url: str, key: str | None = None) -> int:
    req = urllib.request.Request(url)
    if key:
        req.add_header("X-API-Key", key)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code


def test_liveness_probe_answers_without_a_key(lan_server):
    """What the rewritten probes target must actually be reachable."""
    assert _get(f"{lan_server}/health/ping") == 200


def test_informative_health_is_gated_in_lan_mode(lan_server):
    """The premise: the old probe target now 401s for the callers below."""
    assert _get(f"{lan_server}/health") == 401
    assert _get(f"{lan_server}/health", key=LAN_API_KEY) != 401


@pytest.mark.parametrize("rel", LIVENESS_CALLERS)
def test_liveness_callers_probe_an_exempt_path(rel):
    from app.middleware.auth import EXEMPT_PATHS

    paths = _health_paths(rel)
    assert paths, f"{rel} no longer probes the health surface — update this test"
    for path in paths:
        assert path in EXEMPT_PATHS, (
            f"{rel} probes {path}, which requires X-API-Key off loopback: the "
            "probe reports the stack as down in LAN mode"
        )


@pytest.mark.parametrize("rel", PAYLOAD_CALLERS)
def test_payload_callers_resolve_the_operator_key(rel):
    """A payload consumer cannot use /health/ping — it must carry the key.

    The key has to be resolvable from the operator .env: `make
    pro-feature-health` and a standalone `tests/beta/smoke.sh` both run in a
    shell that never exported it.
    """
    code = [
        line for line in (REPO_ROOT / rel).read_text().splitlines()
        if not line.lstrip().startswith(("#", "//"))
    ]
    assert any("CERID_API_KEY" in line for line in code), (
        f"{rel} never resolves the API key"
    )
    assert any("CERID_API_KEY" in line and ".env" in line for line in code), (
        f"{rel} only reads CERID_API_KEY from the environment, so it fails "
        "against a keyed stack whenever the caller did not export it"
    )


def _run_pro_feature_gate(base: str, cwd: Path) -> str:
    proc = subprocess.run(
        [
            sys.executable, str(REPO_ROOT / "scripts" / "lint-pro-feature-health.py"),
            "--base", base,
        ],
        cwd=cwd,
        env={"PATH": "/usr/bin:/bin", "HOME": str(cwd)},
        capture_output=True, text=True, timeout=120,
    )
    return proc.stdout + proc.stderr


def test_pro_feature_gate_authenticates_from_the_operator_env(lan_server, tmp_path):
    """End to end against the real gated server: the key is only in .env.

    `make pro-feature-health` runs in a shell that never exported it, so
    "environment only" means the CI gate 401s against every keyed stack.
    """
    (tmp_path / ".env").write_text(f"CERID_API_KEY={LAN_API_KEY}\n")
    out = _run_pro_feature_gate(lan_server, tmp_path)
    assert "401" not in out, out
    assert "requires an API key" not in out, out


def test_pro_feature_gate_names_the_auth_failure(lan_server, tmp_path):
    """With no key anywhere, the gate must not blame reachability.

    A 401 landed in the `cannot reach ...` branch, which reads as a dead stack
    and sends the operator after the wrong problem.
    """
    out = _run_pro_feature_gate(lan_server, tmp_path)
    assert "requires an API key" in out, out


@pytest.mark.parametrize("rel", ["tests/beta/security.sh", "tests/beta/performance.sh"])
def test_beta_harness_forwards_the_key_to_its_raw_probes(rel):
    """These two curl /health directly instead of through assert.sh's helpers,
    so assert.sh's header injection never reaches them."""
    probes = [
        line for line in (REPO_ROOT / rel).read_text().splitlines()
        if "curl" in line and "/health" in line and not line.lstrip().startswith("#")
    ]
    assert probes, f"{rel} no longer probes /health — update this test"
    for line in probes:
        assert any(tok in line for tok in ("_CERID_AUTH_ARGS", "KEY_HDR", "X-API-Key")), (
            f"{rel}: raw probe sends no key and 401s in LAN mode:\n{line}"
        )


def test_desktop_probe_comment_is_not_false():
    """`connection.ts` justified its two-probe design with a claim the gate
    invalidated: that /health is "deliberately unauthenticated". The code is
    still right — it treats anything under 500 as "reached" — but the reasoning
    a future reader would rely on was not."""
    connection = REPO_ROOT / "packages/desktop/src/main/connection.ts"
    if not connection.exists():
        pytest.skip("desktop app not present (public checkout)")
    text = connection.read_text()
    assert "deliberately unauthenticated" not in text, text[:0] or (
        "connection.ts still claims /health is unauthenticated"
    )
    assert "/health/ping" in text, (
        "the corrected comment should name the paths that do stay open"
    )
