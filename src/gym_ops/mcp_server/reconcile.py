"""Bill/transfer reconciliation, computed at query time (SPEC FR-14, ADR-0005).

Linking — each transfer links to at most one bill, decided from that transfer alone,
so the result never depends on the order transfers were inserted:

1. `reference` equals a bill's reference -> that bill, whatever the dates.
2. Otherwise, a known `member_id` with exactly one bill due within
   +/- `window_days` of `transfer_date` -> that bill. Two or more -> `ambiguous`.
3. Otherwise unidentified: `unknown_payer` (no member) or `no_open_bill`.

Bill status comes from the exact-cents sum of its linked transfers. No carry-over,
no splitting, no guessing.
"""

import sqlite3
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from gym_ops.mcp_server import queries
from gym_ops.mcp_server.models import (
    MAX_RECEIPT_TEXT_LEN,
    BillReconciliation,
    BillStatus,
    LinkedTransfer,
    LinkRule,
    ReconciliationTotals,
    UnidentifiedReason,
    UnidentifiedTransfer,
)

# Safety bound on rows loaded for one reconciliation. Totals need every row, so
# exceeding it is an error rather than a silent truncation.
MAX_LOADED_ROWS = 50_000

_STATUS_ORDER: dict[BillStatus, int] = {
    "overpaid": 0,
    "partially_paid": 1,
    "unpaid": 2,
    "paid": 3,
}


class TooManyRowsError(Exception):
    """The period holds more rows than MAX_LOADED_ROWS."""


@dataclass(frozen=True)
class Bill:
    expected_payment_id: int
    member_id: int
    member_name: str
    due_date: date
    amount_cents: int
    reference: str


@dataclass(frozen=True)
class Transfer:
    extracted_payment_id: int
    receipt_id: str
    member_id: int | None
    payer_name: str | None
    amount_cents: int
    transfer_date: date
    reference: str | None
    ref_bill_id: int | None  # bill whose reference equals this transfer's, if any


@dataclass(frozen=True)
class Reconciliation:
    bills: list[BillReconciliation]  # every bill due in the period, attention-first
    unidentified: list[UnidentifiedTransfer]  # unlinked transfers dated in the period
    totals: ReconciliationTotals


def format_usd(cents: int) -> str:
    """Format integer cents as USD, e.g. 123456 -> '$1,234.56'."""
    sign = "-" if cents < 0 else ""
    dollars, rem = divmod(abs(cents), 100)
    return f"{sign}${dollars:,}.{rem:02d}"


def clip(text: str | None) -> str | None:
    """Clip untrusted receipt text to MAX_RECEIPT_TEXT_LEN characters."""
    if text is None or len(text) <= MAX_RECEIPT_TEXT_LEN:
        return text
    return text[: MAX_RECEIPT_TEXT_LEN - 1] + "…"


def link_transfer(
    transfer: Transfer,
    bills_by_member: dict[int, list[Bill]],
    window_days: int,
) -> tuple[int, LinkRule] | UnidentifiedReason:
    """Return (bill id, rule) for a linked transfer, or the reason it is unidentified.

    `bills_by_member` must hold every bill due within `window_days` of the transfer.
    """
    if transfer.ref_bill_id is not None:
        return transfer.ref_bill_id, "reference"
    if transfer.member_id is None:
        return "unknown_payer"
    window = timedelta(days=window_days)
    candidates = [
        bill
        for bill in bills_by_member.get(transfer.member_id, [])
        if abs(bill.due_date - transfer.transfer_date) <= window
    ]
    if len(candidates) == 1:
        return candidates[0].expected_payment_id, "member_date_window"
    return "ambiguous" if candidates else "no_open_bill"


def _bill_status(amount_due: int, paid: int, has_transfers: bool) -> BillStatus:
    if not has_transfers:
        return "unpaid"
    if paid == amount_due:
        return "paid"
    return "partially_paid" if paid < amount_due else "overpaid"


def reconcile(
    period_start: date,
    period_end: date,
    context_bills: Iterable[Bill],
    transfers: Iterable[Transfer],
    window_days: int,
) -> Reconciliation:
    """Reconcile bills due in [period_start, period_end] against `transfers`.

    `context_bills` must cover due dates in [period_start - 2*window, period_end +
    2*window] so every transfer dated within the period +/- window sees all of its
    candidate bills. `transfers` must include every transfer that references a bill
    due in the period and every transfer dated in period +/- window.
    """
    bills_by_member: dict[int, list[Bill]] = defaultdict(list)
    period_bills: list[Bill] = []
    for bill in context_bills:
        bills_by_member[bill.member_id].append(bill)
        if period_start <= bill.due_date <= period_end:
            period_bills.append(bill)
    period_bill_ids = {b.expected_payment_id for b in period_bills}

    linked: dict[int, list[LinkedTransfer]] = defaultdict(list)
    unidentified: list[UnidentifiedTransfer] = []
    for t in sorted(transfers, key=lambda t: (t.transfer_date, t.receipt_id)):
        outcome = link_transfer(t, bills_by_member, window_days)
        if isinstance(outcome, tuple):
            bill_id, rule = outcome
            if bill_id in period_bill_ids:
                linked[bill_id].append(
                    LinkedTransfer(
                        receipt_id=t.receipt_id,
                        transfer_date=t.transfer_date,
                        amount_cents=t.amount_cents,
                        amount=format_usd(t.amount_cents),
                        payer_name=clip(t.payer_name),
                        reference=clip(t.reference),
                        link_rule=rule,
                    )
                )
            # Linked to a bill outside the period: out of scope here.
        elif period_start <= t.transfer_date <= period_end:
            unidentified.append(
                UnidentifiedTransfer(
                    receipt_id=t.receipt_id,
                    transfer_date=t.transfer_date,
                    amount_cents=t.amount_cents,
                    amount=format_usd(t.amount_cents),
                    payer_name=clip(t.payer_name),
                    reference=clip(t.reference),
                    member_id=t.member_id,
                    reason=outcome,
                )
            )

    results: list[BillReconciliation] = []
    for bill in period_bills:
        bill_transfers = linked.get(bill.expected_payment_id, [])
        paid = sum(t.amount_cents for t in bill_transfers)
        status = _bill_status(bill.amount_cents, paid, bool(bill_transfers))
        results.append(
            BillReconciliation(
                expected_payment_id=bill.expected_payment_id,
                member_id=bill.member_id,
                member_name=bill.member_name,
                reference=bill.reference,
                due_date=bill.due_date,
                amount_due_cents=bill.amount_cents,
                paid_cents=paid,
                outstanding_cents=max(bill.amount_cents - paid, 0),
                surplus_cents=max(paid - bill.amount_cents, 0),
                status=status,
                needs_review=status == "overpaid",
                transfers=bill_transfers,
            )
        )
    results.sort(key=lambda b: (_STATUS_ORDER[b.status], b.due_date, b.expected_payment_id))

    return Reconciliation(
        bills=results, unidentified=unidentified, totals=_totals(results, unidentified)
    )


def _totals(
    bills: Sequence[BillReconciliation], unidentified: Sequence[UnidentifiedTransfer]
) -> ReconciliationTotals:
    def count(status: BillStatus) -> int:
        return sum(1 for b in bills if b.status == status)

    amount_due = sum(b.amount_due_cents for b in bills)
    paid = sum(b.paid_cents for b in bills)
    outstanding = sum(b.outstanding_cents for b in bills)
    surplus = sum(b.surplus_cents for b in bills)
    unidentified_cents = sum(t.amount_cents for t in unidentified)
    return ReconciliationTotals(
        bills=len(bills),
        paid=count("paid"),
        partially_paid=count("partially_paid"),
        overpaid=count("overpaid"),
        unpaid=count("unpaid"),
        unidentified_transfers=len(unidentified),
        amount_due_cents=amount_due,
        paid_cents=paid,
        outstanding_cents=outstanding,
        surplus_cents=surplus,
        unidentified_cents=unidentified_cents,
        amount_due=format_usd(amount_due),
        paid_amount=format_usd(paid),
        outstanding=format_usd(outstanding),
        surplus=format_usd(surplus),
        unidentified_amount=format_usd(unidentified_cents),
    )


def _fetch_bounded(conn: sqlite3.Connection, sql: str, params: tuple[object, ...]) -> list[Any]:
    rows: list[Any] = conn.execute(sql, (*params, MAX_LOADED_ROWS + 1)).fetchall()
    if len(rows) > MAX_LOADED_ROWS:
        raise TooManyRowsError
    return rows


def load_and_reconcile(
    conn: sqlite3.Connection, period_start: date, period_end: date, window_days: int
) -> Reconciliation:
    """Load the bills and transfers that can affect the period, then reconcile."""
    window = timedelta(days=window_days)
    bill_rows = _fetch_bounded(
        conn,
        queries.BILLS_DUE_BETWEEN,
        ((period_start - 2 * window).isoformat(), (period_end + 2 * window).isoformat()),
    )
    transfer_rows = _fetch_bounded(
        conn,
        queries.TRANSFERS_FOR_PERIOD,
        (
            (period_start - window).isoformat(),
            (period_end + window).isoformat(),
            period_start.isoformat(),
            period_end.isoformat(),
        ),
    )
    bills = [
        Bill(
            expected_payment_id=r[0],
            member_id=r[1],
            member_name=r[2],
            due_date=date.fromisoformat(r[3]),
            amount_cents=r[4],
            reference=r[5],
        )
        for r in bill_rows
    ]
    transfers = [
        Transfer(
            extracted_payment_id=r[0],
            receipt_id=r[1],
            member_id=r[2],
            payer_name=r[3],
            amount_cents=r[4],
            transfer_date=date.fromisoformat(r[5]),
            reference=r[6],
            ref_bill_id=r[7],
        )
        for r in transfer_rows
    ]
    return reconcile(period_start, period_end, bills, transfers, window_days)
