"""Shared MCP server helpers: SDK compat + transport trust boundary.

Each MCP server checks for a shared token before starting. stdio subprocesses
inherit the parent's trust, so this is a guard against accidental unauthenticated
launches — production would terminate TLS + OAuth at the HTTP transport.
"""
from __future__ import annotations

import os
import sys

try:  # mcp 1.x
    from mcp.server.fastmcp import FastMCP as MCPServer
except (ImportError, ModuleNotFoundError):  # mcp 2.x
    from mcp.server.mcpserver import MCPServer

TOKEN_ENV = "MCP_AUTH_TOKEN"
MIN_TOKEN_LEN = 8


def require_token(server_name: str) -> str:
    token = os.environ.get(TOKEN_ENV, "")
    if len(token) < MIN_TOKEN_LEN:
        print(f"[{server_name}] refusing to start: {TOKEN_ENV} missing or too short.",
              file=sys.stderr)
        raise SystemExit(2)
    return token


def make_server(name: str) -> MCPServer:
    return MCPServer(name)
