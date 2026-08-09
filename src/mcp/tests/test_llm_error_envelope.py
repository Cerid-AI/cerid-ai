"""An HTTP-200 error envelope must raise, not degrade to an empty string.

Regression guard for the 2026-07-29 test-validity audit. OpenAI-compatible
gateways (OpenRouter included) answer **200** with ``{"error": {...}}`` for
moderation blocks, routing failures and upstream provider errors. The client
extracted content via chained ``.get()`` defaults, so those became ``""`` and
travelled silently through every downstream stage as "the model returned
nothing" — the silent-zero class the 2026-05-17 ablations surfaced once already.

No test covered it: all 16 files faking ``choices`` responses faked happy paths.
"""

import pytest

from core.utils.llm_client import _extract_content
from errors import ProviderError


def test_normal_response_returns_content():
    data = {"choices": [{"message": {"content": "the answer"}}]}
    assert _extract_content(data) == "the answer"


def test_empty_content_is_allowed():
    """A genuinely empty completion is not an error."""
    data = {"choices": [{"message": {"content": ""}}]}
    assert _extract_content(data) == ""


def test_null_content_normalises_to_empty_string():
    data = {"choices": [{"message": {"content": None}}]}
    assert _extract_content(data) == ""


@pytest.mark.parametrize("envelope", [
    {"error": {"message": "moderation blocked", "code": 403}},
    {"error": {"message": "no allowed provider", "code": 502}},
    {"error": "flat string error"},
])
def test_error_envelope_raises(envelope):
    with pytest.raises(ProviderError) as exc:
        _extract_content(envelope)
    assert "error envelope" in str(exc.value)


def test_error_envelope_message_is_surfaced():
    with pytest.raises(ProviderError) as exc:
        _extract_content({"error": {"message": "moderation blocked", "code": 403}})
    text = str(exc.value)
    assert "moderation blocked" in text
    assert "403" in text


def test_response_missing_choices_and_error_raises():
    """Neither shape present — surface it rather than returning ''."""
    with pytest.raises(ProviderError):
        _extract_content({"id": "x", "object": "chat.completion"})


def test_empty_choices_list_raises():
    with pytest.raises(ProviderError):
        _extract_content({"choices": []})
