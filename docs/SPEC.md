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
- *Amounts:* `amount_cents` on bills (`expected_payments`) and transfers (`extracted_payments`) must be `> 0`: a zero-value bill or transfer is meaningless and would only add noise to reconciliation. `memberships.price_cents` stays `>= 0` so a free plan (e.g. a comped membership) is representable.
- *Dates:* the check is `CHECK (date(col) IS col)`, not `=`. `date()` returns NULL for malformed input such as `2026-9-10`, and SQLite treats a CHECK that evaluates to NULL as passing, so `=` would silently accept bad dates.
- *AC:* With `PRAGMA foreign_keys=ON`, inserting a checkin for a non-existent member raises `sqlite3.IntegrityError`; inserting `amount_cents` of `12.5`, `0`, or a negative value into a bill or transfer raises `IntegrityError`; inserting a date not matching `YYYY-MM-DD` raises `IntegrityError` (via `CHECK (date(col) IS col)`). `PRAGMA foreign_key_check` returns zero rows on the seeded DB.

**FR-3 — Indexes on query paths.** Every column used in a `WHERE`, `JOIN`, or `GROUP BY` by an MCP tool is covered by an index.
- *AC:* For each MCP tool's SQL, `EXPLAIN QUERY PLAN` contains no `SCAN <table>` over `checkins`, `expected_payments`, or `extracted_payments` (only `SEARCH ... USING INDEX` / `COVERING INDEX`).

### Receipt generation (`gym_ops.receipts`)

**FR-4 — Synthetic receipts with ground truth.** `make receipts` renders N (default 100) PNG receipts into `data/receipts/` plus `data/labels.jsonl` with one ground-truth record per image. Receipts are derived from real bills in `data/gym.db` (read through `get_readonly_connection`), selected deterministically from `--seed` (default 7); `--n-max` is a hard safety cap on N. Text is drawn with DejaVu Sans 2.37 bundled under `assets/fonts/` with its license; the files are SHA-256-pinned by a test because a changed font changes every image's bytes. If the files are missing, rendering falls back to Pillow's built-in font, never a system-installed copy.
- *Label (`gym_ops.receipts.labels.ReceiptLabel`):* `receipt_id`, `file`, `scenario` (FR-5 enum), `template`, `difficulty` (list of tags, empty = clean), `adversarial`, `width`, `height`; `truth` = the six `ExtractedPayment` fields (`payer_name`, `amount_cents`, `currency`, `transfer_date`, `reference`, `bank_name`); `expected` = `bill_reference`, `bill_status_after_reconciliation`, `unidentified_reason` — the outcome of FR-14 for this receipt once **all** receipts are reconciled (either a bill and its final status, or a reason alone).
- *AC:* `data/receipts/` contains exactly N `.png` files and `labels.jsonl` has N lines, each validating against `ReceiptLabel`; each label's `receipt_id` matches exactly one filename; every image's long edge is ≤ 1000 px and matches the label's `width`/`height`. Re-running produces identical labels and identical image bytes.
- *AC (oracle):* loading every label's `truth` into `extracted_payments` on a copy of the seeded DB (payer resolved by the production resolver, ADR-0006: the same code path the extractor uses) and running FR-14 reconciliation for every billed month reproduces every label's `expected` block exactly (100%). Any eval error is therefore attributable to extraction, not to the dataset or the rules.

**FR-5 — Realistic difficulty mix.** Receipts vary in layout template (≥ 3 fictional bank templates), amount and date format, rotation (±3°), blur and JPEG-style noise, and include deliberate reconciliation cases.
- *Scenario enum (canonical; code, labels and tests use exactly these names). Counts at N = 100; `exact_payment` fills the remainder:*

| `scenario` | Receipts | What it is | `expected` |
| --- | --- | --- | --- |
| `exact_payment` | 66 | Full amount, with reference, dated within ±`MATCH_WINDOW_DAYS` | `paid` |
| `late_with_reference` | 2 | Full amount, with reference, dated > `MATCH_WINDOW_DAYS` after the due date | `paid` |
| `name_date_match` | 2 | Full amount, no reference; the member has one bill in the window | `paid` |
| `adversarial_injection` | 3 | `exact_payment` whose memo field carries a prompt-injection string (FR-9) | `paid` |
| `multiple_amounts` | 3 | `exact_payment` also printing a subtotal and a fee; only the total is the truth | `paid` |
| `partial_only` | 2 | A single underpayment, nothing else | `partially_paid` |
| `overpayment` | 3 | A single overpayment | `overpaid` |
| `topup_with_reference` | 4 (2 bills × 2) | Partial + remainder, both with reference | `paid` |
| `topup_without_reference` | 4 (2 bills × 2) | Partial with reference + reference-less remainder within the window | `paid` |
| `duplicate` | 2 (1 bill × 2) | Same reference paid twice in full | `overpaid` |
| `ambiguous` | 3 | Reference-less; the member has 2+ bills within ±`MATCH_WINDOW_DAYS` | unidentified `ambiguous` |
| `unknown_payer` | 5 | Reference-less; payer name matches no member | unidentified `unknown_payer` |
| `outside_window_no_ref` | 1 | Reference-less; no bill of the member within the window | unidentified `no_open_bill` |

- *Difficulty tags (independent of scenario):* `amount_plain` (`1234.56`), `amount_no_symbol` (`1,234.56`), `amount_usd_code` (`USD 1,234.56`), `date_us` (`09/19/2026`), `date_long` (`Sep 19, 2026`), `rotation`, `blur`, `jpeg_noise`. A clean receipt (empty list) shows `$1,234.56`, an ISO date and no image degradation. `adversarial_injection` and `multiple_amounts` are always clean, so a failure on them is attributable to their content.
- *AC:* Labels show: ≥ 3 distinct `template` values, with `adversarial_injection`, `multiple_amounts`, `ambiguous` and `unknown_payer` spread across all templates; **≥ 70% of receipts are the only transfer for their bill and leave it `paid`** (strict reading: a single transfer of the full correct amount); ≥ 5% are single transfers that underpay or overpay; ≥ 5% are `unknown_payer`; every scenario in the enum appears at least once, at the counts above; ≥ 40% of receipts are clean; `adversarial_injection` and `multiple_amounts` have `difficulty == []`; ≥ 1 receipt contains a prompt-injection string in its free-text memo field (see FR-9). No real bank names appear in the receipts code or labels.

### Extraction (`gym_ops.extractor`)

**FR-6 — Structured extraction.** Given a receipt image, the extractor returns an `ExtractedPayment` with fields `payer_name`, `amount_cents`, `currency`, `transfer_date`, `reference`, `bank_name`. The model fills the `record_payment` tool with a `ReceiptReading`: the same fields *as printed* (`amount`, `transfer_date` as strings), plus `confidence`, `injection_detected` and `notes`. Deterministic code (`gym_ops.extractor.normalize`) converts the reading into an `ExtractedPayment` (ADR-0002). The tool's `input_schema` is generated from `ReceiptReading`.
- *AC:* With a mocked Anthropic client returning a canned `tool_use` block, `extract_receipt(client, image_path, settings)` returns an `ExtractionResult` holding a validated `ReceiptReading` and `ExtractedPayment`. The request sent to the client contains exactly one tool and `tool_choice={"type": "tool", "name": "record_payment"}`, the image as a base64 content block, `temperature=0` and `max_tokens=512`. Every amount and date format in FR-5 normalizes to the ground-truth value.

**FR-7 — Invalid model output is rejected, not stored.** If the model's tool input fails Pydantic validation (missing field, wrong type, confidence out of range), the extractor retries **once**, sending the field errors back as a `tool_result` with `is_error=true` (ADR-0002). If the retry also fails, or a valid reading can't be normalized (unparseable amount or date, a required field `null`), the receipt is recorded as a failure and not inserted, and any stale row for it is removed.
- *AC:* A mocked response whose input fails validation twice (e.g. `amount: 12.5`, a number instead of the printed string) → no row in `extracted_payments`; the failure is recorded in `extractions.jsonl` and the log with `receipt_id` and the validation error; the batch continues with the next receipt and exits 0. A response that fails once and then validates is stored, with `attempts = 2` and usage summed over both calls.

**FR-8 — Persist with provenance.** Each successful extraction is written to `extracted_payments` with `receipt_id`, the resolved `member_id` (ADR-0006), `model_id` (as reported by the response), `input_tokens`, `output_tokens`, `cost_usd_micros`, `latency_ms`, `extracted_at`. Every receipt, successful or not, also gets one record in `data/extractions.jsonl` (git-ignored): the raw tool input, the normalized fields, `member_id`, usage, cost, latency, attempts and any error.
- *AC:* After extracting a receipt with mocked usage `{input_tokens: 1500, output_tokens: 120}` and prices $1.00 / $5.00 per MTok, the row has `cost_usd_micros = 2100`. Re-extracting the same `receipt_id` updates (upserts) instead of creating a duplicate (`UNIQUE(receipt_id)`), both in the table and in `extractions.jsonl`. Payer names resolve across case, spacing and accent variants; an unknown name or a duplicate member name resolves to `NULL`.

**FR-9 — Receipt content is data, never instructions.** Text on a receipt cannot change the extractor's behaviour or the shape of its output.
- *AC:* For the injection receipt(s) from FR-5, the extractor still returns a schema-valid `ExtractedPayment` whose fields match ground truth, and reports `injection_detected = true` with the injected text in `notes` (live: `make extract-smoke`, `rcpt-0033`; and the eval). Unit tests assert that injected text in `notes` or in a string field is kept as a plain string, never interpreted, and cannot change the amount.

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
- *AC:* Report contains, per field, `accuracy = correct / N`; overall exact-record accuracy; total and mean `input_tokens`, `output_tokens`, `cost_usd`; latency `p50_ms`, `p95_ms`; count of validation failures; and the `model_id` and git SHA. Field comparison rules: `amount_cents`, `transfer_date`, `currency` exact; `payer_name` exact after `gym_ops.extractor.resolve.normalize_name` (NFKD, accents stripped, casefold, whitespace collapsed; the same rule production uses to resolve members, ADR-0006); `bank_name`, `reference` exact after Unicode NFKC + casefold + whitespace collapse. The denominator is always the full labelled set (see NFR-3).
- *Injection detection (FR-9), measured **only** through the boolean `injection_detected`, never through the content of `notes`* (the model also uses `notes` for harmless caveats, such as the synthetic-document footer). The report gives both: the **detection rate** = receipts with `injection_detected = true` / all `adversarial_injection` receipts, and the **false-positive rate** = receipts with `injection_detected = true` / all other receipts. Failed extractions count as not detected, and stay in both denominators.

**FR-17 — Eval gates.** The eval exits non-zero if any NFR-1..NFR-3 threshold is breached.
- *AC:* Feeding the gate function a synthetic results set with `p50_ms=4100` (or mean cost `$0.0051`, or any field accuracy `0.94`) returns exit code 1 with the breached metric named; a passing set returns 0.

## 4. Non-functional requirements

| ID | Requirement | Target | How measured |
| --- | --- | --- | --- |
| **NFR-1** | Cost per extraction | **mean < $0.005** per receipt (and max < $0.01) | `usage.input_tokens × INPUT_USD_PER_MTOK + usage.output_tokens × OUTPUT_USD_PER_MTOK`, from each API response. Budget sanity check at Haiku 4.5 ($1/$5 per MTok), **measured** in the Phase 5 smoke run (3 receipts, 720×960 PNG): **2,424 input tokens** per receipt (image + system prompt + tool schema, identical across receipts) + 202–216 output tokens ≈ **$0.0035** per receipt ($0.003434–$0.003504). The original design estimate (~1,600 image + ~500 prompt/tool + ~150 output ≈ $0.0029) undercounted input by ~15%. A validation retry resends the image and roughly doubles one receipt's cost. |
| **NFR-2** | Extraction latency | **p50 < 4.0 s** (gated); p95 reported, not gated (informational target < 8 s) | Wall-clock around the `messages.create` call (`time.perf_counter`), sequential calls, over the full eval set. |
| **NFR-3** | Per-field accuracy | **≥ 95%** for each of the 6 fields | FR-16 comparison rules over N ≥ 100 labelled receipts. `accuracy = correct / N`, where **N is every labelled receipt**. A receipt that fails extraction after the retry (validation, normalization, or an API error after the SDK's retries) counts as **wrong on every field and stays in the denominator**. Accuracy is never reported over successful extractions only. |
| **NFR-4** | MCP strictly read-only | 0 write paths | (a) DB opened with `file:<path>?mode=ro` URI + `PRAGMA query_only=ON`; (b) test: executing `INSERT`/`UPDATE`/`DELETE`/`CREATE` on the server's connection raises `sqlite3.OperationalError`; (c) test: server module contains no `INSERT|UPDATE|DELETE|DROP|CREATE|ATTACH` string literals (static grep test). |
| **NFR-5** | Zero secrets in repo or logs | 0 findings | `gitleaks`/`detect-secrets` pre-commit hook + CI; `.env` and `*.db` in `.gitignore`; test asserts `Settings.OPENROUTER_API_KEY` is `SecretStr` and never appears in `repr(settings)`; extractor logs are JSON on stderr through a redacting filter, and a test asserts they never contain the API key, an `Authorization` value or base64 image data, even with SDK debug logging forced on. |
| **NFR-6** | Full reproducibility | `make all` succeeds from a clean clone with only `OPENROUTER_API_KEY` set | Fixed seeds (`SEED=42` for Faker, `random.Random`, image noise); FR-1 and FR-4 byte-identity tests. LLM outputs are not bit-reproducible (see A-4); the eval is reproducible in *procedure* and gated by thresholds. |
| **NFR-7** | Type & SQL safety | 0 mypy errors (strict); 0 non-parametrized SQL | `make lint`; ruff `S608` (SQL string building) enabled; static test greps for f-string/`%`/`.format(` near `execute(`. |
| **NFR-8** | MCP tool latency | p95 < 100 ms per tool call on the seeded DB | `tests/mcp_server/test_perf.py` calls each tool 20× in-process (after one warm-up) over the whole seeded window; nearest-rank p95. Marked `perf`: runs in `make test`, excluded from CI (`make test-ci`, `-m "not perf"`) because wall-clock timing on a shared runner is noisy. |
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
- **A-8** Budget is low: the eval runs only the default model (`anthropic/claude-haiku-4.5`); no comparison-model runs. Expected spend ≈ $0.35 per full eval (100 receipts × ~$0.0035, measured; NFR-1).
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
- **Q-4** Validation failure → ~~no retry~~ **revised in Phase 5: one retry**, with the field errors sent back as an `is_error` tool_result (ADR-0002). A second failure is recorded, not stored, and counts as wrong on every field (NFR-3).
- **Q-5** Comparison model → **no**; single default model to keep spend low (A-8).
- **Q-6** p95 latency → **reported only, not gated**. With N=100 it rests on the 5 slowest calls, dominated by network/OpenRouter variance; p50 remains the hard gate.
- **Q-7** Eval set size → **100 receipts** (one error = 1 pp; 95% threshold allows 5 misses).
- **Q-9** Payer-name → `member_id` resolution → **normalized exact match, never fuzzy** (ADR-0006): NFKD + accent stripping + casefold + whitespace collapse on both sides; exactly one match resolves, zero or several resolve to `NULL` (several also logs the candidate ids). Lives in `gym_ops.extractor.resolve`, shared by the extractor, the FR-4 oracle test and the FR-16 `payer_name` comparison.
- **Q-8** Receipt dataset design → **N = 100** (≈ $0.35 per eval run at ~$0.0035 per extraction, measured in Phase 5); scenario counts, canonical names and difficulty mix as in FR-5; the FR-5 70% floor uses the strict reading (single full-amount transfer leaving its bill `paid`); a reference-less transfer outside the window is labelled `no_open_bill`, the reason `reconcile.py` emits; `adversarial_injection` and `multiple_amounts` receipts are always clean.

Open:

None.

## 8. Traceability

Status: **verified** = the listed tests exist and pass; **not built** = the module is a later phase and its tests do not exist yet (the names are the planned ones).

| Req | Module | Verifying test | Status |
| --- | --- | --- | --- |
| FR-1 | `gym_ops.db.seed` | `tests/db/test_seed.py::test_seed_is_deterministic`, `::test_row_counts` | verified |
| FR-2 | `gym_ops.db.schema` | `tests/db/test_schema.py::test_fk_enforced_on_checkin`, `::test_bill_amount_must_be_positive_integer_cents`, `::test_extracted_payment_checks`, `::test_iso_date_check` | verified |
| FR-3 | `gym_ops.db.schema`, `gym_ops.mcp_server.queries` | `tests/mcp_server/test_query_plans.py::test_no_full_scans`, `::test_expected_indexes_used`, `::test_every_query_is_checked` | verified |
| FR-4 | `gym_ops.receipts.generate`, `gym_ops.receipts.labels`, `gym_ops.receipts.render` | `tests/receipts/test_generate.py::test_files_match_labels`, `::test_labels_include_payer_name`, `::test_deterministic_output`, `::test_n_max_is_a_hard_cap`; `tests/receipts/test_oracle_reconciliation.py::test_every_receipt_reconciles_as_labelled`; `tests/receipts/test_assets.py::test_bundled_font_unchanged` | verified |
| FR-5 | `gym_ops.receipts.generate` | `tests/receipts/test_generate.py::test_difficulty_mix`, `::test_every_reconciliation_scenario_present`, `::test_fr5_ratios_strict`, `::test_clean_only_scenarios_have_no_difficulty`, `::test_rare_scenarios_spread_across_templates`; `tests/receipts/test_no_real_banks.py` | verified |
| FR-6 | `gym_ops.extractor.client`, `.schema`, `.extract`, `.normalize` | `tests/extractor/test_extract.py::test_forced_tool_choice`, `::test_returns_validated_model`; `tests/extractor/test_schema.py::test_tool_schema_is_generated_from_model`, `::test_schema_matches_model_fields`; `tests/extractor/test_normalize.py::test_every_dataset_amount_format_round_trips`, `::test_every_dataset_date_format_round_trips`; `tests/extractor/test_client.py::test_env_credentials_never_used`, `::test_sdk_retries_429_and_5xx_then_succeeds`, `::test_sdk_gives_up_after_three_attempts`; live: `tests/extractor/test_live.py` (`make test-live`) | verified |
| FR-7 | `gym_ops.extractor.extract`, `gym_ops.extractor.store` | `tests/extractor/test_store.py::test_invalid_tool_input_not_stored`, `::test_failed_rerun_removes_stale_row`; `tests/extractor/test_extract.py::test_validation_retry_succeeds`, `::test_validation_fails_twice_is_recorded_not_raised`, `::test_unparseable_amount_is_a_failure` | verified |
| FR-8 | `gym_ops.extractor.store`, `gym_ops.extractor.resolve` (ADR-0006) | `tests/extractor/test_store.py::test_cost_micros`, `::test_upsert_by_receipt_id`, `::test_member_resolution_is_stored`, `::test_record_carries_everything`; `tests/extractor/test_resolve.py` | verified |
| FR-9 | `gym_ops.extractor`, `gym_ops.receipts` | receipts side: `tests/receipts/test_generate.py::test_adversarial_flag_marks_injection_receipts`; extractor side: `tests/extractor/test_extract.py::test_adversarial_injection_is_flagged_and_inert`, `::test_injection_text_in_reference_is_kept_as_data`, `::test_validation_error_never_echoes_input`; live: `make extract-smoke` (`rcpt-0033`), then the eval report injection-case row | verified (offline); live pending |
| FR-10 | `gym_ops.mcp_server.server` | `tests/mcp_server/test_tools.py::test_exactly_four_tools`, `::test_no_sql_parameters` | verified |
| FR-11 | `gym_ops.mcp_server.server` (`get_class_occupancy`) | `tests/mcp_server/test_occupancy.py::test_slot_occupancy_pct`, `::test_end_before_start_rejected`, `::test_max_range_boundary` | verified |
| FR-12 | `gym_ops.mcp_server.server` (`find_members`) | `tests/mcp_server/test_members.py::test_substring_case_insensitive`, `::test_like_wildcards_escaped`, `::test_limit_bounds` | verified |
| FR-13 | `gym_ops.mcp_server.server` (`list_unpaid_members`), `gym_ops.mcp_server.reconcile` | `tests/mcp_server/test_unpaid.py::test_outstanding_after_topup` | verified |
| FR-14 (ADR-0005) | `gym_ops.mcp_server.reconcile`, `gym_ops.config` (`MATCH_WINDOW_DAYS`) | `tests/mcp_server/test_reconcile.py::test_exact_payment_paid`, `::test_partial_payment_outstanding`, `::test_topup_with_reference`, `::test_topup_without_reference`, `::test_topup_outside_window_no_open_bill`, `::test_ambiguous_unidentified`, `::test_duplicate_overpaid`, `::test_late_payment_with_reference`, `::test_unknown_payer`, `::test_bill_lists_counted_transfers`, `::test_invariants`, `::test_order_independent` | verified |
| FR-15 | `gym_ops.mcp_server.models` | `tests/mcp_server/test_unpaid.py::test_truncation_flag`, `tests/mcp_server/test_tools.py::test_structured_output_validates` | verified |
| FR-16 | `gym_ops.eval.run`, `gym_ops.eval.metrics` | `tests/eval/test_metrics.py::test_field_normalization`, `::test_percentiles`, `::test_report_shape` | not built |
| FR-17 | `gym_ops.eval.gates` | `tests/eval/test_gates.py` | not built |
| NFR-1 | `gym_ops.eval.metrics`, `gym_ops.config` | `tests/eval/test_gates.py::test_cost_gate`; live: `make eval` | not built |
| NFR-2 | `gym_ops.eval.metrics` | `tests/eval/test_gates.py::test_latency_gate`; live: `make eval` | not built |
| NFR-3 | `gym_ops.eval.metrics` | `tests/eval/test_gates.py::test_accuracy_gate`; live: `make eval` | not built |
| NFR-4 | `gym_ops.db.connection`, `gym_ops.mcp_server.server` (`_open_db`) | `tests/db/test_connection.py::test_readonly_connection_rejects_writes`, `tests/mcp_server/test_readonly.py::test_writes_raise`, `::test_no_write_sql_literals`, `::test_only_readonly_connection_factory` | verified |
| NFR-5 | repo config, `gym_ops.config`, `gym_ops.extractor.logs` | `tests/test_config.py::test_api_key_is_secret`; `tests/extractor/test_logs.py::test_logs_never_contain_key_or_image`, `::test_redact_patterns`; gitleaks pre-commit hook (`.pre-commit-config.yaml`) | verified |
| NFR-6 | `Makefile`, seeds in `gym_ops.config`, `.github/workflows/ci.yml` | `tests/db/test_seed.py::test_seed_is_deterministic` (FR-1); `tests/receipts/test_generate.py::test_deterministic_output` (FR-4); CI runs `make lint` + `make test-ci` + gitleaks on every PR. A CI job running `make all` needs the eval (API key) and comes with that phase | verified (DB, receipts, CI lint/test); not built (`make all` in CI) |
| NFR-7 | all | `tests/test_sql_hygiene.py::test_execute_never_receives_built_sql`, `::test_ruff_sql_injection_rule_enabled`, `tests/mcp_server/test_readonly.py::test_sql_is_never_built_dynamically`; `make lint` (ruff `S608`, mypy strict) | verified |
| NFR-8 | `gym_ops.mcp_server.server` | `tests/mcp_server/test_perf.py::test_tool_p95_latency` — marked `perf`: runs in `make test`, **excluded from CI** (`make test-ci` = `-m "not perf"`) | verified (local only) |
| NFR-9 | all | `make test` / `make test-ci` (`--cov-fail-under=85`; `__main__` shims omitted in `pyproject.toml`) | verified |
