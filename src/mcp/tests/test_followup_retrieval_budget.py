"""CH4: follow-up retrieval stays within the wall-clock budget on CPU inference
without losing coherence.

Two levers, both non-arbitrary:
  * `_prioritize_domains` ranks the all-domain follow-up fan-out most-likely-first
    (cheap taxonomy lexical match) and caps the *tail* — so a capped/partial
    retrieval keeps the most-relevant domains (coherent), never an arbitrary set.
  * `_followup_retrieval_top_k` trims per-domain candidate depth on follow-ups.
"""
import config
from core.agents.query_agent import _followup_retrieval_top_k, _prioritize_domains

_MSGS = [{"role": "user", "content": "and what about last quarter?"}]


# --- domain prioritization (coherence on partial/capped retrieval) ----------

def test_ranks_matching_domain_first():
    # "investments"/"budgets" are finance sub-categories; "python" is coding.
    ranked = _prioritize_domains("how are my investments and budgets doing", ["coding", "finance", "personal"], 0)
    assert ranked[0] == "finance"


def test_caps_tail_keeping_most_likely_when_signal_present():
    ranked = _prioritize_domains("python architecture devops", ["finance", "coding", "personal", "projects"], 1)
    assert ranked == ["coding"]  # only the most-likely domain kept


def test_no_cap_when_no_lexical_signal():
    # No taxonomy words match → don't drop anything (no basis to choose).
    out = _prioritize_domains("zzzz qqqq", ["finance", "coding", "personal"], 1)
    assert set(out) == {"finance", "coding", "personal"}


def test_no_cap_when_cap_zero_or_disabled():
    out = _prioritize_domains("python", ["finance", "coding"], 0)
    assert set(out) == {"finance", "coding"}


def test_keeps_all_when_within_cap():
    out = _prioritize_domains("python", ["finance", "coding"], 5)
    assert set(out) == {"finance", "coding"}


# --- per-domain depth trim --------------------------------------------------

def test_followup_trims_top_k(monkeypatch):
    monkeypatch.setattr(config, "AGENT_QUERY_FOLLOWUP_TOP_K", 5, raising=False)
    assert _followup_retrieval_top_k(10, _MSGS, None) == 5


def test_no_trim_when_not_a_followup(monkeypatch):
    monkeypatch.setattr(config, "AGENT_QUERY_FOLLOWUP_TOP_K", 5, raising=False)
    assert _followup_retrieval_top_k(10, None, None) == 10


def test_no_trim_when_explicit_domain_filter(monkeypatch):
    monkeypatch.setattr(config, "AGENT_QUERY_FOLLOWUP_TOP_K", 5, raising=False)
    assert _followup_retrieval_top_k(10, _MSGS, ["finance"]) == 10


def test_no_trim_when_cap_not_below_base(monkeypatch):
    monkeypatch.setattr(config, "AGENT_QUERY_FOLLOWUP_TOP_K", 20, raising=False)
    assert _followup_retrieval_top_k(10, _MSGS, None) == 10


def test_depth_trim_disabled_when_cap_zero(monkeypatch):
    monkeypatch.setattr(config, "AGENT_QUERY_FOLLOWUP_TOP_K", 0, raising=False)
    assert _followup_retrieval_top_k(10, _MSGS, None) == 10
