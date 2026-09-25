"""Spend guards: pre-batch key check and the per-run cost cap (mocked HTTP, no network)."""

import json
from pathlib import Path
from typing import Any

import pytest

from gym_ops.extractor import budget
from gym_ops.extractor.batch import run_batch
from gym_ops.extractor.budget import (
    KEY_URL,
    BudgetError,
    KeyStatus,
    RunningCost,
    check_budget,
    estimated_batch_cost,
    fetch_key_status,
)
from tests.extractor.conftest import FAKE_KEY, GOOD_INPUT, Harness, MakeReceipt, message_json


def key_fetch(
    limit_remaining: float | None, seen: list[tuple[str, dict[str, str]]] | None = None
) -> budget.Fetch:
    """A fake GET /api/v1/key with OpenRouter's response shape."""

    def fetch(url: str, headers: dict[str, str]) -> bytes:
        if seen is not None:
            seen.append((url, headers))
        data = {
            "label": "sk-or-v1-FAK...",
            "limit": 2.0,
            "usage": 0.1,
            "limit_remaining": limit_remaining,
        }
        return json.dumps({"data": data}).encode()

    return fetch


def test_fetch_key_status_sends_bearer_to_key_endpoint() -> None:
    seen: list[tuple[str, dict[str, str]]] = []
    status = fetch_key_status(FAKE_KEY, key_fetch(1.9, seen))
    assert status == KeyStatus(limit=2.0, limit_remaining=1.9, usage=0.1)
    assert seen == [(KEY_URL, {"Authorization": f"Bearer {FAKE_KEY}"})]


def test_urlopen_fetch(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"data": {"limit_remaining": 1.5}}'

    def fake_urlopen(request: Any, timeout: float) -> FakeResponse:
        captured["url"] = request.full_url
        captured["auth"] = request.get_header("Authorization")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    assert fetch_key_status(FAKE_KEY).limit_remaining == 1.5
    assert captured == {
        "url": KEY_URL,
        "auth": f"Bearer {FAKE_KEY}",
        "timeout": budget.HTTP_TIMEOUT_S,
    }


@pytest.mark.parametrize(
    ("remaining", "n", "ok"),
    [
        (1.00, 100, True),  # exactly 2 x 100 x $0.005
        (0.99, 100, False),
        (0.03, 3, True),  # smoke run: 2 x 3 x $0.005
        (0.029, 3, False),
        (0.0, 1, False),
    ],
)
def test_check_budget_threshold(remaining: float, n: int, ok: bool) -> None:
    status = KeyStatus(limit_remaining=remaining)
    if ok:
        check_budget(status, n)
    else:
        with pytest.raises(BudgetError, match="limit_remaining"):
            check_budget(status, n)


def test_no_limit_is_allowed_with_warning(caplog: pytest.LogCaptureFixture) -> None:
    check_budget(KeyStatus(limit_remaining=None), 100)
    assert any(r.__dict__.get("event") == "budget_unlimited" for r in caplog.records)


def test_estimate() -> None:
    assert estimated_batch_cost(100) == pytest.approx(0.5)


def test_running_cost_cap() -> None:
    running = RunningCost(cap_usd=1.0)
    assert running.add(0.6) == pytest.approx(0.6)
    assert running.add(0.4) == pytest.approx(1.0)  # at the cap is fine
    with pytest.raises(BudgetError, match="exceeded"):
        running.add(0.01)


def test_batch_aborts_before_any_model_call_when_budget_low(
    harness: Harness, make_receipt: MakeReceipt, members_db: Path, tmp_path: Path
) -> None:
    images = [make_receipt(f"rcpt-{i:04d}") for i in range(1, 4)]
    with pytest.raises(BudgetError):
        run_batch(
            images,
            client=harness.client,
            counter=harness.counter,
            settings=harness.settings,
            db_path=members_db,
            out_path=tmp_path / "extractions.jsonl",
            fetch=key_fetch(0.01),
        )
    assert harness.api.requests == []
    assert not (tmp_path / "extractions.jsonl").exists()


def test_batch_stops_when_run_cost_passes_cap(
    harness: Harness, make_receipt: MakeReceipt, members_db: Path, tmp_path: Path
) -> None:
    # 600k input tokens at $1/MTok = $0.60 per receipt: the 2nd receipt crosses $1.00.
    images = [make_receipt(f"rcpt-{i:04d}") for i in range(1, 4)]
    harness.api.queue(
        *(message_json(GOOD_INPUT, input_tokens=600_000, output_tokens=0) for _ in range(3))
    )

    summary = run_batch(
        images,
        client=harness.client,
        counter=harness.counter,
        settings=harness.settings,
        db_path=members_db,
        out_path=tmp_path / "extractions.jsonl",
        fetch=key_fetch(1.9),
    )

    assert summary.aborted is not None and "exceeded" in summary.aborted
    assert summary.processed == 2
    assert len(harness.api.requests) == 2  # the third receipt was never sent
    assert summary.total_cost_usd == pytest.approx(1.2)  # real spend, incl. the crossing call
