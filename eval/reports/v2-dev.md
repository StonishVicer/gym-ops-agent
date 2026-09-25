# Eval report: `v2-dev` (dev)

Model `anthropic/claude-haiku-4.5`, prompt `v2`, git `650fb40`, receipts seed 7, DB seed 42; run 2026-09-25T22:14:58Z → 2026-09-25T22:20:10Z. Integrity: verified: extractions hash, regenerated labels hash, id coverage.

n = 100 receipts. Every rate shows a 95% Wilson interval and k/n. Failed extractions are wrong on every field and stay in every denominator.

## Extraction

| Field | Accuracy |
|---|---|
| payer_name | 98.0% [93.0, 99.4] (98/100) |
| amount_cents | 98.0% [93.0, 99.4] (98/100) |
| currency | 98.0% [93.0, 99.4] (98/100) |
| transfer_date | 98.0% [93.0, 99.4] (98/100) |
| reference | 98.0% [93.0, 99.4] (98/100) |
| bank_name | 98.0% [93.0, 99.4] (98/100) |
| **all six fields** | **98.0% [93.0, 99.4] (98/100)** |

Failures after retry: 2 (rcpt-0043, rcpt-0100).

### All-six-correct rate by difficulty tag (a receipt counts under each of its tags)

| Difficulty | All six correct |
|---|---|
| amount_no_symbol | 91.7% [64.6, 98.5] (11/12) |
| amount_plain | 91.7% [64.6, 98.5] (11/12) |
| amount_usd_code | 100.0% [75.7, 100.0] (12/12) |
| blur | 92.9% [68.5, 98.7] (13/14) |
| clean | 100.0% [92.1, 100.0] (45/45) |
| date_long | 91.7% [64.6, 98.5] (11/12) |
| date_us | 100.0% [75.7, 100.0] (12/12) |
| jpeg_noise | 94.4% [74.2, 99.0] (17/18) |
| rotation | 100.0% [82.4, 100.0] (18/18) |

### By bank template

| Template | All six correct |
|---|---|
| banco_demo | 94.1% [80.9, 98.4] (32/34) |
| banco_ficticio_del_sur | 100.0% [89.6, 100.0] (33/33) |
| cooperativa_ejemplo | 100.0% [89.6, 100.0] (33/33) |

### By scenario

| Scenario | All six correct |
|---|---|
| adversarial_injection | 100.0% [43.8, 100.0] (3/3) |
| ambiguous | 100.0% [43.8, 100.0] (3/3) |
| duplicate | 100.0% [34.2, 100.0] (2/2) |
| exact_payment | 97.0% [89.6, 99.2] (64/66) |
| late_with_reference | 100.0% [34.2, 100.0] (2/2) |
| multiple_amounts | 100.0% [43.8, 100.0] (3/3) |
| name_date_match | 100.0% [34.2, 100.0] (2/2) |
| outside_window_no_ref | 100.0% [20.7, 100.0] (1/1) |
| overpayment | 100.0% [43.8, 100.0] (3/3) |
| partial_only | 100.0% [34.2, 100.0] (2/2) |
| topup_with_reference | 100.0% [51.0, 100.0] (4/4) |
| topup_without_reference | 100.0% [51.0, 100.0] (4/4) |
| unknown_payer | 100.0% [56.6, 100.0] (5/5) |

### Every wrong field

| Receipt | Field | Expected | Got | Difficulty | Template | Note |
|---|---|---|---|---|---|---|
| rcpt-0043 | payer_name | Brian Mccoy | None | amount_plain, date_long, blur | banco_demo | extraction failed: normalization failed: unrecognized amount format: '50,00' |
| rcpt-0043 | amount_cents | 5000 | None | amount_plain, date_long, blur | banco_demo | extraction failed: normalization failed: unrecognized amount format: '50,00' |
| rcpt-0043 | currency | USD | None | amount_plain, date_long, blur | banco_demo | extraction failed: normalization failed: unrecognized amount format: '50,00' |
| rcpt-0043 | transfer_date | 2026-08-21 | None | amount_plain, date_long, blur | banco_demo | extraction failed: normalization failed: unrecognized amount format: '50,00' |
| rcpt-0043 | reference | GYM-000390-2026-08 | None | amount_plain, date_long, blur | banco_demo | extraction failed: normalization failed: unrecognized amount format: '50,00' |
| rcpt-0043 | bank_name | Banco Demo | None | amount_plain, date_long, blur | banco_demo | extraction failed: normalization failed: unrecognized amount format: '50,00' |
| rcpt-0100 | payer_name | James Morrison | None | amount_no_symbol, jpeg_noise | banco_demo | extraction failed: normalization failed: unrecognized amount format: '50,00' |
| rcpt-0100 | amount_cents | 5000 | None | amount_no_symbol, jpeg_noise | banco_demo | extraction failed: normalization failed: unrecognized amount format: '50,00' |
| rcpt-0100 | currency | USD | None | amount_no_symbol, jpeg_noise | banco_demo | extraction failed: normalization failed: unrecognized amount format: '50,00' |
| rcpt-0100 | transfer_date | 2026-09-17 | None | amount_no_symbol, jpeg_noise | banco_demo | extraction failed: normalization failed: unrecognized amount format: '50,00' |
| rcpt-0100 | reference | GYM-000374-2026-09 | None | amount_no_symbol, jpeg_noise | banco_demo | extraction failed: normalization failed: unrecognized amount format: '50,00' |
| rcpt-0100 | bank_name | Banco Demo | None | amount_no_symbol, jpeg_noise | banco_demo | extraction failed: normalization failed: unrecognized amount format: '50,00' |

## Security (FR-9; measured only through `injection_detected`)

| Metric | Result |
|---|---|
| Detection on adversarial receipts | 100.0% [43.8, 100.0] (3/3) |
| False positives on all other receipts | 0.0% [0.0, 3.8] (0/97) |
| Amount correct on adversarial receipts | 100.0% [43.8, 100.0] (3/3) |

## End-to-end reconciliation (months 2026-07, 2026-08, 2026-09)

| Per receipt | Result |
|---|---|
| Linked to the right bill (or correctly unlinked) | 98.0% [93.0, 99.4] (98/100) |
| Final bill status correct | 98.0% [93.0, 99.4] (98/100) |
| Unidentified reason correct | 98.0% [93.0, 99.4] (98/100) |
| All three correct | 98.0% [93.0, 99.4] (98/100) |

Stored transfers: 98. Resolver vs recorded `member_id` mismatches: 0. Bills touched without a label: 0.

- Bill `GYM-000374-2026-09`: expected `paid`, observed `unpaid`; failed extraction(s): rcpt-0100; stored-but-wrong: —.
- Bill `GYM-000390-2026-08`: expected `paid`, observed `unpaid`; failed extraction(s): rcpt-0043; stored-but-wrong: —.

## Cost and latency

| Metric | Value |
|---|---|
| Mean input / output tokens | 2443 / 206.76 |
| Cost per extraction: mean / max | $0.003477 / $0.003578 (rcpt-0096) |
| Total cost | $0.347680 |
| Projected cost per 1,000 receipts | $3.48 |
| Latency p50 / p95 / max | 3034 / 4069 / 6198 ms |
| Percentile method | nearest-rank over all n=100 receipts |
| Slowest receipt | rcpt-0098 (1 HTTP attempt(s)) |
| Validation retries / HTTP retries | 0 / 0 |

## Confidence calibration (reported only; nothing is tuned with it)

Mean confidence: all six correct 0.95 (n=98); any field wrong 0.95 (n=2); excluded (no reading): 0.

## Gates (informational; only the headline run is gated)

| Gate | Metric | Threshold | Measured | Wilson 95% | Result |
|---|---|---|---|---|---|
| NFR-1 | mean cost per extraction | < $0.005 | $0.003477 | — | PASS |
| NFR-1 | max cost per extraction | < $0.01 | $0.003578 | — | PASS |
| NFR-2 | latency p50 | < 4000 ms | 3034 ms | — | PASS |
| NFR-3 | payer_name accuracy | >= 95% | 98.0% (98/100) | [93.0%, 99.4%] | PASS |
| NFR-3 | amount_cents accuracy | >= 95% | 98.0% (98/100) | [93.0%, 99.4%] | PASS |
| NFR-3 | currency accuracy | >= 95% | 98.0% (98/100) | [93.0%, 99.4%] | PASS |
| NFR-3 | transfer_date accuracy | >= 95% | 98.0% (98/100) | [93.0%, 99.4%] | PASS |
| NFR-3 | reference accuracy | >= 95% | 98.0% (98/100) | [93.0%, 99.4%] | PASS |
| NFR-3 | bank_name accuracy | >= 95% | 98.0% (98/100) | [93.0%, 99.4%] | PASS |
