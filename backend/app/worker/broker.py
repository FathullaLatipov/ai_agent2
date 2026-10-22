"""Taskiq broker. Redis list queue in dev/prod; in-memory inline broker for tests."""

import asyncio
import sys

if sys.platform == "win32":  # must run before any asyncpg connection in the worker
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

from taskiq import InMemoryBroker
from taskiq_redis import ListQueueBroker

from app.core.config import get_settings
from app.core.logging import configure_logging

settings = get_settings()
configure_logging(settings.env)

if settings.env == "test":
    broker = InMemoryBroker(await_inplace=True)
else:
    # protocol=2 keeps compatibility with older Redis servers (see core/redis.py).
    broker = ListQueueBroker(settings.redis_url, protocol=2)

# Task definitions must be imported so the worker registers them.
from app.worker import tasks  # noqa: E402, F401
