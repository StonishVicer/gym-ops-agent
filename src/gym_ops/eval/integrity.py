"""Load a frozen run and prove it is the run that was frozen, before scoring anything.

* `extractions.jsonl` must hash to the snapshot's `extractions_sha256`.
* The labels are regenerated from the seeds in `metadata.json` (no images, no API):
  generation is deterministic (SPEC FR-4), so the regenerated `labels.jsonl` bytes
  must hash to `labels_sha256`. If a local copy under `data/` exists it must match
  too. This makes the eval self-contained and runnable in CI.
* The records must cover exactly the labelled receipts.

Any mismatch raises `IntegrityError`; nothing is scored from a run that fails it.
"""

import hashlib
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

from gym_ops.config import get_settings
from gym_ops.db.connection import get_readonly_connection
from gym_ops.db.seed import SeedConfig, seed_database
from gym_ops.extractor.runs import RunMetadata
from gym_ops.extractor.store import ExtractionRecord, read_records
from gym_ops.receipts.generate import (
    GeneratorConfig,
    build_labels,
    load_bills,
    load_member_names,
)
from gym_ops.receipts.labels import IdPrefix, ReceiptLabel

ID_PREFIX: Final[dict[str, IdPrefix]] = {"dev": "rcpt", "holdout": "hold"}
LOCAL_LABELS: Final = {
    "dev": Path("data/labels.jsonl"),
    "holdout": Path("data/holdout/labels.jsonl"),
}


class IntegrityError(RuntimeError):
    """A snapshot's files do not match its recorded hashes (or each other)."""


@dataclass(frozen=True)
class LoadedRun:
    name: str
    meta: RunMetadata
    records: dict[str, ExtractionRecord]
    labels: dict[str, ReceiptLabel]  # receipt_id -> label, in id order
    db_path: Path  # fresh seeded DB (no transfers) the labels were regenerated from


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def serialize_labels(labels: list[ReceiptLabel]) -> bytes:
    """Byte-for-byte what `receipts.generate` writes to labels.jsonl."""
    return "".join(label.model_dump_json() + "\n" for label in labels).encode("utf-8")


def seed_fresh_db(meta: RunMetadata, path: Path) -> Path:
    """A fresh DB from the run's seed, as `make holdout` builds it: no transfers."""
    seed_database(
        path,
        SeedConfig(
            seed=meta.db_seed,
            members=meta.db_members,
            reference_date=date.fromisoformat(meta.reference_date),
        ),
    )
    return path


def regenerate_labels(meta: RunMetadata, db_path: Path) -> list[ReceiptLabel]:
    if meta.split not in ID_PREFIX:
        raise IntegrityError(f"unknown split {meta.split!r}")
    conn = get_readonly_connection(db_path)
    try:
        bills, names = load_bills(conn), load_member_names(conn)
    finally:
        conn.close()
    config = GeneratorConfig(
        seed=meta.receipts_seed,
        n=meta.receipts,
        n_max=meta.receipts,
        reference_date=date.fromisoformat(meta.reference_date),
        window_days=get_settings().MATCH_WINDOW_DAYS,
        id_prefix=ID_PREFIX[meta.split],
    )
    labels, _specs = build_labels(bills, names, config)
    return labels


def load_run(run_dir: Path, workdir: Path, local_labels: Path | None = None) -> LoadedRun:
    """Verify and load one snapshot. `workdir` receives a fresh seeded DB."""
    meta = RunMetadata.model_validate_json((run_dir / "metadata.json").read_text("utf-8"))
    extractions = (run_dir / "extractions.jsonl").read_bytes()
    if sha256_bytes(extractions) != meta.extractions_sha256:
        raise IntegrityError(f"{run_dir.name}: extractions.jsonl does not match its metadata hash")

    db_path = seed_fresh_db(meta, workdir / f"{meta.name}.db")
    labels = regenerate_labels(meta, db_path)
    if sha256_bytes(serialize_labels(labels)) != meta.labels_sha256:
        raise IntegrityError(f"{run_dir.name}: regenerated labels do not match labels_sha256")
    local = local_labels if local_labels is not None else LOCAL_LABELS[meta.split]
    if local.is_file() and sha256_bytes(local.read_bytes()) != meta.labels_sha256:
        raise IntegrityError(f"{run_dir.name}: {local} does not match labels_sha256")

    records = read_records(run_dir / "extractions.jsonl")
    by_id = {label.receipt_id: label for label in labels}
    if set(records) != set(by_id) or len(records) != meta.receipts:
        raise IntegrityError(f"{run_dir.name}: records do not cover exactly the labelled receipts")
    return LoadedRun(meta.name, meta, records, by_id, db_path)
