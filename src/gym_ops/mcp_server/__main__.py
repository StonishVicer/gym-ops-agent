"""`python -m gym_ops.mcp_server` (used by `make mcp`) runs the MCP server on stdio."""

from gym_ops.mcp_server.server import main

raise SystemExit(main())
