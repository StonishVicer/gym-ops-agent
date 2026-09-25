# gym-ops-agent: extraction results

Receipt extraction with Claude Haiku 4.5 (`anthropic/claude-haiku-4.5` via OpenRouter), prompt **v2**, scored on 100% synthetic data. **Headline numbers come from the held-out set only** (`v2-holdout`: 100 receipts, seed 8, run once after the prompt was frozen on the dev set). **n = 100 per set, so every result is indicative**: each rate carries a 95% Wilson interval and k/n. Failed extractions count as wrong on every field and stay in every denominator. Every number is recomputed by `make eval` from frozen, hash-verified snapshots in `eval/runs/` (per-run detail: `eval/reports/`).

## Headline (v2-holdout)

| Field | Accuracy [Wilson 95%] (k/n) |
|---|---|
| payer_name | 98.0% [93.0, 99.4] (98/100) |
| amount_cents | 98.0% [93.0, 99.4] (98/100) |
| currency | 98.0% [93.0, 99.4] (98/100) |
| transfer_date | 98.0% [93.0, 99.4] (98/100) |
| reference | 98.0% [93.0, 99.4] (98/100) |
| bank_name | 98.0% [93.0, 99.4] (98/100) |
| **all six fields** | **98.0% [93.0, 99.4] (98/100)** |

Failures after retry: 2 (hold-0002, hold-0069); every other receipt is right on all six fields.

## Gates (v2-holdout; thresholds from SPEC §4, never tuned)

| Gate | Metric | Threshold | Measured | Wilson 95% | Result |
|---|---|---|---|---|---|
| NFR-1 | mean cost per extraction | < $0.005 | $0.003475 | — | PASS |
| NFR-1 | max cost per extraction | < $0.01 | $0.003538 | — | PASS |
| NFR-2 | latency p50 | < 4000 ms | 3087 ms | — | PASS |
| NFR-3 | payer_name accuracy | >= 95% | 98.0% (98/100) | [93.0%, 99.4%] | PASS |
| NFR-3 | amount_cents accuracy | >= 95% | 98.0% (98/100) | [93.0%, 99.4%] | PASS |
| NFR-3 | currency accuracy | >= 95% | 98.0% (98/100) | [93.0%, 99.4%] | PASS |
| NFR-3 | transfer_date accuracy | >= 95% | 98.0% (98/100) | [93.0%, 99.4%] | PASS |
| NFR-3 | reference accuracy | >= 95% | 98.0% (98/100) | [93.0%, 99.4%] | PASS |
| NFR-3 | bank_name accuracy | >= 95% | 98.0% (98/100) | [93.0%, 99.4%] | PASS |

**Overall: PASS.** NFR-3 passes on the measured value, as the SPEC defines the gate, but it is **not statistically established at n = 100**: the lowest field, 98/100, has a Wilson 95% lower bound of 93.0%, below the 95% threshold. If the true rate were 98%, the lower bound would clear 95% at about n ≈ 203 receipts. NFR-2 gates p50 only (SPEC Q-6); the tail is reported below.

## Security (prompt injection, FR-9)

| Metric (v2-holdout) | Result |
|---|---|
| Injection detected on adversarial receipts | 100.0% [43.8, 100.0] (3/3) |
| False positives on all other receipts | 0.0% [0.0, 3.8] (0/97) |
| Amount correct on adversarial receipts (injection asked for 999999) | 100.0% [43.8, 100.0] (3/3) |

Measured only through the boolean `injection_detected`, never through `notes`. With 3 adversarial receipts the detection interval is wide; it shows that detection works here, not how often it would.

## End-to-end reconciliation (the system-level result)

Each run's frozen extractions are written through the production path (resolver + upsert) into a fresh database built from the seed, and reconciled for July to September with the real rules (ADR-0005).

| Per receipt (v2-holdout) | Result |
|---|---|
| Linked to the right bill (or correctly unlinked) | 98.0% [93.0, 99.4] (98/100) |
| Final bill status correct | 98.0% [93.0, 99.4] (98/100) |
| Unidentified reason correct | 98.0% [93.0, 99.4] (98/100) |

Resolver mismatches: 0; transfers leaking onto unlabelled bills: 0. How the failed extractions propagate: a failure is never stored (FR-7), so its transfer is simply absent.

- `hold-0069`: bill `GYM-000342-2026-07` ends `unpaid` instead of `paid` (a false "unpaid" the operator would chase).
- `hold-0002` (unknown_payer): no bill changes, but the transfer is missing from the operator's list of unidentified transfers.

No stored extraction produced a wrong link or status: every end-to-end error traces to a failed extraction.

## v1 → v2-dev → v2-holdout

|  | v1-baseline (dev) | v2-dev | **v2-holdout** |
|---|---|---|---|
| Prompt / git | v1 / `4e33288` | v2 / `650fb40` | v2 / `3a5b144` |
| Receipts (seed) | 100 (7) | 100 (7) | 100 (8) |
| Failures after retry | 3 | 2 | 2 |
| All six fields correct | 97.0% [91.5, 99.0] (97/100) | 98.0% [93.0, 99.4] (98/100) | 98.0% [93.0, 99.4] (98/100) |
| Amount correct | 97.0% [91.5, 99.0] (97/100) | 98.0% [93.0, 99.4] (98/100) | 98.0% [93.0, 99.4] (98/100) |
| Injection detection | 3/3 | 3/3 | 3/3 |
| False positives | 0/97 | 0/97 | 0/97 |
| E2E all three correct | 97.0% [91.5, 99.0] (97/100) | 98.0% [93.0, 99.4] (98/100) | 98.0% [93.0, 99.4] (98/100) |
| Mean cost | $0.003454 | $0.003477 | $0.003475 |
| Latency p50 / p95 / max (ms) | 3071 / 3972 / 5197 | 3034 / 4069 / 6198 | 3087 / 4630 / 12799 |

v1 and v2-dev are the same 100 dev receipts; the dev set was used to choose v2 and is not an unbiased estimate. v1 → v2 changed one prompt line (keep digits and separators as printed); no receipt that v1 got right became wrong.

## Cost and latency (v2-holdout)

| Metric | Value |
|---|---|
| Tokens per receipt (mean input / output) | 2443 / 206.38 |
| Cost per extraction (mean / max) | $0.003475 / $0.003538 |
| Total (100 receipts) | $0.3475 |
| Projected per 1,000 receipts | $3.47 |
| Latency p50 / p95 / max | 3087 / 4630 / 12799 ms (nearest-rank, n=100) |
| Slowest receipt | `hold-0047` (1 HTTP attempt: upstream tail latency, not a retry) |
| Validation / HTTP retries | 0 / 0 |
| Mean confidence: all correct vs any wrong | 0.9505 (n=98) vs 0.95 (n=2); 0 without a reading |

Model confidence does not separate right from wrong readings here (means within 0.02), so it cannot be used to route receipts for review.

Cost is estimated from each response's `usage` times configured prices ($1 / $5 per MTok), not billed amounts. Latency is end-to-end from the developer's machine through OpenRouter, sequential calls (SPEC A-6).

**Billing check (one-time, v1-baseline only).** OpenRouter's `limit_remaining` dropped by $0.328 across the v1 run ($1.989578 to $1.661588, read via `GET /api/v1/key` right before and after) against a usage-based estimate of $0.345: the estimate was 5% above the billed amount, i.e. conservative. Not repeated for v2-dev or v2-holdout.

## Failure analysis

**1. Every failure is a decimal-comma misread** (7 failures across the three runs; all have a raw amount like `50,00`; misreads that did not fail: 0). Decimal-comma misreads by template (k/n per template):

| Run | banco_demo | banco_ficticio_del_sur | cooperativa_ejemplo |
|---|---|---|---|
| v1-baseline | 3/34 | 0/33 | 0/33 |
| v2-dev | 2/34 | 0/33 | 0/33 |
| v2-holdout | 2/34 | 0/33 | 0/33 |
| **combined (receipt-runs)** | **7/102** | **0/99** | **0/99** |

Misreads occur only on: `banco_demo` (7/102 receipt-runs); the other templates show 0/198. Combined counts are receipt-runs: v1 and v2-dev score the same 100 dev receipts, so they are not independent.

**2. A missing currency symbol is refuted as the cause.** `hold-0069` printed `$` and the model still returned a decimal comma (raw amount `$50,00`).

**3. Hypothesis, not verified:** the Spanish-sounding bank name ("Banco Demo") nudges the model toward European number formatting. It is consistent with the misreads clustering on that one template, but it is weak as stated: **all three fictional banks have Spanish-sounding names** (Banco Demo, Banco Ficticio del Sur, Cooperativa Ejemplo), and the other two show no misreads. Whatever drives it is specific to the `banco_demo` template, whose name, layout (`_layout_rows` in `render.py`; the others use `_layout_hero` and `_layout_stacked`) and field labels all differ from the others; the data cannot separate these. Test with an ablation on the dev set: render the same `banco_demo` receipts changing one factor at a time (bank name → a neutral English name; then the layout; then the field labels), everything else byte-identical, and compare decimal-comma rates. Not run in this phase.

**4. The v2 prompt fixed the clean case, not the degraded ones** (v1-baseline failures, rescored in v2-dev): clean 1/1 fixed, degraded 0/2 fixed. With 3 receipts this is a description, not a rate.

| v1 failure | Difficulty | Degradation | In v2-dev |
|---|---|---|---|
| rcpt-0043 | amount_plain, date_long, blur | blur | still fails |
| rcpt-0084 | amount_plain, date_long | none | fixed |
| rcpt-0100 | amount_no_symbol, jpeg_noise | jpeg_noise | still fails |

**5. Tail latency.** `hold-0047` took 12799 ms on a single HTTP attempt: an upstream slow response, not a retry. p50 3087 ms passes NFR-2; p95 4630 ms and max 12799 ms are reported next to it and are not gated.

**6. Mitigation (SPEC §9 Future work, not implemented).** Escalating only failed readings to a larger model would target exactly this pattern. At the holdout's failure rate (2/100) and its mean tokens, one escalation costs about $0.0069 at the Claude Sonnet 5 list price ($2 / $10 per MTok; source: https://platform.claude.com/docs/en/about-claude/pricing, retrieved 2026-09-25), adding ~$0.00014 per receipt (+4% on $0.00347). The same page notes that Claude 4.7 and later models use a tokenizer producing ~30% more tokens; with the Haiku token counts scaled by 1.3, that is ~$0.0090 per escalation and ~$0.00018 per receipt (+5%). Both stay well within NFR-1. Adding it would require a new prompt/system version, a dev run and a **new** holdout set.
