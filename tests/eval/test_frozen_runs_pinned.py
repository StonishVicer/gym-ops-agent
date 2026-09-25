"""The frozen runs in eval/runs/ are pinned here, outside the snapshot itself.

`integrity.load_run` checks `extractions.jsonl` against the hash in `metadata.json`,
but both files sit side by side: editing both together would pass that check. These
pins close the gap: changing a frozen run now also means editing this file, which a
reviewer sees in the diff. A new run adds a pin; an existing pin never changes.
"""

import hashlib
from pathlib import Path

import pytest

RUNS = Path(__file__).parents[2] / "eval" / "runs"

# fmt: off
PINNED_SHA256 = {
    "v1-baseline/extractions.jsonl":
        "2aff45d6d948ea4c30a51ddd4c34d321255af6483f9f8bcad6fc95b7824ba8de",
    "v1-baseline/metadata.json":
        "de254a29fdafcfdc72d264ca3d829e92ac13bf488361e99c8564972963c8f177",
    "v2-dev/extractions.jsonl":
        "9481d8b10e1e0beb9f5b10d70e9fc0328daa9edec5f94342982cfcabf46dca05",
    "v2-dev/metadata.json":
        "996f1103f73418f018732990e287364b4ef283ff2643539ee3525c29887aba63",
    "v2-holdout/extractions.jsonl":
        "95a5176beefd32e9ec36e8c12afbf8e2e1965c86371b0b093f44e5a659e23c4f",
    "v2-holdout/metadata.json":
        "2fbc9ffad3c41169d23753af05b4c3a43363287618dbf7b56831b25a1e5a63e4",
}
# fmt: on


@pytest.mark.parametrize(("name", "digest"), sorted(PINNED_SHA256.items()))
def test_frozen_run_file_unchanged(name: str, digest: str) -> None:
    assert hashlib.sha256((RUNS / name).read_bytes()).hexdigest() == digest


def test_every_frozen_file_is_pinned() -> None:
    on_disk = {p.relative_to(RUNS).as_posix() for p in RUNS.rglob("*") if p.is_file()}
    assert on_disk == set(PINNED_SHA256)
