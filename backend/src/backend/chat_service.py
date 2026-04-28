"""User chat: LLM + MCP read-only tools (no swaps)."""

from __future__ import annotations

import json
import logging
from typing import Any

import mcp.types as mtypes
from mcp import ClientSession
from openai import AsyncOpenAI

from .mcp_tools import tool_result_text
from .settings import Settings

log = logging.getLogger(__name__)

CHAT_SYSTEM = """You are Skyview: a warm, clear Solana guide. You only have READ-ONLY tools:
native SOL balance, SPL token balance for a mint, SPL token account count, recent signatures for an
address, details for one transaction by signature, account info, and network status (slot/epoch).
When the messages above include earlier user or assistant turns in this same conversation, treat them
as real context: answer follow-ups (e.g. “what address did I paste before?”) using that thread,
without claiming you have no memory of the chat.
The server has no default wallet—if the user does not give an address or signature in the thread,
explain kindly that you need one to look things up, and what to paste (pubkey, tx id, or mint) with
a friendly tone.
Use tools to fetch facts; never invent balances, signatures, or transaction outcomes.
Keep answers readable and concise; use short bullets for multiple facts when helpful.
Do not offer to send transactions, swap, or sign. Sound human and encouraging, not bureaucratic."""


def mcp_tools_to_openai(mcp_tools: list[mtypes.Tool]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for t in mcp_tools:
        params = t.inputSchema if isinstance(t.inputSchema, dict) else {"type": "object"}
        if "type" not in params:
            params = {"type": "object", "properties": params.get("properties", {}), **params}
        out.append(
            {
                "type": "function",
                "function": {
                    "name": t.name,
                    "description": (t.description or t.name)[:2000],
                    "parameters": params,
                },
            }
        )
    return out


async def run_chat_with_mcp(
    session: ClientSession,
    client: AsyncOpenAI,
    settings: Settings,
    mcp_tools: list[mtypes.Tool],
    user_message: str,
    *,
    history: list[dict[str, str]] | None = None,
) -> tuple[str, str]:
    """
    Returns (assistant_text, tool_trace) where tool_trace is a short log for the output guardrail.
    """
    tools = mcp_tools_to_openai(mcp_tools)
    if not tools:
        return (
            "Chain tools are not available (MCP not ready).",
            "no tools",
        )

    messages: list[dict[str, Any]] = [
        {"role": "system", "content": CHAT_SYSTEM},
    ]
    for h in history or []:
        r = h.get("role")
        c = h.get("content", "")
        if r in ("user", "assistant") and c:
            messages.append({"role": r, "content": c[:4000]})
    messages.append({"role": "user", "content": user_message[:8000]})

    model = settings.openrouter_model
    max_rounds = max(1, min(settings.chat_max_tool_rounds, 20))
    trace_parts: list[str] = []

    for round_idx in range(max_rounds):
        log.debug("chat agent round %s/%s", round_idx + 1, max_rounds)
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "max_tokens": 2000,
            "temperature": 0.35,
        }
        res = await client.chat.completions.create(**kwargs)  # type: ignore[arg-type]
        choice = res.choices[0]
        msg = choice.message
        tcalls = list(msg.tool_calls) if msg.tool_calls else []
        if not tcalls:
            log.info("chat agent: model finished without tool calls (round %s)", round_idx + 1)
            return (msg.content or "(no content)", "\n".join(trace_parts)[:20000])

        names = [tc.function.name for tc in tcalls]
        log.info("chat agent: tool round %s calling %s", round_idx + 1, names)

        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": msg.content,
        }
        assistant_msg["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments or "{}",
                },
            }
            for tc in tcalls
        ]
        messages.append(assistant_msg)

        for tc in tcalls:
            tname = tc.function.name
            try:
                args = json.loads(tc.function.arguments) if tc.function.arguments else {}
            except json.JSONDecodeError:
                args = {}
            try:
                result = await session.call_tool(tname, args)
                text = tool_result_text(result)[:100000]
            except Exception as e:  # noqa: BLE001
                log.exception("MCP tool %s failed", tname)
                text = f"Tool error: {e!s}"
            trace_parts.append(f"TOOL {tname} -> {text[:2000]}{'...' if len(text) > 2000 else ''}")
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": text,
                }
            )

    log.warning("chat agent: hit max tool rounds (%s)", max_rounds)
    return (
        "Stopped: too many tool rounds. Try a narrower question.",
        "\n".join(trace_parts)[:20000],
    )
