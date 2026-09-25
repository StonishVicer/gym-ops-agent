"""Logs are JSON on stderr and never carry the API key, an auth header, or image data."""

import base64
import json
import logging
from collections.abc import Iterator
from pathlib import Path

import pytest

from gym_ops.extractor.batch import run_batch
from gym_ops.extractor.logs import REDACTED, JsonFormatter, configure_logging, redact
from tests.extractor.conftest import FAKE_KEY, GOOD_INPUT, Harness, MakeReceipt, message_json
from tests.extractor.test_budget import key_fetch

LONG_B64 = base64.standard_b64encode(bytes(range(256)) * 4).decode()


@pytest.fixture
def json_logs() -> Iterator[None]:
    handler = configure_logging(secrets=[FAKE_KEY])
    yield
    root = logging.getLogger()
    root.removeHandler(handler)
    for name in ("anthropic", "httpx2"):
        logging.getLogger(name).setLevel(logging.NOTSET)


@pytest.mark.parametrize("sdk_debug", [False, True], ids=["default", "sdk-debug-forced"])
def test_logs_never_contain_key_or_image(
    json_logs: None,
    sdk_debug: bool,
    harness: Harness,
    make_receipt: MakeReceipt,
    members_db: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    if sdk_debug:  # worst case: the SDK dumps full request options (incl. the image)
        logging.getLogger("anthropic").setLevel(logging.DEBUG)
    image = make_receipt("rcpt-0001")
    image_b64 = base64.standard_b64encode(image.read_bytes()).decode()
    bad = {**GOOD_INPUT, "confidence": 3}
    harness.api.queue_status(429, {"retry-after-ms": "1"})
    harness.api.queue(message_json(bad), message_json(GOOD_INPUT))

    run_batch(
        [image],
        client=harness.client,
        counter=harness.counter,
        settings=harness.settings,
        db_path=members_db,
        out_path=tmp_path / "x.jsonl",
        fetch=key_fetch(1.9),
    )

    err = capsys.readouterr().err
    lines = [json.loads(line) for line in err.splitlines()]
    assert any(line.get("event") == "receipt_done" for line in lines)
    assert any(line.get("event") == "validation_retry" for line in lines)
    assert FAKE_KEY not in err
    assert image_b64 not in err and image_b64[:64] not in err
    assert "Bearer sk-" not in err
    if sdk_debug:
        assert any(line["logger"].startswith("anthropic") for line in lines)


def test_redact_patterns() -> None:
    text = (
        f"key={FAKE_KEY} Authorization: Bearer abc.def-123 x-api-key: sk-ant-api03-zzz "
        f"'x-api-key': 'secretvalue' data={LONG_B64} short=aGVsbG8="
    )
    out = redact(text)
    assert FAKE_KEY not in out and "abc.def-123" not in out
    assert "sk-ant-api03-zzz" not in out and "secretvalue" not in out
    assert LONG_B64[:64] not in out
    assert "Bearer " + REDACTED in out
    assert "short=aGVsbG8=" in out  # short base64-looking text is left alone


def test_redact_explicit_secret_without_known_prefix() -> None:
    assert redact("token=hunter2hunter2", ["hunter2hunter2"]) == f"token={REDACTED}"


def test_extras_args_and_exceptions_are_redacted(
    json_logs: None, capsys: pytest.CaptureFixture[str]
) -> None:
    log = logging.getLogger("gym_ops.test")
    log.info("header %s", f"Bearer {FAKE_KEY}", extra={"payload": {"data": [LONG_B64]}})
    try:
        raise RuntimeError(f"boom {FAKE_KEY}")
    except RuntimeError:
        log.exception("failed")
    err = capsys.readouterr().err
    first, second = (json.loads(line) for line in err.splitlines())
    assert first["msg"] == f"header Bearer {REDACTED}"
    assert first["payload"] == {"data": [REDACTED]}
    assert FAKE_KEY not in second["exc"] and "RuntimeError" in second["exc"]


def test_json_formatter_shape() -> None:
    record = logging.LogRecord("gym_ops.x", logging.WARNING, __file__, 1, "hello", None, None)
    record.event = "e"
    payload = json.loads(JsonFormatter().format(record))
    assert payload["level"] == "warning" and payload["msg"] == "hello" and payload["event"] == "e"
    assert payload["ts"].endswith("+00:00")


def test_configure_logging_is_idempotent(json_logs: None) -> None:
    handler = configure_logging(secrets=[FAKE_KEY])
    try:
        json_handlers = [
            h for h in logging.getLogger().handlers if isinstance(h.formatter, JsonFormatter)
        ]
        assert json_handlers == [handler]
        assert logging.getLogger("anthropic").level == logging.WARNING
    finally:
        logging.getLogger().removeHandler(handler)
