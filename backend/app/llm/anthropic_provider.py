"""Anthropic provider.

Structured output uses the Messages API structured-outputs feature via the SDK's
`messages.parse` (output_format = the target Pydantic model). Forced tool choice
(`tool_choice: {"type": "tool"}`) is rejected with a 400 on current models
(claude-sonnet-5-5 and newer) and must not be reintroduced here.

Opt-in live test: pytest -m live_llm (requires AGENTOS_ANTHROPIC_API_KEY).
"""

from pydantic import ValidationError

from app.core.config import get_settings
from app.core.logging import get_logger
from app.llm.base import LLMError, LLMTransientError, LLMUsage, TModel

logger = get_logger("agentos.llm")


class AnthropicProvider:
    name = "anthropic"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.anthropic_api_key:
            raise LLMError("AGENTOS_ANTHROPIC_API_KEY is not configured")
        from anthropic import AsyncAnthropic

        self._client = AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            max_retries=2,
            timeout=float(settings.llm_timeout_s),
        )
        self._model = settings.llm_model

    async def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[TModel],
        max_tokens: int = 2048,
    ) -> tuple[TModel, LLMUsage]:
        import anthropic

        try:
            response = await self._client.messages.parse(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                output_format=schema,
            )
        except anthropic.RateLimitError as exc:
            raise LLMTransientError(f"anthropic rate limit: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMTransientError(f"anthropic server error: {exc.status_code}") from exc
            detail = ""
            try:
                detail = f" — {exc.body['error']['message'][:200]}"
            except (TypeError, KeyError):
                pass
            raise LLMError(f"anthropic API error: {exc.status_code}{detail}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMTransientError(f"anthropic connection error: {exc}") from exc
        except ValidationError as exc:
            raise LLMError(f"model output failed schema validation: {exc}") from exc

        parsed = response.parsed_output
        if parsed is None:
            raise LLMError("model did not return the requested structured output")

        usage = LLMUsage(
            input_tokens=getattr(response.usage, "input_tokens", None),
            output_tokens=getattr(response.usage, "output_tokens", None),
            request_id=getattr(response, "_request_id", None),
        )
        return parsed, usage
