# AgentOS backend — API and worker share this image.
FROM python:3.12-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.9.28 /uv /uvx /bin/

WORKDIR /srv/backend

# Install dependencies first for layer caching.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

COPY . .
RUN uv sync --frozen --no-dev

ENV PATH="/srv/backend/.venv/bin:$PATH"

# Non-root runtime user.
RUN useradd --create-home --uid 1001 agentos \
    && mkdir -p /srv/backend/var/uploads \
    && chown -R agentos:agentos /srv/backend/var
USER agentos

EXPOSE 8000
# API by default; the worker service overrides the command in docker-compose.yml.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
