"""Anthropic SDK client pointed at OpenRouter's Anthropic-compatible endpoint (ADR-0001).

Portability: this is the stock `anthropic.Anthropic` client speaking the Messages
API. Switching to api.anthropic.com only requires changing three values: `base_url`
(drop it, or `https://api.anthropic.com`), the key (an Anthropic key, passed as
`api_key` because Anthropic authenticates with `x-api-key`), and the model id
(`claude-haiku-4-5-20251001` instead of OpenRouter's `anthropic/claude-haiku-4.5`).

Credentials are always passed explicitly. With any explicit credential the SDK does
not consult `ANTHROPIC_API_KEY` / `ANTHROPIC_AUTH_TOKEN` (anthropic 1.8.0,
`_client.py`), and `base_url` is always given, so `ANTHROPIC_BASE_URL` is ignored:
nothing here reads or depends on the env vars Claude Code uses for its own auth.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

import anthropic
import httpx2

from gym_ops.config import Settings

OPENROUTER_BASE_URL: Final = "https://openrouter.ai/api"

# SDK retries: 429, 408, 409, 5xx and connection errors, exponential backoff
# (0.5 s doubling, capped at 8 s) with jitter, honoring `retry-after`.
# 2 retries = at most 3 HTTP attempts per model call.
MAX_RETRIES: Final = 2
TIMEOUT_S: Final = 30.0


@dataclass
class RequestCounter:
    """httpx event hook counting HTTP requests, so SDK retries are visible per receipt."""

    count: int = field(default=0)

    def __call__(self, request: httpx2.Request) -> None:
        self.count += 1


def build_client(
    settings: Settings,
    *,
    counter: RequestCounter | None = None,
    transport: httpx2.BaseTransport | None = None,
) -> anthropic.Anthropic:
    """Build the extractor's client. `transport` is for tests (httpx2.MockTransport)."""
    hooks: dict[str, list[Callable[..., Any]]] = {"request": [counter]} if counter else {}
    http_client = anthropic.DefaultHttpxClient(transport=transport, event_hooks=hooks)
    # `auth_token`, not `api_key`: in anthropic 1.8.0 `api_key` is sent as `x-api-key`
    # and `auth_token` as `Authorization: Bearer <token>`. OpenRouter authenticates
    # with a Bearer token (its Claude Code guide sets ANTHROPIC_AUTH_TOKEN and blanks
    # ANTHROPIC_API_KEY). Passing it explicitly also disables the SDK's env-var
    # credential lookup, so no second credential can ride along as `x-api-key`.
    return anthropic.Anthropic(
        base_url=OPENROUTER_BASE_URL,
        auth_token=settings.require_openrouter_api_key().get_secret_value(),
        max_retries=MAX_RETRIES,
        timeout=TIMEOUT_S,
        http_client=http_client,
    )
