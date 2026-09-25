.PHONY: setup seed receipts mcp extract extract-smoke eval test test-ci test-live lint all

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

# Three receipts, ~$0.01: a clean exact_payment, one rotated + blurred, one adversarial_injection.
SMOKE_RECEIPTS := rcpt-0008.png rcpt-0013.png rcpt-0033.png

extract-smoke:
	$(UV_RUN) python -m gym_ops.extractor $(addprefix --only ,$(SMOKE_RECEIPTS))

eval:
	$(UV_RUN) python -m gym_ops.eval

COV := --cov --cov-report=term-missing --cov-fail-under=85

# `live` tests spend money on the real API: never part of `test` or `test-ci`.
test:
	$(UV_RUN) pytest $(COV) -m "not live"

# CI: also skip wall-clock `perf` tests; a slow shared runner must not fail the build at random.
test-ci:
	$(UV_RUN) pytest $(COV) -m "not perf and not live"

test-live:
	$(UV_RUN) pytest -m live

lint:
	$(UV_RUN) ruff check .
	$(UV_RUN) ruff format --check .
	$(UV_RUN) mypy

all: lint test seed receipts eval
