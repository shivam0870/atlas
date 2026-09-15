.PHONY: configure check integration migrate services
configure:
	python3 scripts/configure.py
	uv sync
check:
	uv run ruff check .
	uv run ruff format --check .
	uv run mypy src
integration:
	uv run pytest -q
migrate:
	uv run alembic upgrade head
services:
	docker-compose up -d postgres redis collector
