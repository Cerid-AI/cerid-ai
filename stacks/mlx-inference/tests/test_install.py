# Copyright (c) 2026 Cerid AI. All rights reserved.
# SPDX-License-Identifier: FSL-1.1-ALv2
"""install.py's logic, and the shipped catalogs it installs."""

from __future__ import annotations

import json
import plistlib
from pathlib import Path

import install
import pytest
import serve

CATALOGS = Path(install.STACK) / "catalogs"
PROFILES = ("compact", "standard")


def load(name):
    return json.loads((CATALOGS / f"{name}.json").read_text())


def materialise(catalog, catalog_file):
    """Create an empty stand-in for every row's weights, so serve.Catalog can
    load the catalog without downloading anything."""
    for spec in catalog["models"].values():
        path = install.row_path(catalog_file, spec)
        if spec["tier"] == "embed":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"x")
        else:
            path.mkdir(parents=True, exist_ok=True)
    catalog_file.write_text(json.dumps(catalog))


@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("heavy", (False, True))
def test_every_installable_catalog_loads(tmp_path, profile, heavy):
    catalog = install.build_catalog(load(profile), load("heavy") if heavy else None, None)
    catalog_file = tmp_path / "home" / "models.json"
    catalog_file.parent.mkdir()
    materialise(catalog, catalog_file)

    loaded = serve.Catalog(catalog_file)

    assert loaded.resolve("llama3.2:3b") == loaded.by_tier["small"]
    assert all(str(spec["path"]).startswith(str(tmp_path / "home" / "models")) for spec in loaded.models.values())
    assert ("gpt-oss-120b" in loaded.models) is heavy


def test_models_dir_moves_every_row(tmp_path):
    catalog = install.build_catalog(load("standard"), load("heavy"), tmp_path / "weights")
    for spec in catalog["models"].values():
        assert Path(spec["path"]).is_relative_to(tmp_path / "weights")
    assert catalog["models"]["gemma-4-26b-a4b"]["path"] == str(tmp_path / "weights" / "mlx-community" / "gemma-4-26B-A4B-it-qat-4bit")


def test_heavy_brings_its_tier_limits():
    catalog = install.build_catalog(load("compact"), load("heavy"), None)
    assert catalog["tiers"]["heavy"]["max_active"] == 1
    assert catalog["tiers"]["default"] == load("compact")["tiers"]["default"]


def test_a_heavy_row_cannot_replace_a_profile_row():
    profile = load("compact")
    clash = {"models": {"qwen3.5-9b-instruct": load("heavy")["models"]["gpt-oss-120b"]}}
    with pytest.raises(SystemExit, match="already in the profile"):
        install.build_catalog(profile, clash, None)


@pytest.mark.parametrize("name", (*PROFILES, "heavy"))
def test_every_row_says_where_its_weights_come_from(name):
    for row, spec in load(name)["models"].items():
        assert spec["path"].startswith("models/"), row
        assert "/" in spec["source"]["repo"], row
        if spec["tier"] == "embed":
            assert spec["source"]["file"] == Path(spec["path"]).name, row
            assert len(spec["source"]["sha256"]) == 64, row


def test_the_embedder_is_the_gguf_the_reference_vectors_came_from():
    # check.py's parity test and every index built from quenchforge assume this file.
    for name in PROFILES:
        row = load(name)["models"]["nomic-embed-text-v1.5"]
        assert row["source"]["sha256"] == "3e24342164b3d94991ba9692fdc0dd08e3fd7362e0aacc396a9a5c54a544c3b7"  # pragma: allowlist secret
        assert row["pooling"] == "cls"


@pytest.mark.parametrize(
    ("gib", "want"), ((16, "compact"), (47.9, "compact"), (48, "standard"), (256, "standard"))
)
def test_profile_by_memory(gib, want):
    assert install.choose_profile(gib, "auto") == want


def test_too_little_memory_is_refused():
    with pytest.raises(SystemExit, match="16 GiB"):
        install.choose_profile(8, "auto")


def test_an_explicit_profile_wins():
    assert install.choose_profile(256, "compact") == "compact"


def test_cerid_settings_name_the_default_and_small_rows():
    catalog = install.build_catalog(load("compact"), None, None)
    assert install.cerid_settings(catalog, 11434) == {
        "OLLAMA_ENABLED": "true",
        "INTERNAL_LLM_PROVIDER": "ollama",
        "OLLAMA_URL": "http://host.docker.internal:11434",
        "INTERNAL_LLM_MODEL": "qwen3.5-9b-instruct",
        "OLLAMA_DEFAULT_MODEL": "qwen3.5-4b-instruct",
    }


def test_env_plan_never_changes_a_key_that_is_set():
    existing = 'OLLAMA_URL=http://host.docker.internal:11434\nexport INTERNAL_LLM_PROVIDER="openrouter"\n# OLLAMA_DEFAULT_MODEL=x\n'
    wanted = {
        "OLLAMA_URL": "http://host.docker.internal:11434",
        "INTERNAL_LLM_PROVIDER": "ollama",
        "OLLAMA_DEFAULT_MODEL": "qwen3.5-4b-instruct",
    }
    add, differ = install.env_plan(existing, wanted)
    assert add == {"OLLAMA_DEFAULT_MODEL": "qwen3.5-4b-instruct"}
    assert differ == {"INTERNAL_LLM_PROVIDER": "openrouter"}


def test_append_env_only_appends(tmp_path):
    env = tmp_path / ".env"
    env.write_text("KEEP=1\nNO_NEWLINE=2")
    install.append_env(env, {"A": "x"})
    text = env.read_text()
    assert text.startswith("KEEP=1\nNO_NEWLINE=2\n")
    assert install.env_keys(text) == {"KEEP": "1", "NO_NEWLINE": "2", "A": "x"}


def test_append_env_with_nothing_to_add_leaves_the_file_alone(tmp_path):
    env = tmp_path / ".env"
    env.write_text("KEEP=1")
    install.append_env(env, {})
    assert env.read_text() == "KEEP=1"


def test_plist_renders_and_parses(tmp_path):
    template = (Path(install.STACK) / "launchd" / "cerid-mlx.plist.template").read_text()
    home = "/Users/a & b/.local/share/cerid-mlx"
    text = install.render_plist(template, {
        "LABEL": "com.cerid.mlx",
        "PYTHON": f"{home}/.venv/bin/python",
        "HOME_DIR": home,
        "LOG": "/tmp/l.log",
        "ERR_LOG": "/tmp/e.log",
        "PORT": "11434",
        "ALLOW": "127.0.0.1,::1,192.168.5.1",
    })
    plist = plistlib.loads(text.encode())
    assert plist["Label"] == "com.cerid.mlx"
    assert plist["ProgramArguments"] == [f"{home}/.venv/bin/python", "-u", f"{home}/serve.py"]
    assert plist["EnvironmentVariables"]["CERID_MLX_PORT"] == "11434"


def test_plist_with_an_unfilled_placeholder_is_refused():
    with pytest.raises(SystemExit, match="LABEL"):
        install.render_plist("<string>{{LABEL}}</string>", {})


def _ports(monkeypatch, listeners, label_pid, version="cerid-mlx-0123456789ab"):
    monkeypatch.setattr(install, "listener_pids", lambda port: set(listeners))
    monkeypatch.setattr(install, "label_pid", lambda label: label_pid)
    monkeypatch.setattr(install, "get", lambda url, timeout=3: (200, json.dumps({"version": version})))


def test_a_free_port_is_free(monkeypatch):
    _ports(monkeypatch, [], None)
    assert install.port_owner(11434, "com.cerid.mlx") is None


def test_the_labels_own_process_may_be_replaced(monkeypatch):
    _ports(monkeypatch, [4242], 4242)
    assert install.port_owner(11434, "com.cerid.mlx") is None


def test_a_loaded_label_serving_another_port_does_not_own_this_one(monkeypatch):
    # The label is loaded (pid 4242) but 11434 belongs to a different server.
    _ports(monkeypatch, [50323], 4242)
    assert "another cerid-mlx server" in install.port_owner(11434, "com.cerid.mlx")


def test_ollama_on_the_port_is_named(monkeypatch):
    _ports(monkeypatch, [777], None, version="0.12.3")
    assert "Ollama" in install.port_owner(11434, "com.cerid.mlx")


def test_refused_peers_are_read_from_the_server_log():
    log = (
        '127.0.0.1 "GET /api/version HTTP/1.1" 200 -\n'
        '192.168.65.1 "GET /api/version HTTP/1.1" 403 -\n'
        '192.168.65.1 "GET /api/tags HTTP/1.1" 403 -\n'
        '10.0.0.5 "POST /api/chat HTTP/1.1" 404 -\n'
    )
    assert install.refused_peers(log) == ["192.168.65.1"]


def test_the_server_logs_a_refusal_in_the_shape_install_reads(tmp_path, capsys):
    # The probe finds the peer to allow by reading serve.py's own log line.
    handler = serve.Handler.__new__(serve.Handler)
    handler.client_address = ("192.168.65.1", 5000)
    handler.requestline = "GET /api/version HTTP/1.1"
    handler.log_request(403)
    assert install.refused_peers(capsys.readouterr().out) == ["192.168.65.1"]
