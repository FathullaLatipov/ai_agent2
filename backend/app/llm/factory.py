"""Provider selection. Returns None when no LLM is configured (deterministic mode)."""

from app.core.config import get_settings
from app.llm.base import LLMProvider

_override: LLMProvider | None = None


def set_provider_override(provider: LLMProvider | None) -> None:
    """Test hook: inject a fake provider."""
    global _override
    _override = provider


def get_llm_provider() -> LLMProvider | None:
    if _override is not None:
        return _override
    settings = get_settings()
    if settings.llm_provider == "anthropic" and settings.anthropic_api_key:
        from app.llm.anthropic_provider import AnthropicProvider

        return AnthropicProvider()
    return None
