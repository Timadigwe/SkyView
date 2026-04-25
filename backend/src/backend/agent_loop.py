"""Background monitor: balances, drift, OpenRouter (rebalance tool is log-only, no on-chain tx)."""

from __future__ import annotations

import asyncio
import random
from typing import Any

import mcp.types as mtypes
from mcp import ClientSession

from .llm import openrouter_client, run_rebalance_conversation
from .pricing import sol_price_usd
from .settings import Settings
from .state import (
    AppState,
    RebalanceEvent,
    WalletStatus,
    get_state,
    utcnow_iso,
)


def _drift_from_usd(sol_usd: float, usdc_usd: float) -> tuple[float, float, float]:
    t = sol_usd + usdc_usd
    if t <= 0:
        return 0.0, 0.0, 0.0
    share = sol_usd / t
    drift = abs(share - 0.5) / 0.5
    return drift, share, t


def _suggest_trade(
    sol_usd: float,
    usdc_usd: float,
    sol_price: float,
    settings: Settings,
) -> tuple[str, str, str]:
    """
    Return (side, prompt_lines, side_enum) for human-readable + LLM.
    side_enum: sol_to_usdc | usdc_to_sol
    """
    t = sol_usd + usdc_usd
    if t <= 0 or sol_price <= 0:
        return (
            "none",
            "Cannot suggest trade: zero portfolio or price.",
            "none",
        )
    share = sol_usd / t
    if share > 0.5:
        usd = sol_usd - 0.5 * t
        sol_amt = usd / sol_price
        amount_raw = str(int(max(sol_amt * 1e9, 1)))
        return (
            "sol_to_usdc",
            (
                f"Target 50/50: SOL share is {share*100:.2f}%. Sell ~{sol_amt:.6f} SOL (~${usd:.2f}) for USDC.\n\n"
                f"execute_rebalance_swap parameters:\n"
                f"- input_mint: {settings.sol_mint}\n"
                f"- output_mint: {settings.usdc_mint}\n"
                f"- amount_raw: {amount_raw}  (lamports)\n"
                f"- slippage_bps: {settings.trade_slippage_bps}\n"
                "Note: rebalances are advisory; the server does not send on-chain transactions."
            ),
            "sol_to_usdc",
        )
    usd = 0.5 * t - sol_usd
    amount_raw = str(int(max(usd * 1e6, 1)))
    return (
        "usdc_to_sol",
        (
            f"Target 50/50: SOL share is {share*100:.2f}%. Swap ~{usd:.2f} USDC to SOL.\n\n"
            f"execute_rebalance_swap parameters:\n"
            f"- input_mint: {settings.usdc_mint}\n"
            f"- output_mint: {settings.sol_mint}\n"
            f"- amount_raw: {amount_raw}  (6-decimal USDC base units)\n"
            f"- slippage_bps: {settings.trade_slippage_bps}\n"
            "Note: rebalances are advisory; the server does not send on-chain transactions."
        ),
        "usdc_to_sol",
    )


async def _read_wallet(
    _session: ClientSession, _settings: Settings, state: AppState, _names: list[str]
) -> dict[str, Any]:
    await state.push_thought(
        "Monitor: no server default wallet; use the chat and pass pubkeys explicitly."
    )
    return {}


async def one_cycle(
    session: ClientSession,
    settings: Settings,
    state: AppState,
    mcp_tools: list[mtypes.Tool],
) -> None:
    names = [t.name for t in mcp_tools]
    await state.push_thought("Poll: reading wallet via MCP…")
    data = await _read_wallet(session, settings, state, names)
    sol_price = await sol_price_usd(settings)
    if not data:
        st = state.status
        st.mcp_healthy = True
        st.error = "Could not read wallet (missing getDefaultWallet/getBalance?)."
        await state.update_status(st)
        return
    sol = float(data.get("sol", 0.0) or 0.0)
    usdc = float(data.get("usdc", 0.0) or 0.0)
    waddr = str(data.get("wallet", ""))
    sol_usd = sol * sol_price
    usdc_usd = usdc
    drift, share, total = _drift_from_usd(sol_usd, usdc_usd)
    st = WalletStatus(
        wallet_address=waddr,
        sol_balance=sol,
        usdc_balance=usdc,
        sol_price_usd=sol_price,
        sol_usd=sol_usd,
        usdc_usd=usdc_usd,
        total_usd=total,
        sol_share=share,
        drift_ratio=drift,
        last_poll=utcnow_iso(),
        mcp_healthy=True,
        error=None,
    )
    await state.update_status(st)
    await state.push_thought(
        f"Balances: {sol:.4f} SOL (~${sol_usd:.2f}), {usdc:.2f} USDC. Drift: {drift*100:.2f}% (target band ≤5%)."
    )
    if total <= 0 or sol_price <= 0:
        return
    if drift <= settings.rebalance_drift_threshold + 1e-9:
        return
    side, plan, se = _suggest_trade(sol_usd, usdc_usd, sol_price, settings)
    if se == "none" or not settings.openrouter_api_key:
        if not settings.openrouter_api_key:
            await state.push_thought("Skip rebalance: OPENROUTER_API_KEY is not set.")
        return
    client = openrouter_client(settings)
    up = "\n".join(
        [
            f"Network RPC. Wallet: {waddr}",
            f"SOL: {sol} (~${sol_usd:.2f} at ${sol_price}/SOL), USDC: {usdc} (~${usdc_usd:.2f})",
            f"Total ${total:.2f}, SOL share {share*100:.1f}%, drift: {drift*100:.1f}%.",
            plan,
        ]
    )
    await state.push_thought(
        f"Drift {drift*100:.1f}% > 5% — calling OpenRouter for {side}…"
    )
    try:
        summary = await run_rebalance_conversation(client, settings, up)
    except Exception as e:  # noqa: BLE001
        await state.push_thought(f"OpenRouter/MCP error: {e!s}")
        await state.record_rebalance(
            RebalanceEvent(
                at=utcnow_iso(),
                side=se if se in ("sol_to_usdc", "usdc_to_sol", "none") else "none",
                detail=plan,
                success=False,
                tool_name="execute_rebalance_swap",
                tool_output=str(e),
            )
        )
        return
    await state.push_thought(f"Model: {summary[:2000]}")
    await state.record_rebalance(
        RebalanceEvent(
            at=utcnow_iso(),
            side=se if se in ("sol_to_usdc", "usdc_to_sol", "none") else "none",  # type: ignore[arg-type]
            detail=plan,
            success=True,
            tool_name="execute_rebalance_swap",
            tool_output=summary,
        )
    )


async def monitor_task(
    session: ClientSession,
    settings: Settings,
    mcp_tools: list[mtypes.Tool],
) -> None:
    state = get_state()
    while True:
        try:
            await one_cycle(session, settings, state, mcp_tools)
        except Exception as e:  # noqa: BLE001
            st = state.status
            st.error = str(e)
            st.mcp_healthy = False
            await state.update_status(st)
            await state.push_thought(f"Loop error: {e!r}")
        base = settings.poll_interval_sec
        jitter = random.uniform(-5.0, 5.0)
        await asyncio.sleep(max(20.0, base + jitter))
