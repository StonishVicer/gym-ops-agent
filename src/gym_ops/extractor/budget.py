"""Spend guards around a batch run.

1. Before the batch: ask OpenRouter how much of the key's spend limit is left and
   refuse to start unless it covers twice the conservative batch estimate.
2. During the batch: stop once this run's estimated cost passes `RUN_COST_CAP_USD`.

Uses stdlib `urllib` (one GET, no retries needed) so the guard does not depend on
the SDK's vendored HTTP client.
"""

import json
import logging
import urllib.request
from collections.abc import Callable
from typing import Final

from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

KEY_URL: Final = "https://openrouter.ai/api/v1/key"
EST_COST_PER_RECEIPT_USD: Final = 0.005  # conservative; NFR-1 expects ~$0.003
SAFETY_FACTOR: Final = 2.0
RUN_COST_CAP_USD: Final = 1.00
HTTP_TIMEOUT_S: Final = 10.0

# (url, headers) -> response body. Injected in tests; `urlopen` in production.
Fetch = Callable[[str, dict[str, str]], bytes]


class BudgetError(RuntimeError):
    """Not enough budget to start, or this run's spend cap was reached."""


class KeyStatus(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    limit: float | None = None
    limit_remaining: float | None = None  # None = the key has no spend limit
    usage: float | None = None


def _urlopen_fetch(url: str, headers: dict[str, str]) -> bytes:
    request = urllib.request.Request(url, headers=headers, method="GET")  # noqa: S310 - constant https URL
    with urllib.request.urlopen(request, timeout=HTTP_TIMEOUT_S) as response:  # noqa: S310
        body: bytes = response.read()
    return body


def fetch_key_status(api_key: str, fetch: Fetch = _urlopen_fetch) -> KeyStatus:
    body = fetch(KEY_URL, {"Authorization": f"Bearer {api_key}"})
    return KeyStatus.model_validate(json.loads(body).get("data", {}))


def estimated_batch_cost(n_receipts: int) -> float:
    return n_receipts * EST_COST_PER_RECEIPT_USD


def check_budget(status: KeyStatus, n_receipts: int) -> None:
    """Raise BudgetError unless limit_remaining >= SAFETY_FACTOR x estimated batch cost."""
    required = SAFETY_FACTOR * estimated_batch_cost(n_receipts)
    if status.limit_remaining is None:
        logger.warning(
            "key has no spend limit; budget pre-check skipped",
            extra={"event": "budget_unlimited", "required_usd": required},
        )
        return
    if status.limit_remaining < required:
        raise BudgetError(
            f"limit_remaining ${status.limit_remaining:.4f} < required ${required:.4f} "
            f"({SAFETY_FACTOR:g} x {n_receipts} x ${EST_COST_PER_RECEIPT_USD})"
        )
    logger.info(
        "budget check passed",
        extra={
            "event": "budget_ok",
            "limit_remaining_usd": status.limit_remaining,
            "required_usd": required,
        },
    )


class RunningCost:
    """Accumulates this run's estimated cost and enforces the per-run cap."""

    def __init__(self, cap_usd: float = RUN_COST_CAP_USD) -> None:
        self.cap_usd = cap_usd
        self.total_usd = 0.0

    def add(self, cost_usd: float) -> float:
        self.total_usd += cost_usd
        if self.total_usd > self.cap_usd:
            raise BudgetError(
                f"run cost ${self.total_usd:.4f} exceeded the ${self.cap_usd:.2f} cap"
            )
        return self.total_usd
