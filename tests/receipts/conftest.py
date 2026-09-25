from dataclasses import dataclass
from datetime import date
from pathlib import Path

import pytest

from gym_ops.db.seed import SeedConfig, seed_database
from gym_ops.receipts.generate import GenerationSummary, GeneratorConfig, generate, read_labels
from gym_ops.receipts.labels import ReceiptLabel

REFERENCE_DATE = date(2026, 9, 25)
WINDOW_DAYS = 5
SEED_CONFIG = SeedConfig(seed=42, members=300, reference_date=REFERENCE_DATE)
GEN_CONFIG = GeneratorConfig(seed=7, reference_date=REFERENCE_DATE, window_days=WINDOW_DAYS)
# Held-out test set (SPEC "Evaluation protocol"): same DB, seed 8, `hold-` ids.
HOLDOUT_CONFIG = GeneratorConfig(
    seed=8, reference_date=REFERENCE_DATE, window_days=WINDOW_DAYS, id_prefix="hold"
)


@dataclass(frozen=True)
class GeneratedSet:
    db_path: Path
    out_dir: Path
    labels_path: Path
    summary: GenerationSummary
    labels: list[ReceiptLabel]


@pytest.fixture(scope="session")
def seeded_db_path(tmp_path_factory: pytest.TempPathFactory) -> Path:
    path = tmp_path_factory.mktemp("receipts_seeded") / "gym.db"
    seed_database(path, SEED_CONFIG)
    return path


@pytest.fixture(scope="session")
def generated(seeded_db_path: Path, tmp_path_factory: pytest.TempPathFactory) -> GeneratedSet:
    """One full N=100 generation, shared by every read-only test."""
    root = tmp_path_factory.mktemp("receipts_out")
    out_dir, labels_path = root / "receipts", root / "labels.jsonl"
    summary = generate(seeded_db_path, out_dir, labels_path, GEN_CONFIG)
    return GeneratedSet(seeded_db_path, out_dir, labels_path, summary, read_labels(labels_path))


@pytest.fixture(scope="session")
def generated_holdout(
    seeded_db_path: Path, tmp_path_factory: pytest.TempPathFactory
) -> GeneratedSet:
    """The held-out N=100 set (`make holdout`), generated from the same seeded DB."""
    root = tmp_path_factory.mktemp("receipts_holdout")
    out_dir, labels_path = root / "receipts", root / "labels.jsonl"
    summary = generate(seeded_db_path, out_dir, labels_path, HOLDOUT_CONFIG)
    return GeneratedSet(seeded_db_path, out_dir, labels_path, summary, read_labels(labels_path))
