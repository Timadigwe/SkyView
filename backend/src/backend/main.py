"""FastAPI app: MCP lifecycle, chat API, REST + SSE."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from sse_starlette.sse import EventSourceResponse

from pydantic import BaseModel, Field

from .chat_service import run_chat_with_mcp
from .memory_store import append_turn, load_messages, save_messages, to_llm_history


def _prior_turns_for_guard(
    history: list[dict[str, str]], *, max_len: int = 12_000
) -> str:
    """Text from prior turns for output guard (address/sig reuse, tone)."""
    if not history:
        return ""
    parts: list[str] = []
    n = 0
    for m in history:
        r = m.get("role", "")
        c = (m.get("content", "") or "").strip()
        if not c:
            continue
        line = f"{r}: {c}"
        if n + len(line) + 1 > max_len:
            break
        parts.append(line)
        n += len(line) + 1
    return "\n".join(parts)
from .guardrails import (
    heuristic_block_output,
    run_input_guardrail,
    run_output_guardrail,
)
from .llm import openrouter_client
from .paths import default_mcp_minimal_entry
from .settings import Settings, get_settings, refresh_settings
from .state import AppState, get_state, set_state

log = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    message: str
    history: list[dict[str, str]] = Field(default_factory=list)
    session_id: str | None = None


class ChatResponseBody(BaseModel):
    ok: bool
    stage: Literal["input", "agent", "output", "error", "mcp", "config"]
    answer: str | None = None
    input_allowed: bool | None = None
    input_reason: str | None = None
    output_flags: list[str] = Field(default_factory=list)
    detail: str | None = None
    session_id: str | None = None


async def mcp_lifecycle(app: FastAPI) -> None:
    """Holds stdio MCP session for chat tool calls until shutdown."""
    settings = get_settings()
    entry = (settings.mcp_entry or "").strip() or str(default_mcp_minimal_entry())
    if not Path(entry).is_file():
        log.error(
            "MCP server script not found: %s — run: "
            "cd solana-mcp-minimal && npm install && npm run build",
            entry,
        )
        try:
            s = get_state()
            s.status.error = f"MCP not built: {entry}"
            await s.push_thought(
                "MCP: run `cd solana-mcp-minimal && npm install && npm run build`"
            )
        except Exception:  # noqa: BLE001
            pass
        return
    mcp_env = {**os.environ, "SOLANA_RPC_URL": settings.solana_rpc_url}
    params = StdioServerParameters(
        command=settings.mcp_node_command,
        args=[entry],
        env=mcp_env,
    )
    st = get_state()
    try:
        await st.push_thought("Connecting to solana-mcp-minimal (local Node build)…")
    except Exception:  # noqa: BLE001
        pass
    try:
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = (await session.list_tools()).tools
                st = get_state()
                st.mcp_tool_names = [t.name for t in tools]
                app.state.mcp = session
                app.state.mcp_tools = list(tools)
                try:
                    await st.push_thought(
                        f"MCP ready. Tools: {', '.join(st.mcp_tool_names[:20])}…"
                    )
                except Exception:  # noqa: BLE001
                    pass
                stx = get_state().status
                stx.mcp_healthy = True
                stx.error = None
                await get_state().update_status(stx)
                try:
                    while True:
                        await asyncio.sleep(24 * 3600)
                except asyncio.CancelledError:
                    raise
    except asyncio.CancelledError:
        log.info("MCP lifecycle cancelled (shutdown).")
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("MCP / solana-mcp-minimal failed to start: %s", e)
        try:
            s = get_state()
            s.status.error = f"MCP: {e!s}"
            s.status.mcp_healthy = False
        except Exception:
            pass
        try:
            await get_state().push_thought(
                f"MCP error (Node, solana-mcp-minimal build, keys, RPC). {e!s}"
            )
        except Exception:  # noqa: BLE001
            pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    load_dotenv()
    refresh_settings()
    set_state(AppState())
    app.state.mcp = None
    app.state.mcp_tools = []
    mcp_task = asyncio.create_task(mcp_lifecycle(app), name="mcp_lifecycle")
    app.state.mcp_task = mcp_task
    try:
        # Yield immediately so /api/health and the dashboard load while npx/mcp start.
        yield
    finally:
        mcp_task.cancel()
        try:
            await mcp_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Skyview API", lifespan=lifespan)
_cors = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=_cors.cors_origin_regex,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict[str, str]:
    s = get_settings()
    return {
        "status": "ok",
        "conversation_store": "s3"
        if (s.use_s3 and (s.s3_bucket or "").strip())
        else ("file" if s.persist_conversations else "off"),
    }


@app.get("/api/chat/history/{session_id}")
def get_session_history(session_id: str) -> dict[str, Any]:
    """Restore transcript for a client after refresh (if persistence is enabled)."""
    settings = get_settings()
    if not settings.persist_conversations or not (session_id or "").strip():
        return {"ok": True, "messages": [], "session_id": session_id or ""}
    sid = session_id.strip()
    try:
        use_s3 = bool(settings.use_s3 and (settings.s3_bucket or "").strip())
        messages = load_messages(
            sid,
            use_s3=use_s3,
            s3_bucket=(settings.s3_bucket or "").strip(),
            memory_dir=settings.memory_dir,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:  # noqa: BLE001
        log.exception("history get failed: %s", e)
        return {
            "ok": False,
            "messages": [],
            "session_id": sid,
            "detail": str(e),
        }
    return {"ok": True, "messages": messages, "session_id": sid}


def _resolve_llm_history(
    settings: Settings, body: ChatRequest, session_id: str
) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """(history for model, raw stored list for append)."""
    use_s3 = bool(settings.use_s3 and (settings.s3_bucket or "").strip())
    bucket = (settings.s3_bucket or "").strip()
    stored: list[dict[str, Any]] = load_messages(
        session_id,
        use_s3=use_s3,
        s3_bucket=bucket if use_s3 else bucket,
        memory_dir=settings.memory_dir,
    )
    if stored:
        return (
            to_llm_history(stored, max_messages=settings.chat_memory_max_messages),
            stored,
        )
    return (list(body.history or []), stored)


@app.post("/api/chat", response_model=ChatResponseBody)
async def chat(request: Request, body: ChatRequest) -> ChatResponseBody:
    """Plain-English Q&A over read-only Solana (MCP tools), with input/output guardrails."""
    settings = get_settings()
    persist = bool(settings.persist_conversations)
    session_id: str | None = (body.session_id or "").strip() or None
    if persist:
        session_id = session_id or str(uuid.uuid4())
    if not (settings.openrouter_api_key or "").strip():
        return ChatResponseBody(
            ok=False,
            stage="config",
            answer=None,
            detail="We’re not quite set up yet—add OPENROUTER_API_KEY to the backend environment and try again.",
            session_id=session_id,
        )
    mcp: ClientSession | None = getattr(request.app.state, "mcp", None)
    mcp_tools: list[Any] = list(getattr(request.app.state, "mcp_tools", []) or [])
    if mcp is None or not mcp_tools:
        return ChatResponseBody(
            ok=False,
            stage="mcp",
            answer=None,
            detail="Hang tight—chain tools are still starting. Build solana-mcp-minimal if needed, wait a few seconds, and try again.",
            session_id=session_id,
        )

    stored: list[dict[str, Any]] = []
    llm_history: list[dict[str, str]] = list(body.history or [])
    if persist and session_id:
        try:
            llm_history, stored = _resolve_llm_history(settings, body, session_id)
        except Exception as e:  # noqa: BLE001
            log.exception("memory load failed: %s", e)
            return ChatResponseBody(
                ok=False,
                stage="error",
                answer=None,
                detail=f"Could not load saved conversation: {e!s}"[:2000],
                session_id=session_id,
            )

    guard_model = (settings.guardrail_model or "").strip() or settings.openrouter_model
    client = openrouter_client(settings)

    inp = await run_input_guardrail(client, guard_model, body.message)
    if not inp.allowed:
        return ChatResponseBody(
            ok=True,
            stage="input",
            answer=inp.reason,
            input_allowed=False,
            input_reason=inp.reason,
            session_id=session_id,
        )

    try:
        st = get_state()
        await st.push_thought(f"Chat (allowed, {inp.category}): {body.message[:200]}")
    except Exception:  # noqa: BLE001
        pass

    try:
        draft, tool_trace = await run_chat_with_mcp(
            mcp,
            client,
            settings,
            mcp_tools,
            body.message,
            history=llm_history,
        )
    except Exception as e:  # noqa: BLE001
        log.exception("chat agent failed")
        return ChatResponseBody(
            ok=False,
            stage="error",
            answer=None,
            detail=f"Oops—that didn’t work on our side. Here’s what we know: {e!s}"[:2000],
            session_id=session_id,
        )

    out = await run_output_guardrail(
        client,
        guard_model,
        body.message,
        draft,
        tool_trace,
        prior_turns_text=_prior_turns_for_guard(llm_history),
    )
    final = out.final_text
    if not out.ok:
        heur = None
    else:
        heur = heuristic_block_output(final)
    if heur:
        out = out.model_copy(
            update={"ok": False, "final_text": heur, "flags": (out.flags + ["heuristic"])}
        )
        final = heur

    if not out.ok:
        return ChatResponseBody(
            ok=True,
            stage="output",
            answer=final,
            input_allowed=True,
            input_reason=inp.reason,
            output_flags=out.flags,
            session_id=session_id,
        )

    if persist and session_id:
        use_s3 = bool(settings.use_s3 and (settings.s3_bucket or "").strip())
        bucket = (settings.s3_bucket or "").strip()
        try:
            to_save = append_turn(stored, body.message, final)
            save_messages(
                session_id,
                to_save,
                use_s3=use_s3,
                s3_bucket=bucket,
                memory_dir=settings.memory_dir,
            )
        except Exception:  # noqa: BLE001
            log.exception("memory save failed (user still got the reply)")

    try:
        await get_state().push_thought(f"Chat reply: {final[:500]}")
    except Exception:  # noqa: BLE001
        pass
    return ChatResponseBody(
        ok=True,
        stage="output",
        answer=final,
        input_allowed=True,
        input_reason=inp.reason,
        output_flags=out.flags,
        session_id=session_id,
    )


@app.post("/api/chat/stream")
async def chat_stream(request: Request, body: ChatRequest) -> StreamingResponse:
    """
    SSE stream for chat responses.
    We compute the full answer server-side (same as /api/chat), then stream it in small chunks so
    the UI can render progressively (more user-friendly than a long single response).
    """
    settings = get_settings()
    persist = bool(settings.persist_conversations)
    session_id: str | None = (body.session_id or "").strip() or None
    if persist:
        session_id = session_id or str(uuid.uuid4())

    async def _emit(obj: dict[str, Any]) -> bytes:
        return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode("utf-8")

    async def gen():
        # meta first (so UI can store session id)
        yield await _emit({"type": "meta", "session_id": session_id})

        # Reuse existing handler logic by calling it directly.
        res = await chat(request, body.model_copy(update={"session_id": session_id}))
        if not res.ok or not res.answer:
            msg = (res.detail or res.answer or "Request failed")[:2000]
            yield await _emit({"type": "error", "message": msg})
            yield await _emit({"type": "done"})
            return

        text = res.answer
        # Stream in chunks (cheap/robust; avoids tool-stream complexity).
        chunk_size = 80
        for i in range(0, len(text), chunk_size):
            yield await _emit({"type": "chunk", "text": text[i : i + chunk_size]})
            await asyncio.sleep(0)  # allow flush
        yield await _emit({"type": "done"})

    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/status")
async def status() -> dict[str, Any]:
    s = get_state()
    return s.status.model_dump()


@app.get("/api/rebalances")
async def rebalances(
    limit: int = Query(50, ge=1, le=200),
) -> list[dict[str, Any]]:
    s = get_state()
    return [e.model_dump() for e in s.rebalances[-limit:]]


@app.get("/api/thoughts")
async def thoughts(
    limit: int = Query(100, ge=1, le=300),
) -> list[dict[str, Any]]:
    s = get_state()
    return s.thoughts[-limit:]


@app.get("/api/events")
async def events() -> EventSourceResponse:
    s = get_state()
    q = s.register_sse()

    async def gen() -> Any:
        try:
            yield {"data": json.dumps({"type": "ping"})}
            while True:
                item = await q.get()
                yield {"data": json.dumps(item)}
        finally:
            s.unregister_sse(q)

    return EventSourceResponse(gen())
