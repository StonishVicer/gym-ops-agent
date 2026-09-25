# Connecting the gym-ops MCP server

The server (`gym_ops.mcp_server`) speaks MCP over **stdio** and exposes four read-only tools: `get_class_occupancy`, `find_members`, `list_unpaid_members`, `reconcile_payments` (SPEC FR-10, ADR-0004). It opens the SQLite database read-only on every call.

## Prerequisites

```bash
make setup   # uv sync + pre-commit hooks
make seed    # builds data/gym.db (synthetic data)
```

MCP clients start the server themselves, usually from their own working directory and without your shell's `PATH`. So every config below uses **absolute paths**:

```bash
which uv   # e.g. /opt/homebrew/bin/uv          -> <UV>
pwd        # run in the repo root, e.g. /Users/you/dev/gym-ops-agent -> <REPO>
```

The configs pass `--directory <REPO>` to `uv`. uv switches to the repo before it starts Python, so `.env` and the default `DB_PATH=data/gym.db` resolve correctly. To use a database somewhere else, set `DB_PATH` to an absolute path in the client's `env`.

## Claude Desktop

Edit `claude_desktop_config.json`:

- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows: `%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "gym-ops": {
      "command": "<UV>",
      "args": [
        "--directory",
        "<REPO>",
        "run",
        "python",
        "-m",
        "gym_ops.mcp_server"
      ]
    }
  }
}
```

Example with real paths:

```json
{
  "mcpServers": {
    "gym-ops": {
      "command": "/opt/homebrew/bin/uv",
      "args": [
        "--directory",
        "/Users/you/dev/gym-ops-agent",
        "run",
        "python",
        "-m",
        "gym_ops.mcp_server"
      ]
    }
  }
}
```

Fully quit and reopen Claude Desktop. The four tools then appear under the tools (hammer/plug) menu. Server logs are at `~/Library/Logs/Claude/mcp-server-gym-ops.log` on macOS.

## Claude Code

```bash
claude mcp add gym-ops -- <UV> --directory <REPO> run python -m gym_ops.mcp_server
```

Example:

```bash
claude mcp add gym-ops -- /opt/homebrew/bin/uv --directory /Users/you/dev/gym-ops-agent run python -m gym_ops.mcp_server
```

- Everything after `--` is the server command, so `uv`'s own flags aren't parsed by `claude`.
- The default scope is `local` (you, this project). `--scope project` writes a `.mcp.json` to commit, but the absolute paths above are machine-specific, so keep this one local.
- To override settings: `claude mcp add gym-ops -e DB_PATH=/abs/path/gym.db -- <UV> ...`.

Check the connection with `claude mcp list` or `/mcp` inside a session. `gym-ops` should show as connected with 4 tools.

## MCP Inspector

```bash
npx @modelcontextprotocol/inspector <UV> --directory <REPO> run python -m gym_ops.mcp_server
```

Open the printed URL, click **Connect**, then **List Tools**.

## Running it by hand

```bash
make mcp
```

This starts the server on stdio and waits for JSON-RPC on stdin. It is mostly useful as the command an MCP client launches (`make` is silenced so nothing but protocol reaches stdout). Stop it with Ctrl-C.

## Troubleshooting

| Symptom | Cause / fix |
| --- | --- |
| Tool returns "The gym database was not found at data/gym.db" | Run `make seed`, or check that `--directory` points at the repo root. |
| "The database could not be queried; it may be missing tables" | The DB predates the current schema. Run `make seed` again. |
| Client shows "spawn uv ENOENT" / server never starts | `command` isn't an absolute path to `uv`. GUI apps don't inherit your shell `PATH`. |
| Client reports invalid JSON from the server | Something wrote to stdout. The server logs only to stderr, so check any wrapper script you added around the command. |
