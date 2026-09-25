"""Extraction call (SPEC FR-6, FR-7, FR-8 cost, FR-9; ADR-0002)."""

import base64
from pathlib import Path

import pytest

from gym_ops.extractor.extract import MAX_TOKENS, cost_usd_micros, extract_receipt
from gym_ops.extractor.prompt import PROMPT_VERSION, SYSTEM_PROMPT
from gym_ops.extractor.schema import TOOL_NAME
from gym_ops.receipts.generate import INJECTION_TEXT
from tests.extractor.conftest import GOOD_INPUT, MODEL_REPORTED, Harness, MakeReceipt, message_json


def test_forced_tool_choice(harness: Harness, make_receipt: MakeReceipt) -> None:
    harness.api.queue(message_json(GOOD_INPUT))
    image = make_receipt("rcpt-0001")

    extract_receipt(harness.client, image, harness.settings, harness.counter)

    body = harness.api.body(0)
    assert [t["name"] for t in body["tools"]] == [TOOL_NAME]
    assert body["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert body["temperature"] == 0
    assert body["max_tokens"] == MAX_TOKENS == 512
    assert body["model"] == harness.settings.MODEL_ID
    assert body["system"] == SYSTEM_PROMPT
    [message] = body["messages"]
    image_block, text_block = message["content"]
    assert image_block["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.standard_b64encode(image.read_bytes()).decode(),
    }
    assert text_block["type"] == "text" and TOOL_NAME in text_block["text"]


def test_temperature_zero_in_every_request_body(
    harness: Harness, make_receipt: MakeReceipt
) -> None:
    """anthropic 1.x dropped `temperature` from the signature; we send it via extra_body.

    Guards against an SDK change silently dropping it (SPEC A-4), on the first call
    and on the validation retry.
    """
    harness.api.queue(message_json({**GOOD_INPUT, "confidence": 2}), message_json(GOOD_INPUT))

    extract_receipt(harness.client, make_receipt("rcpt-0001"), harness.settings, harness.counter)

    assert len(harness.api.requests) == 2
    for i in range(2):
        body = harness.api.body(i)
        assert body["temperature"] == 0
        assert "top_p" not in body and "top_k" not in body


def test_returns_validated_model(harness: Harness, make_receipt: MakeReceipt) -> None:
    harness.api.queue(message_json(GOOD_INPUT, input_tokens=1500, output_tokens=120))

    result = extract_receipt(
        harness.client, make_receipt("rcpt-0007"), harness.settings, harness.counter
    )

    assert result.ok
    assert result.receipt_id == "rcpt-0007"
    assert result.raw_input == GOOD_INPUT
    assert result.payment is not None
    assert result.payment.amount_cents == 123456
    assert result.payment.transfer_date.isoformat() == "2026-09-19"
    assert result.payment.reference == "GYM-000123-2026-09"
    assert result.payment.bank_name == "Banco Demo"
    assert result.flags == []
    # FR-8: 1500 x $1 + 120 x $5 per MTok = $0.0021
    assert (result.input_tokens, result.output_tokens) == (1500, 120)
    assert result.cost_usd_micros == 2100
    assert result.cost_usd == pytest.approx(0.0021)
    assert result.model_id == MODEL_REPORTED
    assert result.attempts == result.http_attempts == 1
    assert result.latency_ms >= 0
    assert result.prompt_version == PROMPT_VERSION == "v2"


def test_cost_micros_uses_settings_prices(harness: Harness) -> None:
    assert cost_usd_micros(1500, 120, harness.settings) == 2100
    assert cost_usd_micros(0, 0, harness.settings) == 0


def test_validation_retry_succeeds(harness: Harness, make_receipt: MakeReceipt) -> None:
    bad = {**GOOD_INPUT, "confidence": 1.7}
    del bad["currency"]
    harness.api.queue(
        message_json(bad, input_tokens=1500, output_tokens=100, tool_id="toolu_bad"),
        message_json(GOOD_INPUT, input_tokens=1700, output_tokens=110, tool_id="toolu_good"),
    )

    result = extract_receipt(
        harness.client, make_receipt("rcpt-0001"), harness.settings, harness.counter
    )

    assert result.ok
    assert result.attempts == 2
    assert (result.input_tokens, result.output_tokens) == (3200, 210)
    assert result.cost_usd_micros == 3200 + 210 * 5
    retry = harness.api.body(1)["messages"]
    assert [m["role"] for m in retry] == ["user", "assistant", "user"]
    assert retry[1]["content"][0]["id"] == "toolu_bad"
    [tool_result] = retry[2]["content"]
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == "toolu_bad"
    assert tool_result["is_error"] is True
    assert "currency" in tool_result["content"] and "confidence" in tool_result["content"]


def test_validation_fails_twice_is_recorded_not_raised(
    harness: Harness, make_receipt: MakeReceipt
) -> None:
    bad = {**GOOD_INPUT, "injection_detected": "maybe", "amount": 12.5}
    harness.api.queue(message_json(bad), message_json(bad))

    result = extract_receipt(
        harness.client, make_receipt("rcpt-0001"), harness.settings, harness.counter
    )

    assert not result.ok
    assert result.payment is None and result.reading is None
    assert result.attempts == 2
    assert result.error is not None and result.error.startswith("validation failed after retry")
    assert result.raw_input == bad  # kept for debugging, never stored


def test_validation_error_never_echoes_input(harness: Harness, make_receipt: MakeReceipt) -> None:
    """The retry prompt names fields, not values: receipt text is not fed back as prose."""
    bad = {**GOOD_INPUT, "payer_name": "x" * 500}
    harness.api.queue(message_json(bad), message_json(GOOD_INPUT))

    extract_receipt(harness.client, make_receipt("rcpt-0001"), harness.settings, harness.counter)

    feedback = harness.api.body(1)["messages"][2]["content"][0]["content"]
    assert "payer_name" in feedback and "x" * 50 not in feedback


def test_adversarial_injection_is_flagged_and_inert(
    harness: Harness, make_receipt: MakeReceipt
) -> None:
    """FR-9: the injection lands in notes as plain text; amounts are the printed ones."""
    reading = {**GOOD_INPUT, "injection_detected": True, "notes": INJECTION_TEXT}
    harness.api.queue(message_json(reading))

    result = extract_receipt(
        harness.client, make_receipt("rcpt-0033"), harness.settings, harness.counter
    )

    assert result.ok
    assert result.reading is not None
    assert result.reading.injection_detected is True
    assert result.reading.notes == INJECTION_TEXT
    assert result.payment is not None
    assert result.payment.amount_cents == 123456  # not 999999
    assert result.payment.reference == GOOD_INPUT["reference"]


def test_injection_text_in_reference_is_kept_as_data(
    harness: Harness, make_receipt: MakeReceipt
) -> None:
    """If injected text lands in a string field, it is stored verbatim (uppercased), flagged."""
    reading = {**GOOD_INPUT, "reference": INJECTION_TEXT[:100]}
    harness.api.queue(message_json(reading))

    result = extract_receipt(
        harness.client, make_receipt("rcpt-0033"), harness.settings, harness.counter
    )

    assert result.payment is not None
    assert result.payment.reference == INJECTION_TEXT[:100].upper()
    assert result.payment.amount_cents == 123456
    assert "reference_shape" in result.flags


def test_no_tool_use_block_is_a_failure(harness: Harness, make_receipt: MakeReceipt) -> None:
    harness.api.queue(message_json(None, text="I cannot read this."))

    result = extract_receipt(
        harness.client, make_receipt("rcpt-0001"), harness.settings, harness.counter
    )

    assert not result.ok
    assert result.error is not None and "no record_payment tool_use" in result.error
    assert result.attempts == 1


def test_unparseable_amount_is_a_failure(harness: Harness, make_receipt: MakeReceipt) -> None:
    harness.api.queue(message_json({**GOOD_INPUT, "amount": "12,50 EUR"}))

    result = extract_receipt(
        harness.client, make_receipt("rcpt-0001"), harness.settings, harness.counter
    )

    assert not result.ok
    assert result.reading is not None  # valid reading ...
    assert result.payment is None  # ... that cannot be normalized
    assert result.error is not None and result.error.startswith("normalization failed")
    assert result.attempts == 1  # copying "as printed" again would not help: no retry


def test_unreadable_required_field_is_a_failure(
    harness: Harness, make_receipt: MakeReceipt
) -> None:
    harness.api.queue(message_json({**GOOD_INPUT, "amount": None, "confidence": 0.2}))

    result = extract_receipt(
        harness.client, make_receipt("rcpt-0001"), harness.settings, harness.counter
    )

    assert result.error is not None and "amount" in result.error


def test_unsupported_image_type(harness: Harness, tmp_path: Path) -> None:
    gif = tmp_path / "rcpt-0001.gif"
    gif.write_bytes(b"GIF89a")
    with pytest.raises(ValueError, match="unsupported image type"):
        extract_receipt(harness.client, gif, harness.settings, harness.counter)
    assert harness.api.requests == []
