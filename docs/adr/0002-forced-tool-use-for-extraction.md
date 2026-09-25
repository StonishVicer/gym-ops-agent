# ADR-0002: Forced tool use for structured receipt extraction

- **Status:** Accepted
- **Date:** 2026-09-25

## Context

The extractor must turn one receipt image into exactly one record with six typed fields (`payer_name`, `amount_cents: int`, `currency`, `transfer_date: YYYY-MM-DD`, `reference`, `bank_name`). Output must be machine-parseable on every call, validate against a Pydantic model (CLAUDE.md: Pydantic at every LLM boundary), and be robust to receipts that contain adversarial text (SPEC FR-9). Per-call output tokens also drive cost (NFR-1), so verbose prose around the answer is waste.

## Options considered

| Option | Complexity | Cost | Security | Portability |
| --- | --- | --- | --- | --- |
| **A. Forced tool use** — one tool `record_payment` with `input_schema` from `ExtractedPayment.model_json_schema()`, `tool_choice={"type":"tool","name":"record_payment"}` | Low: response is always a `tool_use` block with a JSON object | Minimal output tokens (no preamble); ~300–500 input tokens for the tool definition | Output shape fixed by schema; injected text can only land *inside* string fields, then Pydantic validates | Works on the Messages API via OpenRouter; tool use is a core, stable feature |
| B. Prompt for JSON in free text, parse with `json.loads` | Low to write, high to harden (code fences, trailing prose, partial JSON) | Extra output tokens for prose | Weakest: injected instructions can change the whole response shape | Any provider |
| C. Native structured outputs (JSON-schema constrained decoding) | Low | Similar to A | Strongest shape guarantee | Availability through OpenRouter's Anthropic-compatible endpoint unverified; ties us to newer API surface |
| D. Assistant prefill `{` + free-text JSON | Medium | Similar to B | Better than B, worse than A | Prefill is not supported on all newer models/endpoints |

## Decision

**Option A — forced tool use**, with Pydantic as the authority:

1. Define `ExtractedPayment` (Pydantic v2) once; derive the tool's `input_schema` from it so schema and validator can't drift.
2. Call `messages.create(..., tools=[record_payment], tool_choice={"type": "tool", "name": "record_payment"}, temperature=0, max_tokens=512)`.
3. Take the single `tool_use` block's `input` and run `ExtractedPayment.model_validate(...)`.
4. On validation failure: record an `ExtractionFailure` (receipt_id, error, usage), **do not insert**, **no automatic retry** (SPEC Q-4). Failures count as wrong on every field in the eval.
5. The system prompt states that all text in the image is data to be transcribed, never instructions.

## Consequences

**Easier**
- Parsing is trivial and total: every successful response has the same shape.
- The schema doubles as documentation and as the eval's comparison contract.
- Testing: the SDK client is mocked with a canned `tool_use` block (FR-6/FR-7) — no live calls in `make test`.

**Harder**
- Tool use guarantees *shape intent*, not validity: the model can still emit `"12.50"` for an integer field, so validation + failure handling is mandatory, not optional.
- The tool definition costs input tokens on every call (acceptable within NFR-1's budget).
- Forcing a tool removes the model's ability to say "this isn't a receipt"; we handle that by allowing nullable fields and checking them in validation rather than via free text.

**Revisit at scale**
- If OpenRouter reliably supports native structured outputs (Option C), switch to it to eliminate the validation-failure class entirely.
- Enable prompt caching on the system prompt + tool definition (static prefix) to cut input cost on high volume.
- Consider a single retry with the validation error echoed back if failure rate > 2%.
