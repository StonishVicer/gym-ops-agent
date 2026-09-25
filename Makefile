.PHONY: setup seed receipts mcp extract eval test lint all

UV_RUN := uv run

setup:
	uv sync --all-groups
	$(UV_RUN) pre-commit install

seed:
	$(UV_RUN) python -m gym_ops.db

receipts:
	$(UV_RUN) python -m gym_ops.receipts

mcp:
	$(UV_RUN) python -m gym_ops.mcp_server

extract:
	$(UV_RUN) python -m gym_ops.extractor

eval:
	$(UV_RUN) python -m gym_ops.eval

test:
	$(UV_RUN) pytest --cov --cov-report=term-missing

lint:
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .
	$(UV_RUN) mypy

all: lint test seed receipts eval
