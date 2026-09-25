"""Pydantic models for every MCP tool boundary: validated inputs and typed outputs.

The annotated parameter types (`StartDate`, `Month`, ...) are shared by the tool
signatures in `server.py`, which the SDK turns into each tool's JSON input schema,
and by the `*Input` models, which add cross-field rules such as date-range limits.
Validators raise short, actionable `ValueError` messages because the calling model
reads them to correct its next call.
"""

import re
from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

MAX_ROWS = 200  # FR-15: every list output is capped and reports `truncated`
MAX_RANGE_DAYS = 366
MAX_MEMBER_LIMIT = 50
MAX_NAME_QUERY_LEN = 100
MAX_RECEIPT_TEXT_LEN = 120  # ADR-0004: untrusted receipt text is clipped

ClassName = Literal["HIIT", "Spin", "Yoga", "Pilates", "Boxing", "Strength"]
MemberStatus = Literal["active", "frozen", "cancelled"]
Plan = Literal["monthly", "quarterly", "annual", "student"]
Weekday = Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
BillStatus = Literal["paid", "partially_paid", "overpaid", "unpaid"]
LinkRule = Literal["reference", "member_date_window"]
UnidentifiedReason = Literal["unknown_payer", "no_open_bill", "ambiguous"]

_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_MONTH_RE = re.compile(r"[0-9]{4}-(0[1-9]|1[0-2])")
_NO_CONTROL_CHARS = r"^[^\x00-\x1f\x7f]*$"


def _parse_iso_date(value: object) -> date:
    # Stricter than pydantic's lax date parsing, which also accepts unix timestamps
    # and datetime strings: only an exact YYYY-MM-DD calendar date gets through.
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, str) and _ISO_DATE_RE.fullmatch(value):
        try:
            return date.fromisoformat(value)
        except ValueError:
            pass
    raise ValueError("must be a calendar date in YYYY-MM-DD format, e.g. 2026-09-01")


def _check_month(value: object) -> str:
    if isinstance(value, str) and _MONTH_RE.fullmatch(value):
        return value
    raise ValueError("must be a month in YYYY-MM format, e.g. 2026-09")


IsoDate = Annotated[date, BeforeValidator(_parse_iso_date)]

StartDate = Annotated[IsoDate, Field(description="First day to include, YYYY-MM-DD (inclusive).")]
EndDate = Annotated[
    IsoDate,
    Field(description="Last day to include, YYYY-MM-DD (inclusive). Must be >= start_date."),
]
Month = Annotated[
    str,
    BeforeValidator(_check_month),
    Field(
        pattern=r"^[0-9]{4}-(0[1-9]|1[0-2])$",
        description="Billing month, YYYY-MM (e.g. 2026-09).",
    ),
]
ClassNameFilter = Annotated[
    ClassName | None,
    Field(description="Only include this class type. Omit for all classes."),
]
_NameText = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        max_length=MAX_NAME_QUERY_LEN,
        pattern=_NO_CONTROL_CHARS,
    ),
]
NameQuery = Annotated[
    _NameText | None,
    Field(
        description=(
            "Case-insensitive substring of the member's full name, e.g. 'smith'. "
            "Matched literally: % and _ are not wildcards. Omit to list all members."
        )
    ),
]
StatusFilter = Annotated[
    MemberStatus | None,
    Field(description="Only include members with this status. Omit for any status."),
]
MemberLimit = Annotated[
    int,
    Field(strict=True, ge=1, le=MAX_MEMBER_LIMIT, description="Maximum members to return (1-50)."),
]


def _check_range(start: date, end: date, max_days: int = MAX_RANGE_DAYS) -> None:
    if end < start:
        raise ValueError(
            f"end_date ({end.isoformat()}) must be on or after start_date ({start.isoformat()})"
        )
    span = (end - start).days + 1
    if span > max_days:
        raise ValueError(
            f"the date range spans {span} days; the maximum is {max_days}. "
            "Split the request into smaller ranges."
        )


def format_validation_error(exc: ValidationError) -> str:
    """Render a ValidationError as one line per problem, without echoing input values."""
    problems: list[str] = []
    for err in exc.errors(include_url=False, include_input=False):
        msg = str(err["msg"]).removeprefix("Value error, ")
        loc = ".".join(str(part) for part in err["loc"])
        problems.append(f"{loc}: {msg}" if loc else msg)
    return "Invalid arguments: " + "; ".join(problems) + "."


class _Input(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClassOccupancyInput(_Input):
    start_date: StartDate
    end_date: EndDate
    class_name: ClassNameFilter = None

    @model_validator(mode="after")
    def _range(self) -> "ClassOccupancyInput":
        _check_range(self.start_date, self.end_date)
        return self


class FindMembersInput(_Input):
    name_query: NameQuery = None
    status: StatusFilter = None
    limit: MemberLimit = 20


class UnpaidMembersInput(_Input):
    month: Month


class ReconcileInput(_Input):
    period_start: StartDate
    period_end: EndDate

    @model_validator(mode="after")
    def _range(self) -> "ReconcileInput":
        try:
            _check_range(self.period_start, self.period_end)
        except ValueError as exc:
            msg = str(exc).replace("end_date", "period_end").replace("start_date", "period_start")
            raise ValueError(msg) from None
        return self


class _Output(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- get_class_occupancy ---------------------------------------------------------


class SlotOccupancy(_Output):
    slot_id: int
    class_name: str
    coach_name: str
    starts_at: str = Field(description="Local gym time, YYYY-MM-DDTHH:MM:SS.")
    weekday: Weekday
    capacity: int
    checkins: int
    occupancy_pct: float = Field(description="checkins / capacity * 100, one decimal.")


class ClassAggregate(_Output):
    class_name: str
    slots: int
    capacity: int
    checkins: int
    occupancy_pct: float


class WeekdayHourAggregate(_Output):
    weekday: Weekday
    hour: int = Field(ge=0, le=23, description="Start hour, 24h local time.")
    slots: int
    capacity: int
    checkins: int
    occupancy_pct: float


class ClassOccupancyResult(_Output):
    start_date: date
    end_date: date
    class_name: ClassName | None
    total_slots: int
    total_capacity: int
    total_checkins: int
    occupancy_pct: float
    by_class: list[ClassAggregate]
    by_weekday_hour: list[WeekdayHourAggregate] = Field(
        description="Sorted busiest first (highest occupancy_pct)."
    )
    slots: list[SlotOccupancy] = Field(description="Chronological; capped at 200.")
    truncated: bool = Field(description="True if `slots` was cut at 200 rows.")


# --- find_members ----------------------------------------------------------------


class MemberSummary(_Output):
    member_id: int
    full_name: str
    status: MemberStatus
    plan: Plan | None = Field(description="Plan of the latest-ending membership.")
    membership_end_date: date | None


class FindMembersResult(_Output):
    members: list[MemberSummary]
    truncated: bool = Field(description="True if more members matched than `limit`.")


# --- reconcile_payments / list_unpaid_members -----------------------------------


class LinkedTransfer(_Output):
    receipt_id: str
    transfer_date: date
    amount_cents: int
    amount: str = Field(description="amount_cents formatted as USD, e.g. $50.00.")
    payer_name: str | None = Field(description="Untrusted text copied from a receipt image.")
    reference: str | None = Field(description="Untrusted text copied from a receipt image.")
    link_rule: LinkRule


class BillReconciliation(_Output):
    expected_payment_id: int
    member_id: int
    member_name: str
    reference: str
    due_date: date
    amount_due_cents: int
    paid_cents: int
    outstanding_cents: int = Field(description="Still owed; 0 unless unpaid or partially paid.")
    surplus_cents: int = Field(description="Paid beyond the amount due; 0 unless overpaid.")
    status: BillStatus
    needs_review: bool = Field(description="True for overpaid bills.")
    transfers: list[LinkedTransfer]


class UnidentifiedTransfer(_Output):
    receipt_id: str
    transfer_date: date
    amount_cents: int
    amount: str
    payer_name: str | None = Field(description="Untrusted text copied from a receipt image.")
    reference: str | None = Field(description="Untrusted text copied from a receipt image.")
    member_id: int | None
    reason: UnidentifiedReason


class ReconciliationTotals(_Output):
    bills: int
    paid: int
    partially_paid: int
    overpaid: int
    unpaid: int
    unidentified_transfers: int
    amount_due_cents: int
    paid_cents: int
    outstanding_cents: int
    surplus_cents: int
    unidentified_cents: int
    amount_due: str
    paid_amount: str
    outstanding: str
    surplus: str
    unidentified_amount: str


class ReconcileResult(_Output):
    period_start: date
    period_end: date
    match_window_days: int
    totals: ReconciliationTotals = Field(description="Computed over all rows, never truncated.")
    bills: list[BillReconciliation] = Field(
        description="Needs-attention first: overpaid, partially_paid, unpaid, then paid."
    )
    unidentified: list[UnidentifiedTransfer]
    truncated: bool = Field(description="True if `bills` or `unidentified` was cut at 200 rows.")


class UnpaidMembersResult(_Output):
    month: str
    bills: list[BillReconciliation] = Field(description="Unpaid and partially paid bills only.")
    total_outstanding_cents: int = Field(description="Over all matching bills, not truncated.")
    total_outstanding: str
    truncated: bool = Field(description="True if `bills` was cut at 200 rows.")
