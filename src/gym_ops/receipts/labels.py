"""Ground-truth label schema for synthetic receipts (SPEC FR-4, FR-5).

One `ReceiptLabel` per image, one JSON object per line in `labels.jsonl`.

* `truth` holds the six fields the extractor must read off the image, with the same
  names as `ExtractedPayment` (FR-6), so the eval compares field to field.
* `expected` is what reconciliation (FR-14, ADR-0005) must report for this receipt
  once *every* receipt in the set is loaded: the bill it links to and that bill's
  final status, or why it stays unidentified.
"""

from datetime import date
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from gym_ops.mcp_server.models import BillStatus, UnidentifiedReason

# Canonical scenario enum (SPEC FR-5). Code, labels and tests use exactly these names.
Scenario = Literal[
    "exact_payment",
    "partial_only",
    "topup_with_reference",
    "topup_without_reference",
    "overpayment",
    "duplicate",
    "late_with_reference",
    "name_date_match",
    "ambiguous",
    "unknown_payer",
    "outside_window_no_ref",
    "adversarial_injection",
    "multiple_amounts",
]

# Bank layout templates. All three banks are fictional.
Template = Literal["banco_demo", "banco_ficticio_del_sur", "cooperativa_ejemplo"]

# Difficulty tags, independent of scenario. An empty list means a "clean" receipt:
# `$1,234.56` amounts, ISO dates, no image degradation.
DifficultyTag = Literal[
    "amount_plain",  # 1234.56
    "amount_no_symbol",  # 1,234.56
    "amount_usd_code",  # USD 1,234.56
    "date_us",  # 09/19/2026
    "date_long",  # Sep 19, 2026
    "rotation",  # +/- 3 degrees
    "blur",  # light Gaussian blur
    "jpeg_noise",  # JPEG round-trip artifacts
]


class Truth(BaseModel):
    """What the receipt image shows: the extractor's target (`ExtractedPayment` fields)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    payer_name: str = Field(min_length=1)
    amount_cents: int = Field(gt=0, strict=True)
    currency: Literal["USD"]
    transfer_date: date
    reference: str | None
    bank_name: str = Field(min_length=1)


class Expected(BaseModel):
    """Reconciliation outcome for this receipt after all receipts are reconciled."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    bill_reference: str | None
    bill_status_after_reconciliation: BillStatus | None
    unidentified_reason: UnidentifiedReason | None

    @model_validator(mode="after")
    def _linked_xor_unidentified(self) -> Self:
        linked = (
            self.bill_reference is not None
            and self.bill_status_after_reconciliation is not None
            and self.unidentified_reason is None
        )
        unlinked = (
            self.bill_reference is None
            and self.bill_status_after_reconciliation is None
            and self.unidentified_reason is not None
        )
        if not (linked or unlinked):
            raise ValueError(
                "either bill_reference + bill_status_after_reconciliation, "
                "or unidentified_reason alone"
            )
        return self


class ReceiptLabel(BaseModel):
    """One line of `labels.jsonl`."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    receipt_id: str = Field(pattern=r"^rcpt-[0-9]{4}$")
    file: str  # image filename, relative to the receipts directory
    scenario: Scenario
    template: Template
    difficulty: list[DifficultyTag]
    adversarial: bool
    width: int = Field(gt=0, le=1000)
    height: int = Field(gt=0, le=1000)
    truth: Truth
    expected: Expected
