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
    """When cgroup files exist, correctly compute available MB (headroom only, no stat)."""
    from core.agents.hallucination.streaming import _container_memory_available_mb
    max_file = tmp_path / "memory.max"
    current_file = tmp_path / "memory.current"
    stat_file = tmp_path / "memory.stat"  # deliberately absent

    # 4GB limit, 2.5GB used = 1.5GB headroom = 1536 MB
    max_file.write_text("4294967296\n")  # 4 * 1024^3
    current_file.write_text("2684354560\n")  # 2.5 * 1024^3

    with patch("core.agents.hallucination.streaming._CGROUP_MEMORY_MAX", max_file), \
         patch("core.agents.hallucination.streaming._CGROUP_MEMORY_CURRENT", current_file), \
         patch("core.agents.hallucination.streaming._CGROUP_MEMORY_STAT", stat_file):
        result = _container_memory_available_mb()

    assert result is not None
    assert abs(result - 1536.0) < 1.0  # headroom-only when stat is missing


def test_container_memory_available_includes_reclaimable(tmp_path):
    """Reclaimable memory (page cache + slab) is added to raw headroom."""
    from core.agents.hallucination.streaming import _container_memory_available_mb
    max_file = tmp_path / "memory.max"
    current_file = tmp_path / "memory.current"
    stat_file = tmp_path / "memory.stat"

    # 6GB limit, 5.5GB used = ~512MB headroom; 200MB file cache + 50MB slab = 250MB reclaimable
    # Expected available ≈ 762MB — demonstrates that a container near its cap
    # with cached pages is NOT actually out of memory.
    max_file.write_text("6442450944\n")        # 6 * 1024^3
    current_file.write_text("5905580032\n")    # ~5.5 * 1024^3
    stat_file.write_text(
        "anon 5600000000\n"
        "file 209715200\n"            # 200 MB reclaimable page cache
        "slab_reclaimable 52428800\n"  # 50 MB reclaimable slab
        "kernel 24539136\n"
        "sock 8192\n",
    )

    with patch("core.agents.hallucination.streaming._CGROUP_MEMORY_MAX", max_file), \
         patch("core.agents.hallucination.streaming._CGROUP_MEMORY_CURRENT", current_file), \
         patch("core.agents.hallucination.streaming._CGROUP_MEMORY_STAT", stat_file):
        result = _container_memory_available_mb()

    assert result is not None
    # headroom = (6442450944 - 5905580032) / MB ≈ 512; reclaimable = 250; total ≈ 762
    assert 750.0 < result < 775.0


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
    """_wait_for_memory should block (up to the bounded wait) until memory clears."""
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


@pytest.mark.asyncio
async def test_wait_for_memory_fails_open_after_bounded_wait():
    """When the floor never clears, _wait_for_memory exits (fail-open) — never deadlocks.

    Regression guard for the beta-test incident where steady-state memory
    legitimately sat below the floor and verification was paused indefinitely.
    """
    import core.agents.hallucination.streaming as streaming_mod
    from core.agents.hallucination.streaming import _wait_for_memory

    # Fake monotonic clock that advances 1.5s per call so we exceed the 5s
    # bounded-wait budget after a small number of iterations.
    clock = [0.0]

    def fake_monotonic():
        clock[0] += 1.5
        return clock[0]

    async def fast_sleep(duration):
        pass

    with patch("core.agents.hallucination.streaming._container_memory_available_mb", return_value=16.0), \
         patch.object(streaming_mod.time, "monotonic", fake_monotonic), \
         patch.object(streaming_mod.asyncio, "sleep", fast_sleep):
        # Floor is 512MB, available permanently reports 16MB — must still exit.
        await asyncio.wait_for(_wait_for_memory(512, "regression"), timeout=2.0)
