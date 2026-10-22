"""Anthropic provider.

Structured output is obtained by forcing a single tool call whose input schema is
the target Pydantic model's JSON schema — the most reliable way to get valid JSON.

HONEST STATUS: implemented against the anthropic SDK 2.0 contract and covered by
fake-provider tests; live API connectivity is unverified until credentials are
configured (see README §Limitations). Opt-in live test: pytest -m live_llm.
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

        tool_name = "emit_result"
        try:
            raw = await self._client.messages.with_raw_response.create(
                model=self._model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": user}],
                tools=[
                    {
                        "name": tool_name,
                        "description": "Emit the final structured result.",
                        "input_schema": schema.model_json_schema(),
                    }
                ],
                tool_choice={"type": "tool", "name": tool_name},
            )
        except anthropic.RateLimitError as exc:
            raise LLMTransientError(f"anthropic rate limit: {exc}") from exc
        except anthropic.APIStatusError as exc:
            if exc.status_code >= 500:
                raise LLMTransientError(f"anthropic server error: {exc.status_code}") from exc
            raise LLMError(f"anthropic API error: {exc.status_code}") from exc
        except anthropic.APIConnectionError as exc:
            raise LLMTransientError(f"anthropic connection error: {exc}") from exc

        request_id = raw.headers.get("request-id")
        response = raw.parse()

        tool_input = None
        for block in response.content:
            if getattr(block, "type", None) == "tool_use" and block.name == tool_name:
                tool_input = block.input
                break
        if tool_input is None:
            raise LLMError("model did not return the requested structured output")

        try:
            parsed = schema.model_validate(tool_input)
        except ValidationError as exc:
            raise LLMError(f"model output failed schema validation: {exc}") from exc

        usage = LLMUsage(
            input_tokens=getattr(response.usage, "input_tokens", None),
            output_tokens=getattr(response.usage, "output_tokens", None),
            request_id=request_id,
        )
        return parsed, usage
