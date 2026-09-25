"""Batch extraction CLI: `python -m gym_ops.extractor [--limit N] [--only FILE] [--scenario S]`.

Receipts are processed one at a time, in filename order, so each call's latency is
measured without concurrent requests competing for the connection (NFR-2). A failed
receipt is recorded and the batch moves on (FR-7); only the budget guards stop a run.
"""

import argparse
import json
import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, get_args

import anthropic

from gym_ops.config import Settings, get_settings
from gym_ops.db.connection import get_write_connection
from gym_ops.extractor.budget import (
    BudgetError,
    Fetch,
    RunningCost,
    check_budget,
    fetch_key_status,
)
from gym_ops.extractor.client import RequestCounter, build_client
from gym_ops.extractor.extract import extract_receipt
from gym_ops.extractor.logs import configure_logging
from gym_ops.extractor.resolve import MemberDirectory
from gym_ops.extractor.store import ExtractionRecord, save_payment, utc_now, write_records
from gym_ops.receipts.generate import read_labels
from gym_ops.receipts.labels import Scenario

logger = logging.getLogger(__name__)

DEFAULT_RECEIPTS_DIR: Final = "data/receipts"
DEFAULT_LABELS: Final = "data/labels.jsonl"
DEFAULT_OUT: Final = "data/extractions.jsonl"
RECEIPT_GLOB: Final = "rcpt-*.png"
EXIT_BUDGET: Final = 2


class SelectionError(ValueError):
    """--only / --scenario selected a receipt that does not exist, or nothing at all."""


@dataclass
class BatchSummary:
    selected: int
    processed: int = 0
    stored: int = 0
    failed: int = 0
    injection_detected: int = 0
    total_cost_usd: float = 0.0
    limit_remaining_before_usd: float | None = None
    aborted: str | None = None
    receipt_ids: list[str] = field(default_factory=list)


def select_receipts(
    receipts_dir: Path,
    labels_path: Path,
    only: Sequence[str] = (),
    scenario: str | None = None,
    limit: int | None = None,
) -> list[Path]:
    """Filter the receipt images; `only` accepts file names or receipt ids."""
    images = sorted(receipts_dir.glob(RECEIPT_GLOB))
    if only:
        wanted = {Path(name).stem for name in only}
        by_id = {p.stem: p for p in images}
        missing = sorted(wanted - by_id.keys())
        if missing:
            raise SelectionError(f"receipt(s) not found in {receipts_dir}: {', '.join(missing)}")
        images = [by_id[stem] for stem in sorted(wanted)]
    if scenario is not None:
        ids = {lb.receipt_id for lb in read_labels(labels_path) if lb.scenario == scenario}
        images = [p for p in images if p.stem in ids]
    if limit is not None:
        images = images[:limit]
    if not images:
        raise SelectionError("no receipts selected")
    return images


def run_batch(
    images: Sequence[Path],
    *,
    client: anthropic.Anthropic,
    counter: RequestCounter,
    settings: Settings,
    db_path: Path,
    out_path: Path,
    fetch: Fetch | None = None,
) -> BatchSummary:
    """Budget-check, then extract, resolve, store and record each receipt in order."""
    summary = BatchSummary(selected=len(images))
    if not db_path.is_file():
        raise FileNotFoundError(f"database not found: {db_path} (run `make seed`)")
    api_key = settings.require_openrouter_api_key().get_secret_value()
    status = fetch_key_status(api_key, fetch) if fetch else fetch_key_status(api_key)
    summary.limit_remaining_before_usd = status.limit_remaining
    check_budget(status, len(images))  # raises BudgetError before any model call

    running = RunningCost()
    conn = get_write_connection(db_path)
    try:
        members = MemberDirectory.load(conn)
        for image in images:
            result = extract_receipt(client, image, settings, counter)
            payer = result.payment.payer_name if result.payment else None
            member_id = members.resolve(payer) if result.ok else None
            extracted_at = utc_now()
            stored = save_payment(conn, result, member_id, extracted_at)
            record = ExtractionRecord(
                **result.model_dump(),
                file=image.name,
                member_id=member_id,
                extracted_at=extracted_at,
            )
            write_records(out_path, [record])

            summary.processed += 1
            summary.stored += stored
            summary.failed += not result.ok
            summary.injection_detected += bool(result.reading and result.reading.injection_detected)
            summary.receipt_ids.append(result.receipt_id)
            logger.info(
                "receipt processed",
                extra={
                    "event": "receipt_done",
                    "receipt_id": result.receipt_id,
                    "ok": result.ok,
                    "error": result.error,
                    "flags": result.flags,
                    "member_resolved": member_id is not None,
                    "injection_detected": bool(
                        result.reading and result.reading.injection_detected
                    ),
                    "input_tokens": result.input_tokens,
                    "output_tokens": result.output_tokens,
                    "cost_usd": result.cost_usd,
                    "latency_ms": result.latency_ms,
                    "attempts": result.attempts,
                    "http_attempts": result.http_attempts,
                    "http_statuses": result.http_statuses,
                    "prompt_version": result.prompt_version,
                    "running_total_usd": round(running.total_usd + result.cost_usd, 6),
                    "progress": f"{summary.processed}/{summary.selected}",
                },
            )
            summary.total_cost_usd = running.add(result.cost_usd)  # raises past the cap
    except BudgetError as exc:
        summary.total_cost_usd = running.total_usd
        summary.aborted = str(exc)
        logger.error("run aborted: cost cap", extra={"event": "budget_abort", "reason": str(exc)})
    finally:
        conn.close()
    return summary


def build_parser(settings: Settings) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract receipts with Claude (via OpenRouter).")
    parser.add_argument("--limit", type=int, help="process at most N receipts")
    parser.add_argument(
        "--only",
        action="append",
        default=[],
        metavar="FILE",
        help="receipt file name or id; repeatable",
    )
    parser.add_argument("--scenario", choices=get_args(Scenario), help="filter by label scenario")
    parser.add_argument("--receipts-dir", default=DEFAULT_RECEIPTS_DIR)
    parser.add_argument("--labels", default=DEFAULT_LABELS)
    parser.add_argument("--out", default=DEFAULT_OUT)
    parser.add_argument("--db-path", default=settings.DB_PATH)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    settings = get_settings()
    args = build_parser(settings).parse_args(argv)
    key = settings.OPENROUTER_API_KEY
    configure_logging(secrets=[key.get_secret_value()] if key else [])
    if args.limit is not None and args.limit < 1:
        logger.error("--limit must be >= 1", extra={"event": "bad_args"})
        return 1
    try:
        images = select_receipts(
            Path(args.receipts_dir), Path(args.labels), args.only, args.scenario, args.limit
        )
        counter = RequestCounter()
        client = build_client(settings, counter=counter)
        summary = run_batch(
            images,
            client=client,
            counter=counter,
            settings=settings,
            db_path=Path(args.db_path),
            out_path=Path(args.out),
        )
    except BudgetError as exc:
        logger.error("budget check failed", extra={"event": "budget_abort", "reason": str(exc)})
        return EXIT_BUDGET
    except (SelectionError, FileNotFoundError, ValueError) as exc:
        logger.error(str(exc), extra={"event": "startup_error"})
        return 1
    json.dump(summary.__dict__, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return EXIT_BUDGET if summary.aborted else 0
