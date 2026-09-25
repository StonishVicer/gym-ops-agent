# ADR-0003: SQLite as the ops store, opened read-only by the MCP server

- **Status:** Accepted
- **Date:** 2026-09-25

## Context

The project needs a relational store for members, memberships, class slots, check-ins, expected payments, and extracted payments (~300 members, a few thousand check-ins, hundreds of payments). It must be reproducible from `make seed` with no external services (NFR-6), and the MCP server must be *strictly* read-only (NFR-4): an LLM-driven client should have no path to mutate data even if a tool has a bug.

Writers are exactly two offline processes: the seeder (`gym_ops.db`) and the extractor (`gym_ops.extractor`, writes `extracted_payments` only). The MCP server is the only reader exposed to an LLM.

## Options considered

| Option | Complexity | Cost | Security | Portability |
| --- | --- | --- | --- | --- |
| **A. SQLite; MCP opens `file:<path>?mode=ro` (URI) + `PRAGMA query_only=ON`** | Lowest: stdlib `sqlite3`, single file | Free | Read-only enforced by the SQLite engine at open time, plus a second connection-level guard | A single file; runs anywhere Python runs |
| B. SQLite, normal read-write connection; "read-only" by convention | Lowest | Free | Weak: any bug or future tool can write | Same as A |
| C. PostgreSQL with a `SELECT`-only role | Medium–high: server, Docker, migrations, role grants | Hosting or local Docker | Strong, and role-based | Needs a running server; hurts `make all` from a clean clone |
| D. DuckDB `read_only=True` | Low | Free | Strong read-only mode | Good, but columnar/OLAP engine is a poor fit for FK-heavy OLTP-ish data; less familiar to reviewers |

## Decision

**Option A.**

- Schema conventions: money as `INTEGER` cents with `CHECK (amount_cents >= 0)`; dates as ISO-8601 `TEXT` with `CHECK (date(col) = col)` (datetimes: `CHECK (datetime(col) IS NOT NULL)`); explicit `FOREIGN KEY` clauses; `PRAGMA foreign_keys=ON` on every read-write connection; indexes on every column used in `WHERE` / `JOIN` / `GROUP BY` (SPEC FR-3).
- The MCP server opens the DB exactly one way:
  ```python
  sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)  # path from Settings, not user input
  conn.execute("PRAGMA query_only = ON")
  ```
- Rollback-journal mode (default), not WAL, so a read-only connection needs no write access to `-wal`/`-shm` sidecar files (SPEC A-7).
- A test asserts `INSERT`/`UPDATE`/`DELETE`/`CREATE TABLE` on the server's connection raise `sqlite3.OperationalError` (NFR-4).
- `data/*.db` is git-ignored; the DB is always rebuilt from the seed.

## Consequences

**Easier**
- Zero infrastructure; `make seed && make mcp` works on a clean laptop.
- Read-only is enforced by the engine, not by code review — defence in depth with ADR-0004.
- Tests use `:memory:` or `tmp_path` DBs built from the same DDL.

**Harder**
- Single writer at a time: the extractor must not run concurrently with the seeder. Concurrent MCP reads during an extractor write may briefly see `SQLITE_BUSY`; mitigated with `timeout=5.0` on connect.
- No roles/users; "read-only" is per-connection, so the guarantee depends on the MCP server using the one connection factory (enforced by a static test).
- SQLite's loose typing means the `CHECK` constraints carry real weight — they must be tested (FR-2).
- Date arithmetic via `date()`/`julianday()` on TEXT is less ergonomic than native types.

**Revisit at scale**
- Multi-location gyms, concurrent writers, or a hosted MCP server (HTTP transport, many clients) → migrate to PostgreSQL with a `SELECT`-only role and row-level security per gym; the parametrized SQL ports with minimal change.
- If reads contend with writes, switch to WAL mode and grant the MCP process write access only to the sidecar files.
