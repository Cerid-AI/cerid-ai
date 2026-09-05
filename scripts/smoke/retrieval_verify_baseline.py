# Copyright (c) 2026 Justin Michaels. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""Read-only capture of retrieval / verify / Quenchforge pressure.

Personal stack only (:8888). Never raises timeouts, never loads a QF slot.

Usage (host, repo root):
    python3 scripts/smoke/retrieval_verify_baseline.py
    python3 scripts/smoke/retrieval_verify_baseline.py --burst-query "what is in my knowledge base"
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

MCP = os.environ.get("CERID_MCP_URL", "http://127.0.0.1:8888")
QF = os.environ.get("QUENCHFORGE_URL", "http://127.0.0.1:11434")
ARTIFACT_DIR = Path("tasks/artifacts")


def classify_qf_rerank_response(*, status: int, retry_after: str | None, body: str) -> str:
    if status == 200:
        return "ok"
    if status == 503 and retry_after:
        return "backoff"
    if status == 503 and "no " in body.lower() and "slot configured" in body.lower():
        return "no_slot"
    if status == 503:
        return "unavailable_unclassified"
    return f"http_{status}"


def build_payload(**parts: Any) -> dict[str, Any]:
    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "git_head": parts.get("git_head", ""),
        "mcp_health": parts.get("health") or {},
        "queue_depth": parts.get("queue_depth") or {},
        "verification_rates": parts.get("verification_rates") or {},
        "qf_rerank": parts.get("qf_rerank") or {},
        "cgroup": parts.get("cgroup") or {},
        "log_counts": parts.get("log_counts") or {},
        "burst": parts.get("burst"),
    }


def _get_json(url: str, timeout: float = 5.0, *, auth: bool = False) -> tuple[int, dict, dict]:
    headers = {"Accept": "application/json"}
    if auth:
        key = os.environ.get("CERID_API_KEY", "")
        if key:
            headers["X-API-Key"] = key
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode()
            return resp.status, json.loads(raw) if raw else {}, dict(resp.headers)
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() if exc.fp else ""
        try:
            body = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body = {"raw": raw}
        return exc.code, body, dict(exc.headers)
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        return 0, {"error": str(exc)}, {}


def probe_qf_rerank() -> dict[str, Any]:
    body = json.dumps({"model": "bge-reranker-v2-m3", "query": "ping", "documents": ["pong"]}).encode()
    req = urllib.request.Request(
        f"{QF}/v1/rerank", data=body,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=5.0) as resp:
            raw = resp.read().decode()
            cls = classify_qf_rerank_response(
                status=resp.status, retry_after=resp.headers.get("Retry-After"), body=raw,
            )
            return {"class": cls, "status": resp.status, "retry_after": resp.headers.get("Retry-After")}
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode() if exc.fp else ""
        return {
            "class": classify_qf_rerank_response(
                status=exc.code, retry_after=exc.headers.get("Retry-After"), body=raw,
            ),
            "status": exc.code,
            "retry_after": exc.headers.get("Retry-After"),
            "body_excerpt": raw[:200],
        }
    except (urllib.error.URLError, TimeoutError) as exc:
        return {"class": "unreachable", "error": str(exc)}


def cgroup_mem() -> dict[str, Any]:
    try:
        out = subprocess.check_output(
            ["docker", "inspect", "ai-companion-mcp", "--format", "{{.Id}}"],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        return {"error": str(exc), "current_bytes": None, "max_bytes": None}
    # docker inspect Id is the full container id; cgroup v2 path on Docker Desktop / linux.
    candidates = [
        Path(f"/sys/fs/cgroup/docker/{out}/memory.current"),
        Path(f"/sys/fs/cgroup/system.slice/docker-{out}.scope/memory.current"),
    ]
    current = max_b = None
    for cand in candidates:
        if cand.exists():
            try:
                current = int(cand.read_text().strip())
                max_path = cand.with_name("memory.max")
                if max_path.exists():
                    raw_max = max_path.read_text().strip()
                    max_b = None if raw_max == "max" else int(raw_max)
            except (OSError, ValueError):
                pass
            break
    # Fallback: docker stats --no-stream
    try:
        stats = subprocess.check_output(
            ["docker", "stats", "ai-companion-mcp", "--no-stream", "--format", "{{.MemUsage}}"],
            text=True,
        ).strip()
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        stats = f"error: {exc}"
    return {"docker_stats": stats, "container_id": out[:12], "current_bytes": current, "max_bytes": max_b}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--burst-query", default="")
    parser.add_argument("--out", default="")
    args = parser.parse_args()

    health_status, health, _ = _get_json(f"{MCP}/health")
    _, queue_depth, _ = _get_json(f"{MCP}/observability/queue-depth", auth=True)
    _, verification_rates, _ = _get_json(f"{MCP}/observability/verification-rates", auth=True)
    qf = probe_qf_rerank()
    try:
        git_head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    except subprocess.CalledProcessError:
        git_head = ""

    burst = None
    if args.burst_query:
        t0 = time.perf_counter()
        # POST /agent/query once (the harness itself must never dual-fire)
        payload = json.dumps({"query": args.burst_query, "top_k": 5, "use_reranking": True}).encode()
        req = urllib.request.Request(
            f"{MCP}/agent/query", data=payload,
            headers={"Content-Type": "application/json", "X-API-Key": os.environ.get("CERID_API_KEY", "")},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as resp:
                raw = json.loads(resp.read().decode())
                burst = {
                    "http_status": resp.status,
                    "elapsed_s": round(time.perf_counter() - t0, 3),
                    "budget_exceeded": bool(raw.get("budget_exceeded") or raw.get("degraded_reason")),
                    "degraded_reason": raw.get("degraded_reason") or raw.get("reason"),
                    "total_results": raw.get("total_results"),
                }
        except Exception as exc:  # noqa: BLE001 — capture is the product
            burst = {"error": str(exc), "elapsed_s": round(time.perf_counter() - t0, 3)}

    payload = build_payload(
        health=health, queue_depth=queue_depth, verification_rates=verification_rates,
        qf_rerank=qf, cgroup=cgroup_mem(), log_counts={}, git_head=git_head, burst=burst,
    )
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    out = Path(args.out) if args.out else ARTIFACT_DIR / f"retrieval-verify-{stamp}.json"
    out.write_text(json.dumps(payload, indent=2) + "\n")
    print(out)
    print(json.dumps({"qf_class": qf.get("class"), "kb": queue_depth.get("kb"), "burst": burst}, indent=2))
    return 0 if health_status == 200 else 2


if __name__ == "__main__":
    raise SystemExit(main())
