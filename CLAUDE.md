# gym-ops-agent

Portfolio project demonstrating the Claude API and MCP on 100% synthetic gym data: a read-only MCP server over a SQLite ops database, a Claude-vision receipt extractor, and an eval harness measuring accuracy, cost, and latency.

## Architecture

1. `gym_ops.db` — SQLite schema + deterministic Faker seed (synthetic data only).
2. `gym_ops.mcp_server` — read-only FastMCP server over stdio exposing 4 query tools.
3. `gym_ops.receipts` — renders 100 deterministic synthetic receipts (`--seed 7`) from real bills into `data/receipts/` + `data/labels.jsonl` (`truth` + expected reconciliation outcome). Scenario names are the canonical enum in SPEC FR-5.
4. `gym_ops.extractor` — Claude vision + forced tool use via the `anthropic` SDK against OpenRouter (`base_url="https://openrouter.ai/api"`).
5. `gym_ops.eval` — per-field accuracy, token usage, cost from `usage`, latency p50/p95.

Settings live in `gym_ops.config.Settings` (pydantic-settings, reads `.env`).

## Commands

| Command | Purpose |
| --- | --- |
| `make setup` | Install deps (`uv sync`) and pre-commit hooks |
| `make seed` | Build `data/gym.db` from the deterministic seed |
| `make receipts` | Generate synthetic receipt images + labels |
| `make docs-img` | Copy the two README sample receipts into `docs/img/` (deterministic, $0) |
| `make mcp` | Run the MCP server on stdio |
| `make extract` | Run the receipt extractor on all 100 receipts (spends money; needs explicit approval) |
| `make holdout` | Build the held-out set: fresh `data/holdout/gym.db` + 100 `hold-` receipts (seed 8), $0 |
| `make extract-holdout` | Run the holdout ONCE and freeze it in `eval/runs/` (spends money; needs explicit approval) |
| `make extract-smoke` | Extract 3 receipts (clean, rotated+blurred, adversarial), ~$0.01 |
| `make eval` | Run the eval harness |
| `make test` | pytest with coverage, excluding `live` tests |
| `make test-ci` | pytest with coverage, excluding `perf` and `live` tests (what CI runs) |
| `make test-live` | Only `live` tests: real API calls through OpenRouter (spends money) |
| `make lint` | ruff check, ruff format --check, mypy (strict) |
| `make all` | Offline $0 pipeline: setup, seed, receipts, lint, test-ci, eval (never calls the API) |

## Conventions

- Type hints on every function signature; mypy runs in strict mode.
- Pydantic models at every boundary: env config, MCP tool I/O, LLM tool schemas, eval records.
- SQL: parametrized queries only (`?` placeholders). Never build SQL with f-strings, `%`, `.format()`, or concatenation.
- The MCP server opens SQLite read-only (`file:...?mode=ro`).
- Use `uv run` / `uv add`; never `pip install`.
- CI (`.github/workflows/ci.yml`): `make lint`, `make test-ci`, gitleaks; actions pinned to commit SHAs. Every PR must be green before merge.

## NEVER

- Never read, print, or `cat` `.env`. Use `.env.example` for reference.
- Never commit secrets, API keys, or generated `.db` files.
- Never change reconciliation rules (`reconcile.py`, ADR-0005) to make the receipts oracle test pass; a mismatch means the dataset or the rules are wrong, so stop and report it.
- Never replace files in `assets/fonts/` without updating the SHA-256 pins in `tests/receipts/test_assets.py`; a font change changes every receipt image.
- Never set `ANTHROPIC_BASE_URL` or `ANTHROPIC_AUTH_TOKEN` env vars — they hijack Claude Code's own auth. Pass `base_url` and the credential (`auth_token`, ADR-0001) to the `anthropic.Anthropic(...)` client explicitly instead.
