"""Oracle: the dataset and the reconciliation rules agree (SPEC FR-5, FR-14, ADR-0005).

Loads every label's ground truth into `extracted_payments` on a copy of the seeded
database, as a perfect extractor would (payers resolved by the production resolver,
ADR-0006), runs the real reconciliation for every billed month, and checks each
receipt's `expected` block exactly. With this at 100%, any eval error is attributable
to extraction, not to the dataset or the rules.
"""

import shutil
from pathlib import Path

import pytest

from gym_ops.config import Settings
from gym_ops.db.connection import get_write_connection
from gym_ops.eval.e2e import reconcile_all_months
from gym_ops.extractor.resolve import MemberDirectory
from gym_ops.mcp_server.models import LinkRule
from gym_ops.receipts.labels import ReceiptLabel
from tests.receipts.conftest import WINDOW_DAYS, GeneratedSet

INSERT_TRANSFER = (
    "INSERT INTO extracted_payments (receipt_id, member_id, payer_name, amount_cents, "
    "currency, transfer_date, reference, bank_name, model_id, input_tokens, output_tokens, "
    "cost_usd_micros, latency_ms, extracted_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'oracle', 0, 0, 0, 0, '2026-09-25T00:00:00Z')"
)


def _load_truth(db: Path, labels: list[ReceiptLabel]) -> None:
    conn = get_write_connection(db)
    try:
        # The production resolver (ADR-0006): tests and extractor share one code path.
        members = MemberDirectory.load(conn)
        with conn:
            for label in labels:
                t = label.truth
                conn.execute(
                    INSERT_TRANSFER,
                    (
                        label.receipt_id,
                        members.resolve(t.payer_name),
                        t.payer_name,
                        t.amount_cents,
                        t.currency,
                        t.transfer_date.isoformat(),
                        t.reference,
                        t.bank_name,
                    ),
                )
    finally:
        conn.close()


def test_window_matches_settings_default() -> None:
    assert Settings.model_fields["MATCH_WINDOW_DAYS"].default == WINDOW_DAYS


@pytest.mark.parametrize("dataset", ["generated", "generated_holdout"], ids=["dev", "holdout"])
def test_every_receipt_reconciles_as_labelled(
    dataset: str, request: pytest.FixtureRequest, tmp_path: Path
) -> None:
    generated: GeneratedSet = request.getfixturevalue(dataset)
    db = tmp_path / "oracle.db"
    shutil.copy(generated.db_path, db)  # never touch data/gym.db or the shared seed
    _load_truth(db, generated.labels)

    # The eval's month loop (gym_ops.eval.e2e): oracle and eval share one code path.
    rec = reconcile_all_months(db, WINDOW_DAYS)
    observed, rules = rec.observed, rec.rules
    touched_bills = {o.bill_reference for o in observed.values() if o.bill_reference}

    mismatches = {
        label.receipt_id: (label.scenario, label.expected, observed.get(label.receipt_id))
        for label in generated.labels
        if observed.get(label.receipt_id) != label.expected
    }
    assert mismatches == {}
    assert set(observed) == {label.receipt_id for label in generated.labels}
    # Only the labelled bills move off `unpaid`: no receipt leaks onto another bill.
    assert touched_bills == {
        label.expected.bill_reference for label in generated.labels if label.expected.bill_reference
    }
    # Reference-less linked receipts are linked by name + date, not by accident.
    for label in generated.labels:
        if label.receipt_id in rules:
            want: LinkRule = "reference" if label.truth.reference else "member_date_window"
            assert rules[label.receipt_id] == want, label.receipt_id
