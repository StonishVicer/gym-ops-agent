-- gym-ops-agent schema. Source of truth: docs/architecture.md §2 (ER diagram).
--
-- Conventions (ADR-0003):
--   * money   = INTEGER cents, CHECK typeof = 'integer' (rejects 12.5 and '12.50')
--   * dates   = TEXT 'YYYY-MM-DD',           CHECK (date(col) IS col)
--     IS, not =: date('2026-9-10') is NULL, and a CHECK that evaluates to NULL
--     passes, so `=` would silently accept malformed dates.
--   * local datetimes = TEXT 'YYYY-MM-DDTHH:MM:SS' (gym wall-clock time)
--   * UTC timestamps  = TEXT 'YYYY-MM-DDTHH:MM:SSZ'
--   * PRAGMA foreign_keys = ON is set by the connection factory, not here.
--
-- No table links transfers to bills: reconciliation is computed at query time
-- (SPEC FR-14, ADR-0005), so the schema holds no derived state.

CREATE TABLE members (
    member_id   INTEGER PRIMARY KEY,
    full_name   TEXT    NOT NULL CHECK (length(trim(full_name)) > 0),
    email       TEXT    NOT NULL UNIQUE,  -- synthetic; never returned by MCP
    phone       TEXT    NOT NULL,         -- synthetic; never returned by MCP
    status      TEXT    NOT NULL CHECK (status IN ('active', 'frozen', 'cancelled')),
    joined_on   TEXT    NOT NULL CHECK (date(joined_on) IS joined_on)
);

-- price_cents is the monthly fee billed for this membership; the plan only
-- changes the price (longer commitments are cheaper per month).
CREATE TABLE memberships (
    membership_id INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES members (member_id),
    plan          TEXT    NOT NULL CHECK (plan IN ('monthly', 'quarterly', 'annual', 'student')),
    price_cents   INTEGER NOT NULL CHECK (typeof(price_cents) = 'integer' AND price_cents >= 0),
    start_date    TEXT    NOT NULL CHECK (date(start_date) IS start_date),
    end_date      TEXT    NOT NULL CHECK (date(end_date) IS end_date AND end_date >= start_date),
    -- Target of the composite FK below, so expected_payments.member_id can
    -- never disagree with its membership's member.
    UNIQUE (membership_id, member_id)
);

CREATE TABLE class_slots (
    slot_id      INTEGER PRIMARY KEY,
    class_name   TEXT    NOT NULL CHECK (length(trim(class_name)) > 0),
    coach_name   TEXT    NOT NULL CHECK (length(trim(coach_name)) > 0),
    starts_at    TEXT    NOT NULL CHECK (strftime('%Y-%m-%dT%H:%M:%S', starts_at) IS starts_at),
    duration_min INTEGER NOT NULL CHECK (typeof(duration_min) = 'integer' AND duration_min > 0),
    capacity     INTEGER NOT NULL CHECK (typeof(capacity) = 'integer' AND capacity > 0)
);

CREATE TABLE checkins (
    checkin_id    INTEGER PRIMARY KEY,
    member_id     INTEGER NOT NULL REFERENCES members (member_id),
    slot_id       INTEGER NOT NULL REFERENCES class_slots (slot_id),
    checked_in_at TEXT    NOT NULL
        CHECK (strftime('%Y-%m-%dT%H:%M:%S', checked_in_at) IS checked_in_at),
    UNIQUE (member_id, slot_id)
);

-- One bill per membership per month. member_id is denormalized for the
-- reconcile fallback match and kept consistent by the composite FK.
CREATE TABLE expected_payments (
    expected_payment_id INTEGER PRIMARY KEY,
    membership_id       INTEGER NOT NULL,
    member_id           INTEGER NOT NULL REFERENCES members (member_id),
    due_date            TEXT    NOT NULL CHECK (date(due_date) IS due_date),
    amount_cents        INTEGER NOT NULL
        CHECK (typeof(amount_cents) = 'integer' AND amount_cents > 0),
    reference           TEXT    NOT NULL UNIQUE CHECK (length(trim(reference)) > 0),
    UNIQUE (membership_id, due_date),
    FOREIGN KEY (membership_id, member_id) REFERENCES memberships (membership_id, member_id)
);

-- Filled only by the extractor. Every text column here comes from an untrusted
-- receipt image; it is stored as data, never interpreted.
CREATE TABLE extracted_payments (
    extracted_payment_id INTEGER PRIMARY KEY,
    receipt_id           TEXT    NOT NULL UNIQUE,
    member_id            INTEGER NULL REFERENCES members (member_id),  -- NULL = unknown payer
    payer_name           TEXT    NULL,
    amount_cents         INTEGER NOT NULL
        CHECK (typeof(amount_cents) = 'integer' AND amount_cents > 0),
    currency             TEXT    NOT NULL CHECK (length(currency) = 3 AND currency = upper(currency)),
    transfer_date        TEXT    NOT NULL CHECK (date(transfer_date) IS transfer_date),
    reference            TEXT    NULL,  -- reference-less transfers are valid (ADR-0005)
    bank_name            TEXT    NOT NULL,
    model_id             TEXT    NOT NULL,
    input_tokens         INTEGER NOT NULL
        CHECK (typeof(input_tokens) = 'integer' AND input_tokens >= 0),
    output_tokens        INTEGER NOT NULL
        CHECK (typeof(output_tokens) = 'integer' AND output_tokens >= 0),
    cost_usd_micros      INTEGER NOT NULL
        CHECK (typeof(cost_usd_micros) = 'integer' AND cost_usd_micros >= 0),
    latency_ms           INTEGER NOT NULL
        CHECK (typeof(latency_ms) = 'integer' AND latency_ms >= 0),
    extracted_at         TEXT    NOT NULL
        CHECK (strftime('%Y-%m-%dT%H:%M:%SZ', extracted_at) IS extracted_at)
);

-- Indexes: one per WHERE / JOIN / GROUP BY path of an MCP tool
-- (docs/architecture.md "Indexes", SPEC FR-3). UNIQUE constraints above are
-- indexed implicitly and also cover checkins.member_id and
-- expected_payments.membership_id FK lookups.
CREATE INDEX idx_members_status          ON members (status);
CREATE INDEX idx_memberships_member      ON memberships (member_id, end_date);
CREATE INDEX idx_class_slots_starts_at   ON class_slots (starts_at);
CREATE INDEX idx_class_slots_name_starts ON class_slots (class_name, starts_at);
CREATE INDEX idx_checkins_slot           ON checkins (slot_id);
CREATE INDEX idx_expected_due            ON expected_payments (due_date);
CREATE INDEX idx_expected_member_due     ON expected_payments (member_id, due_date);
CREATE INDEX idx_extracted_date          ON extracted_payments (transfer_date);
CREATE INDEX idx_extracted_reference     ON extracted_payments (reference);
CREATE INDEX idx_extracted_member_date   ON extracted_payments (member_id, transfer_date);
