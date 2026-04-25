"""OpenRouter (OpenAI SDK) for rebalance reasoning; tool calls are logged only (no on-chain swap)."""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

from openai import AsyncOpenAI

from .settings import Settings, get_settings


REBALANCE_SWAP_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "execute_rebalance_swap",
        "description": (
            "Record the intended rebalance (mints and amount_raw). "
            "This deployment does not send on-chain transactions from the server."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "input_mint": {"type": "string", "description": "Input token mint (base58)"},
                "output_mint": {"type": "string", "description": "Output token mint (base58)"},
                "amount_raw": {
                    "type": "string",
                    "description": "Integer string: input size in base units of input mint",
                },
                "slippage_bps": {
                    "type": "integer",
                    "description": "Slippage in basis points, e.g. 150 for 1.5%",
                },
            },
            "required": ["input_mint", "output_mint", "amount_raw"],
        },
    },
}


async def execute_rebalance_from_tool_args(
    args: dict[str, Any], settings: Settings | None = None
) -> str:
    """Satisfy the model tool call without signing or broadcasting any transaction."""
    _ = settings or get_settings()
    inp = str(args.get("input_mint", "")).strip()
    out = str(args.get("output_mint", "")).strip()
    raw_s = str(args.get("amount_raw", "0")).strip()
    if not inp or not out:
        return "input_mint and output_mint are required."
    return (
        "On-chain swap not executed: this API does not sign or broadcast transactions. "
        f"Recorded intent: {inp[:8]}… -> {out[:8]}… amount_raw={raw_s}."
    )


def openrouter_client(settings: Settings) -> AsyncOpenAI:
    headers: dict[str, str] = {}
    if settings.openrouter_referer:
        headers["HTTP-Referer"] = settings.openrouter_referer
    if settings.openrouter_title:
        headers["X-Title"] = settings.openrouter_title
    return AsyncOpenAI(
        base_url="https://openrouter.ai/api/v1",
        api_key=settings.openrouter_api_key,
        default_headers=headers or None,
    )


async def run_rebalance_conversation(
    client: AsyncOpenAI,
    settings: Settings,
    user_prompt: str,
    execute_swap: Callable[[dict[str, Any]], Awaitable[str]] | None = None,
    *,
    require_first_tool: bool = True,
) -> str:
    """
    Call OpenRouter; the model calls `execute_rebalance_swap` (handled locally, no chain tx).
    """
    async def _default_exec(a: dict[str, Any]) -> str:
        return await execute_rebalance_from_tool_args(a, settings)

    exec_fn = execute_swap or _default_exec

    tools: list[dict[str, Any]] = [REBALANCE_SWAP_TOOL]
    model = settings.openrouter_model
    messages: list[dict[str, Any]] = [
        {
            "role": "system",
            "content": (
                "You are a Solana portfolio agent. Target: 50/50 by USD (SOL vs USDC). "
                "The user message includes exact amounts and suggested amount_raw in lamports "
                "or USDC base units. Call execute_rebalance_swap exactly once with input_mint, "
                "output_mint, amount_raw (string integer), and slippage_bps. Then summarize."
            ),
        },
        {"role": "user", "content": user_prompt},
    ]
    for turn in range(6):
        kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "tools": tools,
            "max_tokens": 1500,
        }
        if require_first_tool and turn == 0:
            kwargs["tool_choice"] = {
                "type": "function",
                "function": {"name": "execute_rebalance_swap"},
            }
        res = await client.chat.completions.create(**kwargs)  # type: ignore[arg-type]
        choice = res.choices[0]
        msg = choice.message
        tcalls = list(msg.tool_calls) if msg.tool_calls else []
        if not tcalls:
            return msg.content or "Model finished without a tool call."
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
                    "arguments": tc.function.arguments,
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
            if tname == "execute_rebalance_swap":
                text = await exec_fn(args)
            else:
                text = f"Unknown tool {tname}"
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "content": text,
                }
            )
    return "Model stopped after max tool rounds without final message."
