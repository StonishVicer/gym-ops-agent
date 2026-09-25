# Eval report: `v2-holdout` (holdout, headline)

Model `anthropic/claude-haiku-4.5`, prompt `v2`, git `3a5b144`, receipts seed 8, DB seed 42; run 2026-09-25T22:23:49Z → 2026-09-25T22:29:22Z. Integrity: verified: extractions hash, regenerated labels hash, id coverage.

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

Failures after retry: 2 (hold-0002, hold-0069).

### All-six-correct rate by difficulty tag (a receipt counts under each of its tags)

| Difficulty | All six correct |
|---|---|
| amount_no_symbol | 100.0% [75.7, 100.0] (12/12) |
| amount_plain | 91.7% [64.6, 98.5] (11/12) |
| amount_usd_code | 100.0% [75.7, 100.0] (12/12) |
| blur | 100.0% [78.5, 100.0] (14/14) |
| clean | 100.0% [92.1, 100.0] (45/45) |
| date_long | 100.0% [75.7, 100.0] (12/12) |
| date_us | 91.7% [64.6, 98.5] (11/12) |
| jpeg_noise | 100.0% [82.4, 100.0] (18/18) |
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
| exact_payment | 98.5% [91.9, 99.7] (65/66) |
| late_with_reference | 100.0% [34.2, 100.0] (2/2) |
| multiple_amounts | 100.0% [43.8, 100.0] (3/3) |
| name_date_match | 100.0% [34.2, 100.0] (2/2) |
| outside_window_no_ref | 100.0% [20.7, 100.0] (1/1) |
| overpayment | 100.0% [43.8, 100.0] (3/3) |
| partial_only | 100.0% [34.2, 100.0] (2/2) |
| topup_with_reference | 100.0% [51.0, 100.0] (4/4) |
| topup_without_reference | 100.0% [51.0, 100.0] (4/4) |
| unknown_payer | 80.0% [37.6, 96.4] (4/5) |

### Every wrong field

| Receipt | Field | Expected | Got | Difficulty | Template | Note |
|---|---|---|---|---|---|---|
| hold-0002 | payer_name | Darlene Stevens | None | amount_plain | banco_demo | extraction failed: normalization failed: unrecognized amount format: '40,00' |
| hold-0002 | amount_cents | 4000 | None | amount_plain | banco_demo | extraction failed: normalization failed: unrecognized amount format: '40,00' |
| hold-0002 | currency | USD | None | amount_plain | banco_demo | extraction failed: normalization failed: unrecognized amount format: '40,00' |
| hold-0002 | transfer_date | 2026-07-04 | None | amount_plain | banco_demo | extraction failed: normalization failed: unrecognized amount format: '40,00' |
| hold-0002 | reference | None | None | amount_plain | banco_demo | extraction failed: normalization failed: unrecognized amount format: '40,00' |
| hold-0002 | bank_name | Banco Demo | None | amount_plain | banco_demo | extraction failed: normalization failed: unrecognized amount format: '40,00' |
| hold-0069 | payer_name | Sarah Gallegos | None | date_us | banco_demo | extraction failed: normalization failed: unrecognized amount format: '$50,00' |
| hold-0069 | amount_cents | 5000 | None | date_us | banco_demo | extraction failed: normalization failed: unrecognized amount format: '$50,00' |
| hold-0069 | currency | USD | None | date_us | banco_demo | extraction failed: normalization failed: unrecognized amount format: '$50,00' |
| hold-0069 | transfer_date | 2026-08-02 | None | date_us | banco_demo | extraction failed: normalization failed: unrecognized amount format: '$50,00' |
| hold-0069 | reference | GYM-000342-2026-07 | None | date_us | banco_demo | extraction failed: normalization failed: unrecognized amount format: '$50,00' |
| hold-0069 | bank_name | Banco Demo | None | date_us | banco_demo | extraction failed: normalization failed: unrecognized amount format: '$50,00' |

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

- Bill `GYM-000342-2026-07`: expected `paid`, observed `unpaid`; failed extraction(s): hold-0069; stored-but-wrong: —.

## Cost and latency

| Metric | Value |
|---|---|
| Mean input / output tokens | 2443 / 206.38 |
| Cost per extraction: mean / max | $0.003475 / $0.003538 (hold-0011) |
| Total cost | $0.347490 |
| Projected cost per 1,000 receipts | $3.47 |
| Latency p50 / p95 / max | 3087 / 4630 / 12799 ms |
| Percentile method | nearest-rank over all n=100 receipts |
| Slowest receipt | hold-0047 (1 HTTP attempt(s)) |
| Validation retries / HTTP retries | 0 / 0 |

## Confidence calibration (reported only; nothing is tuned with it)

Mean confidence: all six correct 0.9505 (n=98); any field wrong 0.95 (n=2); excluded (no reading): 0.

## Gates (decide the exit code)

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
