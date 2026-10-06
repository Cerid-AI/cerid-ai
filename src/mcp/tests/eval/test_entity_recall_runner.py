"""The recall runner's scoring protocol, driven with a fake extractor.

Covers what the live runner must do before its number can be trusted: three
attempts recorded and the best taken, spread printed beside the mean, a
``non_discriminating`` flag when every fixture scores the same, a loaded host
skipped with a stated reason, and a floor miss exiting non-zero.
"""

import asyncio
import json
import pathlib
from types import SimpleNamespace

import pytest

from tests.eval import entity_recall_runner as runner


def _annot(tmp_path: pathlib.Path, name: str, expected: list[str], forbidden: list[str] | None = None):
    fixtures = tmp_path / "fixtures"
    (fixtures / "entities").mkdir(parents=True, exist_ok=True)
    (fixtures / f"{name}.md").write_text(" ".join(expected) + " filler text")
    annot = fixtures / "entities" / f"{name}.json"
    annot.write_text(json.dumps({
        "fixture": f"{name}.md", "expected": expected, "forbidden": forbidden or [],
    }))
    return annot


def _extractor_from(sequences: dict[str, list[list[str]]]):
    """Fake extractor: per fixture text, successive calls return successive name lists."""
    calls: dict[str, int] = {}

    async def extract(text: str):
        key = next(k for k in sequences if k in text)
        i = calls.get(key, 0)
        calls[key] = i + 1
        return [SimpleNamespace(name=n) for n in sequences[key][i]]

    extract.calls = calls  # type: ignore[attr-defined]
    return extract


IDLE = runner.LoadReading(loaded=False, reason="idle", version_ms=1.0, probe_ms=150.0)
LOADED = runner.LoadReading(
    loaded=True, reason="probe 7400ms > 3000ms", version_ms=1.0, probe_ms=7400.0,
)


@pytest.fixture
def fixtures_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "FIXTURES", tmp_path / "fixtures")
    return tmp_path


def test_run_one_records_three_attempts_and_takes_the_best(fixtures_dir):
    annot = _annot(fixtures_dir, "alpha", ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"])
    extract = _extractor_from({"Alpha": [
        ["alpha", "beta", "gamma", "delta", "epsilon"],  # 1.0
        ["alpha", "beta", "gamma"],  # 0.6
        ["alpha"],  # 0.2
    ]})
    result = asyncio.run(runner.run_one(annot, extractor=extract))
    assert extract.calls["Alpha"] == 3
    assert result.attempts == [1.0, 0.6, 0.2]
    assert result.best == 1.0
    assert result.spread == pytest.approx(0.8)
    assert result.passed


def test_extraction_failure_is_one_attempt_not_the_verdict(fixtures_dir):
    annot = _annot(fixtures_dir, "alpha", ["Alpha", "Beta"])
    state = {"n": 0}

    async def extract(text: str):
        state["n"] += 1
        if state["n"] == 1:
            raise runner.ExtractionFailed("malformed JSON")
        return [SimpleNamespace(name="alpha"), SimpleNamespace(name="beta")]

    result = asyncio.run(runner.run_one(annot, extractor=extract))
    assert result.attempts == [None, 1.0, 1.0]
    assert result.best == 1.0 and result.passed


def test_summary_reports_spread_and_flags_identical_scores():
    flat = runner.summarize([1.0, 1.0, 1.0])
    assert flat["mean"] == 1.0 and flat["spread"] == 0.0
    assert flat["non_discriminating"] is True
    varied = runner.summarize([1.0, 0.6, 0.8])
    assert varied["mean"] == pytest.approx(0.8)
    assert varied["spread"] == pytest.approx(0.4)
    assert varied["non_discriminating"] is False


def test_loaded_host_skips_with_reason_and_calls_no_extractor(fixtures_dir, capsys):
    _annot(fixtures_dir, "alpha", ["Alpha", "Beta"])
    extract = _extractor_from({"Alpha": [["alpha", "beta"]] * 3})
    code = asyncio.run(runner.main(extractor=extract, load_probe=lambda: LOADED))
    out = capsys.readouterr().out
    assert code == 0
    assert "recall SKIPPED" in out and "probe 7400ms > 3000ms" in out
    assert extract.calls == {}


def test_floor_miss_exits_non_zero_and_prints_all_attempts(fixtures_dir, capsys):
    _annot(fixtures_dir, "alpha", ["Alpha", "Beta", "Gamma", "Delta", "Epsilon"])
    _annot(fixtures_dir, "zeta", ["Zeta", "Eta"])
    extract = _extractor_from({
        "Alpha": [["alpha"], ["alpha", "beta"], ["alpha"]],  # best 0.4 < floor
        "Zeta": [["zeta", "eta"]] * 3,
    })
    code = asyncio.run(runner.main(extractor=extract, load_probe=lambda: IDLE))
    out = capsys.readouterr().out
    assert code == 1
    assert "recall[alpha.md] = 0.40 attempts=[0.20, 0.40, 0.20] spread=0.20" in out
    assert "[FAIL]" in out and "[PASS]" in out
    assert "AGGREGATE n=2 mean=0.70" in out and "non_discriminating=False" in out


def test_forbidden_hit_on_the_best_attempt_fails(fixtures_dir):
    annot = _annot(fixtures_dir, "alpha", ["Alpha", "Beta"], forbidden=["# heading"])
    extract = _extractor_from({"Alpha": [["alpha", "beta", "# heading"]] * 3})
    result = asyncio.run(runner.run_one(annot, extractor=extract))
    assert result.best == 1.0
    assert result.forbidden == ["# heading"]
    assert not result.passed


def test_classify_load_thresholds():
    assert runner.classify_load(1.0, 150.0, probe_max_ms=3000).loaded is False
    slow = runner.classify_load(1.0, 7400.0, probe_max_ms=3000)
    assert slow.loaded is True and "7400ms > 3000ms" in slow.reason
    down = runner.classify_load(None, None, probe_max_ms=3000, error="connection refused")
    assert down.loaded is True and "unreachable" in down.reason


def test_measure_load_over_the_wire():
    import httpx

    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        if request.url.path == "/api/chat":
            assert json.loads(request.content)["options"]["num_predict"] == 1
        return httpx.Response(200, json={"ok": True})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        reading = runner.measure_load("http://mlx:11434", probe_max_ms=3000, model="m", client=client)
    assert reading.loaded is False
    assert seen == ["/api/version", "/api/chat", "/api/chat"]

    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with httpx.Client(transport=httpx.MockTransport(refuse)) as client:
        reading = runner.measure_load("http://mlx:11434", probe_max_ms=3000, model="m", client=client)
    assert reading.loaded is True and "unreachable" in reading.reason and "mlx:11434" in reading.reason
