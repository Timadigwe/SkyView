export function getApiBase(): string {
  return process.env.NEXT_PUBLIC_API_URL?.replace(/\/$/, "") ?? "http://127.0.0.1:8000";
}

export type WalletStatus = {
  wallet_address: string;
  sol_balance: number;
  usdc_balance: number;
  sol_price_usd: number;
  sol_usd: number;
  usdc_usd: number;
  total_usd: number;
  sol_share: number;
  drift_ratio: number;
  last_poll: string;
  mcp_healthy: boolean;
  error: string | null;
};

export type RebalanceRow = {
  at: string;
  side: "sol_to_usdc" | "usdc_to_sol" | "none";
  detail: string;
  success: boolean;
  tool_name: string | null;
  tool_output: string | null;
};

export type Thought = {
  id: string;
  ts: string;
  message: string;
};

export const SKYVIEW_SESSION_KEY = "skyview_session_id";

export type ChatMessage = { role: "user" | "assistant"; content: string };

export type ChatResponse = {
  ok: boolean;
  stage: "input" | "agent" | "output" | "error" | "mcp" | "config";
  answer: string | null;
  input_allowed: boolean | null;
  input_reason: string | null;
  output_flags: string[];
  detail: string | null;
  session_id?: string | null;
};

export type ChatStreamEvent =
  | { type: "meta"; session_id: string | null }
  | { type: "chunk"; text: string }
  | { type: "done" }
  | { type: "error"; message: string };

export async function fetchChatHistory(
  sessionId: string,
): Promise<ChatMessage[]> {
  const r = await fetch(
    `${getApiBase()}/api/chat/history/${encodeURIComponent(sessionId)}`,
  );
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || r.statusText);
  }
  const j = (await r.json()) as {
    ok: boolean;
    messages: { role: string; content: string }[];
  };
  if (!j.ok || !j.messages?.length) return [];
  return j.messages
    .filter(
      (m) =>
        (m.role === "user" || m.role === "assistant") &&
        typeof m.content === "string",
    )
    .map((m) => ({
      role: m.role as "user" | "assistant",
      content: m.content,
    }));
}

export async function postChat(
  message: string,
  history: { role: string; content: string }[],
  sessionId: string | null,
): Promise<ChatResponse> {
  const r = await fetch(`${getApiBase()}/api/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      history,
      session_id: sessionId || undefined,
    }),
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || r.statusText);
  }
  return (await r.json()) as ChatResponse;
}

export async function postChatStream(
  message: string,
  history: { role: string; content: string }[],
  sessionId: string | null,
  onEvent: (e: ChatStreamEvent) => void,
): Promise<void> {
  const r = await fetch(`${getApiBase()}/api/chat/stream`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      history,
      session_id: sessionId || undefined,
    }),
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || r.statusText);
  }
  if (!r.body) return;

  const reader = r.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";

  // Minimal SSE parsing: looks for lines like `data: {...}\n\n`
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    while (true) {
      const idx = buf.indexOf("\n\n");
      if (idx === -1) break;
      const raw = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      const lines = raw.split("\n");
      for (const line of lines) {
        const trimmed = line.trim();
        if (!trimmed.startsWith("data:")) continue;
        const payload = trimmed.slice(5).trim();
        if (!payload) continue;
        try {
          const evt = JSON.parse(payload) as ChatStreamEvent;
          onEvent(evt);
        } catch {
          // ignore malformed chunks
        }
      }
    }
  }
}
