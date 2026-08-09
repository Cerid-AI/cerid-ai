"""CH5: cross-model claim verification runs on OpenRouter (call_llm_raw, not
local inference). The old hardcoded 12s per-claim cap was too tight for cloud
latency under the verify semaphore, so claims timed out and were regenerated.
The cap is now an env-tunable, more generous config value; the global
STREAMING_TOTAL_TIMEOUT still backstops total runtime."""
import config
from core.agents.hallucination.streaming import _per_claim_base_timeout


def test_cross_model_uses_config_value(monkeypatch):
    monkeypatch.setattr(config, "STREAMING_CROSS_MODEL_CLAIM_TIMEOUT", 18.0, raising=False)
    assert _per_claim_base_timeout(expert_mode=False, needs_web=False) == 18.0


def test_web_claims_use_web_timeout(monkeypatch):
    monkeypatch.setattr(config, "STREAMING_WEB_CLAIM_TIMEOUT", 25.0, raising=False)
    assert _per_claim_base_timeout(expert_mode=False, needs_web=True) == 25.0


def test_expert_mode_uses_expert_timeout(monkeypatch):
    monkeypatch.setattr(config, "STREAMING_EXPERT_CLAIM_TIMEOUT", 30.0, raising=False)
    assert _per_claim_base_timeout(expert_mode=True, needs_web=False) == 30.0


def test_cross_model_cap_more_generous_than_old_12s():
    # Regression guard: the tight 12s cap that caused the CH5 timeouts is gone.
    assert _per_claim_base_timeout(expert_mode=False, needs_web=False) >= 15.0
