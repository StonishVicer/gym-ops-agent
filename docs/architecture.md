# Architecture

See [SPEC.md](SPEC.md) for requirements and [adr/](adr/) for decisions. All data is synthetic.

## 1. Data flow

```mermaid
flowchart LR
    subgraph offline["Offline processes (make seed / receipts / extract / eval)"]
        SEED["gym_ops.db<br/>deterministic Faker seed<br/>SEED=42"]
        GEN["gym_ops.receipts<br/>generate · render · labels<br/>--seed 7"]
        EXT["gym_ops.extractor<br/>vision + forced tool use"]
        EVAL["gym_ops.eval<br/>accuracy · cost · latency"]
    end

    subgraph files["data/ (git-ignored)"]
        IMG[/"receipts/*.png"/]
        LBL[/"labels.jsonl<br/>truth + expected"/]
    end

    REP[/"reports/eval-*.json + .md"/]

    subgraph db["data/gym.db (SQLite)"]
        CORE[("members · memberships<br/>class_slots · checkins<br/>expected_payments")]
        XP[("extracted_payments")]
    end

    subgraph ext["External"]
        OR["OpenRouter<br/>openrouter.ai/api/v1/messages"]
        CL["Claude<br/>anthropic/claude-haiku-4.5"]
    end

    subgraph mcp["gym_ops.mcp_server (stdio, mode=ro)"]
        T1["get_class_occupancy"]
        T2["find_members"]
        T3["list_unpaid_members"]
        T4["reconcile_payments"]
    end

    CLIENT["MCP client<br/>(Claude Desktop / Claude Code)<br/>Gym operator"]

    SEED -->|"INSERT (rw)"| CORE
    CORE -->|"read bills + members (ro)<br/>to derive receipts"| GEN
    GEN --> IMG
    GEN --> LBL
    IMG -->|"base64 image"| EXT
    EXT -->|"anthropic SDK<br/>base_url + auth_token"| OR
    OR --> CL
    CL -->|"tool_use: record_payment"| OR
    OR -->|"tool_use + usage"| EXT
    EXT -->|"validated + normalized<br/>UPSERT (rw)"| XP
    EXT -->|"one record per receipt"| XJ[/"extractions.jsonl"/]
    EXT -->|"per-call usage, latency"| EVAL
    LBL --> EVAL
    EVAL --> REP

    CORE -. "SELECT (ro)" .-> T1
    CORE -. "SELECT (ro)" .-> T2
    CORE -. "SELECT (ro)" .-> T3
    CORE -. "SELECT (ro)" .-> T4
    XP -. "SELECT (ro)" .-> T3
    XP -. "SELECT (ro)" .-> T4
    T1 & T2 & T3 & T4 <-->|"JSON-RPC over stdio<br/>typed tool I/O"| CLIENT
```

**Trust boundaries**

- *Receipt images are untrusted.* Their text passes through Claude into `extracted_payments` as data (FR-9) and later reaches the MCP client's model via `reconcile_payments`. This is why the MCP surface is four fixed, read-only, PII-minimal tools (ADR-0004).
- *OpenRouter is an external processor.* Only synthetic receipt images leave the machine. The API key lives in `.env` → `Settings.OPENROUTER_API_KEY: SecretStr` and is passed explicitly to the client (ADR-0001).
- *The MCP server is read-only at the engine level* (`mode=ro` + `query_only`, ADR-0003). Only the seeder and extractor hold read-write connections.

**Extraction sequence (one receipt)**

```mermaid
sequenceDiagram
    participant X as extractor
    participant S as Anthropic SDK
    participant O as OpenRouter
    participant C as Claude
    participant D as gym.db
    X->>S: messages.create(image, tools=[record_payment],<br/>tool_choice=record_payment, temperature=0)
    S->>O: POST /api/v1/messages (SDK retries 429/5xx, max 3 attempts)
    O->>C: route
    C-->>O: tool_use{fields as printed} + usage
    O-->>S: response
    S-->>X: Message (t = perf_counter delta)
    X->>X: ReceiptReading.model_validate(tool_use.input)
    opt invalid (once)
        X->>S: same request + tool_result(is_error=true, field errors)
        S-->>X: Message
    end
    X->>X: normalize → ExtractedPayment (cents, ISO date, canonical bank)
    X->>D: resolve payer → member_id (ADR-0006)
    alt valid and normalized
        X->>D: INSERT ... ON CONFLICT(receipt_id) DO UPDATE
    else still invalid, or unparseable
        X->>D: DELETE stale row for receipt_id (failure recorded, never stored)
    end
    X->>X: append record to data/extractions.jsonl
```

## 2. Data model

Conventions: money is `INTEGER` cents (`CHECK (typeof(col) = 'integer' AND col > 0)` for bill and transfer amounts, `>= 0` for `price_cents`); dates are ISO-8601 `TEXT` (`YYYY-MM-DD`, `CHECK (date(col) IS col)` — `IS`, because a NULL CHECK passes); local datetimes are `TEXT` `YYYY-MM-DDTHH:MM:SS` and timestamps are ISO-8601 UTC `TEXT` (`YYYY-MM-DDTHH:MM:SSZ`), both checked with `strftime(fmt, col) IS col`; every FK is declared and `PRAGMA foreign_keys=ON` on read-write connections.

```mermaid
erDiagram
    members ||--o{ memberships : holds
    members ||--o{ checkins : makes
    class_slots ||--o{ checkins : receives
    memberships ||--o{ expected_payments : bills
    members ||--o{ expected_payments : owes
    members |o--o{ extracted_payments : "resolved payer (nullable)"

    members {
        INTEGER member_id PK
        TEXT full_name "NOT NULL"
        TEXT email UK "synthetic; never returned by MCP"
        TEXT phone "synthetic; never returned by MCP"
        TEXT status "CHECK IN (active, frozen, cancelled)"
        TEXT joined_on "YYYY-MM-DD"
    }
    memberships {
        INTEGER membership_id PK
        INTEGER member_id FK "-> members"
        TEXT plan "CHECK IN (monthly, quarterly, annual, student)"
        INTEGER price_cents "CHECK >= 0"
        TEXT start_date "YYYY-MM-DD"
        TEXT end_date "YYYY-MM-DD, CHECK >= start_date"
    }
    class_slots {
        INTEGER slot_id PK
        TEXT class_name "e.g. HIIT, Yoga, Spin"
        TEXT coach_name
        TEXT starts_at "ISO-8601 datetime"
        INTEGER duration_min "CHECK > 0"
        INTEGER capacity "CHECK > 0"
    }
    checkins {
        INTEGER checkin_id PK
        INTEGER member_id FK "-> members"
        INTEGER slot_id FK "-> class_slots"
        TEXT checked_in_at "ISO-8601 datetime"
    }
    expected_payments {
        INTEGER expected_payment_id PK
        INTEGER membership_id FK "-> memberships"
        INTEGER member_id FK "-> members (denormalized for reconcile)"
        TEXT due_date "YYYY-MM-DD"
        INTEGER amount_cents "CHECK > 0"
        TEXT reference UK "e.g. GYM-000123-2026-09"
    }
    extracted_payments {
        INTEGER extracted_payment_id PK
        TEXT receipt_id UK "filename stem"
        INTEGER member_id FK "nullable; resolved at extract time"
        TEXT payer_name
        INTEGER amount_cents "CHECK > 0"
        TEXT currency "ISO-4217, CHECK length = 3"
        TEXT transfer_date "YYYY-MM-DD"
        TEXT reference "nullable; untrusted text"
        TEXT bank_name
        TEXT model_id
        INTEGER input_tokens
        INTEGER output_tokens
        INTEGER cost_usd_micros "1e-6 USD"
        INTEGER latency_ms
        TEXT extracted_at "ISO-8601 UTC"
    }
```

Additional constraints not expressible in Mermaid:

- `checkins`: `UNIQUE (member_id, slot_id)` — a member checks in to a slot at most once.
- `expected_payments`: `UNIQUE (membership_id, due_date)`.
- `extracted_payments.member_id` is resolved by the extractor from the payer name (normalized exact match, ADR-0006) and may be `NULL` for unidentifiable payers — these surface as unidentified transfers (reason `unknown_payer`) in reconciliation. Links between transfers and bills are **not stored**: `reconcile_payments` computes them at query time (SPEC FR-14, ADR-0005), which keeps the MCP server read-only.

### Indexes

Each index is justified by a `WHERE` / `JOIN` / `GROUP BY` in a specific tool (verified by `EXPLAIN QUERY PLAN`, SPEC FR-3).

| Index | Columns | Used by |
| --- | --- | --- |
| `idx_members_status` | `members(status)` | `find_members` filter |
| `idx_memberships_member` | `memberships(member_id, end_date)` | `find_members` current-plan join |
| `idx_class_slots_starts_at` | `class_slots(starts_at)` | `get_class_occupancy` date range |
| `idx_class_slots_name_starts` | `class_slots(class_name, starts_at)` | `get_class_occupancy` class filter + `GROUP BY class_name` |
| `idx_checkins_slot` | `checkins(slot_id)` | occupancy `JOIN` / `COUNT` per slot |
| `idx_expected_due` | `expected_payments(due_date)` | `list_unpaid_members`, `reconcile_payments` window |
| `idx_expected_member_due` | `expected_payments(member_id, due_date)` | reconcile fallback match |
| `idx_extracted_date` | `extracted_payments(transfer_date)` | reconcile window |
| `idx_extracted_reference` | `extracted_payments(reference)` | reconcile primary match |
| `idx_extracted_member_date` | `extracted_payments(member_id, transfer_date)` | reconcile fallback match |

`UNIQUE` constraints are indexed implicitly: `members.email`, `expected_payments.reference`, `extracted_payments.receipt_id`, plus `checkins(member_id, slot_id)` and `expected_payments(membership_id, due_date)`, whose leading columns already serve the `checkins.member_id` and `expected_payments.membership_id` FK lookups — so no separate indexes are created for those.

## 3. Module map

| Module | Responsibility | DB access |
| --- | --- | --- |
| `gym_ops.config` | `Settings` (pydantic-settings, `.env`), prices, seed | — |
| `gym_ops.db` | DDL, connection factories (`get_write_connection`, `get_readonly_connection`), seeder | rw (seed) |
| `gym_ops.receipts` | `generate`: pick real bills per FR-5 scenario (deterministic from `--seed`), assign templates and difficulty; `render`: draw PNGs for 3 fictional bank layouts (bundled DejaVu Sans, SHA-256-pinned; watermark; rotation/blur/JPEG noise); `labels`: `ReceiptLabel` with `truth` + reconciliation `expected`. Writes `data/receipts/*.png` and `data/labels.jsonl`. An oracle test replays `truth` through `reconcile.py` on a DB copy | ro |
| `gym_ops.extractor` | `client`: Anthropic SDK → OpenRouter; `schema`: `ReceiptReading` (tool input, as printed) / `ExtractedPayment` (stored); `extract`: forced tool use, one validation retry; `normalize`: amounts, dates, banks, references; `resolve`: payer → member (ADR-0006); `store`: upsert + `extractions.jsonl`; `budget`: spend guards; `logs`: redacting JSON logs | rw (`extracted_payments` only) |
| `gym_ops.mcp_server` | FastMCP stdio server, 4 tools, Pydantic I/O | **ro only** |
| `gym_ops.eval` | Run extractor over labels, metrics, gates, reports | ro + extractor |

## 4. Scale & reliability notes

Current envelope: ~300 members, ~500 slots, ~10k check-ins, ≤ 500 receipts/month — every tool query is index-backed and sub-100 ms (NFR-8).

- **Extractor failures:** SDK `max_retries=2` (3 attempts) with exponential backoff + jitter for 429/5xx/connection errors; 30 s timeout; one validation retry with the error as an `is_error` tool_result; per-receipt failure isolation (one bad receipt never aborts the run); upsert makes re-runs idempotent.
- **Cost control:** fixed `max_tokens=512`; before a batch, `GET /api/v1/key` must show `limit_remaining ≥ 2 × N × $0.005` or the run does not start; a run stops once its own estimated cost passes $1.00.
- **Log hygiene:** JSON logs on stderr pass through a redacting filter (API key, `Authorization` / `x-api-key` values, base64 blobs); SDK/HTTP loggers are capped at WARNING.
- **Beyond this envelope:** Message Batches API direct to Anthropic for bulk extraction; PostgreSQL with a read-only role and per-gym RLS; HTTP MCP transport with authz (see "Revisit at scale" in each ADR).
