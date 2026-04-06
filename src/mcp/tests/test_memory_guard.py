"""Tests for cgroup-aware memory guard in verification streaming."""
import asyncio
from unittest.mock import patch

import pytest


def test_container_memory_available_returns_none_outside_container():
    """When cgroup files don't exist, return None (no-op guard)."""
    from core.agents.hallucination.streaming import _container_memory_available_mb
    # On host (no cgroup files), should return None
    result = _container_memory_available_mb()
    # Could be None (no cgroup) or a float (if running in container)
    assert result is None or isinstance(result, float)


def test_container_memory_available_parses_cgroup_files(tmp_path):
    """When cgroup files exist, correctly compute available MB."""
    from core.agents.hallucination.streaming import _container_memory_available_mb
    max_file = tmp_path / "memory.max"
    current_file = tmp_path / "memory.current"

    # 4GB limit, 2.5GB used = 1.5GB available = 1536 MB
    max_file.write_text("4294967296\n")  # 4 * 1024^3
    current_file.write_text("2684354560\n")  # 2.5 * 1024^3

    with patch("core.agents.hallucination.streaming._CGROUP_MEMORY_MAX", max_file), \
         patch("core.agents.hallucination.streaming._CGROUP_MEMORY_CURRENT", current_file):
        result = _container_memory_available_mb()

    assert result is not None
    assert abs(result - 1536.0) < 1.0  # ~1536 MB


def test_container_memory_available_returns_none_for_unlimited(tmp_path):
    """When cgroup memory.max is 'max' (no limit), return None."""
    from core.agents.hallucination.streaming import _container_memory_available_mb
    max_file = tmp_path / "memory.max"
    current_file = tmp_path / "memory.current"

    max_file.write_text("max\n")
    current_file.write_text("1073741824\n")

    with patch("core.agents.hallucination.streaming._CGROUP_MEMORY_MAX", max_file), \
         patch("core.agents.hallucination.streaming._CGROUP_MEMORY_CURRENT", current_file):
        result = _container_memory_available_mb()

    assert result is None


@pytest.mark.asyncio
async def test_wait_for_memory_noop_outside_container():
    """_wait_for_memory should return immediately when not in a container."""
    from core.agents.hallucination.streaming import _wait_for_memory
    with patch("core.agents.hallucination.streaming._container_memory_available_mb", return_value=None):
        # Should not block
        await asyncio.wait_for(_wait_for_memory(512, "test"), timeout=1.0)


@pytest.mark.asyncio
async def test_wait_for_memory_blocks_when_low():
    """_wait_for_memory should block when available memory is below floor."""
    import core.agents.hallucination.streaming as streaming_mod
    from core.agents.hallucination.streaming import _wait_for_memory
    call_count = 0

    def mock_available():
        nonlocal call_count
        call_count += 1
        # First 2 calls: low memory. Third call: enough memory.
        return 256.0 if call_count <= 2 else 1024.0

    async def fast_sleep(duration):
        pass  # Skip actual sleep in tests

    with patch("core.agents.hallucination.streaming._container_memory_available_mb", side_effect=mock_available), \
         patch.object(streaming_mod.asyncio, "sleep", fast_sleep):
        await asyncio.wait_for(_wait_for_memory(512, "test"), timeout=5.0)

    assert call_count == 3  # 2 low + 1 sufficient
