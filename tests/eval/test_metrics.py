"""Metric functions on hand-built fixtures (SPEC FR-16, NFR-3)."""

from datetime import date
from typing import Any

import pytest

from gym_ops.eval.metrics import (
    FIELDS,
    ReceiptScore,
    calibration,
    cost_latency,
    extraction_metrics,
    field_correct,
    score_receipt,
    security_metrics,
)
from gym_ops.extractor.schema import ExtractedPayment, ReceiptReading
from gym_ops.extractor.store import ExtractionRecord
from gym_ops.receipts.labels import ReceiptLabel

TRUTH: dict[str, Any] = {
    "payer_name": "José Pérez",
    "amount_cents": 5000,
    "currency": "USD",
    "transfer_date": "2026-09-19",
    "reference": "GYM-000123-2026-09",
    "bank_name": "Banco Demo",
}


def label(
    rid: str,
    scenario: str = "exact_payment",
    difficulty: list[str] | None = None,
    template: str = "banco_demo",
) -> ReceiptLabel:
    return ReceiptLabel.model_validate(
        {
            "receipt_id": rid,
            "file": f"{rid}.png",
            "scenario": scenario,
            "template": template,
            "difficulty": difficulty or [],
            "adversarial": scenario == "adversarial_injection",
            "width": 720,
            "height": 960,
            "truth": TRUTH,
            "expected": {
                "bill_reference": "GYM-000123-2026-09",
                "bill_status_after_reconciliation": "paid",
                "unidentified_reason": None,
            },
        }
    )


def reading(**over: Any) -> ReceiptReading:
    base: dict[str, Any] = {
        "payer_name": "JOSE PEREZ",
        "amount": "$50.00",
        "currency": "USD",
        "reference": "GYM-000123-2026-09",
        "bank_name": "BANCO DEMO",
        "transfer_date": "2026-09-19",
        "confidence": 0.9,
        "injection_detected": False,
        "notes": None,
    }
    return ReceiptReading.model_validate(base | over)


def record(
    rid: str,
    *,
    ok: bool = True,
    latency: int = 3000,
    cost: float = 0.0035,
    injection: bool = False,
    confidence: float = 0.9,
    **pay: Any,
) -> ExtractionRecord:
    payment = (
        ExtractedPayment.model_validate({**TRUTH, "transfer_date": date(2026, 9, 19)} | pay)
        if ok
        else None
    )
    return ExtractionRecord(
        receipt_id=rid,
        file=f"{rid}.png",
        extracted_at="2026-09-25T00:00:00Z",
        reading=reading(injection_detected=injection, confidence=confidence),
        payment=payment,
        error=None if ok else "normalization failed: unrecognized amount format: '50,00'",
        raw_input={"amount": "$50.00" if ok else "50,00"},
        latency_ms=latency,
        cost_usd=cost,
        input_tokens=2443,
        output_tokens=200,
        attempts=1,
        http_attempts=1,
    )


def scores(*pairs: tuple[ExtractionRecord, ReceiptLabel]) -> list[ReceiptScore]:
    return [score_receipt(r, lb) for r, lb in pairs]


def test_field_rules() -> None:
    assert field_correct("payer_name", "JOSE  PEREZ", "José Pérez")  # ADR-0006 normalization
    assert not field_correct("payer_name", None, "José Pérez")
    assert field_correct("bank_name", " banco   demo ", "Banco Demo")
    assert field_correct("reference", "gym-000123-2026-09", "GYM-000123-2026-09")
    assert field_correct("reference", None, None)
    assert not field_correct("amount_cents", 5001, 5000)
    assert not field_correct("currency", "usd", "USD")  # currency is exact
    assert field_correct("transfer_date", date(2026, 9, 19), date(2026, 9, 19))


def test_failed_extraction_is_wrong_on_every_field_and_stays_in_denominator() -> None:
    s = scores(
        (record("rcpt-0001"), label("rcpt-0001")),
        (record("rcpt-0002", ok=False), label("rcpt-0002")),
    )
    assert s[1].wrong == list(FIELDS)
    m = extraction_metrics(s)
    assert m.n == 2 and m.failures == ["rcpt-0002"]
    for f in FIELDS:
        assert (m.per_field[f].k, m.per_field[f].n) == (1, 2)
    assert (m.all_six.k, m.all_six.n) == (1, 2)


def test_one_wrong_field_only_costs_that_field() -> None:
    s = scores((record("rcpt-0001", amount_cents=4000), label("rcpt-0001")))
    m = extraction_metrics(s)
    assert s[0].wrong == ["amount_cents"]
    assert m.per_field["amount_cents"].k == 0 and m.per_field["payer_name"].k == 1
    assert m.all_six.k == 0


def test_breakdowns_count_every_tag_with_n() -> None:
    s = scores(
        (record("rcpt-0001"), label("rcpt-0001", difficulty=["blur", "rotation"])),
        (
            record("rcpt-0002", ok=False),
            label("rcpt-0002", difficulty=["blur"], template="cooperativa_ejemplo"),
        ),
        (record("rcpt-0003"), label("rcpt-0003")),
    )
    m = extraction_metrics(s)
    assert {k: (v.k, v.n) for k, v in m.by_difficulty.items()} == {
        "blur": (1, 2),
        "rotation": (1, 1),
        "clean": (1, 1),
    }
    assert {k: (v.k, v.n) for k, v in m.by_template.items()} == {
        "banco_demo": (2, 2),
        "cooperativa_ejemplo": (0, 1),
    }


def test_security_uses_only_the_flag() -> None:
    s = scores(
        (record("rcpt-0001", injection=True), label("rcpt-0001", "adversarial_injection")),
        (record("rcpt-0002", ok=False), label("rcpt-0002", "adversarial_injection")),
        (record("rcpt-0003", injection=True), label("rcpt-0003")),
        (record("rcpt-0004"), label("rcpt-0004")),
    )
    m = security_metrics(s)
    assert (m.detection.k, m.detection.n) == (1, 2)
    assert (m.false_positive.k, m.false_positive.n) == (1, 2)
    assert m.flagged_non_adversarial == ["rcpt-0003"]
    assert (m.adversarial_amount_correct.k, m.adversarial_amount_correct.n) == (1, 2)


def test_cost_latency_nearest_rank_and_slowest() -> None:
    lats = [3000, 2500, 12000, 3100]
    s = scores(
        *(
            (record(f"rcpt-000{i}", latency=v, cost=0.001 * i), label(f"rcpt-000{i}"))
            for i, v in enumerate(lats, start=1)
        )
    )
    c = cost_latency(s)
    assert (c.latency_p50_ms, c.latency_p95_ms, c.latency_max_ms) == (3000, 12000, 12000)
    assert c.slowest_receipt == "rcpt-0003"
    assert c.mean_cost_usd == pytest.approx(0.0025) and c.max_cost_receipt == "rcpt-0004"
    assert c.projected_cost_per_1000_usd == pytest.approx(2.5)
    assert "nearest-rank" in c.percentile_method and "n=4" in c.percentile_method


def test_calibration_groups_and_exclusions() -> None:
    no_reading = record("rcpt-0003", ok=False).model_copy(update={"reading": None})
    s = scores(
        (record("rcpt-0001", confidence=0.9), label("rcpt-0001")),
        (record("rcpt-0002", ok=False, confidence=0.5), label("rcpt-0002")),
        (no_reading, label("rcpt-0003")),
    )
    c = calibration(s)
    assert (c.mean_confidence_all_correct, c.n_all_correct) == (0.9, 1)
    assert (c.mean_confidence_any_wrong, c.n_any_wrong) == (0.5, 1)
    assert c.excluded_no_reading == 1
