"""Engine error taxonomy. Only transient errors are retried, within bounds."""


class EngineError(Exception):
    """Base — message safe for users/events."""


class BudgetExceededError(EngineError):
    """A hard execution budget was hit (tool calls, LLM calls, tasks)."""


class TaskFailedError(EngineError):
    """A task failed permanently (after allowed retries)."""


class PlanInvalidError(EngineError):
    """The planner produced an invalid plan."""
