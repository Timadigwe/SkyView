"""Helpers for MCP tool results and name resolution."""

import json
import re
from typing import Any

import mcp.types as mtypes


def tool_result_text(result: mtypes.CallToolResult) -> str:
    parts: list[str] = []
    for block in result.content:
        if isinstance(block, mtypes.TextContent) and block.text:
            parts.append(block.text)
    return "\n".join(parts).strip()


def _parse_json_flexible(s: str) -> Any:
    s = s.strip()
    if not s:
        return None
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", s)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    m = re.search(r"\[[\s\S]*\]", s)
    if m:
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None
    return None


def parse_sol_balance_text(text: str) -> float | None:
    """Best-effort SOL amount from BALANCE or similar text."""
    d = _parse_json_flexible(text) if "{" in text or "[" in text else None
    if isinstance(d, dict):
        for k in (
            "sol",
            "solBalance",
            "balanceSol",
            "lamports",
            "balance",
        ):
            v = d.get(k)
            if v is not None:
                if k == "lamports":
                    return float(v) / 1e9
                try:
                    return float(v)
                except (TypeError, ValueError):
                    pass
    m = re.search(r"([\d.]+)\s*SOL", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(r"([0-9]+(\.[0-9]+)?)\s*sol", text, re.I)
    if m:
        return float(m.group(1))
    m = re.search(
        r"balance[:\s]+([0-9.]+)", text, re.I
    )
    if m:
        return float(m.group(1))
    if re.search(r"^\d+(\.\d+)?$", text.strip().split()[-1] if text else ""):
        try:
            return float(text.strip().split()[-1])
        except (IndexError, ValueError):
            pass
    return None


def _find_in_obj(obj: Any, mint: str) -> float | None:
    mint_lower = mint.lower()
    if isinstance(obj, list):
        for it in obj:
            r = _find_in_obj(it, mint)
            if r is not None:
                return r
    if isinstance(obj, dict):
        mid = str(obj.get("mint", "")).lower()
        if mid and mint_lower in mid:
            for k in ("uiAmount", "ui_amount", "amount", "balance", "value"):
                v = obj.get(k)
                if v is not None:
                    try:
                        if isinstance(v, (int, float, str)) and not isinstance(v, bool):
                            return float(v) / (
                                10**float(obj.get("decimals", 6)) if "decimals" in obj else 1.0
                            ) if "decimals" in str(obj) and k in ("amount",) else float(
                                v
                            )
                    except (TypeError, ValueError, ZeroDivisionError):
                        pass
        for v in obj.values():
            r = _find_in_obj(v, mint)
            if r is not None:
                return r
    return None


def parse_token_balance_for_mint(
    text: str, usdc_mint: str, decimals: int = 6
) -> float | None:
    d = _parse_json_flexible(text)
    if d is not None:
        f = _find_in_obj(d, usdc_mint)
        if f is not None:
            return f
    if usdc_mint in text and re.search(r"[\d.]+", text):
        m = re.search(r"([\d.]+)", text[text.find(usdc_mint) : text.find(usdc_mint) + 80])
        if m:
            return float(m.group(1))
    m = re.search(
        r"usdc[:\s]+([0-9,]+(\.[0-9]+)?)", text, re.I
    )
    if m:
        return float(m.group(1).replace(",", ""))
    m = re.search(
        r"token[^0-9]+([0-9,]+(\.[0-9]+)?)\s*USDC", text, re.I
    )
    if m:
        return float(m.group(1).replace(",", ""))
    return None


def parse_default_wallet_text(text: str) -> str | None:
    m = re.search(
        r"Default wallet:\s*([1-9A-HJ-NP-Za-km-z]{32,50})",
        text,
    )
    return m.group(1) if m else None


def parse_token_ui_units_text(text: str) -> float | None:
    """Parse getTokenBalance line: '... balance: 12.34 (ui units) ...'."""
    m = re.search(
        r"balance:\s*([\d.]+)\s*\(ui units\)",
        text,
        re.I,
    )
    if m:
        return float(m.group(1))
    return None


def pick_tool(names: list[str], *candidates: str) -> str | None:
    upper = {n.upper(): n for n in names}
    for c in candidates:
        u = c.upper()
        if u in upper:
            return upper[u]
    return None


async def call_mcp_parsed(
    session: Any, name: str, arguments: dict | None
) -> tuple[mtypes.CallToolResult, str]:
    result = await session.call_tool(name, arguments or {})
    return result, tool_result_text(result)
