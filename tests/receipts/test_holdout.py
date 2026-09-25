"""Held-out set (SPEC "Evaluation protocol"): its own seed and id namespace."""

import pytest
from pydantic import ValidationError

from gym_ops.receipts.labels import ReceiptLabel
from tests.receipts.conftest import GeneratedSet


def test_holdout_ids_are_namespaced(generated_holdout: GeneratedSet) -> None:
    ids = [lb.receipt_id for lb in generated_holdout.labels]
    assert ids == [f"hold-{i:04d}" for i in range(1, 101)]
    files = sorted(p.name for p in generated_holdout.out_dir.iterdir())
    assert files == [f"{rid}.png" for rid in ids]


def test_dev_and_holdout_ids_never_collide(
    generated: GeneratedSet, generated_holdout: GeneratedSet
) -> None:
    dev = {lb.receipt_id for lb in generated.labels}
    held = {lb.receipt_id for lb in generated_holdout.labels}
    assert dev.isdisjoint(held)
    assert all(rid.startswith("rcpt-") for rid in dev)


def test_holdout_is_a_different_sample(
    generated: GeneratedSet, generated_holdout: GeneratedSet
) -> None:
    """Seed 8 must not replay seed 7: the receipts differ, not just their ids."""

    def content(gs: GeneratedSet) -> list[tuple[str, int, str]]:
        return [(lb.truth.payer_name, lb.truth.amount_cents, str(lb.truth.transfer_date))
                for lb in gs.labels]  # fmt: skip

    assert content(generated) != content(generated_holdout)
    # Same scenario mix (FR-5 counts), so dev and holdout measure the same thing.
    assert generated.summary.scenarios == generated_holdout.summary.scenarios


def test_unknown_prefix_is_rejected() -> None:
    label = ReceiptLabel.model_json_schema()["properties"]["receipt_id"]["pattern"]
    assert label == "^(rcpt|hold)-[0-9]{4}$"
    with pytest.raises(ValidationError):
        ReceiptLabel.model_validate({"receipt_id": "test-0001"})
