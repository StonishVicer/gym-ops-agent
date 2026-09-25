"""Frozen extraction runs under `eval/runs/<name>/` (SPEC FR-16, "Evaluation protocol").

    uv run python -m gym_ops.extractor.runs --name v2-dev --git-sha "$(git rev-parse HEAD)"

A snapshot copies one complete `extractions.jsonl` next to a `metadata.json` that
pins what produced it (prompt version + hash, model, git SHA, seeds, input hashes).
Snapshots are write-once: an existing run directory is never overwritten, and the
copied files are made read-only. A snapshot is refused unless it covers every
labelled receipt exactly once, with a single prompt version and model.
"""

import argparse
import hashlib
import json
import shutil
import stat
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from gym_ops.config import get_settings
from gym_ops.extractor.prompt import PROMPT_VERSION, SYSTEM_PROMPT, USER_INSTRUCTION
from gym_ops.extractor.schema import tool_input_schema
from gym_ops.extractor.store import read_records
from gym_ops.receipts.generate import read_labels

RUNS_DIR: Final = Path("eval/runs")
READ_ONLY: Final = stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH

# Records written before PROMPT_VERSION existed carry no version: that run is v1.
UNVERSIONED: Final = "v1"


class SnapshotError(ValueError):
    """The run is incomplete, mixed, or would overwrite an existing snapshot."""


class RunMetadata(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    split: str  # "dev" or "holdout"
    prompt_version: str
    prompt_sha256: str
    tool_schema_sha256: str
    model_id: str
    git_sha: str
    run_started_at: str
    run_finished_at: str
    receipts: int
    receipts_seed: int
    db_seed: int
    db_members: int
    reference_date: str
    labels_sha256: str
    extractions_sha256: str
    total_cost_usd: float


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prompt_sha256() -> str:
    """Hash of the prompt text the model sees (system prompt + user instruction)."""
    return hashlib.sha256(f"{SYSTEM_PROMPT}\n{USER_INSTRUCTION}".encode()).hexdigest()


def tool_schema_sha256() -> str:
    return hashlib.sha256(json.dumps(tool_input_schema(), sort_keys=True).encode()).hexdigest()


def snapshot(
    *,
    name: str,
    split: str,
    extractions: Path,
    labels: Path,
    git_sha: str,
    receipts_seed: int,
    prompt_version: str = PROMPT_VERSION,
    prompt_hash: str | None = None,
    runs_dir: Path = RUNS_DIR,
) -> Path:
    """Validate one complete run and freeze it as `runs_dir/name`. Never overwrites."""
    target = runs_dir / name
    if target.exists():
        raise SnapshotError(f"{target} already exists; snapshots are never overwritten")
    if prompt_hash is None:
        if prompt_version != PROMPT_VERSION:
            raise SnapshotError(
                f"prompt {prompt_version} is not the current {PROMPT_VERSION}; pass its hash"
            )
        prompt_hash = prompt_sha256()

    records = read_records(extractions)
    wanted = {lb.receipt_id for lb in read_labels(labels)}
    if set(records) != wanted:
        missing, extra = sorted(wanted - set(records)), sorted(set(records) - wanted)
        raise SnapshotError(f"records != labels (missing {missing[:5]}, extra {extra[:5]})")
    versions = {r.prompt_version or UNVERSIONED for r in records.values()}
    if versions != {prompt_version}:
        raise SnapshotError(f"expected prompt {prompt_version} only, found {sorted(versions)}")
    models = {r.model_id for r in records.values() if r.model_id}
    if len(models) != 1:
        raise SnapshotError(f"expected one model id, found {sorted(models)}")
    stamps = sorted(r.extracted_at for r in records.values())

    settings = get_settings()
    meta = RunMetadata(
        name=name,
        split=split,
        prompt_version=prompt_version,
        prompt_sha256=prompt_hash,
        tool_schema_sha256=tool_schema_sha256(),
        model_id=models.pop(),
        git_sha=git_sha,
        run_started_at=stamps[0],
        run_finished_at=stamps[-1],
        receipts=len(records),
        receipts_seed=receipts_seed,
        db_seed=settings.SEED,
        db_members=settings.SEED_MEMBERS,
        reference_date=settings.REFERENCE_DATE.isoformat(),
        labels_sha256=sha256_file(labels),
        extractions_sha256=sha256_file(extractions),
        total_cost_usd=round(sum(r.cost_usd for r in records.values()), 6),
    )
    target.mkdir(parents=True)
    copied = target / "extractions.jsonl"
    shutil.copyfile(extractions, copied)
    meta_path = target / "metadata.json"
    meta_path.write_text(meta.model_dump_json(indent=2) + "\n", encoding="utf-8")
    for path in (copied, meta_path):
        path.chmod(READ_ONLY)
    return target


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Freeze an extraction run under eval/runs/.")
    parser.add_argument("--name", required=True)
    parser.add_argument("--split", choices=["dev", "holdout"], default="dev")
    parser.add_argument("--extractions", default="data/extractions.jsonl")
    parser.add_argument("--labels", default="data/labels.jsonl")
    parser.add_argument("--git-sha", required=True)
    parser.add_argument("--receipts-seed", type=int, default=7)
    parser.add_argument("--prompt-version", default=PROMPT_VERSION)
    parser.add_argument("--prompt-sha256", help="required for a prompt other than the current")
    args = parser.parse_args(argv)
    try:
        target = snapshot(
            name=args.name,
            split=args.split,
            extractions=Path(args.extractions),
            labels=Path(args.labels),
            git_sha=args.git_sha,
            receipts_seed=args.receipts_seed,
            prompt_version=args.prompt_version,
            prompt_hash=args.prompt_sha256,
            runs_dir=RUNS_DIR,
        )
    except (SnapshotError, FileNotFoundError) as exc:
        parser.exit(1, f"error: {exc}\n")
    print(target / "metadata.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
