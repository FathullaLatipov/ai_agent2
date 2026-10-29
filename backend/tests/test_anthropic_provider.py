"""AnthropicProvider request shaping (no network — the client is stubbed)."""

import pytest
from pydantic import BaseModel

from app.core.config import get_settings
from app.llm.anthropic_provider import MIN_STRUCTURED_MAX_TOKENS, AnthropicProvider
from app.llm.base import LLMError


class _Out(BaseModel):
    summary: str


class _Usage:
    input_tokens = 10
    output_tokens = 5


class _Response:
    def __init__(self, stop_reason: str, parsed):
        self.stop_reason = stop_reason
        self.parsed_output = parsed
        self.usage = _Usage()


def _provider(monkeypatch, response: _Response) -> tuple[AnthropicProvider, dict]:
    monkeypatch.setattr(get_settings(), "anthropic_api_key", "sk-ant-test-not-real")
    provider = AnthropicProvider()
    captured: dict = {}

    class _Messages:
        async def parse(self, **kwargs):
            captured.update(kwargs)
            return response

    class _Client:
        messages = _Messages()

    provider._client = _Client()
    return provider, captured


async def test_small_caller_budget_is_raised_to_minimum(monkeypatch):
    """Regression: callers pass budgets tuned for short outputs (1200-3000);
    thinking tokens count against max_tokens and truncated JSON fails parsing."""
    provider, captured = _provider(
        monkeypatch, _Response("end_turn", _Out(summary="ok"))
    )
    parsed, usage = await provider.complete_structured(
        system="s", user="u", schema=_Out, max_tokens=1200
    )
    assert parsed.summary == "ok"
    assert captured["max_tokens"] == MIN_STRUCTURED_MAX_TOKENS
    assert usage.input_tokens == 10


async def test_truncated_output_raises_clear_error(monkeypatch):
    provider, _ = _provider(monkeypatch, _Response("max_tokens", None))
    with pytest.raises(LLMError, match="truncated by the max_tokens limit"):
        await provider.complete_structured(
            system="s", user="u", schema=_Out, max_tokens=1200
        )
