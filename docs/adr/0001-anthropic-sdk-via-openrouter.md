# ADR-0001: Use the Anthropic Python SDK pointed at OpenRouter

- **Status:** Accepted
- **Date:** 2026-09-25

## Context

The extractor (`gym_ops.extractor`) sends receipt images to Claude and needs vision input, forced tool use (ADR-0002), and per-call `usage` token counts for the cost NFR (SPEC NFR-1). The project is a portfolio piece meant to demonstrate the **Claude API** idioms (Messages API, content blocks, `tool_choice`), but billing runs through an existing **OpenRouter** account.

OpenRouter exposes an Anthropic-compatible Messages endpoint, so the official `anthropic` SDK works with `base_url="https://openrouter.ai/api"` (the SDK appends `/v1/messages`). Model IDs use OpenRouter's form (`anthropic/claude-haiku-4.5`).

A hard constraint from the dev environment: the `ANTHROPIC_BASE_URL` / `ANTHROPIC_AUTH_TOKEN` environment variables must never be set, because Claude Code reads them and they would hijack its own auth.

## Options considered

| Option | Complexity | Cost | Security | Portability |
| --- | --- | --- | --- | --- |
| **A. `anthropic` SDK + `base_url` → OpenRouter** | Low: native Messages API types, typed `ToolUseBlock`, `usage` object | Existing OpenRouter credits; OpenRouter's per-token price for Anthropic models | Key held in `SecretStr`, passed explicitly to the client; no global env vars | High: switching to Anthropic direct = change `base_url`, key, and model ID |
| B. `openai` SDK → OpenRouter's OpenAI-compatible endpoint | Low–medium: tool calling translated to OpenAI `functions`; image as `image_url` | Same as A | Same as A | Medium: code shows OpenAI idioms, not Claude's; Anthropic-specific features (e.g. `tool_choice` semantics, content blocks) are translated and may drift |
| C. `anthropic` SDK → Anthropic API directly | Lowest | Requires a separate Anthropic account/billing | Same as A | High |
| D. Raw `httpx` against either endpoint | Medium–high: hand-written request/response models, retries, errors | Same as A | Same, but more surface for mistakes (logging headers, etc.) | High, but reinvents the SDK |

## Decision

**Option A.** Construct the client explicitly:

```python
anthropic.Anthropic(
    base_url="https://openrouter.ai/api",
    api_key=settings.require_openrouter_api_key().get_secret_value(),
    max_retries=2,
    timeout=30.0,
)
```

- `base_url` and `api_key` are always passed as arguments; the code never reads or sets `ANTHROPIC_*` env vars.
- The model ID comes from `Settings.MODEL_ID`; per-MTok prices come from `Settings.INPUT_USD_PER_MTOK` / `OUTPUT_USD_PER_MTOK` with a dated source comment.
- Cost is computed locally from `response.usage`, not from OpenRouter billing data.

## Consequences

**Easier**
- Code reads as idiomatic Claude API usage — the point of the portfolio.
- Moving to Anthropic direct (Option C) is a three-value config change.
- Typed SDK responses slot straight into Pydantic validation.

**Harder**
- Two sources of truth for pricing: our config vs OpenRouter's actual charge. The config values must be updated by hand when prices change; the eval reports *estimated* cost.
- OpenRouter-specific failure modes (provider routing, 402 insufficient credits, occasional schema differences) sit between us and Anthropic; debugging needs awareness of both.
- Anthropic features that OpenRouter's compatibility layer doesn't support (beta headers, newest parameters) may be unavailable or silently ignored.
- Latency includes an extra hop (SPEC A-6).

**Revisit at scale**
- At meaningful volume, go direct to Anthropic (Option C) for lower latency, prompt caching guarantees, and the Message Batches API (50% cheaper for non-interactive extraction).
- Reconcile estimated cost against OpenRouter's generation-stats endpoint if billing accuracy matters.
