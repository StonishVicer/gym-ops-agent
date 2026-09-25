# gym-ops-agent

Portfolio project demonstrating the Claude API and MCP on 100% synthetic gym data: a read-only MCP server over a SQLite ops database, a Claude-vision receipt extractor, and an eval harness measuring accuracy, cost, and latency.

## Architecture

1. `gym_ops.db` — SQLite schema + deterministic Faker seed (synthetic data only).
2. `gym_ops.mcp_server` — read-only FastMCP server over stdio exposing 4 query tools.
3. `gym_ops.receipts` — generates synthetic receipt images with ground-truth labels.
4. `gym_ops.extractor` — Claude vision + forced tool use via the `anthropic` SDK against OpenRouter (`base_url="https://openrouter.ai/api"`).
5. `gym_ops.eval` — per-field accuracy, token usage, cost from `usage`, latency p50/p95.

Settings live in `gym_ops.config.Settings` (pydantic-settings, reads `.env`).

## Commands

| Command | Purpose |
| --- | --- |
| `make setup` | Install deps (`uv sync`) and pre-commit hooks |
| `make seed` | Build `data/gym.db` from the deterministic seed |
| `make receipts` | Generate synthetic receipt images + labels |
| `make mcp` | Run the MCP server on stdio |
| `make extract` | Run the receipt extractor |
| `make eval` | Run the eval harness |
| `make test` | pytest with coverage |
| `make lint` | ruff check, ruff format --check, mypy (strict) |
| `make all` | lint, test, seed, receipts, eval |

## Conventions

- Type hints on every function signature; mypy runs in strict mode.
- Pydantic models at every boundary: env config, MCP tool I/O, LLM tool schemas, eval records.
- SQL: parametrized queries only (`?` placeholders). Never build SQL with f-strings, `%`, `.format()`, or concatenation.
- The MCP server opens SQLite read-only (`file:...?mode=ro`).
- Use `uv run` / `uv add`; never `pip install`.

## NEVER

- Never read, print, or `cat` `.env`. Use `.env.example` for reference.
- Never commit secrets, API keys, or generated `.db` files.
- Never set `ANTHROPIC_BASE_URL` or `ANTHROPIC_AUTH_TOKEN` env vars — they hijack Claude Code's own auth. Pass `base_url` and `api_key` to the `anthropic.Anthropic(...)` client explicitly instead.
