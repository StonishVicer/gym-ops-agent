"""Only fictional banks appear in the receipts code and labels (SPEC §5, no real bank data)."""

import re
from pathlib import Path

from gym_ops.receipts.render import BANKS
from tests.receipts.conftest import GeneratedSet

RECEIPTS_SRC = Path(__file__).resolve().parents[2] / "src" / "gym_ops" / "receipts"
FICTIONAL_BANKS = {"Banco Demo", "Banco Ficticio del Sur", "Cooperativa Ejemplo"}

# Well-known real banks; a small static denylist, matched case-insensitively on word
# boundaries. Payer names are excluded from the label scan: Faker first names such
# as "Chase" are people, not banks.
REAL_BANKS = (
    "Bank of America",
    "Chase",
    "JPMorgan",
    "Wells Fargo",
    "Citibank",
    "Citigroup",
    "Capital One",
    "U.S. Bank",
    "PNC",
    "Truist",
    "TD Bank",
    "Goldman Sachs",
    "Morgan Stanley",
    "HSBC",
    "Barclays",
    "Santander",
    "BBVA",
    "Scotiabank",
    "Banorte",
    "Bancolombia",
    "Itaú",
    "Bradesco",
    "Nubank",
    "BNP Paribas",
    "Deutsche Bank",
    "Revolut",
    "Monzo",
)
DENY = re.compile("|".join(rf"\b{re.escape(name)}\b" for name in REAL_BANKS), re.IGNORECASE)


def test_bank_styles_are_fictional() -> None:
    assert {style.bank_name for style in BANKS.values()} == FICTIONAL_BANKS


def test_receipts_source_names_no_real_bank() -> None:
    files = sorted(RECEIPTS_SRC.glob("*.py"))
    assert files
    for path in files:
        assert DENY.search(path.read_text(encoding="utf-8")) is None, path.name


def test_labels_name_no_real_bank(generated: GeneratedSet) -> None:
    for label in generated.labels:
        assert label.truth.bank_name in FICTIONAL_BANKS
        for text in (label.truth.bank_name, label.template, label.truth.reference or ""):
            assert DENY.search(text) is None, label.receipt_id
