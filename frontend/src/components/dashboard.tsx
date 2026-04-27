"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Binoculars, Loader2, Radio, Send } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { ScrollArea } from "@/components/ui/scroll-area";
import { Textarea } from "@/components/ui/textarea";
import {
  getApiBase,
  type ChatMessage,
  fetchChatHistory,
  postChatStream,
  SKYVIEW_SESSION_KEY,
} from "@/lib/api";
import { cn } from "@/lib/utils";

export function Dashboard() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [connected, setConnected] = useState(false);
  const [mcpLine, setMcpLine] = useState<string | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  /** Same as state but updated synchronously when the stream sends meta (avoids new session on fast follow-up). */
  const sessionIdRef = useRef<string | null>(null);
  const endRef = useRef<HTMLDivElement>(null);
  const api = getApiBase();

  useEffect(() => {
    try {
      const s = localStorage.getItem(SKYVIEW_SESSION_KEY);
      if (!s) return;
      sessionIdRef.current = s;
      setSessionId(s);
      void fetchChatHistory(s)
        .then((msgs) => {
          if (msgs.length) setMessages(msgs);
        })
        .catch(() => {
          /* offline or store disabled */
        });
    } catch {
      /* private mode */
    }
  }, []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, loading]);

  const loadMeta = useCallback(async () => {
    try {
      const s = await fetch(`${api}/api/status`).then((x) => x.json());
      if (s && typeof s === "object") {
        setMcpLine(
          s.mcp_healthy ? "Chain tools ready" : s.error || "MCP starting…",
        );
      }
    } catch {
      setMcpLine("API offline");
    }
  }, [api]);

  useEffect(() => {
    void loadMeta();
  }, [loadMeta]);

  useEffect(() => {
    const es = new EventSource(`${api}/api/events`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    return () => es.close();
  }, [api]);

  const send = async () => {
    const t = input.trim();
    if (!t || loading) return;
    setInput("");
    setErr(null);
    setLoading(true);
    // Prior turns only; current line is `t` in the request body. Compute before we append the user bubble.
    const history = messages.map((m) => ({ role: m.role, content: m.content }));
    let sid: string | null = sessionIdRef.current ?? sessionId;
    if (typeof window !== "undefined" && !sid) {
      try {
        sid = localStorage.getItem(SKYVIEW_SESSION_KEY);
      } catch {
        /* private mode */
      }
    }
    setMessages((m) => [...m, { role: "user", content: t }]);
    try {
      // Add an assistant placeholder and stream into it.
      let assistantText = "";
      const assistantIndex = messages.length + 1; // user was appended optimistically
      setMessages((m) => [...m, { role: "assistant", content: "" }]);

      await postChatStream(t, history, sid, (evt) => {
        if (evt.type === "meta") {
          if (evt.session_id) {
            sessionIdRef.current = evt.session_id;
            setSessionId(evt.session_id);
            try {
              localStorage.setItem(SKYVIEW_SESSION_KEY, evt.session_id);
            } catch {
              /* private mode */
            }
          }
          return;
        }
        if (evt.type === "chunk") {
          assistantText += evt.text;
          setMessages((prev) => {
            const next = [...prev];
            if (next[assistantIndex]) {
              next[assistantIndex] = { role: "assistant", content: assistantText };
            }
            return next;
          });
          return;
        }
        if (evt.type === "error") {
          setErr(evt.message);
          setMessages((prev) => {
            const next = [...prev];
            if (next[assistantIndex]) {
              next[assistantIndex] = { role: "assistant", content: evt.message };
            }
            return next;
          });
          return;
        }
      });
    } catch (e) {
      const m = e instanceof Error ? e.message : "Request failed";
      setErr(m);
      setMessages((prev) => [
        ...prev,
        { role: "assistant", content: `Error: ${m}` },
      ]);
    } finally {
      setLoading(false);
      void loadMeta();
    }
  };

  return (
    <div className="min-h-screen skyview-mesh text-foreground">
      <div className="border-b border-white/5 bg-card/20 backdrop-blur-sm">
        <div className="mx-auto flex max-w-3xl items-center justify-between gap-4 px-5 py-4 sm:py-5">
          <div className="flex items-center gap-3">
            <div
              className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary/15 text-primary"
              aria-hidden
            >
              <Binoculars className="size-4 stroke-[1.75]" />
            </div>
            <div>
              <h1 className="text-lg font-semibold tracking-tight sm:text-xl">Skyview</h1>
              <p className="text-[0.7rem] uppercase tracking-[0.2em] text-muted-foreground sm:text-xs sm:tracking-[0.18em]">
                read-only solana
              </p>
            </div>
          </div>
          <div className="flex flex-col items-end gap-0.5 text-right">
            <div
              className={cn(
                "inline-flex items-center gap-1.5 rounded-full border border-white/5 px-2.5 py-0.5 text-xs font-medium",
                connected
                  ? "text-emerald-400/90"
                  : "text-amber-400/90",
              )}
            >
              <Radio className={cn("size-3", connected && "animate-pulse")} />
              {connected ? "Live" : "Reconnecting…"}
            </div>
            {mcpLine && (
              <span className="max-w-[11rem] truncate text-[0.7rem] text-muted-foreground">
                {mcpLine}
              </span>
            )}
          </div>
        </div>
      </div>

      <main className="mx-auto max-w-3xl px-4 pb-10 pt-6 sm:px-5">
        <Card className="overflow-hidden border border-white/8 bg-card/40 shadow-2xl shadow-black/20 backdrop-blur-md">
          <CardHeader className="space-y-1 border-b border-border/50 bg-card/30 pb-4">
            <CardTitle className="text-base font-medium">Ask the chain</CardTitle>
            <CardDescription className="text-sm leading-relaxed">
              Balances, token amounts, account info, transaction details, and network status. Nothing
              is sent on-chain.
            </CardDescription>
          </CardHeader>
          <CardContent className="p-0">
            <ScrollArea className="h-[min(58vh,26rem)] w-full min-w-0 max-w-full sm:h-[min(60vh,28rem)]">
              <div className="w-full min-w-0 max-w-full space-y-3 overflow-x-hidden px-4 py-4 sm:px-5">
                {messages.length === 0 && (
                  <div className="rounded-xl border border-dashed border-border/60 bg-muted/20 p-4 sm:p-5">
                    <p className="text-sm text-muted-foreground">
                      <span className="font-medium text-foreground/90">Try: </span>
                      default wallet SOL balance, a mint address, recent signatures for a wallet, or
                      paste a transaction signature to decode.
                    </p>
                  </div>
                )}
                {messages.map((m, i) => (
                  <div
                    key={i}
                    className={cn(
                      "flex w-full min-w-0 max-w-full flex-col gap-1",
                      m.role === "user" ? "items-end" : "items-start",
                    )}
                  >
                    <span
                      className={cn(
                        "px-0.5 text-[0.65rem] font-medium uppercase tracking-wider",
                        m.role === "user"
                          ? "text-primary/80"
                          : "text-muted-foreground",
                      )}
                    >
                      {m.role === "user" ? "You" : "Skyview"}
                    </span>
                    <div
                      className={cn(
                        "min-w-0 max-w-full rounded-2xl px-3.5 py-2.5 text-sm leading-relaxed",
                        m.role === "user"
                          ? "w-[min(100%,_28rem)] self-end bg-primary/18 text-foreground"
                          : "w-full max-w-[min(100%,_36rem)] border border-border/50 bg-card/80 text-foreground/95 shadow-sm",
                      )}
                    >
                      {m.role === "assistant" ? (
                        <div
                          className={cn(
                            "prose prose-invert max-w-full min-w-0 [overflow-wrap:anywhere]",
                            "prose-p:my-2 prose-ul:my-2 prose-ol:my-2 prose-li:my-0",
                            "prose-p:min-w-0 prose-li:min-w-0",
                            "prose-strong:font-semibold prose-strong:text-foreground",
                            "prose-strong:min-w-0 prose-strong:max-w-full prose-strong:break-words [word-break:break-all]",
                            "prose-p:break-words prose-p:[word-break:break-all]",
                            "prose-a:text-primary/90 prose-a:break-all",
                            "prose-code:max-w-full prose-code:break-all prose-code:whitespace-pre-wrap",
                            "prose-pre:max-w-full prose-pre:overflow-x-auto prose-pre:break-all",
                            "prose-li:break-words prose-li:[word-break:break-all]",
                            "prose-code:rounded prose-code:bg-muted/30 prose-code:px-1 prose-code:py-0.5",
                            "prose-pre:bg-muted/20",
                          )}
                        >
                          <ReactMarkdown
                            remarkPlugins={[remarkGfm]}
                            components={{
                              a: ({ children, ...props }) => (
                                <a
                                  {...props}
                                  className="min-w-0 break-all underline underline-offset-4"
                                  target="_blank"
                                  rel="noreferrer"
                                >
                                  {children}
                                </a>
                              ),
                              code: ({ children, ...props }) => (
                                <code {...props} className="min-w-0 max-w-full break-all">
                                  {children}
                                </code>
                              ),
                              p: ({ children, ...props }) => (
                                <p {...props} className="min-w-0 max-w-full break-words [word-break:break-all]">
                                  {children}
                                </p>
                              ),
                              strong: ({ children, ...props }) => (
                                <strong
                                  {...props}
                                  className="min-w-0 max-w-full break-words font-semibold [word-break:break-all]"
                                >
                                  {children}
                                </strong>
                              ),
                            }}
                          >
                            {m.content}
                          </ReactMarkdown>
                        </div>
                      ) : (
                        <p className="min-w-0 max-w-full whitespace-pre-wrap break-words [overflow-wrap:anywhere] [word-break:break-all]">
                          {m.content}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
                {loading && (
                  <div className="flex items-center gap-2.5 pl-0.5 text-sm text-muted-foreground">
                    <Loader2 className="size-4 shrink-0 animate-spin text-primary/80" />
                    <span>Thinking…</span>
                  </div>
                )}
                <div ref={endRef} className="h-0.5" />
              </div>
            </ScrollArea>
            {err && (
              <p className="border-t border-border/40 px-4 py-2 text-xs text-destructive sm:px-5">
                {err}
              </p>
            )}
            <div className="border-t border-border/50 bg-muted/10 p-3 sm:p-4">
              <div className="flex flex-col gap-2.5 sm:flex-row sm:items-end">
                <Textarea
                  placeholder="e.g. What is my default wallet’s SOL balance on this network?"
                  value={input}
                  onChange={(e) => setInput(e.target.value)}
                  rows={2}
                  className="min-h-0 flex-1 resize-none"
                  onKeyDown={(e) => {
                    if (e.key === "Enter" && !e.shiftKey) {
                      e.preventDefault();
                      void send();
                    }
                  }}
                />
                <Button
                  type="button"
                  className="h-10 w-full shrink-0 gap-2 sm:h-[4.5rem] sm:w-12 sm:px-0"
                  onClick={() => void send()}
                  disabled={loading}
                >
                  {loading ? <Loader2 className="size-4 animate-spin" /> : <Send className="size-4" />}
                </Button>
              </div>
            </div>
          </CardContent>
        </Card>
      </main>
    </div>
  );
}
