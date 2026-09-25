"""A snapshot is scored only if its files match its recorded hashes."""

import json
import re
import shutil
import stat
from pathlib import Path

import pytest

from gym_ops.eval.cli import EXIT_INTEGRITY, main
from gym_ops.eval.integrity import IntegrityError, load_run

RUNS = Path(__file__).parents[2] / "eval" / "runs"


def _copy_run(name: str, dest: Path) -> Path:
    target = dest / name
    shutil.copytree(RUNS / name, target)
    for f in target.iterdir():
        f.chmod(stat.S_IRUSR | stat.S_IWUSR)
    return target


def test_untampered_snapshot_loads(tmp_path: Path) -> None:
    run = load_run(_copy_run("v2-holdout", tmp_path / "runs"), tmp_path, tmp_path / "absent.jsonl")
    assert run.name == "v2-holdout" and len(run.records) == len(run.labels) == 100
    assert next(iter(run.labels)) == "hold-0001"


def test_tampered_extractions_are_refused(tmp_path: Path) -> None:
    run_dir = _copy_run("v2-holdout", tmp_path / "runs")
    path = run_dir / "extractions.jsonl"
    path.write_text(path.read_text().replace('"amount_cents":5000', '"amount_cents":5001', 1))
    with pytest.raises(IntegrityError, match=r"extractions\.jsonl"):
        load_run(run_dir, tmp_path, tmp_path / "absent.jsonl")


def test_wrong_labels_hash_is_refused(tmp_path: Path) -> None:
    run_dir = _copy_run("v2-dev", tmp_path / "runs")
    meta_path = run_dir / "metadata.json"
    meta = json.loads(meta_path.read_text())
    meta["receipts_seed"] = 9  # labels regenerated from another seed cannot match
    meta_path.write_text(json.dumps(meta))
    with pytest.raises(IntegrityError, match="labels"):
        load_run(run_dir, tmp_path, tmp_path / "absent.jsonl")


def test_mismatching_local_labels_are_refused(tmp_path: Path) -> None:
    run_dir = _copy_run("v1-baseline", tmp_path / "runs")
    local = tmp_path / "labels.jsonl"
    local.write_text("{}\n")
    with pytest.raises(IntegrityError, match=re.escape(str(local))):
        load_run(run_dir, tmp_path, local)


def test_cli_scores_nothing_from_a_tampered_run(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    runs = tmp_path / "runs"
    run_dir = _copy_run("v2-holdout", runs)
    (run_dir / "extractions.jsonl").write_text("")
    out = tmp_path / "reports"
    code = main(
        [
            "--run",
            "v2-holdout",
            "--runs-dir",
            str(runs),
            "--out-dir",
            str(out),
            "--results",
            str(tmp_path / "results.md"),
        ]
    )
    assert code == EXIT_INTEGRITY
    assert not out.exists()
    assert "integrity check failed" in capsys.readouterr().err
