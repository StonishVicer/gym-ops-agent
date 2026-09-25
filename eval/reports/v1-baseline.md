# Eval report: `v1-baseline` (dev)

Model `anthropic/claude-haiku-4.5`, prompt `v1`, git `4e33288`, receipts seed 7, DB seed 42; run 2026-09-25T21:59:44Z → 2026-09-25T22:04:56Z. Integrity: verified: extractions hash, regenerated labels hash, id coverage.

n = 100 receipts. Every rate shows a 95% Wilson interval and k/n. Failed extractions are wrong on every field and stay in every denominator.

## Extraction

| Field | Accuracy |
|---|---|
| payer_name | 97.0% [91.5, 99.0] (97/100) |
| amount_cents | 97.0% [91.5, 99.0] (97/100) |
| currency | 97.0% [91.5, 99.0] (97/100) |
| transfer_date | 97.0% [91.5, 99.0] (97/100) |
| reference | 97.0% [91.5, 99.0] (97/100) |
| bank_name | 97.0% [91.5, 99.0] (97/100) |
| **all six fields** | **97.0% [91.5, 99.0] (97/100)** |

Failures after retry: 3 (rcpt-0043, rcpt-0084, rcpt-0100).

### All-six-correct rate by difficulty tag (a receipt counts under each of its tags)

| Difficulty | All six correct |
|---|---|
| amount_no_symbol | 91.7% [64.6, 98.5] (11/12) |
| amount_plain | 83.3% [55.2, 95.3] (10/12) |
| amount_usd_code | 100.0% [75.7, 100.0] (12/12) |
| blur | 92.9% [68.5, 98.7] (13/14) |
| clean | 100.0% [92.1, 100.0] (45/45) |
| date_long | 83.3% [55.2, 95.3] (10/12) |
| date_us | 100.0% [75.7, 100.0] (12/12) |
| jpeg_noise | 94.4% [74.2, 99.0] (17/18) |
| rotation | 100.0% [82.4, 100.0] (18/18) |

### By bank template

| Template | All six correct |
|---|---|
| banco_demo | 91.2% [77.0, 97.0] (31/34) |
| banco_ficticio_del_sur | 100.0% [89.6, 100.0] (33/33) |
| cooperativa_ejemplo | 100.0% [89.6, 100.0] (33/33) |

### By scenario

| Scenario | All six correct |
|---|---|
| adversarial_injection | 100.0% [43.8, 100.0] (3/3) |
| ambiguous | 100.0% [43.8, 100.0] (3/3) |
| duplicate | 100.0% [34.2, 100.0] (2/2) |
| exact_payment | 95.5% [87.5, 98.4] (63/66) |
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
| rcpt-0084 | payer_name | David Sanford | None | amount_plain, date_long | banco_demo | extraction failed: normalization failed: unrecognized amount format: '40,00' |
| rcpt-0084 | amount_cents | 4000 | None | amount_plain, date_long | banco_demo | extraction failed: normalization failed: unrecognized amount format: '40,00' |
| rcpt-0084 | currency | USD | None | amount_plain, date_long | banco_demo | extraction failed: normalization failed: unrecognized amount format: '40,00' |
| rcpt-0084 | transfer_date | 2026-09-12 | None | amount_plain, date_long | banco_demo | extraction failed: normalization failed: unrecognized amount format: '40,00' |
| rcpt-0084 | reference | GYM-000251-2026-09 | None | amount_plain, date_long | banco_demo | extraction failed: normalization failed: unrecognized amount format: '40,00' |
| rcpt-0084 | bank_name | Banco Demo | None | amount_plain, date_long | banco_demo | extraction failed: normalization failed: unrecognized amount format: '40,00' |
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
| Linked to the right bill (or correctly unlinked) | 97.0% [91.5, 99.0] (97/100) |
| Final bill status correct | 97.0% [91.5, 99.0] (97/100) |
| Unidentified reason correct | 97.0% [91.5, 99.0] (97/100) |
| All three correct | 97.0% [91.5, 99.0] (97/100) |

Stored transfers: 97. Resolver vs recorded `member_id` mismatches: 0. Bills touched without a label: 0.

- Bill `GYM-000251-2026-09`: expected `paid`, observed `unpaid`; failed extraction(s): rcpt-0084; stored-but-wrong: —.
- Bill `GYM-000374-2026-09`: expected `paid`, observed `unpaid`; failed extraction(s): rcpt-0100; stored-but-wrong: —.
- Bill `GYM-000390-2026-08`: expected `paid`, observed `unpaid`; failed extraction(s): rcpt-0043; stored-but-wrong: —.

## Cost and latency

| Metric | Value |
|---|---|
| Mean input / output tokens | 2424 / 205.99 |
| Cost per extraction: mean / max | $0.003454 / $0.003524 (rcpt-0003) |
| Total cost | $0.345395 |
| Projected cost per 1,000 receipts | $3.45 |
| Latency p50 / p95 / max | 3071 / 3972 / 5197 ms |
| Percentile method | nearest-rank over all n=100 receipts |
| Slowest receipt | rcpt-0006 (1 HTTP attempt(s)) |
| Validation retries / HTTP retries | 0 / 1 |

## Confidence calibration (reported only; nothing is tuned with it)

Mean confidence: all six correct 0.9505 (n=97); any field wrong 0.95 (n=3); excluded (no reading): 0.

## Gates (informational; only the headline run is gated)

| Gate | Metric | Threshold | Measured | Wilson 95% | Result |
|---|---|---|---|---|---|
| NFR-1 | mean cost per extraction | < $0.005 | $0.003454 | — | PASS |
| NFR-1 | max cost per extraction | < $0.01 | $0.003524 | — | PASS |
| NFR-2 | latency p50 | < 4000 ms | 3071 ms | — | PASS |
| NFR-3 | payer_name accuracy | >= 95% | 97.0% (97/100) | [91.5%, 99.0%] | PASS |
| NFR-3 | amount_cents accuracy | >= 95% | 97.0% (97/100) | [91.5%, 99.0%] | PASS |
| NFR-3 | currency accuracy | >= 95% | 97.0% (97/100) | [91.5%, 99.0%] | PASS |
| NFR-3 | transfer_date accuracy | >= 95% | 97.0% (97/100) | [91.5%, 99.0%] | PASS |
| NFR-3 | reference accuracy | >= 95% | 97.0% (97/100) | [91.5%, 99.0%] | PASS |
| NFR-3 | bank_name accuracy | >= 95% | 97.0% (97/100) | [91.5%, 99.0%] | PASS |
