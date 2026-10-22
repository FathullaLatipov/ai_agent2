# AgentOS developer commands.
# On Windows without make, run the underlying commands directly (see README).

.PHONY: install migrate seed api worker frontend test lint check demo-mcp

install:
	cd backend && uv sync
	cd frontend && npm install

migrate:
	cd backend && uv run alembic upgrade head

seed:
	cd backend && uv run python -m scripts.seed

api:
	cd backend && uv run uvicorn app.main:app --reload --port 8000

worker:
	cd backend && uv run taskiq worker app.worker.broker:broker --workers 1

frontend:
	cd frontend && npm run dev

test:
	cd backend && uv run pytest -q

lint:
	cd backend && uv run ruff check app tests alembic scripts
	cd frontend && npm run lint

check: lint test
