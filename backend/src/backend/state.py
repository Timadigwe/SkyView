"""In-memory app state, thought log ring buffer, and SSE fan-out."""

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import uuid4

from pydantic import BaseModel, Field

MAX_THOUGHTS = 200
MAX_REBALANCES = 100
MAX_SQUEUES = 20


def utcnow_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class RebalanceEvent(BaseModel):
    at: str
    side: Literal["sol_to_usdc", "usdc_to_sol", "none"]
    detail: str
    success: bool
    tool_name: str | None = None
    tool_output: str | None = None


class WalletStatus(BaseModel):
    wallet_address: str = ""
    sol_balance: float = 0.0
    usdc_balance: float = 0.0
    sol_price_usd: float = 0.0
    sol_usd: float = 0.0
    usdc_usd: float = 0.0
    total_usd: float = 0.0
    sol_share: float = 0.0
    drift_ratio: float = 0.0
    last_poll: str = ""
    mcp_healthy: bool = False
    error: str | None = None


@dataclass
class AppState:
    thoughts: list[dict[str, Any]] = field(default_factory=list)
    rebalances: list[RebalanceEvent] = field(default_factory=list)
    status: WalletStatus = field(default_factory=WalletStatus)
    mcp_tool_names: list[str] = field(default_factory=list)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)
    _sse: list[asyncio.Queue[dict[str, Any]]] = field(default_factory=list, repr=False)

    async def push_thought(self, message: str) -> None:
        row = {
            "id": str(uuid4()),
            "ts": utcnow_iso(),
            "message": message,
        }
        async with self._lock:
            self.thoughts.append(row)
            if len(self.thoughts) > MAX_THOUGHTS:
                self.thoughts = self.thoughts[-MAX_THOUGHTS:]
        await self._broadcast_sse(
            {
                "type": "thought",
                **row,
            }
        )

    async def record_rebalance(self, event: RebalanceEvent) -> None:
        async with self._lock:
            self.rebalances.append(event)
            if len(self.rebalances) > MAX_REBALANCES:
                self.rebalances = self.rebalances[-MAX_REBALANCES:]
        await self._broadcast_sse(
            {
                "type": "rebalance",
                "data": event.model_dump(),
            }
        )

    async def update_status(self, status: WalletStatus) -> None:
        async with self._lock:
            self.status = status
        await self._broadcast_sse(
            {
                "type": "status",
                "data": status.model_dump(),
            }
        )

    async def _broadcast_sse(self, payload: dict[str, Any]) -> None:
        for q in self._sse:
            try:
                q.put_nowait(payload)
            except Exception:
                pass

    def register_sse(self) -> asyncio.Queue[dict[str, Any]]:
        if len(self._sse) > MAX_SQUEUES:
            self._sse.pop(0)
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._sse.append(q)
        return q

    def unregister_sse(self, q: asyncio.Queue) -> None:
        try:
            self._sse.remove(q)
        except ValueError:
            pass


# Set once at app startup
_state: AppState | None = None


def get_state() -> AppState:
    if _state is None:  # pragma: no cover
        raise RuntimeError("AppState not initialised; call set_state in lifespan")
    return _state


def set_state(s: AppState) -> None:
    global _state
    _state = s
