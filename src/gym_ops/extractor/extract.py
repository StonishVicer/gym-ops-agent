"""One receipt image -> one validated, normalized payment (SPEC FR-6, FR-7, FR-9).

The request carries the image as a base64 content block, a short instruction, and
exactly one tool, forced with `tool_choice` (ADR-0002). The tool input is validated
with `ReceiptReading`; on a validation error the model gets exactly one more chance,
with the error sent back as an `is_error` tool_result. Transport failures (429, 5xx,
connection errors) are retried inside the SDK (`client.MAX_RETRIES`).

Nothing here touches the database: callers decide what to store (FR-7: failures
are recorded, never inserted).
"""

import base64
import logging
import time
from pathlib import Path
from typing import Final, Literal

import anthropic
from anthropic.types import ImageBlockParam, Message, MessageParam, ToolUseBlock
from pydantic import ValidationError

from gym_ops.config import Settings
from gym_ops.extractor.client import RequestCounter
from gym_ops.extractor.normalize import NormalizationError, normalize_reading
from gym_ops.extractor.prompt import SYSTEM_PROMPT, USER_INSTRUCTION
from gym_ops.extractor.schema import (
    TOOL_NAME,
    ExtractionResult,
    ReceiptReading,
    record_payment_tool,
)

logger = logging.getLogger(__name__)

MAX_TOKENS: Final = 512
TEMPERATURE: Final = 0.0
MAX_MODEL_CALLS: Final = 2  # first call + one validation retry

MediaType = Literal["image/png", "image/jpeg"]
_MEDIA_TYPES: Final[dict[str, MediaType]] = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
}


def cost_usd_micros(input_tokens: int, output_tokens: int, settings: Settings) -> int:
    """Cost in millionths of a dollar. Prices are per 1M tokens, so 1 token = price micros."""
    return round(
        input_tokens * settings.INPUT_USD_PER_MTOK + output_tokens * settings.OUTPUT_USD_PER_MTOK
    )


def _image_block(image_path: Path) -> ImageBlockParam:
    media_type = _MEDIA_TYPES.get(image_path.suffix.lower())
    if media_type is None:
        raise ValueError(f"unsupported image type: {image_path.name}")
    data = base64.standard_b64encode(image_path.read_bytes()).decode("ascii")
    return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}


def _validation_detail(exc: ValidationError) -> str:
    # include_input=False: never echo untrusted receipt text back into logs or prompts.
    return "; ".join(
        f"{'.'.join(str(p) for p in err['loc']) or '<root>'}: {err['msg']}"
        for err in exc.errors(include_url=False, include_input=False)
    )


def _tool_use(response: Message) -> ToolUseBlock | None:
    for block in response.content:
        if isinstance(block, ToolUseBlock) and block.name == TOOL_NAME:
            return block
    return None


def _create(
    client: anthropic.Anthropic,
    messages: list[MessageParam],
    settings: Settings,
    result: ExtractionResult,
) -> Message:
    """One model call; accumulates usage, cost and latency (the API call only) into result."""
    result.attempts += 1
    started = time.perf_counter()
    try:
        response = client.messages.create(
            model=settings.MODEL_ID,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=[record_payment_tool()],
            tool_choice={"type": "tool", "name": TOOL_NAME},
            messages=messages,
            # anthropic 1.x removed sampling params from the signature, not from the
            # API; Haiku 4.5 still honours temperature (SPEC A-4), so send it raw.
            extra_body={"temperature": TEMPERATURE},
        )
    finally:
        result.latency_ms += round((time.perf_counter() - started) * 1000)
    result.model_id = response.model
    result.input_tokens += response.usage.input_tokens
    result.output_tokens += response.usage.output_tokens
    result.cost_usd_micros = cost_usd_micros(result.input_tokens, result.output_tokens, settings)
    result.cost_usd = result.cost_usd_micros / 1_000_000
    return response


def _run(
    client: anthropic.Anthropic, image_path: Path, settings: Settings, result: ExtractionResult
) -> None:
    messages: list[MessageParam] = [
        {
            "role": "user",
            "content": [_image_block(image_path), {"type": "text", "text": USER_INSTRUCTION}],
        }
    ]
    for call in range(1, MAX_MODEL_CALLS + 1):
        response = _create(client, messages, settings, result)
        tool_use = _tool_use(response)
        if tool_use is None:
            result.error = f"no {TOOL_NAME} tool_use block (stop_reason={response.stop_reason})"
            return
        result.raw_input = dict(tool_use.input) if isinstance(tool_use.input, dict) else None
        try:
            reading = ReceiptReading.model_validate(tool_use.input)
        except ValidationError as exc:
            detail = _validation_detail(exc)
            if call == MAX_MODEL_CALLS:
                result.error = f"validation failed after retry: {detail}"
                return
            logger.warning(
                "tool input failed validation; retrying once",
                extra={"event": "validation_retry", "receipt_id": result.receipt_id},
            )
            messages.append(
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": tool_use.id,
                            "name": tool_use.name,
                            "input": tool_use.input,
                        }
                    ],
                }
            )
            messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": tool_use.id,
                            "is_error": True,
                            "content": (
                                f"Invalid input: {detail}. Call {TOOL_NAME} again with "
                                "corrected input, following the same rules."
                            ),
                        }
                    ],
                }
            )
            continue
        result.reading = reading
        try:
            result.payment, result.flags = normalize_reading(reading)
        except NormalizationError as exc:
            result.error = f"normalization failed: {exc}"
        return


def extract_receipt(
    client: anthropic.Anthropic,
    image_path: str | Path,
    settings: Settings,
    counter: RequestCounter | None = None,
) -> ExtractionResult:
    """Extract one receipt. Never raises for API or model errors: they land in `error`."""
    path = Path(image_path)
    result = ExtractionResult(receipt_id=path.stem)
    http_before = counter.count if counter else 0
    try:
        _run(client, path, settings, result)
    except anthropic.APIStatusError as exc:
        result.error = f"{type(exc).__name__}: HTTP {exc.status_code}"
    except anthropic.APIConnectionError as exc:  # includes APITimeoutError
        result.error = type(exc).__name__
    finally:
        result.http_attempts = (counter.count - http_before) if counter else result.attempts
    return result
