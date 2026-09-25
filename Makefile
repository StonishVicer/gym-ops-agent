.PHONY: setup seed receipts mcp extract eval test test-ci lint all

UV_RUN := uv run

setup:
	uv sync --all-groups
	$(UV_RUN) pre-commit install

seed:
	$(UV_RUN) python -m gym_ops.db

receipts:
	$(UV_RUN) python -m gym_ops.receipts.generate --n-max 100 --seed 7

# `@`: make must not echo the recipe to stdout, which is the MCP protocol channel.
mcp:
	@$(UV_RUN) python -m gym_ops.mcp_server

extract:
	$(UV_RUN) python -m gym_ops.extractor

eval:
	$(UV_RUN) python -m gym_ops.eval

COV := --cov --cov-report=term-missing --cov-fail-under=85

test:
	$(UV_RUN) pytest $(COV)

# CI: skip wall-clock `perf` tests; a slow shared runner must not fail the build at random.
test-ci:
	$(UV_RUN) pytest $(COV) -m "not perf"

lint:
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .
	$(UV_RUN) mypy

all: lint test seed receipts eval
