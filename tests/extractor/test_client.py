"""Client wiring (ADR-0001): endpoint, credentials, and SDK transport retries."""

import httpx2
import pytest

from gym_ops.config import Settings
from gym_ops.extractor.client import OPENROUTER_BASE_URL, RequestCounter, build_client
from gym_ops.extractor.extract import extract_receipt
from tests.extractor.conftest import (
    FAKE_KEY,
    GOOD_INPUT,
    Harness,
    MakeReceipt,
    MockApi,
    message_json,
)


def test_env_credentials_never_used(
    settings: Settings, make_receipt: MakeReceipt, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Claude Code's env vars must not redirect the request or add a second credential."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-api03-ENVKEY-must-not-leak")
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "env-token-must-not-leak")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://hijack.example.invalid")
    api = MockApi()
    api.queue(message_json(GOOD_INPUT))
    client = build_client(settings, transport=httpx2.MockTransport(api.handler))

    result = extract_receipt(client, make_receipt("rcpt-0001"), settings)

    assert result.ok
    [request] = api.requests
    assert str(request.url) == f"{OPENROUTER_BASE_URL}/v1/messages"
    assert request.headers.get_list("authorization") == [f"Bearer {FAKE_KEY}"]
    assert "x-api-key" not in request.headers
    sent = str(request.headers.raw)
    assert "ENVKEY" not in sent and "env-token" not in sent


def test_missing_key_is_rejected() -> None:
    with pytest.raises(ValueError, match="OPENROUTER_API_KEY"):
        build_client(Settings(_env_file=None, OPENROUTER_API_KEY=None))


def test_sdk_retries_429_and_5xx_then_succeeds(harness: Harness, make_receipt: MakeReceipt) -> None:
    harness.api.queue_status(529, {"retry-after-ms": "1"})
    harness.api.queue_status(429, {"retry-after-ms": "1"})
    harness.api.queue(message_json(GOOD_INPUT))

    result = extract_receipt(
        harness.client, make_receipt("rcpt-0001"), harness.settings, harness.counter
    )

    assert result.ok and result.error is None
    assert result.attempts == 1  # one model call...
    assert result.http_attempts == 3  # ...that took three HTTP requests
    assert result.http_statuses == [529, 429, 200]


def test_sdk_gives_up_after_three_attempts(harness: Harness, make_receipt: MakeReceipt) -> None:
    for _ in range(3):
        harness.api.queue_status(500)

    result = extract_receipt(
        harness.client, make_receipt("rcpt-0001"), harness.settings, harness.counter
    )

    assert result.error == "InternalServerError: HTTP 500"
    assert result.http_statuses == [500, 500, 500]
    assert result.payment is None
    assert result.http_attempts == 3
    assert result.input_tokens == result.output_tokens == 0


def test_connection_errors_are_retried_then_reported(
    harness: Harness, make_receipt: MakeReceipt
) -> None:
    harness.api.replies.extend(httpx2.ConnectError("refused") for _ in range(3))

    result = extract_receipt(
        harness.client, make_receipt("rcpt-0001"), harness.settings, harness.counter
    )

    assert result.error == "APIConnectionError"
    assert result.http_attempts == 3
    assert result.http_statuses == []  # no HTTP status: all three failed to connect


def test_non_retryable_status_is_not_retried(harness: Harness, make_receipt: MakeReceipt) -> None:
    harness.api.queue_status(400)

    result = extract_receipt(
        harness.client, make_receipt("rcpt-0001"), harness.settings, harness.counter
    )

    assert result.error == "BadRequestError: HTTP 400"
    assert result.http_attempts == 1


def test_request_counter_counts() -> None:
    counter = RequestCounter()
    counter(httpx2.Request("GET", "https://example.invalid"))
    assert counter.count == 1


def test_http_retry_is_logged_with_statuses(
    harness: Harness, make_receipt: MakeReceipt, caplog: pytest.LogCaptureFixture
) -> None:
    harness.api.queue_status(503, {"retry-after-ms": "1"})
    harness.api.queue(message_json(GOOD_INPUT))

    extract_receipt(harness.client, make_receipt("rcpt-0071"), harness.settings, harness.counter)

    [record] = [r for r in caplog.records if r.__dict__.get("event") == "http_retry"]
    assert record.__dict__["receipt_id"] == "rcpt-0071"
    assert record.__dict__["http_statuses"] == [503, 200]
    assert record.__dict__["connection_failures"] == 0


def test_no_retry_log_on_first_try_success(
    harness: Harness, make_receipt: MakeReceipt, caplog: pytest.LogCaptureFixture
) -> None:
    harness.api.queue(message_json(GOOD_INPUT))
    result = extract_receipt(
        harness.client, make_receipt("rcpt-0001"), harness.settings, harness.counter
    )
    assert result.http_statuses == [200]
    assert not [r for r in caplog.records if r.__dict__.get("event") == "http_retry"]
