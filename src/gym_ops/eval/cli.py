"""CLI: `python -m gym_ops.eval [--run NAME ...]`. No API calls; $0.

Scores frozen snapshots under `eval/runs/`, writes `eval/reports/<run>.{json,md}` and,
when all three runs are scored, `eval/results.md`. Exit code (FR-17): 1 if the headline
run's gates fail, 2 if a snapshot fails its integrity check, else 0.
"""

import argparse
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Final

from gym_ops.eval.gates import breached
from gym_ops.eval.integrity import IntegrityError
from gym_ops.eval.report import RUN_ORDER, evaluate, write_reports

EXIT_INTEGRITY: Final = 2


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Score frozen extraction runs (no API calls).")
    parser.add_argument("--run", action="append", choices=RUN_ORDER, help="default: all three")
    parser.add_argument("--runs-dir", default="eval/runs")
    parser.add_argument("--out-dir", default="eval/reports")
    parser.add_argument("--results", default="eval/results.md")
    args = parser.parse_args(argv)
    names = [n for n in RUN_ORDER if n in (args.run or RUN_ORDER)]
    try:
        reports = evaluate(Path(args.runs_dir), names)
    except IntegrityError as exc:
        print(f"integrity check failed, nothing scored: {exc}", file=sys.stderr)
        return EXIT_INTEGRITY
    for path in write_reports(reports, Path(args.out_dir), Path(args.results)):
        print(path)
    code = 0
    for r in reports:
        status = "PASS" if r.gates_passed else "FAIL"
        print(f"{r.run}: gates {status}{'' if r.gated else ' (informational)'}")
        if r.gated and not r.gates_passed:
            code = 1
            for line in breached(r.gates):
                print(f"  breached: {line}", file=sys.stderr)
    return code
