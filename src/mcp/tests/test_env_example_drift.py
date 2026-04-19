# src/mcp/tests/test_env_example_drift.py
"""CI-visible regression test: .env.example must match settings.py."""
import subprocess
import sys
from pathlib import Path


def test_env_example_is_in_sync():
    repo_root = Path(__file__).resolve().parents[3]
    script = repo_root / "scripts" / "gen_env_example.py"
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
