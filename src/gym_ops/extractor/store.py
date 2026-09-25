"""Persist extraction results (SPEC FR-7, FR-8).

* `extracted_payments`: one row per `receipt_id` (UNIQUE), upserted, so re-running a
  receipt replaces its row instead of duplicating it. A receipt whose latest
  extraction failed has its stale row removed: the table always reflects the most
  recent run and never keeps a value the latest run could not confirm.
* `data/extractions.jsonl`: one `ExtractionRecord` per receipt, keyed by
  `receipt_id` and rewritten atomically, so smoke and full runs merge cleanly.
"""

import sqlite3
from datetime import UTC, datetime
from pathlib import Path

from gym_ops.extractor.schema import ExtractionResult

UPSERT_PAYMENT = (
    "INSERT INTO extracted_payments (receipt_id, member_id, payer_name, amount_cents, "
    "currency, transfer_date, reference, bank_name, model_id, input_tokens, output_tokens, "
    "cost_usd_micros, latency_ms, extracted_at) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
    "ON CONFLICT (receipt_id) DO UPDATE SET "
    "member_id = excluded.member_id, payer_name = excluded.payer_name, "
    "amount_cents = excluded.amount_cents, currency = excluded.currency, "
    "transfer_date = excluded.transfer_date, reference = excluded.reference, "
    "bank_name = excluded.bank_name, model_id = excluded.model_id, "
    "input_tokens = excluded.input_tokens, output_tokens = excluded.output_tokens, "
    "cost_usd_micros = excluded.cost_usd_micros, latency_ms = excluded.latency_ms, "
    "extracted_at = excluded.extracted_at"
)
DELETE_PAYMENT = "DELETE FROM extracted_payments WHERE receipt_id = ?"


class ExtractionRecord(ExtractionResult):
    """One line of `extractions.jsonl`: the result plus resolution and provenance."""

    file: str
    member_id: int | None = None
    extracted_at: str


def utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_payment(
    conn: sqlite3.Connection, result: ExtractionResult, member_id: int | None, extracted_at: str
) -> bool:
    """Upsert a successful extraction, or delete the receipt's stale row. Returns stored?"""
    payment = result.payment
    with conn:
        if not result.ok or payment is None or result.model_id is None:
            conn.execute(DELETE_PAYMENT, (result.receipt_id,))
            return False
        conn.execute(
            UPSERT_PAYMENT,
            (
                result.receipt_id,
                member_id,
                payment.payer_name,
                payment.amount_cents,
                payment.currency,
                payment.transfer_date.isoformat(),
                payment.reference,
                payment.bank_name,
                result.model_id,
                result.input_tokens,
                result.output_tokens,
                result.cost_usd_micros,
                result.latency_ms,
                extracted_at,
            ),
        )
    return True


def read_records(path: str | Path) -> dict[str, ExtractionRecord]:
    target = Path(path)
    if not target.is_file():
        return {}
    with target.open(encoding="utf-8") as fh:
        records = [ExtractionRecord.model_validate_json(line) for line in fh if line.strip()]
    return {r.receipt_id: r for r in records}


def write_records(path: str | Path, records: list[ExtractionRecord]) -> None:
    """Merge `records` into the JSONL file by receipt_id; atomic replace."""
    target = Path(path)
    merged = read_records(target)
    merged.update({r.receipt_id: r for r in records})
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        for receipt_id in sorted(merged):
            fh.write(merged[receipt_id].model_dump_json() + "\n")
    tmp.replace(target)
