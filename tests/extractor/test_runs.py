"""Run snapshots (eval/runs/): complete, single-version, write-once."""

import json
import stat
from pathlib import Path

import pytest

from gym_ops.extractor.prompt import PROMPT_VERSION, SYSTEM_PROMPT
from gym_ops.extractor.runs import SnapshotError, main, prompt_sha256, snapshot
from gym_ops.extractor.store import ExtractionRecord, write_records
from tests.extractor.test_cli import _label

STAMP = "2026-09-25T22:00:00Z"


def _record(rid: str, version: str | None = PROMPT_VERSION, model: str = "m") -> ExtractionRecord:
    return ExtractionRecord(
        receipt_id=rid,
        file=f"{rid}.png",
        extracted_at=STAMP,
        prompt_version=version,
        model_id=model,
        cost_usd=0.0035,
    )


@pytest.fixture
def run_files(tmp_path: Path) -> tuple[Path, Path]:
    labels = tmp_path / "labels.jsonl"
    labels.write_text("".join(_label(f"rcpt-000{i}", "exact_payment") + "\n" for i in (1, 2)))
    extractions = tmp_path / "extractions.jsonl"
    write_records(extractions, [_record("rcpt-0001"), _record("rcpt-0002")])
    return extractions, labels


def _snap(run_files: tuple[Path, Path], runs: Path, **kw: object) -> Path:
    extractions, labels = run_files
    args: dict[str, object] = {
        "name": "v2-dev",
        "split": "dev",
        "extractions": extractions,
        "labels": labels,
        "git_sha": "abc123",
        "receipts_seed": 7,
        "runs_dir": runs,
    }
    return snapshot(**(args | kw))  # type: ignore[arg-type]


def test_snapshot_writes_readonly_copy_and_metadata(
    run_files: tuple[Path, Path], tmp_path: Path
) -> None:
    target = _snap(run_files, tmp_path / "runs")
    meta = json.loads((target / "metadata.json").read_text())
    assert meta["prompt_version"] == PROMPT_VERSION
    assert meta["prompt_sha256"] == prompt_sha256()
    assert meta["model_id"] == "m" and meta["git_sha"] == "abc123"
    assert meta["receipts"] == 2 and meta["total_cost_usd"] == pytest.approx(0.007)
    assert (target / "extractions.jsonl").read_bytes() == run_files[0].read_bytes()
    for name in ("extractions.jsonl", "metadata.json"):
        mode = (target / name).stat().st_mode
        assert not mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH)


def test_snapshot_never_overwrites(run_files: tuple[Path, Path], tmp_path: Path) -> None:
    _snap(run_files, tmp_path / "runs")
    with pytest.raises(SnapshotError, match="never overwritten"):
        _snap(run_files, tmp_path / "runs")


def test_incomplete_run_is_refused(run_files: tuple[Path, Path], tmp_path: Path) -> None:
    extractions = tmp_path / "partial.jsonl"
    write_records(extractions, [_record("rcpt-0001")])
    with pytest.raises(SnapshotError, match="missing"):
        _snap((extractions, run_files[1]), tmp_path / "runs")
    assert not (tmp_path / "runs" / "v2-dev").exists()


def test_mixed_prompt_versions_are_refused(run_files: tuple[Path, Path], tmp_path: Path) -> None:
    write_records(run_files[0], [_record("rcpt-0002", version=None)])  # a leftover v1 record
    with pytest.raises(SnapshotError, match="v1"):
        _snap(run_files, tmp_path / "runs")


def test_mixed_models_are_refused(run_files: tuple[Path, Path], tmp_path: Path) -> None:
    write_records(run_files[0], [_record("rcpt-0002", model="other")])
    with pytest.raises(SnapshotError, match="model"):
        _snap(run_files, tmp_path / "runs")


def test_unversioned_records_are_v1_and_need_explicit_hash(
    run_files: tuple[Path, Path], tmp_path: Path
) -> None:
    write_records(run_files[0], [_record("rcpt-0001", None), _record("rcpt-0002", None)])
    with pytest.raises(SnapshotError, match="pass its hash"):
        _snap(run_files, tmp_path / "runs", prompt_version="v1")
    target = _snap(run_files, tmp_path / "runs", prompt_version="v1", prompt_hash="f" * 64)
    assert json.loads((target / "metadata.json").read_text())["prompt_sha256"] == "f" * 64


def test_prompt_hash_tracks_prompt_text() -> None:
    assert len(prompt_sha256()) == 64
    assert "never convert the number to another locale's format" in SYSTEM_PROMPT


def test_cli(run_files: tuple[Path, Path], tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gym_ops.extractor.runs.RUNS_DIR", tmp_path / "runs")
    argv = ["--name", "x", "--extractions", str(run_files[0]), "--labels", str(run_files[1]),
            "--git-sha", "abc"]  # fmt: skip
    assert main(argv) == 0
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 1
