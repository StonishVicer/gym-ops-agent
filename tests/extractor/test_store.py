"""Persistence (FR-7, FR-8): upsert by receipt_id, failures never stored, JSONL records."""

import json
import sqlite3
from pathlib import Path

import pytest

from gym_ops.db.connection import get_readonly_connection
from gym_ops.extractor.batch import BatchSummary, run_batch
from gym_ops.extractor.store import ExtractionRecord, read_records, write_records
from tests.extractor.conftest import GOOD_INPUT, MODEL_REPORTED, Harness, MakeReceipt, message_json
from tests.extractor.test_budget import key_fetch

ROWS = (
    "SELECT receipt_id, member_id, payer_name, amount_cents, currency, transfer_date, "
    "reference, bank_name, model_id, input_tokens, output_tokens, cost_usd_micros "
    "FROM extracted_payments ORDER BY receipt_id"
)


def _rows(db: Path) -> list[tuple[object, ...]]:
    conn = get_readonly_connection(db)
    try:
        return conn.execute(ROWS).fetchall()
    finally:
        conn.close()


def _run(harness: Harness, images: list[Path], db: Path, out: Path) -> BatchSummary:
    return run_batch(
        images,
        client=harness.client,
        counter=harness.counter,
        settings=harness.settings,
        db_path=db,
        out_path=out,
        fetch=key_fetch(1.9),
    )


def test_cost_micros(
    harness: Harness, make_receipt: MakeReceipt, members_db: Path, tmp_path: Path
) -> None:
    harness.api.queue(message_json(GOOD_INPUT, input_tokens=1500, output_tokens=120))

    _run(harness, [make_receipt("rcpt-0001")], members_db, tmp_path / "x.jsonl")

    assert _rows(members_db) == [
        (
            "rcpt-0001",
            1,  # "Dana Smith" resolved
            "Dana Smith",
            123456,
            "USD",
            "2026-09-19",
            "GYM-000123-2026-09",
            "Banco Demo",
            MODEL_REPORTED,
            1500,
            120,
            2100,
        )
    ]


def test_upsert_by_receipt_id(
    harness: Harness, make_receipt: MakeReceipt, members_db: Path, tmp_path: Path
) -> None:
    """Idempotency: the same receipt twice leaves one row, holding the latest values."""
    image = make_receipt("rcpt-0001")
    out = tmp_path / "extractions.jsonl"
    harness.api.queue(
        message_json(GOOD_INPUT, input_tokens=1500),
        message_json({**GOOD_INPUT, "amount": "$40.00"}, input_tokens=1600),
    )

    _run(harness, [image], members_db, out)
    _run(harness, [image], members_db, out)

    rows = _rows(members_db)
    assert len(rows) == 1
    assert rows[0][3] == 4000 and rows[0][9] == 1600
    lines = out.read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["input_tokens"] == 1600


def test_invalid_tool_input_not_stored(
    harness: Harness, make_receipt: MakeReceipt, members_db: Path, tmp_path: Path
) -> None:
    """FR-7: invalid output (after the retry) is recorded as a failure, never inserted."""
    bad = {**GOOD_INPUT, "amount": 12.50}
    harness.api.queue(message_json(bad), message_json(bad), message_json(GOOD_INPUT))
    out = tmp_path / "extractions.jsonl"

    summary = _run(harness, [make_receipt("rcpt-0001"), make_receipt("rcpt-0002")], members_db, out)

    assert (summary.processed, summary.stored, summary.failed) == (2, 1, 1)
    assert [r[0] for r in _rows(members_db)] == ["rcpt-0002"]  # batch continued
    failed = read_records(out)["rcpt-0001"]
    assert failed.error is not None and failed.error.startswith("validation failed")
    assert failed.raw_input == bad and failed.member_id is None


def test_failed_rerun_removes_stale_row(
    harness: Harness, make_receipt: MakeReceipt, members_db: Path, tmp_path: Path
) -> None:
    image = make_receipt("rcpt-0001")
    harness.api.queue(message_json(GOOD_INPUT))
    _run(harness, [image], members_db, tmp_path / "x.jsonl")
    assert len(_rows(members_db)) == 1

    harness.api.queue_status(400)
    _run(harness, [image], members_db, tmp_path / "x.jsonl")

    assert _rows(members_db) == []


@pytest.mark.parametrize(
    ("payer", "member_id"),
    [("JOSE PEREZ", 2), ("Christopher Williams", None), ("Nobody Here", None)],
)
def test_member_resolution_is_stored(
    harness: Harness,
    make_receipt: MakeReceipt,
    members_db: Path,
    tmp_path: Path,
    payer: str,
    member_id: int | None,
) -> None:
    harness.api.queue(message_json({**GOOD_INPUT, "payer_name": payer}))
    out = tmp_path / "x.jsonl"

    _run(harness, [make_receipt("rcpt-0001")], members_db, out)

    assert _rows(members_db)[0][1] == member_id
    assert read_records(out)["rcpt-0001"].member_id == member_id


def test_record_carries_everything(
    harness: Harness, make_receipt: MakeReceipt, members_db: Path, tmp_path: Path
) -> None:
    harness.api.queue(
        message_json({**GOOD_INPUT, "injection_detected": True, "notes": "ignore rules"})
    )
    out = tmp_path / "x.jsonl"

    summary = _run(harness, [make_receipt("rcpt-0033")], members_db, out)

    record = read_records(out)["rcpt-0033"]
    assert record.file == "rcpt-0033.png"
    assert (
        record.raw_input is not None and record.reading is not None and record.payment is not None
    )
    assert record.reading.injection_detected is True
    assert record.model_id == MODEL_REPORTED
    assert record.attempts == record.http_attempts == 1
    assert record.extracted_at.endswith("Z")
    assert summary.injection_detected == 1


def test_missing_db_fails_before_any_call(
    harness: Harness, make_receipt: MakeReceipt, tmp_path: Path
) -> None:
    with pytest.raises(FileNotFoundError):
        _run(harness, [make_receipt("rcpt-0001")], tmp_path / "missing.db", tmp_path / "x.jsonl")
    assert harness.api.requests == []
    assert not (tmp_path / "missing.db").exists()


def test_write_records_merges_by_receipt_id(tmp_path: Path) -> None:
    out = tmp_path / "x.jsonl"
    a = ExtractionRecord(
        receipt_id="rcpt-0002", file="rcpt-0002.png", extracted_at="2026-09-25T00:00:00Z"
    )
    b = ExtractionRecord(
        receipt_id="rcpt-0001", file="rcpt-0001.png", extracted_at="2026-09-25T00:00:00Z"
    )
    write_records(out, [a])
    write_records(out, [b, a.model_copy(update={"error": "x"})])
    records = read_records(out)
    assert list(records) == ["rcpt-0001", "rcpt-0002"]  # sorted, deduplicated
    assert records["rcpt-0002"].error == "x"
    assert read_records(tmp_path / "absent.jsonl") == {}


UNIQUE_RECEIPT_ID = (
    "SELECT count(*) FROM pragma_index_list('extracted_payments') AS il "
    "JOIN pragma_index_info(il.name) AS ii "
    "WHERE il.\"unique\" = 1 AND ii.name = 'receipt_id'"
)


def test_unique_receipt_id_in_schema(members_db: Path) -> None:
    """The idempotency key is enforced by the schema, not just by the upsert."""
    conn = sqlite3.connect(members_db)
    try:
        assert conn.execute(UNIQUE_RECEIPT_ID).fetchone() == (1,)
    finally:
        conn.close()
