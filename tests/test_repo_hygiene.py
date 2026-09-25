"""Repo-level guards behind SECURITY.md: secrets, supply chain, and the $0 pipeline."""

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
PAID_TARGETS = {"extract", "extract-smoke", "extract-holdout", "test-live"}
SHA_PIN = re.compile(r"@[0-9a-f]{40}\b")


def _make_prerequisites(target: str) -> list[str]:
    match = re.search(rf"^{re.escape(target)}:(.*)$", (ROOT / "Makefile").read_text(), re.M)
    assert match, f"no `{target}:` rule in Makefile"
    return match.group(1).split()


def test_gitignore_covers_secrets_and_databases() -> None:
    patterns = set((ROOT / ".gitignore").read_text().splitlines())
    assert {".env", ".env.*", "!.env.example", "*.db"} <= patterns


@pytest.mark.skipif(
    shutil.which("git") is None or not (ROOT / ".git").exists(), reason="needs a git checkout"
)
def test_no_env_or_db_file_is_tracked() -> None:
    tracked = subprocess.run(
        ["git", "ls-files", "-z"],  # noqa: S607 - git from PATH
        cwd=ROOT,
        capture_output=True,
        check=True,
    ).stdout.decode()
    names = [Path(p).name for p in tracked.split("\0") if p]
    assert not [n for n in names if n == ".env" or (n.startswith(".env.") and n != ".env.example")]
    assert not [n for n in names if n.endswith((".db", ".sqlite", ".sqlite3"))]


def test_ci_actions_pinned_to_commit_sha() -> None:
    uses = re.findall(r"uses:\s*(\S+)", (ROOT / ".github/workflows/ci.yml").read_text())
    assert uses
    assert [u for u in uses if not SHA_PIN.search(u)] == []


def test_ci_needs_no_secret_and_reads_only() -> None:
    """CI runs lint and tests with no API key; its only secret is the scoped GITHUB_TOKEN."""
    ci = (ROOT / ".github/workflows/ci.yml").read_text()
    assert set(re.findall(r"secrets\.(\w+)", ci)) == {"GITHUB_TOKEN"}
    assert "permissions:\n  contents: read" in ci
    assert "OPENROUTER" not in ci


def test_ci_installs_from_the_lockfile() -> None:
    assert "uv sync --all-groups --locked" in (ROOT / ".github/workflows/ci.yml").read_text()


def test_pre_commit_hooks_pinned_to_commit_sha() -> None:
    revs = re.findall(r"rev:\s*(\S+)", (ROOT / ".pre-commit-config.yaml").read_text())
    assert revs
    assert [r for r in revs if not re.fullmatch(r"[0-9a-f]{40}", r)] == []


def test_make_all_is_the_offline_pipeline() -> None:
    """`make all` must never spend money: no extraction, no live tests."""
    assert _make_prerequisites("all") == ["setup", "seed", "receipts", "lint", "test-ci", "eval"]
    for target in _make_prerequisites("all"):
        assert not set(_make_prerequisites(target)) & PAID_TARGETS
