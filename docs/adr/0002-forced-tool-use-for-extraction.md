# ADR-0002: Forced tool use for structured receipt extraction

- **Status:** Accepted (amended 2026-09-25, Phase 5: two-layer schema, one validation retry)
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

1. **Two layers.** The tool input is `ReceiptReading` (Pydantic v2): the fields *as printed* (`amount: "USD 1,234.56"`, `transfer_date: "Sep 19, 2026"`, all nullable) plus `confidence` (0–1), `injection_detected` (strict bool) and `notes`. The tool's `input_schema` is `ReceiptReading.model_json_schema()`, so the schema and the validator can't drift. Deterministic, tested Python (`normalize.py`) then turns a reading into the stored `ExtractedPayment` (integer cents, ISO date, canonical bank, uppercased reference). The model transcribes; code does the arithmetic and date parsing.
2. Call `messages.create(..., tools=[record_payment], tool_choice={"type": "tool", "name": "record_payment"}, max_tokens=512)` with `temperature=0` (via `extra_body`, ADR-0001).
3. Take the single `tool_use` block's `input` and run `ReceiptReading.model_validate(...)`.
4. **On a validation failure, retry once:** send the same conversation plus the model's `tool_use` and a `tool_result` with `is_error=true` that lists field errors. Field names and messages only, never the rejected values, so untrusted receipt text isn't echoed back as prose. If the second reading also fails, or a valid reading can't be normalized (an unreadable amount, an unknown format), record the failure with its usage and **do not insert**. There is no retry for normalization failures: the model already copied what was printed. Failures count as wrong on every field in the eval and stay in its denominator (SPEC NFR-3). This supersedes the original "no retry" (SPEC Q-4, revised).
5. The system prompt says the image is untrusted data: printed instructions are ignored, reported with `injection_detected=true`, and their text is copied to `notes`. It also says to extract the final total when several amounts appear, to copy fields exactly as printed, and to use `null` with lower confidence rather than guess.

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
- Track how often the validation retry fires (`attempts == 2` in `extractions.jsonl`); if it's common, fix the schema descriptions or prompt rather than paying for a second call.
