"""Deterministic fake LLM provider for tests. Returns queued, schema-valid responses."""

from collections import deque

from pydantic import ValidationError

from app.llm.base import LLMError, LLMUsage, TModel


class FakeLLMProvider:
    name = "fake"

    def __init__(self) -> None:
        self._queue: deque[dict] = deque()
        self.calls: list[dict] = []

    def enqueue(self, payload: dict) -> None:
        """Queue the next structured response (validated against the requested schema)."""
        self._queue.append(payload)

    async def complete_structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[TModel],
        max_tokens: int = 2048,
    ) -> tuple[TModel, LLMUsage]:
        self.calls.append({"system": system, "user": user, "schema": schema.__name__})
        if not self._queue:
            raise LLMError(f"FakeLLMProvider queue empty (requested {schema.__name__})")
        payload = self._queue.popleft()
        try:
            parsed = schema.model_validate(payload)
        except ValidationError as exc:
            raise LLMError(f"fake payload failed schema validation: {exc}") from exc
        return parsed, LLMUsage(input_tokens=100, output_tokens=50)
