"""Every SQL statement the MCP server runs (ADR-0004, NFR-7).

Module-level constants with `?` placeholders only: no statement is ever assembled
from tool input. Each list query takes its row cap as the last parameter.

Optional filters use `(? IS NULL OR col = ?)`, so callers pass the value twice.
"""

# get_class_occupancy ---------------------------------------------------------------
# Params: range_start 'YYYY-MM-DD', range_end_exclusive 'YYYY-MM-DD', class, class, limit.
# `starts_at` is 'YYYY-MM-DDTHH:MM:SS', so text comparison against a bare date is a
# correct day boundary. Check-ins are counted with a correlated subquery rather than
# JOIN + GROUP BY slot_id: the GROUP BY would make SQLite walk class_slots in rowid
# order instead of range-searching idx_class_slots_starts_at (FR-3).

OCCUPANCY_SLOTS = """
SELECT s.slot_id, s.class_name, s.coach_name, s.starts_at, s.capacity,
       (SELECT COUNT(*) FROM checkins AS c WHERE c.slot_id = s.slot_id) AS checkins
FROM class_slots AS s
WHERE s.starts_at >= ? AND s.starts_at < ?
  AND (? IS NULL OR s.class_name = ?)
ORDER BY s.starts_at, s.slot_id
LIMIT ?
"""

OCCUPANCY_BY_CLASS = """
WITH slot AS (
    SELECT s.slot_id, s.class_name, s.capacity,
           (SELECT COUNT(*) FROM checkins AS c WHERE c.slot_id = s.slot_id) AS checkins
    FROM class_slots AS s
    WHERE s.starts_at >= ? AND s.starts_at < ?
      AND (? IS NULL OR s.class_name = ?)
)
SELECT class_name, COUNT(*) AS slots, SUM(capacity) AS capacity, SUM(checkins) AS checkins
FROM slot
GROUP BY class_name
ORDER BY class_name
LIMIT ?
"""

# strftime('%w') is 0 = Sunday .. 6 = Saturday.
OCCUPANCY_BY_WEEKDAY_HOUR = """
WITH slot AS (
    SELECT s.slot_id, s.starts_at, s.capacity,
           (SELECT COUNT(*) FROM checkins AS c WHERE c.slot_id = s.slot_id) AS checkins
    FROM class_slots AS s
    WHERE s.starts_at >= ? AND s.starts_at < ?
      AND (? IS NULL OR s.class_name = ?)
)
SELECT CAST(strftime('%w', starts_at) AS INTEGER) AS weekday,
       CAST(strftime('%H', starts_at) AS INTEGER) AS hour,
       COUNT(*) AS slots, SUM(capacity) AS capacity, SUM(checkins) AS checkins
FROM slot
GROUP BY weekday, hour
ORDER BY weekday, hour
LIMIT ?
"""

# find_members ----------------------------------------------------------------------
# Params: like_pattern, like_pattern, status, status, limit.
# The pattern is pre-escaped by the caller; ESCAPE makes `\%`, `\_`, `\\` literal.
# "Current" membership = latest end_date, lowest membership_id on ties (the primary
# membership, not a same-dated add-on), looked up per member through
# idx_memberships_member rather than ranking every membership with a window function.

FIND_MEMBERS = """
SELECT m.member_id, m.full_name, m.status, cur.plan, cur.end_date
FROM members AS m
LEFT JOIN memberships AS cur ON cur.membership_id = (
    SELECT ms.membership_id
    FROM memberships AS ms
    WHERE ms.member_id = m.member_id
    ORDER BY ms.end_date DESC, ms.membership_id
    LIMIT 1
)
WHERE (? IS NULL OR m.full_name LIKE ? ESCAPE '\\')
  AND (? IS NULL OR m.status = ?)
ORDER BY m.full_name, m.member_id
LIMIT ?
"""

# reconcile_payments / list_unpaid_members -------------------------------------------
# Params: due_from, due_to, limit.

BILLS_DUE_BETWEEN = """
SELECT ep.expected_payment_id, ep.member_id, m.full_name, ep.due_date,
       ep.amount_cents, ep.reference
FROM expected_payments AS ep
JOIN members AS m ON m.member_id = ep.member_id
WHERE ep.due_date BETWEEN ? AND ?
ORDER BY ep.due_date, ep.expected_payment_id
LIMIT ?
"""

# Transfers that can affect a period: dated within period +/- window (they may link
# by member + date, or be unidentified in the period), plus any transfer whose
# reference names a bill due in the period (reference links ignore dates).
# `ref_bill_id` is the bill the reference matches anywhere in the table, or NULL.
# Params: transfer_from, transfer_to, due_from, due_to, limit.

TRANSFERS_FOR_PERIOD = """
WITH scoped AS (
    SELECT extracted_payment_id
    FROM extracted_payments
    WHERE transfer_date BETWEEN ? AND ?
    UNION
    SELECT x.extracted_payment_id
    FROM extracted_payments AS x
    JOIN expected_payments AS ep ON ep.reference = x.reference
    WHERE ep.due_date BETWEEN ? AND ?
)
SELECT x.extracted_payment_id, x.receipt_id, x.member_id, x.payer_name, x.amount_cents,
       x.transfer_date, x.reference, ep.expected_payment_id AS ref_bill_id
FROM scoped
JOIN extracted_payments AS x ON x.extracted_payment_id = scoped.extracted_payment_id
LEFT JOIN expected_payments AS ep ON ep.reference = x.reference
ORDER BY x.extracted_payment_id
LIMIT ?
"""

# Empty-result hints ------------------------------------------------------------------
# The span of dates that hold data, returned when a date-filtered tool finds nothing.
# Two scalar subqueries, not `SELECT MIN(x), MAX(x)`: SQLite's min/max optimisation
# only applies to a lone MIN or MAX, so each subquery is one index seek instead of a
# scan of the whole index. They take no parameters and always return one row.

SLOT_DATE_RANGE = """
SELECT (SELECT MIN(starts_at) FROM class_slots),
       (SELECT MAX(starts_at) FROM class_slots)
"""

BILL_DUE_DATE_RANGE = """
SELECT (SELECT MIN(due_date) FROM expected_payments),
       (SELECT MAX(due_date) FROM expected_payments)
"""
