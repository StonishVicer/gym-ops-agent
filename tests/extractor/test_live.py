"""One real extraction through OpenRouter (1 receipt, ~$0.003).

Marked `live`: excluded from `make test` and `make test-ci`; run with `make test-live`.
Needs OPENROUTER_API_KEY (Settings reads .env) and `make receipts`.
"""

from pathlib import Path

import pytest

from gym_ops.config import Settings
from gym_ops.extractor.client import RequestCounter, build_client
from gym_ops.extractor.extract import extract_receipt
from gym_ops.receipts.generate import read_labels

RECEIPT = Path("data/receipts/rcpt-0008.png")  # clean exact_payment
LABELS = Path("data/labels.jsonl")


@pytest.mark.live
def test_live_extraction_matches_ground_truth() -> None:
    settings = Settings()
    if settings.OPENROUTER_API_KEY is None:
        pytest.skip("OPENROUTER_API_KEY not set")
    if not RECEIPT.is_file() or not LABELS.is_file():
        pytest.skip("run `make receipts` first")
    truth = next(lb.truth for lb in read_labels(LABELS) if lb.file == RECEIPT.name)
    counter = RequestCounter()

    result = extract_receipt(build_client(settings, counter=counter), RECEIPT, settings, counter)

    assert result.ok, result.error
    assert result.payment is not None
    assert result.payment.amount_cents == truth.amount_cents
    assert result.payment.transfer_date == truth.transfer_date
    assert result.payment.reference == truth.reference
    assert result.payment.bank_name == truth.bank_name
    assert result.input_tokens > 0 and result.output_tokens > 0
    assert result.cost_usd < 0.01  # NFR-1 max
