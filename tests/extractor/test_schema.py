"""The tool schema is generated from the Pydantic model: one source of truth (ADR-0002)."""

from typing import Any

import pytest
from pydantic import ValidationError

from gym_ops.extractor.schema import (
    MAX_FIELD_LEN,
    TOOL_NAME,
    ReceiptReading,
    record_payment_tool,
    tool_input_schema,
)
from tests.extractor.conftest import GOOD_INPUT

SPEC_FIELDS = {"payer_name", "amount", "currency", "reference", "bank_name", "transfer_date"}
EXTRA_FIELDS = {"confidence", "injection_detected", "notes"}


def test_tool_schema_is_generated_from_model() -> None:
    tool = record_payment_tool()
    assert tool["name"] == TOOL_NAME
    assert tool["input_schema"] == ReceiptReading.model_json_schema() == tool_input_schema()


def test_schema_matches_model_fields() -> None:
    schema: dict[str, Any] = tool_input_schema()
    assert schema["type"] == "object"
    assert (
        set(schema["properties"]) == set(ReceiptReading.model_fields) == SPEC_FIELDS | EXTRA_FIELDS
    )
    # Every key is required (nullable, not optional): the model must address each field.
    assert set(schema["required"]) == set(ReceiptReading.model_fields)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["confidence"]["minimum"] == 0
    assert schema["properties"]["confidence"]["maximum"] == 1
    assert schema["properties"]["injection_detected"]["type"] == "boolean"
    for name in SPEC_FIELDS:
        types = {branch["type"] for branch in schema["properties"][name]["anyOf"]}
        assert types == {"string", "null"}, name
        assert schema["properties"][name]["description"]


def test_schema_example_validates() -> None:
    assert ReceiptReading.model_validate(GOOD_INPUT).amount == "$1,234.56"


@pytest.mark.parametrize(
    "patch",
    [
        {"confidence": 1.5},
        {"confidence": -0.1},
        {"surprise": "field"},
        {"injection_detected": "yes"},
        {"payer_name": "x" * (MAX_FIELD_LEN + 1)},
        {"amount": 1234.56},
    ],
)
def test_model_rejects_invalid(patch: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        ReceiptReading.model_validate(GOOD_INPUT | patch)


def test_every_field_is_required() -> None:
    partial = dict(GOOD_INPUT)
    del partial["reference"]
    with pytest.raises(ValidationError):
        ReceiptReading.model_validate(partial)
