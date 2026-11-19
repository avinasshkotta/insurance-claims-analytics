"""Layer 3 — the triage agent: status check → summarize → risk → route.

Constraints enforced here:
- The agent routes; it never approves/denies/settles (prompt + tool surface).
- Risk score is deterministic rules (risk.py), not LLM judgment.
- Two transports, identical tools: direct in-process handlers, or MCP (Layer 4).
"""
from __future__ import annotations

import json

from . import config
from .prompts import prompt_text
from .toolkit import TOOL_SCHEMAS, build_direct_handlers

MAX_TURNS = 40  # safety net: 6 claims x ~4 tool calls if processed one-by-one


def _assistant_message(resp: dict) -> dict:
    """Convert our tool-call response shape into an OpenAI chat message."""
    msg: dict = {"role": "assistant", "content": resp.get("content")}
    if resp.get("tool_calls"):
        msg["tool_calls"] = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"],
                          "arguments": json.dumps(tc["arguments"])}}
            for tc in resp["tool_calls"]]
    return msg


async def run_agent(claim_ids: list[str], question: str | None, llm,
                    *, via_mcp: bool = False, handlers: dict | None = None,
                    max_turns: int = MAX_TURNS) -> dict:
    """Run the agent loop. Returns {"final", "tool_log", "turns"}."""
    threshold = config.RISK_THRESHOLD
    user_parts = []
    if claim_ids:
        user_parts.append("Triage these claims: " + ", ".join(claim_ids) + ".")
    if question:
        user_parts.append(f"Question: {question}")
    user_parts.append(f"Review threshold: {threshold}")
    messages = [{"role": "system",
                 "content": prompt_text("triage_system").replace(
                     "{threshold}", str(threshold))},
                {"role": "user", "content": " ".join(user_parts)}]

    tool_log: list[dict] = []

    if via_mcp:
        async with _McpToolbox() as tb:
            return await _loop(llm, messages, tb.tools, tb.call,
                               tool_log, max_turns)
    else:
        if handlers is None:
            raise ValueError("direct transport requires handlers=...")
        call = _direct_transport(handlers)
        return await _loop(llm, messages, TOOL_SCHEMAS, call,
                           tool_log, max_turns)


async def _loop(llm, messages: list[dict], tools: list[dict], call, tool_log: list,
                max_turns: int) -> dict:
    for _ in range(max_turns):
        resp = llm.chat_tools(messages, tools)
        messages.append(_assistant_message(resp))
        if not resp.get("tool_calls"):
            return {"final": resp.get("content") or "", "tool_log": tool_log,
                    "turns": len(messages)}
        for tc in resp["tool_calls"]:
            try:
                result = call(tc["name"], tc["arguments"])
                if hasattr(result, "__await__"):  # async MCP transport
                    result = await result
            except Exception as exc:  # keep the loop alive; report to the model
                result = {"error": f"{type(exc).__name__}: {exc}"}
            tool_log.append({"tool": tc["name"], "args": tc["arguments"],
                             "ok": "error" not in result})
            messages.append({"role": "tool", "tool_call_id": tc["id"],
                             "content": json.dumps(result, default=str)})
    return {"final": "MAX_TURNS exceeded", "tool_log": tool_log,
            "turns": max_turns}


def _direct_transport(handlers: dict):
    def call(name: str, args: dict):
        if name not in handlers:
            raise KeyError(f"unknown tool: {name}")
        return handlers[name](**args)
    return call


MCP_SERVERS = [
    "claims_assistant.mcp_claims_server",
    "claims_assistant.mcp_docs_server",
    "claims_assistant.mcp_case_server",
]


class _McpToolbox:
    """Layer 4 transport: one stdio session per system, multiplexed as one toolset.

    Each server owns only its own tools (least privilege); the client merges
    schemas from the live servers, so drift is visible immediately.
    """

    def __init__(self):
        self._cms: list[tuple] = []
        self._owners: dict[str, object] = {}
        self.tools: list[dict] = []

    async def __aenter__(self) -> "_McpToolbox":
        import os
        import sys

        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        from . import config as cfg

        env = dict(os.environ)
        env["MCP_AUTH_TOKEN"] = cfg.MCP_AUTH_TOKEN
        src_dir = str(cfg.PKG_DIR.parent)
        env["PYTHONPATH"] = os.pathsep.join(
            [src_dir, env.get("PYTHONPATH", "")]).strip(os.pathsep)

        for module in MCP_SERVERS:
            params = StdioServerParameters(
                command=sys.executable, args=["-m", module], env=env)
            client_cm = stdio_client(params)
            read, write = await client_cm.__aenter__()
            session_cm = ClientSession(read, write)
            session = await session_cm.__aenter__()
            await session.initialize()
            self._cms.append((session_cm, client_cm))

            listed = await session.list_tools()
            for tool in listed.tools:
                if tool.name in self._owners:
                    raise RuntimeError(f"duplicate MCP tool across servers: {tool.name}")
                self._owners[tool.name] = session
                self.tools.append({"type": "function", "function": {
                    "name": tool.name,
                    "description": tool.description or "",
                    "parameters": _tool_schema(tool)}})
        return self

    async def call(self, name: str, args: dict) -> dict:
        session = self._owners.get(name)
        if session is None:
            return {"error": f"no MCP server owns tool {name}"}
        result = await session.call_tool(name, args)
        return _parse_mcp_result(result)

    async def __aexit__(self, exc_type, exc, tb) -> None:
        for session_cm, client_cm in reversed(self._cms):
            for cm in (session_cm, client_cm):
                try:
                    await cm.__aexit__(exc_type, exc, tb)
                except Exception:
                    pass


def _tool_schema(t) -> dict:
    """Extract a tool's JSON input schema across mcp 1.x/2.x Tool shapes."""
    for attr in ("inputSchema", "input_schema"):
        v = getattr(t, attr, None)
        if isinstance(v, dict):
            return v
    if hasattr(t, "model_dump"):  # pydantic model (both SDK major versions)
        dump = t.model_dump(by_alias=True, exclude_none=True)
        for key in ("inputSchema", "input_schema"):
            if isinstance(dump.get(key), dict):
                return dump[key]
    return {"type": "object", "properties": {}}


def _parse_mcp_result(result) -> dict:
    """Normalize an MCP CallToolResult into a JSON-able dict."""
    text_parts = []
    for block in getattr(result, "content", []) or []:
        if getattr(block, "type", None) == "text":
            text_parts.append(block.text)
    raw = "\n".join(text_parts)
    if getattr(result, "isError", False):
        return {"error": raw or "MCP tool error"}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {"raw": raw}
