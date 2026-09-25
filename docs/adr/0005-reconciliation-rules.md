# ADR-0005: Reconciliation aggregates transfers per bill, with no carry-over

- **Status:** Accepted
- **Date:** 2026-09-25

## Context

`reconcile_payments` and `list_unpaid_members` (SPEC FR-13, FR-14) must decide, for each bill (`expected_payments` row), whether it is settled, using transfers extracted from receipts (`extracted_payments`). Members of a small gym pay a fixed monthly fee by bank transfer. Most pay once, in full, with the reference. The realistic exceptions are:

- **Top-ups:** $40 sent, then the missing $10 a day later, with or without the reference.
- **Duplicates:** the same bill paid twice.
- **Late payments:** paid weeks after the due date, usually with the reference.
- **Unknown or ambiguous payers:** no reference, and a name that matches no member or a member with more than one open bill.

Constraints: the MCP server is strictly read-only (ADR-0003, NFR-4), reconciliation must be deterministic and testable, and the operator — not the system — decides what to do with money that doesn't fit.

## Options considered

| Option | Complexity | Cost | Security | Portability |
| --- | --- | --- | --- | --- |
| A. **Never aggregate** — one transfer settles one bill; any amount difference is a mismatch | Lowest: one-to-one join | None | Read-only, computed at query time | High: plain SQL |
| **B. Aggregate transfers per bill, no carry-over** — several transfers may link to one bill and are summed; no splitting, no credits | Low–medium: one `SUM ... GROUP BY` on top of A's linking rules | None (no extra LLM calls) | Read-only, computed at query time | High: plain SQL |
| C. **Full ledger** — member balance, credits, oldest-bill-first allocation, transfers split across bills | High: allocation algorithm, balance table, ordering rules, reversal handling | None in tokens; high in code and tests | **Requires writes** (balances, allocations) — incompatible with the read-only MCP server | Medium: allocation logic is stateful and order-dependent |

Behaviour on the realistic cases:

| Case | A | B | C |
| --- | --- | --- | --- |
| $40 + $10 top-up | Bill underpaid **and** $10 unidentified: two wrong signals | `paid` | `paid` |
| Same bill paid twice | Second transfer unidentified | `overpaid` (+$50), flagged | Next bill pre-paid by credit |
| Late payment with reference | `paid` | `paid` | `paid` |
| Ambiguous reference-less transfer | Unidentified | Unidentified (`ambiguous`) | Allocated to the oldest bill: a guess |

## Decision

**Option B.** Rules (full text in SPEC FR-14):

1. Each transfer links to **at most one** bill: by exact `reference` (no date limit); otherwise by resolved `member_id` + `transfer_date` within ±`MATCH_WINDOW_DAYS` (5, in `Settings`) of `due_date`.
2. A reference-less transfer that fits two or more of the member's bills is **unidentified: `ambiguous`**. The system never guesses.
3. Bill status comes from the **sum** of linked transfers, exact cents, no tolerance: `paid`, `partially_paid` (with `outstanding_cents`), `overpaid` (with `surplus_cents`, flagged for review), `unpaid`.
4. **No carry-over** between bills, and **no splitting** a transfer across bills.
5. A **duplicate** payment with the same reference links to the same bill and makes it `overpaid`. It is not reported as a separate category.
6. The output lists the transfers counted for each bill, so the operator can check every number.

**Why C was rejected:** credits and allocations are state. They need a balance ledger and writes on every reconciliation, and the MCP server is read-only by design (ADR-0003). They also make results depend on processing order ("oldest bill first"), which breaks the determinism FR-14 tests for. A gym charging a flat monthly fee does not run accounts receivable; the operator handles the rare overpayment by hand.

**Why A was rejected:** it turns a normal top-up into two false alarms: an underpaid bill and an unidentified transfer.

## Consequences

**Easier**
- Top-ups resolve on their own; `list_unpaid_members` shows the real amount still owed.
- Stays read-only: links and statuses are computed at query time, nothing is stored.
- Each transfer is linked independently of the others, so results don't depend on order and can be property-tested.

**Harder**
- A reference-less top-up that arrives more than `MATCH_WINDOW_DAYS` after the due date stays unidentified (`no_open_bill`), and its bill stays `partially_paid`, until the operator settles it by hand.
- Overpayments need a human decision (refund, or accept as credit off-system).
- The fixture set must cover top-ups, duplicates, late payments and ambiguity (SPEC FR-5).

**Revisit at scale**
- If the gym starts selling variable-price items (packs, merchandise, pro-rated plans) or members routinely prepay, move to Option C: a write-side ledger service owned by a finance process, with the MCP server reading its results.
- Tune `MATCH_WINDOW_DAYS` from observed data (how late reference-less top-ups really arrive).
