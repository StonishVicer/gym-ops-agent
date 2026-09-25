# gym-ops-agent — Specification

| | |
| --- | --- |
| Status | Draft (Phase 1: design) |
| Date | 2026-09-25 |
| Related | [architecture.md](architecture.md), [ADRs](adr/) |

All data in this project is synthetic. No real people, gyms, or bank records are used at any point.

---

## 1. Problem statement

A small gym (1 location, ~300 members, ~40 class slots/week) collects monthly membership fees by **bank transfer**. Members send a screenshot or photo of the transfer receipt. Today the operator:

1. Reads each receipt by eye and types payer, amount, date, and reference into a spreadsheet.
2. Cross-checks the spreadsheet against who *should* have paid this month.
3. Has no quick answer to "which classes are full / empty?" without exporting check-in data.

This takes several hours per month, silently misses underpayments and unidentifiable transfers, and gives no visibility into class occupancy.

**gym-ops-agent** addresses this with:

- A **receipt extractor** that turns a receipt image into a validated structured record using Claude vision.
- A **read-only MCP server** that lets the operator ask an MCP client (e.g. Claude Desktop / Claude Code) questions such as "who hasn't paid for September?" or "what was Tuesday-evening occupancy last month?".
- An **eval harness** that proves the extractor is accurate, cheap, and fast enough — with numbers.

## 2. Personas

| Persona | Goal | Interface | Permissions |
| --- | --- | --- | --- |
| **Gym operator** | Know who has/hasn't paid, spot mismatches, understand class occupancy. | MCP client (Claude Desktop / Claude Code) connected to `gym_ops.mcp_server` over stdio. | Read-only via 4 domain tools. Never writes. |
| **Finance assistant** | Get receipts into the system without manual typing. | CLI: drops images in `data/receipts/` and runs `make extract`. | Writes only `extracted_payments` (via the extractor process, not MCP). |

## 3. Functional requirements

Each FR has an acceptance criterion (AC) that an automated test verifies. Test paths are the planned locations (see §8).

### Data layer (`gym_ops.db`)

**FR-1 — Deterministic seed.** `make seed` builds `data/gym.db` containing members, memberships, class_slots, checkins, and expected_payments from a fixed seed.
- *AC:* Running seed twice into two files yields byte-identical table contents (same SHA-256 over `SELECT * ... ORDER BY pk` dumps for every table). Row counts match configured defaults: 300 members, ≥ 300 memberships, 12 weeks × 40 class slots = 480 slots, expected_payments = one per active membership per month in the seeded window.

**FR-2 — Schema integrity.** The schema enforces money as INTEGER cents, dates as ISO-8601 TEXT, foreign keys, and `CHECK` constraints on enums.
- *AC:* With `PRAGMA foreign_keys=ON`, inserting a checkin for a non-existent member raises `sqlite3.IntegrityError`; inserting `amount_cents = 12.5` or a negative amount raises `IntegrityError`; inserting a date not matching `YYYY-MM-DD` raises `IntegrityError` (via `CHECK (date(col) = col)`). `PRAGMA foreign_key_check` returns zero rows on the seeded DB.

**FR-3 — Indexes on query paths.** Every column used in a `WHERE`, `JOIN`, or `GROUP BY` by an MCP tool is covered by an index.
- *AC:* For each MCP tool's SQL, `EXPLAIN QUERY PLAN` contains no `SCAN <table>` over `checkins`, `expected_payments`, or `extracted_payments` (only `SEARCH ... USING INDEX` / `COVERING INDEX`).

### Receipt generation (`gym_ops.receipts`)

**FR-4 — Synthetic receipts with ground truth.** `make receipts` renders N (default 100) PNG receipts plus a `labels.jsonl` with one ground-truth record per image.
- *AC:* `data/receipts/` contains exactly N `.png` files and `labels.jsonl` has N lines, each validating against the `ReceiptLabel` Pydantic model — the six `ExtractedPayment` fields (`payer_name`, `amount_cents`, `currency`, `transfer_date`, `reference`, `bank_name`) plus `receipt_id`, `template`, and `scenario` (the FR-5 case it exercises); each label's `receipt_id` matches exactly one filename. Re-running produces identical labels and identical image bytes.

**FR-5 — Realistic difficulty mix.** Receipts vary in layout template (≥ 3 bank-style templates), font, rotation (±3°), JPEG-style noise, and include deliberate reconciliation cases.
- *AC:* Labels show: ≥ 3 distinct `template` values; ≥ 70% of receipts correspond to an expected payment with the correct amount; ≥ 5% are single transfers that underpay or overpay; ≥ 5% have a payer/reference that matches no member; and at least one receipt for each reconciliation scenario (`scenario` label): `topup_with_reference`, `topup_without_reference`, `ambiguous` (reference-less, member has 2+ bills within ±`MATCH_WINDOW_DAYS`), `duplicate` (same reference paid twice in full), `late_with_reference` (paid > `MATCH_WINDOW_DAYS` after the due date, with the reference); ≥ 1 receipt contains a prompt-injection string in its free-text memo field (see FR-9).

### Extraction (`gym_ops.extractor`)

**FR-6 — Structured extraction.** Given a receipt image, the extractor returns a `ExtractedPayment` with fields `payer_name`, `amount_cents`, `currency`, `transfer_date`, `reference`, `bank_name`.
- *AC:* With a mocked Anthropic client returning a canned `tool_use` block, `extract(image_path)` returns a validated `ExtractedPayment`. The request sent to the client contains exactly one tool and `tool_choice={"type": "tool", "name": "record_payment"}` (ADR-0002).

**FR-7 — Invalid model output is rejected, not stored.** If the model's tool input fails Pydantic validation (missing field, non-integer cents, bad date), the receipt is recorded as a failure, not inserted.
- *AC:* Mocked response with `amount_cents: "12.50"` → no row in `extracted_payments`; an `ExtractionFailure` is logged with `receipt_id` and the validation error; the process exits 0 and continues with the next receipt.

**FR-8 — Persist with provenance.** Each successful extraction is written to `extracted_payments` with `receipt_id`, `model_id`, `input_tokens`, `output_tokens`, `cost_usd_micros`, `latency_ms`, `extracted_at`.
- *AC:* After extracting a receipt with mocked usage `{input_tokens: 1500, output_tokens: 120}` and prices $1.00 / $5.00 per MTok, the row has `cost_usd_micros = 2100`. Re-extracting the same `receipt_id` updates (upserts) instead of creating a duplicate (`UNIQUE(receipt_id)`).

**FR-9 — Receipt content is data, never instructions.** Text on a receipt cannot change the extractor's behaviour or the shape of its output.
- *AC:* For the injection receipt(s) from FR-5, the extractor still returns a schema-valid `ExtractedPayment` whose non-memo fields match ground truth (live eval), and the injected text appears, if at all, only inside `reference` as a plain string (unit test asserts the stored value is escaped/unchanged and never interpreted).

### MCP server (`gym_ops.mcp_server`)

**FR-10 — Exactly four read-only domain tools.** The server exposes exactly: `get_class_occupancy`, `find_members`, `list_unpaid_members`, `reconcile_payments` (ADR-0004). No tool accepts raw SQL.
- *AC:* An in-process MCP client `list_tools()` returns exactly those 4 names; each tool's input schema has no free-form `query`/`sql` string parameter.

**FR-11 — Class occupancy.** `get_class_occupancy(start_date, end_date, class_name?)` returns per-slot `capacity`, `checkins`, `occupancy_pct`, plus an aggregate by `class_name` and weekday/hour.
- *AC:* On a fixture DB with a slot of capacity 20 and 15 checkins, the tool returns `checkins=15, occupancy_pct=75.0`. `end_date < start_date` returns a validation error, not an empty result. Range > 366 days is rejected.

**FR-12 — Member lookup.** `find_members(name_query?, status?, limit≤50)` returns members with current membership plan and end date. Name matching is case-insensitive substring via a parametrized `LIKE ? ESCAPE '\'`.
- *AC:* Fixture query `"ana"` returns "Dana Smith" and "Diana Reed"; query `"%"` returns zero rows (wildcards are escaped, not interpreted); `limit=500` is rejected by the Pydantic input model.

**FR-13 — Unpaid members.** `list_unpaid_members(month)` (format `YYYY-MM`) returns bills due in that month whose status (FR-14) is `unpaid` or `partially_paid`, with `amount_due_cents`, `paid_cents`, `outstanding_cents = amount_due_cents − paid_cents`, and the linked transfers.
- *AC:* Fixture with 3 bills of $50.00 — one with no transfers, one paid by a single $50.00 transfer, one paid by $40.00 only — returns 2 rows: `outstanding_cents = 5000` and `outstanding_cents = 1000`. Adding a $10.00 top-up to the third bill removes it from the result.

**FR-14 — Reconciliation (bounded summing, ADR-0005).** `reconcile_payments(period_start, period_end)` links transfers to bills (expected payments), sums the transfers per bill, and reports a status for every bill and every unidentified transfer. Reconciliation is computed at query time; nothing is written.

*Linking — each transfer links to **at most one** bill, decided independently of other transfers, so results never depend on processing order:*
1. **Reference match:** `reference` equals a bill's reference → linked to that bill, **with no date limit**.
2. **Otherwise, member + window:** the transfer's resolved `member_id` has bills with `due_date` within ±`MATCH_WINDOW_DAYS` (`Settings`, default 5) of `transfer_date`. Exactly one → linked. **Two or more → unidentified: `ambiguous`** (the system never guesses).
3. **Otherwise unidentified:** `unknown_payer` (no `member_id`) or `no_open_bill` (member known, no bill in the window).

*Bill status — from `paid_cents = SUM(amount_cents)` of its linked transfers, exact cents, no tolerance:*

| Status | Condition | Reported |
| --- | --- | --- |
| `paid` | `paid_cents == amount_due_cents` | — |
| `partially_paid` | `0 < paid_cents < amount_due_cents` | `outstanding_cents` (amount still owed) |
| `overpaid` | `paid_cents > amount_due_cents` | `surplus_cents` (extra amount), `needs_review = true` |
| `unpaid` | no linked transfers | `outstanding_cents = amount_due_cents` |

*Duplicates:* a second transfer with the same reference links to the same bill (rule 1), so a bill paid twice is reported as **`overpaid`**, with `surplus_cents` equal to the duplicate amount. There is no separate "duplicate" category.

*Bounds — what the system deliberately does not do:* **no carry-over** of surplus to another bill and no stored credit; **no splitting** a transfer across bills; no oldest-bill-first allocation.

*Output:* each bill lists the transfers counted toward it (`receipt_id`, `transfer_date`, `amount_cents`, `payer_name`, `reference`, link rule used). Unidentified transfers are listed with their `reason`.

*Window:* bills with `due_date` in `[period_start, period_end]`, their linked transfers (whatever their date), and unidentified transfers with `transfer_date` in the window.

- *AC — one test per scenario, on a hand-built fixture with $50.00 bills and `MATCH_WINDOW_DAYS = 5`:*
  - Exact single payment → `paid`.
  - $40.00 only → `partially_paid`, `outstanding_cents = 1000`.
  - $40.00 + $10.00 **top-up with reference** → `paid`, both transfers listed under the bill.
  - $40.00 + $10.00 **top-up without reference**, within 5 days → `paid`; the same top-up 8 days after the due date → bill `partially_paid` + transfer unidentified `no_open_bill`.
  - **Ambiguous:** reference-less transfer from a member with two bills within the window → unidentified `ambiguous`; both bills unaffected.
  - **Duplicate:** $50.00 twice with the same reference → `overpaid`, `surplus_cents = 5000`, `needs_review = true`.
  - **Late payment with reference:** 20 days after the due date → `paid`.
  - Unknown payer → unidentified `unknown_payer`.
- *AC — invariants, property-tested over the seeded DB:* every bill in the window appears exactly once; every transfer appears exactly once (under one bill or as unidentified); `Σ paid_cents over bills + Σ unidentified amount_cents == Σ amount_cents of all transfers in scope`; for every bill `outstanding_cents − surplus_cents == amount_due_cents − paid_cents`. Shuffling transfer insertion order yields an identical result.

**FR-15 — Bounded, typed outputs.** Every tool returns a Pydantic model; list outputs are capped (default 200 rows) and report `truncated: bool`.
- *AC:* A fixture with 250 unpaid rows returns 200 rows and `truncated=true`. Output validates against the tool's declared output schema.

### Evaluation (`gym_ops.eval`)

**FR-16 — Eval report.** `make eval` runs the extractor over all labelled receipts and writes `reports/eval-<timestamp>.json` and a Markdown summary.
- *AC:* Report contains, per field, `accuracy = correct / N`; overall exact-record accuracy; total and mean `input_tokens`, `output_tokens`, `cost_usd`; latency `p50_ms`, `p95_ms`; count of validation failures; and the `model_id` and git SHA. Field comparison rules: `amount_cents`, `transfer_date`, `currency` exact; `payer_name`, `bank_name`, `reference` exact after Unicode NFKC + casefold + whitespace collapse.

**FR-17 — Eval gates.** The eval exits non-zero if any NFR-1..NFR-3 threshold is breached.
- *AC:* Feeding the gate function a synthetic results set with `p50_ms=4100` (or mean cost `$0.0051`, or any field accuracy `0.94`) returns exit code 1 with the breached metric named; a passing set returns 0.

## 4. Non-functional requirements

| ID | Requirement | Target | How measured |
| --- | --- | --- | --- |
| **NFR-1** | Cost per extraction | **mean < $0.005** per receipt (and max < $0.01) | `usage.input_tokens × INPUT_USD_PER_MTOK + usage.output_tokens × OUTPUT_USD_PER_MTOK`, from each API response. Budget sanity check at Haiku 4.5 ($1/$5 per MTok): ~1,600 image tokens + ~500 prompt/tool tokens + ~150 output tokens ≈ $0.0029. |
| **NFR-2** | Extraction latency | **p50 < 4.0 s** (gated); p95 reported, not gated (informational target < 8 s) | Wall-clock around the `messages.create` call (`time.perf_counter`), sequential calls, over the full eval set. |
| **NFR-3** | Per-field accuracy | **≥ 95%** for each of the 6 fields | FR-16 comparison rules over N ≥ 100 labelled receipts. Validation failures count as wrong for every field. |
| **NFR-4** | MCP strictly read-only | 0 write paths | (a) DB opened with `file:<path>?mode=ro` URI + `PRAGMA query_only=ON`; (b) test: executing `INSERT`/`UPDATE`/`DELETE`/`CREATE` on the server's connection raises `sqlite3.OperationalError`; (c) test: server module contains no `INSERT|UPDATE|DELETE|DROP|CREATE|ATTACH` string literals (static grep test). |
| **NFR-5** | Zero secrets in repo | 0 findings | `gitleaks`/`detect-secrets` pre-commit hook + CI; `.env` and `*.db` in `.gitignore`; test asserts `Settings.OPENROUTER_API_KEY` is `SecretStr` and never appears in `repr(settings)`. |
| **NFR-6** | Full reproducibility | `make all` succeeds from a clean clone with only `OPENROUTER_API_KEY` set | Fixed seeds (`SEED=42` for Faker, `random.Random`, image noise); FR-1 and FR-4 byte-identity tests. LLM outputs are not bit-reproducible (see A-4); the eval is reproducible in *procedure* and gated by thresholds. |
| **NFR-7** | Type & SQL safety | 0 mypy errors (strict); 0 non-parametrized SQL | `make lint`; ruff `S608` (SQL string building) enabled; static test greps for f-string/`%`/`.format(` near `execute(`. |
| **NFR-8** | MCP tool latency | p95 < 100 ms per tool call on the seeded DB | pytest benchmark-style test calling each tool 50× in-process. |
| **NFR-9** | Test coverage | ≥ 85% line+branch on `gym_ops` (excluding `__main__` shims) | `pytest --cov --cov-fail-under=85`. |

## 5. Non-goals

- **No real bank data** — no bank APIs, no Open Banking, no real receipts, no real PII. Everything is Faker-generated.
- **No auth system** — the MCP server runs locally over stdio for a single trusted operator; no users, roles, sessions, or tokens.
- **No web UI** — interfaces are the MCP client and the CLI (`make` targets).
- **No write tools in MCP** — the MCP server cannot mark payments as reconciled, edit members, or change anything. Writes happen only in the seed and extractor processes.
- No multi-gym / multi-tenant support, no currency conversion, no payment initiation, no PDF receipts (PNG/JPEG only), no deployment/hosting.

## 6. Assumptions

- **A-1** Single currency: **USD**. Amounts are stored as integer cents; the `currency` field is extracted and must equal `USD`, no conversion. Synthetic data uses Faker `en_US` (English names, US-style receipts, `$1,234.56` amounts).
- **A-2** Members include a payment reference (e.g. `GYM-000123-2026-09`) in most transfers; name-based fallback matching covers the rest.
- **A-8** Budget is low: the eval runs only the default model (`anthropic/claude-haiku-4.5`); no comparison-model runs. Expected spend ≈ $0.30 per full eval (100 receipts × ~$0.003).
- **A-3** Receipt volume is small (≤ 500/month), so sequential extraction is acceptable; no queue or batch API needed.
- **A-4** Model calls use `temperature=0`, but responses are not guaranteed deterministic across runs or provider routing; therefore accuracy/cost/latency are asserted as thresholds, not exact values.
- **A-5** OpenRouter's Anthropic-compatible endpoint returns `usage.input_tokens` / `usage.output_tokens` in the Messages API shape; cost is computed locally from those and the configured per-MTok prices (not from any OpenRouter billing field).
- **A-6** Latency measured from the developer's machine includes network and OpenRouter routing overhead; NFR-2 is judged on that end-to-end number.
- **A-7** SQLite runs in the default rollback-journal mode (not WAL), so a `mode=ro` connection needs no write access to `-shm`/`-wal` files.

## 7. Open questions

Resolved 2026-09-25:

- **Q-1** Currency/locale → **USD, English names** (Faker `en_US`). See A-1.
- **Q-2** Tool set → **confirmed**: `get_class_occupancy`, `find_members`, `list_unpaid_members`, `reconcile_payments`.
- **Q-3** Reconciliation → **bounded summing** (FR-14, ADR-0005): several transfers may add up to one bill; a partially paid bill stays open showing `outstanding_cents`. No transfer splitting, no credit carry-over, no guessing on ambiguous matches; overpayments flagged for review. Reference matches ignore dates; reference-less matches use a ±`MATCH_WINDOW_DAYS` (**5**) window. Exact cents, no tolerance.
- **Q-4** Validation failure → **no retry**.
- **Q-5** Comparison model → **no**; single default model to keep spend low (A-8).
- **Q-6** p95 latency → **reported only, not gated**. With N=100 it rests on the 5 slowest calls, dominated by network/OpenRouter variance; p50 remains the hard gate.
- **Q-7** Eval set size → **100 receipts** (one error = 1 pp; 95% threshold allows 5 misses).

## 8. Traceability

| Req | Module | Verifying test (planned) |
| --- | --- | --- |
| FR-1 | `gym_ops.db.seed` | `tests/db/test_seed.py::test_seed_is_deterministic`, `::test_row_counts` |
| FR-2 | `gym_ops.db.schema` | `tests/db/test_schema.py::test_fk_enforced`, `::test_amount_cents_integer_nonnegative`, `::test_iso_date_check` |
| FR-3 | `gym_ops.db.schema`, `gym_ops.mcp_server.queries` | `tests/mcp_server/test_query_plans.py::test_no_full_scans` |
| FR-4 | `gym_ops.receipts.generate`, `gym_ops.receipts.labels` | `tests/receipts/test_generate.py::test_files_match_labels`, `::test_labels_include_payer_name`, `::test_deterministic_output` |
| FR-5 | `gym_ops.receipts.generate` | `tests/receipts/test_generate.py::test_difficulty_mix`, `::test_every_reconciliation_scenario_present` |
| FR-6 | `gym_ops.extractor.client`, `gym_ops.extractor.schema` | `tests/extractor/test_extract.py::test_forced_tool_choice`, `::test_returns_validated_model` |
| FR-7 | `gym_ops.extractor.extract` | `tests/extractor/test_extract.py::test_invalid_tool_input_not_stored` |
| FR-8 | `gym_ops.extractor.store` | `tests/extractor/test_store.py::test_cost_micros`, `::test_upsert_by_receipt_id` |
| FR-9 | `gym_ops.extractor`, `gym_ops.receipts` | `tests/extractor/test_injection.py::test_injection_text_is_inert`; live: eval report injection-case row |
| FR-10 | `gym_ops.mcp_server.server` | `tests/mcp_server/test_tools.py::test_exactly_four_tools`, `::test_no_sql_parameters` |
| FR-11 | `gym_ops.mcp_server.tools.occupancy` | `tests/mcp_server/test_occupancy.py` |
| FR-12 | `gym_ops.mcp_server.tools.members` | `tests/mcp_server/test_members.py::test_like_wildcards_escaped`, `::test_limit_bounds` |
| FR-13 | `gym_ops.mcp_server.tools.payments` | `tests/mcp_server/test_unpaid.py::test_outstanding_after_topup` |
| FR-14 (ADR-0005) | `gym_ops.mcp_server.tools.payments` (`reconcile.py`), `gym_ops.config` (`MATCH_WINDOW_DAYS`) | `tests/mcp_server/test_reconcile.py::test_exact_payment_paid`, `::test_partial_payment_outstanding`, `::test_topup_with_reference`, `::test_topup_without_reference`, `::test_topup_outside_window_no_open_bill`, `::test_ambiguous_unidentified`, `::test_duplicate_overpaid`, `::test_late_payment_with_reference`, `::test_unknown_payer`, `::test_bill_lists_counted_transfers`, `::test_invariants`, `::test_order_independent` |
| FR-15 | `gym_ops.mcp_server.models` | `tests/mcp_server/test_tools.py::test_truncation_flag` |
| FR-16 | `gym_ops.eval.run`, `gym_ops.eval.metrics` | `tests/eval/test_metrics.py::test_field_normalization`, `::test_percentiles`, `::test_report_shape` |
| FR-17 | `gym_ops.eval.gates` | `tests/eval/test_gates.py` |
| NFR-1 | `gym_ops.eval.metrics`, `gym_ops.config` | `tests/eval/test_gates.py::test_cost_gate`; live: `make eval` |
| NFR-2 | `gym_ops.eval.metrics` | `tests/eval/test_gates.py::test_latency_gate`; live: `make eval` |
| NFR-3 | `gym_ops.eval.metrics` | `tests/eval/test_gates.py::test_accuracy_gate`; live: `make eval` |
| NFR-4 | `gym_ops.mcp_server.db` | `tests/mcp_server/test_readonly.py::test_writes_raise`, `::test_no_write_sql_literals` |
| NFR-5 | repo config, `gym_ops.config` | pre-commit secret scan; `tests/test_config.py::test_api_key_is_secret` |
| NFR-6 | `Makefile`, seeds in `gym_ops.config` | FR-1/FR-4 determinism tests; CI job running `make all` |
| NFR-7 | all | `make lint`; `tests/test_sql_hygiene.py` |
| NFR-8 | `gym_ops.mcp_server.tools.*` | `tests/mcp_server/test_perf.py` |
| NFR-9 | all | `make test` (`--cov-fail-under=85`) |
