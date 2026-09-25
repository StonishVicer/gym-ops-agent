.PHONY: setup seed receipts holdout mcp extract extract-smoke extract-holdout eval test test-ci test-live lint all

UV_RUN := uv run

setup:
	uv sync --all-groups
	$(UV_RUN) pre-commit install

seed:
	$(UV_RUN) python -m gym_ops.db

receipts:
	$(UV_RUN) python -m gym_ops.receipts.generate --n-max 100 --seed 7

# Held-out test set (SPEC "Evaluation protocol"): a fresh DB from the same seed (no dev
# transfers), 100 receipts from seed 8 with `hold-` ids. Costs nothing.
HOLDOUT_DIR := data/holdout
HOLDOUT_RUN := v2-holdout

holdout:
	$(UV_RUN) python -m gym_ops.db --db-path $(HOLDOUT_DIR)/gym.db
	$(UV_RUN) python -m gym_ops.receipts.generate --db-path $(HOLDOUT_DIR)/gym.db \
		--out-dir $(HOLDOUT_DIR)/receipts --labels $(HOLDOUT_DIR)/labels.jsonl \
		--n 100 --n-max 100 --seed 8 --id-prefix hold

# `@`: make must not echo the recipe to stdout, which is the MCP protocol channel.
mcp:
	@$(UV_RUN) python -m gym_ops.mcp_server

extract:
	$(UV_RUN) python -m gym_ops.extractor

# Three receipts, ~$0.01: a clean exact_payment, one rotated + blurred, one adversarial_injection.
SMOKE_RECEIPTS := rcpt-0008.png rcpt-0013.png rcpt-0033.png

extract-smoke:
	$(UV_RUN) python -m gym_ops.extractor $(addprefix --only ,$(SMOKE_RECEIPTS))

# Runs the holdout ONCE, then freezes it under eval/runs/$(HOLDOUT_RUN). Refuses if that
# snapshot or a partial run exists, or if src/ has uncommitted changes (the SHA must be true).
extract-holdout:
	@test ! -e eval/runs/$(HOLDOUT_RUN) || { echo "eval/runs/$(HOLDOUT_RUN) exists: the holdout runs once" >&2; exit 1; }
	@test ! -e $(HOLDOUT_DIR)/extractions.jsonl || { echo "$(HOLDOUT_DIR)/extractions.jsonl exists: a holdout run already started" >&2; exit 1; }
	@git diff --quiet HEAD -- src || { echo "src/ has uncommitted changes; commit first" >&2; exit 1; }
	$(UV_RUN) python -m gym_ops.extractor --receipts-dir $(HOLDOUT_DIR)/receipts \
		--labels $(HOLDOUT_DIR)/labels.jsonl --db-path $(HOLDOUT_DIR)/gym.db \
		--out $(HOLDOUT_DIR)/extractions.jsonl
	$(UV_RUN) python -m gym_ops.extractor.runs --name $(HOLDOUT_RUN) --split holdout \
		--extractions $(HOLDOUT_DIR)/extractions.jsonl --labels $(HOLDOUT_DIR)/labels.jsonl \
		--receipts-seed 8 --git-sha $$(git rev-parse HEAD)

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
