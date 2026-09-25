"""Offline fixtures: a real SDK client over an httpx2 MockTransport, fake receipts, a tiny DB.

The client under test is the production `build_client(...)`; only the transport is
swapped, so request construction, headers, SDK retries and response parsing are all
exercised for real without any network access.
"""

import json
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import anthropic
import httpx2
import pytest
from PIL import Image

from gym_ops.config import Settings
from gym_ops.db.connection import apply_schema, get_write_connection
from gym_ops.extractor.client import RequestCounter, build_client

FAKE_KEY = "sk-or-v1-FAKE0000test0000key0000do0not0use"
MODEL_REPORTED = "anthropic/claude-haiku-4.5"

# A realistic `record_payment` input for a clean `$` receipt.
GOOD_INPUT: dict[str, Any] = {
    "payer_name": "Dana Smith",
    "amount": "$1,234.56",
    "currency": "USD",
    "reference": "GYM-000123-2026-09",
    "bank_name": "Banco Demo",
    "transfer_date": "2026-09-19",
    "confidence": 0.97,
    "injection_detected": False,
    "notes": None,
}


def message_json(
    tool_input: dict[str, Any] | None,
    *,
    input_tokens: int = 1500,
    output_tokens: int = 120,
    tool_id: str = "toolu_01A",
    text: str | None = None,
) -> dict[str, Any]:
    """A Messages API response body, as OpenRouter's Anthropic endpoint returns it."""
    content: list[dict[str, Any]] = []
    if text is not None:
        content.append({"type": "text", "text": text})
    if tool_input is not None:
        content.append(
            {"type": "tool_use", "id": tool_id, "name": "record_payment", "input": tool_input}
        )
    return {
        "id": "msg_01XYZ",
        "type": "message",
        "role": "assistant",
        "model": MODEL_REPORTED,
        "content": content,
        "stop_reason": "tool_use" if tool_input is not None else "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens},
    }


Reply = httpx2.Response | Exception


@dataclass
class MockApi:
    """Scripted replies, one per HTTP request, plus every request that was sent."""

    replies: list[Reply] = field(default_factory=list)
    requests: list[httpx2.Request] = field(default_factory=list)

    def queue(self, *bodies: dict[str, Any]) -> None:
        self.replies.extend(httpx2.Response(200, json=b) for b in bodies)

    def queue_status(self, status: int, headers: dict[str, str] | None = None) -> None:
        body = {"type": "error", "error": {"type": "api_error", "message": "upstream"}}
        self.replies.append(httpx2.Response(status, json=body, headers=headers or {}))

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        if not self.replies:
            raise AssertionError("unexpected extra HTTP request")
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    def body(self, index: int) -> dict[str, Any]:
        parsed: dict[str, Any] = json.loads(self.requests[index].content)
        return parsed


@dataclass
class Harness:
    api: MockApi
    client: anthropic.Anthropic
    counter: RequestCounter
    settings: Settings


@pytest.fixture
def settings() -> Settings:
    # _env_file=None: tests never read the developer's .env.
    return Settings(_env_file=None, OPENROUTER_API_KEY=FAKE_KEY)


@pytest.fixture
def harness(settings: Settings, monkeypatch: pytest.MonkeyPatch) -> Harness:
    # SDK backoff sleeps are real `time.sleep` calls; skip them, keep the retry logic.
    monkeypatch.setattr("anthropic._base_client.time.sleep", lambda _s: None)
    api = MockApi()
    counter = RequestCounter()
    client = build_client(settings, counter=counter, transport=httpx2.MockTransport(api.handler))
    return Harness(api, client, counter, settings)


MakeReceipt = Callable[[str], Path]


@pytest.fixture
def make_receipt(tmp_path: Path) -> MakeReceipt:
    """Write a small PNG named `<receipt_id>.png` under tmp_path/receipts."""
    folder = tmp_path / "receipts"
    folder.mkdir(exist_ok=True)

    def make(receipt_id: str) -> Path:
        path = folder / f"{receipt_id}.png"
        Image.new("RGB", (64, 48), (250, 250, 250)).save(path, format="PNG")
        return path

    return make


MEMBERS = [
    (1, "Dana Smith", "dana@example.test"),
    (2, "José Pérez", "jose@example.test"),
    (3, "Christopher Williams", "cw1@example.test"),
    (4, "Christopher Williams", "cw2@example.test"),
    (5, "Diana Reed", "diana@example.test"),
]
INSERT_MEMBER = (
    "INSERT INTO members (member_id, full_name, email, phone, status, joined_on) "
    "VALUES (?, ?, ?, '555-0100', 'active', '2026-01-01')"
)


@pytest.fixture
def members_db(tmp_path: Path) -> Iterator[Path]:
    """Schema + five members, including an accented name and a duplicate name."""
    path = tmp_path / "gym.db"
    conn = get_write_connection(path)
    apply_schema(conn)
    with conn:
        conn.executemany(INSERT_MEMBER, MEMBERS)
    conn.close()
    yield path
