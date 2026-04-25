"""SOL/USD from CoinGecko (fallback when GET_PRICE is not used)."""

import httpx

from .settings import Settings, get_settings


async def sol_price_usd(settings: Settings | None = None) -> float:
    settings = settings or get_settings()
    if not settings.use_coingecko_sol_price:
        return 0.0
    async with httpx.AsyncClient(timeout=10.0) as client:
        r = await client.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "solana", "vs_currencies": "usd"},
        )
        r.raise_for_status()
        data = r.json()
        p = data.get("solana", {}).get("usd", 0.0)
    return float(p) if p else 0.0
