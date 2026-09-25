"""CLI: receipt selection and exit codes (no network: client and budget fetch are faked)."""

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from gym_ops.config import get_settings
from gym_ops.extractor import batch
from gym_ops.extractor.batch import SelectionError, select_receipts
from gym_ops.extractor.budget import fetch_key_status
from gym_ops.extractor.logs import JsonFormatter
from gym_ops.receipts.labels import ReceiptLabel
from tests.extractor.conftest import FAKE_KEY, GOOD_INPUT, Harness, MakeReceipt, message_json
from tests.extractor.test_budget import key_fetch

FETCH_TARGET = "gym_ops.extractor.batch.fetch_key_status"


def _label(receipt_id: str, scenario: str) -> str:
    label: dict[str, Any] = {
        "receipt_id": receipt_id,
        "file": f"{receipt_id}.png",
        "scenario": scenario,
        "template": "banco_demo",
        "difficulty": [],
        "adversarial": scenario == "adversarial_injection",
        "width": 64,
        "height": 48,
        "truth": {
            "payer_name": "Dana Smith",
            "amount_cents": 123456,
            "currency": "USD",
            "transfer_date": "2026-09-19",
            "reference": "GYM-000123-2026-09",
            "bank_name": "Banco Demo",
        },
        "expected": {
            "bill_reference": "GYM-000123-2026-09",
            "bill_status_after_reconciliation": "paid",
            "unidentified_reason": None,
        },
    }
    return ReceiptLabel.model_validate(label).model_dump_json()


@pytest.fixture
def dataset(make_receipt: MakeReceipt, tmp_path: Path) -> tuple[Path, Path]:
    scenarios = ["exact_payment", "adversarial_injection", "exact_payment", "unknown_payer"]
    labels = tmp_path / "labels.jsonl"
    lines = []
    for i, scenario in enumerate(scenarios, start=1):
        make_receipt(f"rcpt-{i:04d}")
        lines.append(_label(f"rcpt-{i:04d}", scenario))
    labels.write_text("\n".join(lines) + "\n")
    return tmp_path / "receipts", labels


def _stems(paths: list[Path]) -> list[str]:
    return [p.stem for p in paths]


def test_select_all_sorted(dataset: tuple[Path, Path]) -> None:
    assert _stems(select_receipts(*dataset)) == ["rcpt-0001", "rcpt-0002", "rcpt-0003", "rcpt-0004"]


def test_select_only_accepts_files_and_ids(dataset: tuple[Path, Path]) -> None:
    picked = select_receipts(*dataset, only=["rcpt-0003.png", "rcpt-0001"])
    assert _stems(picked) == ["rcpt-0001", "rcpt-0003"]


def test_select_scenario_and_limit(dataset: tuple[Path, Path]) -> None:
    assert _stems(select_receipts(*dataset, scenario="exact_payment")) == ["rcpt-0001", "rcpt-0003"]
    assert _stems(select_receipts(*dataset, scenario="exact_payment", limit=1)) == ["rcpt-0001"]
    assert _stems(select_receipts(*dataset, limit=2)) == ["rcpt-0001", "rcpt-0002"]


def test_select_errors(dataset: tuple[Path, Path]) -> None:
    with pytest.raises(SelectionError, match="rcpt-0099"):
        select_receipts(*dataset, only=["rcpt-0099.png"])
    with pytest.raises(SelectionError, match="no receipts"):
        select_receipts(*dataset, scenario="duplicate")


@pytest.fixture
def cli_env(
    monkeypatch: pytest.MonkeyPatch, harness: Harness, members_db: Path
) -> Iterator[Harness]:
    """Point main() at the fake key and mocked transport (env vars win over .env)."""
    monkeypatch.setenv("OPENROUTER_API_KEY", FAKE_KEY)
    monkeypatch.setenv("DB_PATH", str(members_db))
    get_settings.cache_clear()
    monkeypatch.setattr(batch, "build_client", lambda settings, counter: harness.client)
    yield harness
    get_settings.cache_clear()
    for handler in list(logging.getLogger().handlers):
        if isinstance(handler.formatter, JsonFormatter):
            logging.getLogger().removeHandler(handler)


def _argv(dataset: tuple[Path, Path], tmp_path: Path, *extra: str) -> list[str]:
    receipts, labels = dataset
    return [
        "--receipts-dir", str(receipts),
        "--labels", str(labels),
        "--out", str(tmp_path / "extractions.jsonl"),
        *extra,
    ]  # fmt: skip


def test_main_runs_and_prints_summary(
    cli_env: Harness,
    dataset: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setattr(FETCH_TARGET, lambda key: fetch_key_status(key, key_fetch(1.9)))
    cli_env.api.queue(message_json(GOOD_INPUT))

    code = batch.main(_argv(dataset, tmp_path, "--scenario", "adversarial_injection"))

    assert code == 0
    summary = json.loads(capsys.readouterr().out)
    assert summary["processed"] == 1 and summary["receipt_ids"] == ["rcpt-0002"]
    assert summary["limit_remaining_before_usd"] == 1.9


def test_main_budget_abort_exit_code(
    cli_env: Harness,
    dataset: tuple[Path, Path],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(FETCH_TARGET, lambda key: fetch_key_status(key, key_fetch(0.0)))

    assert batch.main(_argv(dataset, tmp_path)) == batch.EXIT_BUDGET
    assert cli_env.api.requests == []


@pytest.mark.parametrize("extra", [["--limit", "0"], ["--only", "rcpt-0404.png"]])
def test_main_bad_selection_exit_code(
    cli_env: Harness, dataset: tuple[Path, Path], tmp_path: Path, extra: list[str]
) -> None:
    assert batch.main(_argv(dataset, tmp_path, *extra)) == 1
    assert cli_env.api.requests == []
