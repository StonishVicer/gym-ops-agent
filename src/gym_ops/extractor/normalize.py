"""Pure normalization from as-printed receipt text to stored values.

Every format the synthetic dataset prints (SPEC FR-5 difficulty tags) is accepted;
anything else raises `NormalizationError` rather than being guessed at. Soft
problems that still leave a usable value (unknown bank, odd reference shape) are
returned as flags so they reach the logs and `extractions.jsonl`.
"""

import re
from datetime import date
from typing import Final

from gym_ops.extractor.schema import ExtractedPayment, ReceiptReading

# The three fictional banks the gym receives transfers from (SPEC FR-5).
KNOWN_BANKS: Final = ("Banco Demo", "Banco Ficticio del Sur", "Cooperativa Ejemplo")

# Bill references are generated as GYM-<membership:06>-<year>-<month>.
REFERENCE_RE: Final = re.compile(r"^GYM-\d{6}-\d{4}-\d{2}$")

FLAG_UNKNOWN_BANK: Final = "unknown_bank"
FLAG_REFERENCE_SHAPE: Final = "reference_shape"

# `$1,234.56`, `1,234.56`, `1234.56`, `USD 1,234.56` (+ `US$`, trailing `USD`, whole
# dollars). The integer part is either plain digits or correctly grouped thousands,
# so `1,23.45` or European `1.234,56` are rejected, not misread.
_AMOUNT_RE: Final = re.compile(
    r"^(?:(?:USD|US\$|\$)\s*)?(?P<int>\d{1,3}(?:,\d{3})+|\d+)(?:\.(?P<frac>\d{2}))?(?:\s*USD)?$"
)
_ISO_RE: Final = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_US_RE: Final = re.compile(r"^(\d{1,2})/(\d{1,2})/(\d{4})$")
_LONG_RE: Final = re.compile(r"^([A-Za-z]{3,9})\.?\s+(\d{1,2}),?\s+(\d{4})$")
_MONTHS: Final = {
    name: i + 1
    for i, name in enumerate(
        ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")
    )
}
_FULL_MONTHS: Final = (
    "january",
    "february",
    "march",
    "april",
    "may",
    "june",
    "july",
    "august",
    "september",
    "october",
    "november",
    "december",
)
_CURRENCY_ALIASES: Final = {"$": "USD", "US$": "USD", "USD": "USD"}
_WS: Final = re.compile(r"\s+")


class NormalizationError(ValueError):
    """A required field is missing or not in any accepted format."""


def _collapse(text: str) -> str:
    return _WS.sub(" ", text).strip()


def parse_amount_cents(text: str) -> int:
    """`'USD 1,234.56'` -> 123456. Raises NormalizationError for anything ambiguous."""
    match = _AMOUNT_RE.match(_collapse(text).upper())
    if match is None:
        raise NormalizationError(f"unrecognized amount format: {text!r}")
    whole = int(match["int"].replace(",", ""))
    cents = whole * 100 + int(match["frac"] or 0)
    if cents <= 0:
        raise NormalizationError(f"amount must be positive: {text!r}")
    return cents


def _month_number(name: str) -> int:
    key = name.casefold()
    if key in _FULL_MONTHS:
        return _FULL_MONTHS.index(key) + 1
    if key == "sept":
        return 9
    if key in _MONTHS:
        return _MONTHS[key]
    raise NormalizationError(f"unknown month: {name!r}")


def parse_date(text: str) -> date:
    """`'2026-09-19'`, `'09/19/2026'` (US order) or `'Sep 19, 2026'` -> date(2026, 9, 19)."""
    value = _collapse(text)
    try:
        if m := _ISO_RE.match(value):
            return date(int(m[1]), int(m[2]), int(m[3]))
        if m := _US_RE.match(value):
            return date(int(m[3]), int(m[1]), int(m[2]))
        if m := _LONG_RE.match(value):
            return date(int(m[3]), _month_number(m[1]), int(m[2]))
    except ValueError as exc:  # e.g. 02/30/2026
        raise NormalizationError(f"invalid date: {text!r}") from exc
    raise NormalizationError(f"unrecognized date format: {text!r}")


def canonical_bank(text: str) -> tuple[str, bool]:
    """Map to a known bank's canonical name, case/space-insensitively.

    Returns `(name, known)`; an unknown bank keeps its (whitespace-collapsed) text.
    """
    value = _collapse(text)
    if not value:
        raise NormalizationError("bank name is empty")
    key = value.casefold()
    for bank in KNOWN_BANKS:
        if bank.casefold() == key:
            return bank, True
    return value, False


def normalize_reference(text: str | None) -> tuple[str | None, bool]:
    """Trim and uppercase. Returns `(reference, valid_shape)`; empty -> `(None, True)`."""
    if text is None:
        return None, True
    value = text.strip().upper()
    if not value:
        return None, True
    return value, bool(REFERENCE_RE.match(value))


def normalize_currency(text: str) -> str:
    """`'usd'`, `'$'`, `'US$'` -> `'USD'`; any other 3-letter code is kept uppercased."""
    value = _collapse(text).upper()
    if value in _CURRENCY_ALIASES:
        return _CURRENCY_ALIASES[value]
    if re.fullmatch(r"[A-Z]{3}", value):
        return value
    raise NormalizationError(f"unrecognized currency: {text!r}")


def normalize_reading(reading: ReceiptReading) -> tuple[ExtractedPayment, list[str]]:
    """Normalize a validated reading into a storable payment plus soft-warning flags."""
    amount, currency = reading.amount, reading.currency
    day, bank_text = reading.transfer_date, reading.bank_name
    if amount is None or currency is None or day is None or bank_text is None:
        required = {
            "amount": amount,
            "currency": currency,
            "transfer_date": day,
            "bank_name": bank_text,
        }
        missing = ", ".join(name for name, value in required.items() if value is None)
        raise NormalizationError(f"required field(s) unreadable: {missing}")

    flags: list[str] = []
    bank, known = canonical_bank(bank_text)
    if not known:
        flags.append(FLAG_UNKNOWN_BANK)
    reference, valid = normalize_reference(reading.reference)
    if not valid:
        flags.append(FLAG_REFERENCE_SHAPE)
    payer = _collapse(reading.payer_name) if reading.payer_name else None

    payment = ExtractedPayment(
        payer_name=payer or None,
        amount_cents=parse_amount_cents(amount),
        currency=normalize_currency(currency),
        transfer_date=parse_date(day),
        reference=reference,
        bank_name=bank,
    )
    return payment, flags
