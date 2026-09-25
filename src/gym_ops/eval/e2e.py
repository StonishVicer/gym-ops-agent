"""End-to-end reconciliation: what the operator would actually see (SPEC FR-14, FR-16).

For one frozen run: take the fresh seeded DB from `integrity.load_run` (no transfers),
write every frozen extraction through the production path (`MemberDirectory.resolve` +
`store.save_payment`: failures are not stored), reconcile every billed month with the
real `reconcile.py`, and compare each receipt with its label's `expected` block.

Only temporary databases are opened; `data/gym.db` and `data/holdout/gym.db` never are.
The month loop is shared with the FR-4 oracle test, so both use one code path.
"""

import calendar
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Final

from pydantic import BaseModel, ConfigDict

from gym_ops.db.connection import get_readonly_connection, get_write_connection
from gym_ops.eval.integrity import LoadedRun
from gym_ops.eval.stats import Proportion, proportion
from gym_ops.extractor.resolve import MemberDirectory
from gym_ops.extractor.store import save_payment
from gym_ops.mcp_server.models import BillStatus, LinkRule
from gym_ops.mcp_server.reconcile import load_and_reconcile
from gym_ops.receipts.labels import Expected

BILLED_MONTHS: Final = "SELECT DISTINCT substr(due_date, 1, 7) FROM expected_payments ORDER BY 1"


@dataclass(frozen=True)
class Reconciled:
    observed: dict[str, Expected]  # receipt_id -> outcome, for every stored transfer
    rules: dict[str, LinkRule]  # receipt_id -> link rule, for linked transfers
    bill_status: dict[str, BillStatus]  # bill reference -> status, every bill due
    months: list[str]


def reconcile_all_months(db: Path, window_days: int) -> Reconciled:
    """Reconcile every billed month; each transfer must be reported exactly once."""
    observed: dict[str, Expected] = {}
    rules: dict[str, LinkRule] = {}
    bill_status: dict[str, BillStatus] = {}
    conn = get_readonly_connection(db)
    try:
        months = [m for (m,) in conn.execute(BILLED_MONTHS).fetchall()]
        for month in months:
            year, mon = map(int, month.split("-"))
            start = date(year, mon, 1)
            end = date(year, mon, calendar.monthrange(year, mon)[1])
            result = load_and_reconcile(conn, start, end, window_days)
            for bill in result.bills:
                bill_status[bill.reference] = bill.status
                for tr in bill.transfers:
                    if tr.receipt_id in observed:
                        raise AssertionError(f"{tr.receipt_id} counted twice")
                    observed[tr.receipt_id] = Expected(
                        bill_reference=bill.reference,
                        bill_status_after_reconciliation=bill.status,
                        unidentified_reason=None,
                    )
                    rules[tr.receipt_id] = tr.link_rule
            for un in result.unidentified:
                if un.receipt_id in observed:
                    raise AssertionError(f"{un.receipt_id} counted twice")
                observed[un.receipt_id] = Expected(
                    bill_reference=None,
                    bill_status_after_reconciliation=None,
                    unidentified_reason=un.reason,
                )
    finally:
        conn.close()
    return Reconciled(observed, rules, bill_status, months)


class BillError(BaseModel):
    """A labelled bill whose final status differs from the label, and why."""

    model_config = ConfigDict(frozen=True)

    bill_reference: str
    expected_status: str
    observed_status: str | None
    receipts: list[str]  # receipts labelled to this bill
    failed_receipts: list[str]  # of those, the ones whose extraction failed (not stored)
    wrong_receipts: list[str]  # of those, stored but with a wrong field


class E2EMetrics(BaseModel):
    model_config = ConfigDict(frozen=True)

    months: list[str]
    bill_link: Proportion
    bill_status: Proportion
    unidentified_reason: Proportion
    all_three: Proportion
    stored: int
    resolver_mismatches: list[str]  # resolved member_id != the one recorded in the run
    receipts_wrong: list[str]
    bill_errors: list[BillError]
    leaked_bills: list[str]  # bills that got a transfer although no label points at them


def load_extractions(run: LoadedRun) -> tuple[int, list[str]]:
    """Write the frozen extractions into the run's fresh DB via the production path."""
    stored = 0
    mismatches: list[str] = []
    conn = get_write_connection(run.db_path)
    try:
        members = MemberDirectory.load(conn)
        for rid in sorted(run.records):
            record = run.records[rid]
            member_id = (
                members.resolve(record.payment.payer_name) if record.ok and record.payment else None
            )
            if member_id != record.member_id:
                mismatches.append(rid)
            stored += save_payment(conn, record, member_id, record.extracted_at)
    finally:
        conn.close()
    return stored, mismatches


def e2e_metrics(run: LoadedRun, window_days: int, wrong_receipts: set[str]) -> E2EMetrics:
    stored, mismatches = load_extractions(run)
    rec = reconcile_all_months(run.db_path, window_days)
    labels = run.labels
    missing = Expected.model_construct(  # sentinel: never equal to a real outcome
        bill_reference="<not stored>",
        bill_status_after_reconciliation=None,
        unidentified_reason=None,
    )
    obs = {rid: rec.observed.get(rid, missing) for rid in labels}

    def prop(pred_ok: dict[str, bool]) -> Proportion:
        return proportion(sum(pred_ok.values()), len(pred_ok))

    link = {
        rid: obs[rid].bill_reference == lb.expected.bill_reference for rid, lb in labels.items()
    }
    status = {
        rid: rid in rec.observed
        and obs[rid].bill_status_after_reconciliation
        == lb.expected.bill_status_after_reconciliation
        for rid, lb in labels.items()
    }
    reason = {
        rid: rid in rec.observed and obs[rid].unidentified_reason == lb.expected.unidentified_reason
        for rid, lb in labels.items()
    }
    all_three = {rid: link[rid] and status[rid] and reason[rid] for rid in labels}

    by_bill: dict[str, list[str]] = {}
    for rid, lb in labels.items():
        if lb.expected.bill_reference:
            by_bill.setdefault(lb.expected.bill_reference, []).append(rid)
    bill_errors = []
    for ref, rids in sorted(by_bill.items()):
        want = labels[rids[0]].expected.bill_status_after_reconciliation
        got = rec.bill_status.get(ref)
        if got != want:
            bill_errors.append(
                BillError(
                    bill_reference=ref,
                    expected_status=str(want),
                    observed_status=got,
                    receipts=rids,
                    failed_receipts=[r for r in rids if r not in rec.observed],
                    wrong_receipts=[r for r in rids if r in rec.observed and r in wrong_receipts],
                )
            )
    touched = {o.bill_reference for o in rec.observed.values() if o.bill_reference}
    return E2EMetrics(
        months=rec.months,
        bill_link=prop(link),
        bill_status=prop(status),
        unidentified_reason=prop(reason),
        all_three=prop(all_three),
        stored=stored,
        resolver_mismatches=mismatches,
        receipts_wrong=sorted(rid for rid, ok in all_three.items() if not ok),
        bill_errors=bill_errors,
        leaked_bills=sorted(touched - set(by_bill)),
    )
