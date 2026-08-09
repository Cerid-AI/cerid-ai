# src/mcp/tests/test_env_example_drift.py
"""CI-visible regression test: .env.example must match settings.py."""
import subprocess
import sys

import pytest

from ._helpers import scripts_dir


def test_env_example_is_in_sync():
    # Skip in environments where the repo-root ``scripts/`` dir isn't
    # reachable (e.g. the ai-companion-mcp container with only
    # ``src/mcp`` bind-mounted at ``/app``). CI's full checkout always
    # has it, so the drift gate still fires there.
    sd = scripts_dir()
    if sd is None:
        pytest.skip("scripts/ dir not reachable from test env (repo-root not mounted)")
    script = sd / "gen_env_example.py"
    result = subprocess.run(
        [sys.executable, str(script), "--check"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f".env.example is out of sync with settings.py. "
        f"Regenerate with: python {script}\n\n"
        f"stderr:\n{result.stderr}"
    )
