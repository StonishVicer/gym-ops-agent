"""Normalization of as-printed receipt text (pure functions)."""

from datetime import date
from itertools import product
from typing import get_args

import pytest

from gym_ops.extractor.normalize import (
    FLAG_REFERENCE_SHAPE,
    FLAG_UNKNOWN_BANK,
    KNOWN_BANKS,
    NormalizationError,
    canonical_bank,
    normalize_currency,
    normalize_reading,
    normalize_reference,
    parse_amount_cents,
    parse_date,
)
from gym_ops.extractor.schema import ReceiptReading
from gym_ops.receipts.labels import DifficultyTag
from gym_ops.receipts.render import BANKS, format_amount, format_date

AMOUNT_TAGS: list[tuple[DifficultyTag, ...]] = [
    (),
    ("amount_plain",),
    ("amount_no_symbol",),
    ("amount_usd_code",),
]
DATE_TAGS: list[tuple[DifficultyTag, ...]] = [(), ("date_us",), ("date_long",)]
SAMPLE_CENTS = [1, 99, 100, 4000, 3599, 123456, 100000, 99999999]
SAMPLE_DAYS = [date(2026, 1, 1), date(2026, 9, 19), date(2026, 12, 31), date(2024, 2, 29)]


# --- the dataset's own formats, produced by the real renderer -----------------------


@pytest.mark.parametrize(("tags", "cents"), list(product(AMOUNT_TAGS, SAMPLE_CENTS)))
def test_every_dataset_amount_format_round_trips(
    tags: tuple[DifficultyTag, ...], cents: int
) -> None:
    assert parse_amount_cents(format_amount(cents, tags)) == cents


@pytest.mark.parametrize(("tags", "day"), list(product(DATE_TAGS, SAMPLE_DAYS)))
def test_every_dataset_date_format_round_trips(tags: tuple[DifficultyTag, ...], day: date) -> None:
    assert parse_date(format_date(day, tags)) == day


def test_every_difficulty_tag_for_amounts_and_dates_is_covered() -> None:
    covered = {t for tags in AMOUNT_TAGS + DATE_TAGS for t in tags}
    expected = {t for t in get_args(DifficultyTag) if t.startswith(("amount_", "date_"))}
    assert covered == expected


def test_known_banks_match_the_receipt_templates() -> None:
    assert set(KNOWN_BANKS) == {style.bank_name for style in BANKS.values()}


# --- amounts ------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "cents"),
    [
        ("1,234.56", 123456),
        ("1234.56", 123456),
        ("$1,234.56", 123456),
        ("USD 1,234.56", 123456),
        ("  $ 1,234.56 ", 123456),
        ("usd 1,234.56", 123456),
        ("USD1234.56", 123456),
        ("US$ 40.00", 4000),
        ("40.00 USD", 4000),
        ("$40", 4000),
        ("0.01", 1),
        ("$1,000,000.00", 100000000),
    ],
)
def test_amount_variants(text: str, cents: int) -> None:
    assert parse_amount_cents(text) == cents


@pytest.mark.parametrize(
    "text",
    [
        "",
        "$",
        "0.00",
        "-40.00",
        "1.234,56",  # European grouping: ambiguous, rejected
        "1,23.45",  # bad grouping
        "12,34",
        "40.5",  # one decimal: not a printed money format
        "40.000",
        "EUR 40.00",
        "$40.00 fee",
        "forty dollars",
    ],
)
def test_amount_rejects(text: str) -> None:
    with pytest.raises(NormalizationError):
        parse_amount_cents(text)


# --- dates --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "day"),
    [
        ("2026-09-19", date(2026, 9, 19)),
        ("09/19/2026", date(2026, 9, 19)),
        ("9/5/2026", date(2026, 9, 5)),
        ("Sep 19, 2026", date(2026, 9, 19)),
        ("sep 19 2026", date(2026, 9, 19)),
        ("Sept. 5, 2026", date(2026, 9, 5)),
        ("September 19, 2026", date(2026, 9, 19)),
        ("  May   1,  2026 ", date(2026, 5, 1)),
    ],
)
def test_date_variants(text: str, day: date) -> None:
    assert parse_date(text) == day


@pytest.mark.parametrize(
    "text",
    ["", "2026-9-19", "19/09/2026", "02/30/2026", "2026-13-01", "Smarch 1, 2026", "yesterday"],
)
def test_date_rejects(text: str) -> None:
    with pytest.raises(NormalizationError):
        parse_date(text)


# --- banks, references, currency -----------------------------------------------------


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Banco Demo", ("Banco Demo", True)),
        ("COOPERATIVA EJEMPLO", ("Cooperativa Ejemplo", True)),
        ("  banco   ficticio del SUR ", ("Banco Ficticio del Sur", True)),
        ("Some Other Bank", ("Some Other Bank", False)),
        ("  Some   Other Bank ", ("Some Other Bank", False)),
    ],
)
def test_canonical_bank(text: str, expected: tuple[str, bool]) -> None:
    assert canonical_bank(text) == expected


def test_empty_bank_rejected() -> None:
    with pytest.raises(NormalizationError):
        canonical_bank("   ")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (" gym-000123-2026-09 ", ("GYM-000123-2026-09", True)),
        ("GYM-000123-2026-09", ("GYM-000123-2026-09", True)),
        ("TX-99", ("TX-99", False)),
        ("GYM-123-2026-09", ("GYM-123-2026-09", False)),
        ("", (None, True)),
        ("   ", (None, True)),
        (None, (None, True)),
    ],
)
def test_normalize_reference(text: str | None, expected: tuple[str | None, bool]) -> None:
    assert normalize_reference(text) == expected


@pytest.mark.parametrize(
    ("text", "code"),
    [("USD", "USD"), ("usd", "USD"), ("$", "USD"), ("US$", "USD"), (" eur ", "EUR")],
)
def test_currency(text: str, code: str) -> None:
    assert normalize_currency(text) == code


@pytest.mark.parametrize("text", ["", "dollars", "U$", "12"])
def test_currency_rejects(text: str) -> None:
    with pytest.raises(NormalizationError):
        normalize_currency(text)


# --- whole reading --------------------------------------------------------------------


def _reading(**overrides: object) -> ReceiptReading:
    base: dict[str, object] = {
        "payer_name": "  Dana   Smith ",
        "amount": "USD 1,234.56",
        "currency": "USD",
        "reference": " gym-000123-2026-09",
        "bank_name": "BANCO DEMO",
        "transfer_date": "Sep 19, 2026",
        "confidence": 0.9,
        "injection_detected": False,
        "notes": None,
    }
    return ReceiptReading.model_validate(base | overrides)


def test_normalize_reading() -> None:
    payment, flags = normalize_reading(_reading())
    assert payment.model_dump() == {
        "payer_name": "Dana Smith",
        "amount_cents": 123456,
        "currency": "USD",
        "transfer_date": date(2026, 9, 19),
        "reference": "GYM-000123-2026-09",
        "bank_name": "Banco Demo",
    }
    assert flags == []


def test_normalize_reading_flags_soft_problems() -> None:
    payment, flags = normalize_reading(_reading(bank_name="Other Bank", reference="abc"))
    assert payment.bank_name == "Other Bank"
    assert payment.reference == "ABC"
    assert flags == [FLAG_UNKNOWN_BANK, FLAG_REFERENCE_SHAPE]


def test_normalize_reading_allows_missing_payer_and_reference() -> None:
    payment, _ = normalize_reading(_reading(payer_name=None, reference=None))
    assert payment.payer_name is None and payment.reference is None
    payment, _ = normalize_reading(_reading(payer_name="   "))
    assert payment.payer_name is None


@pytest.mark.parametrize("field", ["amount", "currency", "transfer_date", "bank_name"])
def test_normalize_reading_requires_core_fields(field: str) -> None:
    with pytest.raises(NormalizationError, match=field):
        normalize_reading(_reading(**{field: None}))
