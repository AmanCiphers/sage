"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { fmtClock, fmtElapsed, fmtMs, fmtChars, shortModel } from "@/lib/format";

interface Line {
  id: number;
  kind: "in" | "sage" | "tool" | "sys" | "say";
  text: string;
  at: number;
  sealed: boolean;
}

interface Approval {
  id?: string;
  approval_id?: string;
  description?: string;
  target?: string;
  worker_task_id?: string;
  task?: string;
}

interface InfoResponse {
  model: string;
  transport: string;
  runtime: string;
  store: string;
  endpoint: string;
}

interface SysEvent {
  id: string;
  at: number;
  source: string;
  eventType: string;
  summary: string;
  correlationId?: string;
}

interface TaskRow {
  task_id: string;
  objective: string;
  tool_name: string;
  status: string;
  error?: string | null;
  created_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
}

const TONE = {
  in: { text: "text-zinc-100", tag: "text-zinc-500" },
  sage: { text: "text-zinc-400", tag: "text-zinc-700" },
  tool: { text: "text-zinc-600", tag: "text-zinc-800" },
  sys: { text: "text-zinc-600", tag: "text-zinc-800" },
  say: { text: "text-zinc-300", tag: "text-emerald-800" },
} as const;

const TAG = { in: ">", sage: "sage", tool: "log", sys: "::", say: "say" } as const;

const SPINNER_FRAMES = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"];

function ThinkingLine() {
  const [frame, setFrame] = useState(0);
  useEffect(() => {
    const id = setInterval(() => setFrame((f) => (f + 1) % SPINNER_FRAMES.length), 80);
    return () => clearInterval(id);
  }, []);
  return (
    <div className="flex gap-2">
      <span className="w-9 shrink-0 select-none text-right text-[11px] leading-[1.9] text-zinc-800">::</span>
      <span className="min-w-0 flex-1 text-zinc-500 break-words overflow-wrap-anywhere">
        <span className="mr-2 inline-block w-3 text-emerald-500/80">{SPINNER_FRAMES[frame]}</span>
        <span className="animate-pulse">sage is thinking</span>
        <span className="ml-1 inline-block h-3.5 w-2 translate-y-0.5 animate-blink bg-zinc-500" />
      </span>
    </div>
  );
}

const MD = {
  h1: ({ children }: { children: React.ReactNode }) => (
    <h1 className="mt-3 mb-1 text-[15px] font-semibold text-zinc-100">{children}</h1>
  ),
  h2: ({ children }: { children: React.ReactNode }) => (
    <h2 className="mt-3 mb-1 text-[14px] font-semibold text-zinc-100">{children}</h2>
  ),
  h3: ({ children }: { children: React.ReactNode }) => (
    <h3 className="mt-2 mb-1 text-[13px] font-semibold text-zinc-200">{children}</h3>
  ),
  h4: ({ children }: { children: React.ReactNode }) => (
    <h4 className="mt-2 mb-1 text-[13px] font-medium text-zinc-300">{children}</h4>
  ),
  p: ({ children }: { children: React.ReactNode }) => (
    <p className="my-1 leading-relaxed">{children}</p>
  ),
  strong: ({ children }: { children: React.ReactNode }) => (
    <strong className="font-semibold text-zinc-200">{children}</strong>
  ),
  em: ({ children }: { children: React.ReactNode }) => (
    <em className="italic text-zinc-300">{children}</em>
  ),
  del: ({ children }: { children: React.ReactNode }) => (
    <del className="text-zinc-600">{children}</del>
  ),
  ul: ({ children }: { children: React.ReactNode }) => (
    <ul className="my-1 list-disc pl-5 space-y-0.5 marker:text-zinc-600">{children}</ul>
  ),
  ol: ({ children }: { children: React.ReactNode }) => (
    <ol className="my-1 list-decimal pl-5 space-y-0.5 marker:text-zinc-600">{children}</ol>
  ),
  li: ({ children }: { children: React.ReactNode }) => (
    <li className="leading-relaxed">{children}</li>
  ),
  hr: () => <hr className="my-2 border-zinc-800" />,
  a: ({ children, href }: { children?: React.ReactNode; href: string }) => (
    <a
      href={href}
      target="_blank"
      rel="noreferrer noopener"
      className="text-zinc-300 underline underline-offset-2 hover:text-zinc-100"
    >
      {children}
    </a>
  ),
  code: ({ children, className }: { children: React.ReactNode; className?: string }) =>
    className ? (
      <code className="rounded bg-zinc-900 px-1 py-0.5 text-[12px] text-zinc-300">{children}</code>
    ) : (
      <code className="rounded bg-zinc-900 px-1 py-0.5 text-[12px] text-zinc-300">{children}</code>
    ),
  pre: ({ children }: { children: React.ReactNode }) => (
    <pre className="my-2 overflow-x-auto overflow-y-auto rounded border border-zinc-800 bg-zinc-950 p-2 text-[12px] leading-relaxed text-zinc-300 break-words overflow-wrap-anywhere">
      {children}
    </pre>
  ),
  table: ({ children }: { children: React.ReactNode }) => (
    <div className="my-2 overflow-x-auto">
      <table className="w-full border-collapse text-[12px]">{children}</table>
    </div>
  ),
  thead: ({ children }: { children: React.ReactNode }) => (
    <thead className="border-b border-zinc-700">{children}</thead>
  ),
  th: ({ children }: { children: React.ReactNode }) => (
    <th className="px-2 py-1 text-left font-semibold text-zinc-300">{children}</th>
  ),
  td: ({ children }: { children: React.ReactNode }) => (
    <td className="border-t border-zinc-800 px-2 py-1 align-top text-zinc-400">{children}</td>
  ),
  blockquote: ({ children }: { children: React.ReactNode }) => (
    <blockquote className="my-1 border-l-2 border-zinc-700 pl-3 text-zinc-500">{children}</blockquote>
  ),
};

const emptyStats = () => ({
  turns: 0,
  inChars: 0,
  outChars: 0,
  lastMs: null as number | null,
  lastStatus: "--",
});

const createBootLines = () => [
  { id: 0, kind: "sys" as const, text: "sage online. personal intelligence core loaded.", at: 0, sealed: true },
];

// SSE must bypass the Next.js dev proxy, which buffers compressed streamed
// bodies until completion (breaking live deltas). Hit the backend directly.
const SSE_BASE = "http://localhost:8000";

function Chip({ active, onClick, children }: { active: boolean; onClick: () => void; children: React.ReactNode }) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={`border px-2 py-1 text-[10px] uppercase tracking-[0.15em] transition-colors ${
        active
          ? "border-zinc-600 text-zinc-200"
          : "border-zinc-900 text-zinc-600 hover:border-zinc-700 hover:text-zinc-400"
      }`}
    >
      {children}
    </button>
  );
}

function Row({ label, value, tone = "text-zinc-300" }: { label: string; value: string; tone?: string }) {
  return (
    <div className="flex items-center justify-between gap-3">
      <span className="shrink-0 text-[10px] uppercase tracking-[0.15em] text-zinc-600">{label}</span>
      <span className={`truncate text-[11px] ${tone}`}>{value}</span>
    </div>
  );
}

const STATUS_TONE: Record<string, string> = {
  queued: "text-zinc-500",
  running: "text-emerald-500/80",
  completed: "text-zinc-400",
  failed: "text-rose-500/80",
  unknown: "text-amber-500/80",
};

function WorkerPanel({ tasks, events }: { tasks: TaskRow[]; events: SysEvent[] }) {
  const workerEvents = events
    .filter((e) => e.source === "worker" || e.eventType.startsWith("task_") || e.eventType.startsWith("worker_"))
    .slice(0, 8);
  const active = tasks.filter((t) => t.status === "running" || t.status === "queued").length;

  return (
    <div className="flex min-h-0 flex-col border border-zinc-800 bg-zinc-950">
      <div className="flex items-center justify-between border-b border-zinc-800 px-4 py-2">
        <span className="text-sm font-medium text-zinc-300">workers</span>
        <span className="text-[10px] uppercase tracking-[0.15em] text-zinc-600">
          {active} active · {tasks.length} total
        </span>
      </div>

      <div className="max-h-[240px] min-h-0 overflow-y-auto">
        {tasks.length === 0 ? (
          <div className="px-4 py-4 text-[11px] text-zinc-700">no background tasks</div>
        ) : (
          <div className="divide-y divide-zinc-900">
            {tasks.slice(0, 12).map((t) => (
              <div key={t.task_id} className="px-4 py-2">
                <div className="flex items-center justify-between gap-2">
                  <span className="truncate text-[12px] text-zinc-300">{t.objective || t.tool_name}</span>
                  <span className={`shrink-0 text-[10px] uppercase tracking-[0.15em] ${STATUS_TONE[t.status] || "text-zinc-500"}`}>
                    {t.status}
                  </span>
                </div>
                <div className="mt-0.5 flex items-center justify-between gap-2 text-[10px] text-zinc-600">
                  <span className="truncate">{t.tool_name} · {t.task_id}</span>
                </div>
                {t.status === "failed" && t.error && (
                  <div className="mt-0.5 truncate text-[10px] text-rose-500/70">{t.error}</div>
                )}
              </div>
            ))}
          </div>
        )}

        {workerEvents.length > 0 && (
          <div className="border-t border-zinc-900 px-4 py-2">
            <div className="mb-1 text-[10px] uppercase tracking-[0.15em] text-zinc-700">activity</div>
            <div className="space-y-0.5">
              {workerEvents.map((e) => (
                <div key={e.id} className="flex gap-2 text-[10px] text-zinc-600">
                  <span className="shrink-0 text-zinc-800">{fmtClock(e.at)}</span>
                  <span className="truncate">{e.summary}</span>
                </div>
              ))}
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function Console() {
  const [lines, setLines] = useState<Line[]>(() => createBootLines());
  const [info, setInfo] = useState<InfoResponse>({
    model: "unknown",
    transport: "fastapi + websocket",
    runtime: "python 3.12",
    store: "sqlite",
    endpoint: "POST /api/chat/stream",
  });
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [apiUp, setApiUp] = useState(true);
  const [stamps, setStamps] = useState(false);
  const [wrap, setWrap] = useState(true);
  const [autoScroll, setAutoScroll] = useState(true);
  const [stats, setStats] = useState<{
    turns: number;
    inChars: number;
    outChars: number;
    lastMs: number | null;
    lastStatus: string;
  }>(emptyStats());
  const [approval, setApproval] = useState<Approval | null>(null);
  const [thinking, setThinking] = useState(false);
  const [sysEvents, setSysEvents] = useState<SysEvent[]>([]);
  const [tasks, setTasks] = useState<TaskRow[]>([]);
  const [now, setNow] = useState(0);

  const seq = useRef(0);
  const startedAt = useRef(0);
  const logRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const lastApprovalId = useRef<string | undefined>(undefined);

  const push = useCallback((kind: Line["kind"], text: string) => {
    setLines((cur) => [
      ...cur,
      { id: ++seq.current, kind, text, at: Date.now(), sealed: false },
    ]);
  }, []);

  const append = useCallback((kind: Line["kind"], text: string) => {
    setLines((cur) => {
      const last = cur[cur.length - 1];
      if (!last || last.kind !== kind || last.sealed) {
        return [...cur, { id: ++seq.current, kind, text, at: Date.now(), sealed: false }];
      }
      return [...cur.slice(0, -1), { ...last, text: last.text + text }];
    });
  }, []);

  const seal = useCallback((kind: Line["kind"]) => {
    setLines((cur) => {
      const last = cur[cur.length - 1];
      if (!last || last.kind !== kind) return cur;
      return [...cur.slice(0, -1), { ...last, sealed: true }];
    });
  }, []);

  const focusComposer = useCallback(() => inputRef.current?.focus(), []);

  useEffect(() => {
    const now = Date.now();
    setNow(now);
    startedAt.current = now;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, []);

  // Auto-scroll the chat feed to the bottom as new lines stream in.
  useEffect(() => {
    if (!autoScroll) return;
    const el = logRef.current;
    if (!el) return;
    el.scrollTop = el.scrollHeight;
  }, [lines, autoScroll, thinking]);

  // WebSocket connection for live events
  useEffect(() => {
    const connect = () => {
      const ws = new WebSocket("ws://localhost:8000/ws/sage");
      wsRef.current = ws;

      ws.onopen = () => {
        console.log("WebSocket connected");
        setApiUp(true);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          handleWsMessage(msg);
        } catch (e) {
          console.error("WS parse error:", e);
        }
      };

      ws.onclose = () => {
        console.log("WebSocket disconnected, reconnecting...");
        setApiUp(false);
        setTimeout(connect, 2000);
      };

      ws.onerror = (err) => {
        console.error("WebSocket error:", err);
      };
    };

    connect();
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  const handleWsMessage = useCallback((msg: any) => {
    if (msg.type === "event") {
      // Raw system event: keep it out of chat, route to panels by source.
      const ev: SysEvent = {
        id: msg.data?.event_id || `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        at: Date.now(),
        source: msg.source || msg.data?.source || "system",
        eventType: msg.event_type || msg.data?.event_type || "",
        summary: msg.summary || (msg.event_type || "").replace(/_/g, " "),
        correlationId: msg.data?.correlation_id,
      };
      setSysEvents((cur) => [ev, ...cur].slice(0, 200));
      return;
    }

    // Legacy/loop-style frames (defensive): keep working if backend sends them.
    if (msg.delta) {
      append("sage", msg.delta);
    } else if (msg.approval) {
      setApproval(msg.approval);
      push("sys", `:: approval required · ${msg.approval.description || msg.approval.target || ""}`);
    } else if (msg.done) {
      seal("sage");
    }
  }, [push, append, seal]);

  // Fetch initial info
  useEffect(() => {
    let alive = true;
    fetch("/api/info", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (alive && data) setInfo(data);
      })
      .catch(() => {});
    return () => { alive = false; };
  }, []);

  // Health check polling
  useEffect(() => {
    let alive = true;
    const probe = async () => {
      try {
        const res = await fetch("/api/health", { cache: "no-store" });
        if (alive) setApiUp(res.ok);
      } catch {
        if (alive) setApiUp(false);
      }
    };
    probe();
    const id = setInterval(probe, 5000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Worker/task state polling (REST backfill alongside live WS events)
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch("/api/tasks", { cache: "no-store" });
        if (!res.ok) return;
        const data = await res.json();
        if (alive && Array.isArray(data.tasks)) setTasks(data.tasks);
      } catch {
        /* ignore */
      }
    };
    load();
    const id = setInterval(load, 4000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  const handleSendMessage = useCallback(async () => {
    const msg = input.trim();
    if (!msg || busy) return;

    setInput("");
    setBusy(true);
    setThinking(true);
    push("in", `> ${msg}`);

    const started = performance.now();
    let outChars = 0;
    let status = "ERR";
    let sawText = false;
    let sawError = false;
    let sawDone = false;
    let sawNotice = false;

    try {
      const res = await fetch(`${SSE_BASE}/api/chat/stream`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message: msg }),
      });

      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      if (!res.body) throw new Error("streaming unsupported by this browser");

      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";

      for (;;) {
        const { value, done } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const frames = buffer.split("\n\n");
        buffer = frames.pop() ?? "";

        for (const frame of frames) {
          const line = frame.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          try {
            const payload = JSON.parse(line.slice(6));

            if (payload.approval) {
              setThinking(false);
              const apprId = payload.approval.id ?? payload.approval.approval_id;
              if (apprId && apprId === lastApprovalId.current) {
                continue;
              }
              lastApprovalId.current = apprId;
              setApproval(payload.approval);
              push("sys", `:: approval required · ${payload.approval.description || payload.approval.target || ""}`);
              continue;
            }

            if (payload.handoff) {
              seal("sage");
              const handoff = payload.handoff;
              push("sys", `:: worker ${handoff.task_id} · ${handoff.title || ""}`);
              continue;
            }

            if (payload.stage) {
              if (payload.stage === "tool" && payload.name) {
                push("tool", `[call] ${payload.name}(${payload.arguments || ""})`);
                continue;
              }
              // "thinking" (llm_start) is rendered as a live animated line below.
              if (payload.stage !== "thinking") {
                push("sys", `:: ${payload.stage}`);
              }
              continue;
            }

            if (payload.tool) {
              const mark = payload.ok === false ? "fail" : "ok";
              push("tool", `[${mark}] ${payload.tool} · ${payload.summary || ""}`);
              continue;
            }

            if (payload.notice) {
              sawNotice = true;
              push("sys", `:: ${payload.notice}`);
              continue;
            }

            if (payload.error) {
              sawError = true;
              setThinking(false);
              push("tool", `[err] ${payload.error}`);
              seal("sage");
              continue;
            }

            if (typeof payload.delta === "string" && payload.delta) {
              setThinking(false);
              append("sage", payload.delta);
              outChars += payload.delta.length;
              sawText = true;
            }

            if (payload.say) {
              push("say", payload.say as string);
            }

            if (payload.done) {
              sawDone = true;
              setThinking(false);
              seal("sage");
            }
          } catch {
            // ignore malformed frame
          }
        }
      }

      if (!sawText && !sawError) {
        throw new Error("empty response");
      } else if (!sawDone) {
        sawError = true;
        push("tool", "[err] stream ended early — reply may be incomplete");
        seal("sage");
        status = "ERR";
      } else {
        status = sawNotice ? "PARTIAL" : "OK";
      }

      setApiUp(true);
    } catch (e) {
      push("tool", `[err] ${e instanceof Error ? e.message : String(e)}`);
      seal("sage");
      setApiUp(false);
    } finally {
      const ms = performance.now() - started;
      setStats((cur) => ({
        turns: cur.turns + 1,
        inChars: cur.inChars + msg.length,
        outChars: cur.outChars + outChars,
        lastMs: ms,
        lastStatus: status,
      }));
      setBusy(false);
      setThinking(false);
      focusComposer();
    }
  }, [input, busy, push, append, seal, focusComposer]);

  const handleApprove = useCallback(
    async (approved: boolean) => {
      if (!approval) return;
      const pending = approval;
      const approvalId = pending.id ?? pending.approval_id;
      if (!approvalId) {
        push("tool", "[err] approval id missing");
        return;
      }
      setApproval(null);
      lastApprovalId.current = undefined;
      setBusy(true);
      push("in", `> ${approved ? "yes" : "no"}`);

      try {
        const res = await fetch(`/api/approvals/${approvalId}/${approved ? "approve" : "deny"}`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({}),
        });
        const data = await res.json();

        if (!res.ok || data.status === "failed") {
          push("tool", `[err] ${data.error || `HTTP ${res.status}`}`);
          return;
        }

        push("sage", data.response || (approved ? "Approved." : "Declined."));
        setStats((cur) => ({ ...cur, turns: cur.turns + 1, lastStatus: "OK" }));
        setApiUp(true);
      } catch (e) {
        push("tool", `[err] ${e instanceof Error ? e.message : String(e)}`);
        setApiUp(false);
      } finally {
        setBusy(false);
        focusComposer();
      }
    },
    [approval, push, focusComposer],
  );

  const clear = useCallback(() => {
    setLines(createBootLines());
    setStats(emptyStats());
    focusComposer();
  }, [focusComposer]);

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSendMessage();
    }
  };

  const showWelcome = lines.length <= 1;

  return (
    <main className="scanlines vignette relative mx-auto flex h-screen max-w-[1500px] flex-col gap-3 overflow-hidden p-4">
      <header className="flex items-center justify-between border border-zinc-800 bg-zinc-950 px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="grid h-6 w-6 place-items-center bg-zinc-100 text-[12px] font-bold text-black">
            S
          </span>
          <span className="text-sm font-bold tracking-[0.3em] text-zinc-100">SAGE</span>
          <span className="hidden text-xs text-zinc-600 sm:inline">:: PERSONAL INTELLIGENCE</span>
        </div>
        <div className="flex items-center gap-4 text-[11px] text-zinc-500">
          <span className="hidden md:inline">{shortModel(info.model)}</span>
          <span className="hidden md:inline">up {fmtElapsed(now - startedAt.current)}</span>
          <span>{fmtClock(now)}</span>
          <span className={apiUp ? "text-emerald-500/70" : "text-rose-500/80"}>
            {apiUp ? "API OK" : "API DOWN"}
          </span>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 gap-3 lg:grid-cols-[1fr_340px] ">
        <div className="flex min-h-0 flex-col overflow-hidden">
          <div className="border border-zinc-800 bg-zinc-950 flex-1 flex flex-col min-h-0 ">
            <div
              ref={logRef}
              className="flex-1 overflow-y-scroll px-4 py-3 text-[13px] leading-relaxed "
            >
              {showWelcome && (
                <div className="mb-6 border border-zinc-900 bg-black px-4 py-6 text-center">
                  <span className="mx-auto mb-4 grid h-11 w-11 place-items-center bg-zinc-100 text-lg font-bold text-black">
                    S
                  </span>
                  <h2 className="text-base font-medium tracking-[0.2em] text-zinc-200">SAGE</h2>
                  <p className="mt-2 text-[12px] text-zinc-600">
                    Personal intelligence. Ask anything, or describe a goal.
                  </p>
                </div>
              )}

              <div className="tape overflow-y-scroll space-y-1">
                {lines.map((line) => (
                  <div key={line.id} className="flex gap-2">
                    {stamps && (
                      <span className="w-[74px] shrink-0 select-none text-[10px] leading-[1.9] text-zinc-800">
                        {fmtClock(line.at)}
                      </span>
                    )}
                    <span
                      className={`w-9 shrink-0 select-none text-right text-[11px] leading-[1.9] ${
                        TONE[line.kind].tag
                      }`}
                    >
                      {TAG[line.kind]}
                    </span>
                    <span className={`min-w-0 flex-1 ${TONE[line.kind].text} break-words overflow-wrap-anywhere`}>
                      {line.kind === "sage" && line.sealed ? (
                        <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD as any}>
                          {line.text}
                        </ReactMarkdown>
                      ) : (
                        <span
                          className={
                            wrap ? "whitespace-pre-wrap break-words overflow-wrap-anywhere" : "whitespace-pre"
                          }
                        >
                          {line.kind === "in" ? line.text.replace(/^>\s*/, "") : line.text}
                        </span>
                      )}
                    </span>
                  </div>
                ))}
              </div>

              {thinking ? (
                <ThinkingLine />
              ) : (
                busy && (
                  <div className="mt-1 flex gap-2 text-[13px] text-zinc-600">
                    <span className="w-9 shrink-0 text-right text-[11px] text-zinc-800">sage</span>
                    <span className="inline-block h-3.5 w-2 animate-blink bg-zinc-400" />
                  </div>
                )
              )}
            </div>

            {approval && (
              <div className="border-t border-amber-500/40 bg-amber-500/5 px-4 py-3">
                <div className="text-[11px] tracking-[0.2em] text-amber-400/80">
                  APPROVAL REQUIRED
                </div>
                <div className="mt-1 text-[13px] leading-relaxed text-zinc-200">
                  {approval.description || approval.target}
                </div>
                {approval.target && approval.target !== approval.description && (
                  <pre className="mt-2 overflow-x-auto border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-[12px] text-zinc-300">
                    {approval.target}
                  </pre>
                )}
                <div className="mt-2 flex gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => handleApprove(true)}
                    className="border border-amber-400/60 px-3 py-1 text-[12px] text-amber-200 hover:bg-amber-400/10 disabled:opacity-50"
                  >
                    allow once
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => handleApprove(false)}
                    className="border border-zinc-700 px-3 py-1 text-[12px] text-zinc-400 hover:bg-zinc-800 disabled:opacity-50"
                  >
                    decline
                  </button>
                </div>
              </div>
            )}

            <div className="composer flex items-start gap-2 border-t border-zinc-800 px-4 py-3">
              <span className="pt-1.5 text-zinc-500">{">>"}</span>
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                rows={1}
                disabled={busy}
                spellCheck={false}
                placeholder={busy ? "sage is working..." : "talk to sage  ·  enter to send  ·  shift+enter for newline"}
                className="max-h-40 w-full resize-none bg-transparent py-1.5 text-sm leading-relaxed text-zinc-100 caret-zinc-100 outline-none placeholder:text-zinc-700 disabled:opacity-50"
              />
              <button
                type="button"
                onClick={handleSendMessage}
                disabled={busy || !input.trim()}
                className="mt-0.5 shrink-0 border border-zinc-700 px-3 py-1.5 text-[11px] uppercase tracking-[0.2em] text-zinc-200 transition-colors hover:border-zinc-400 disabled:border-zinc-900 disabled:text-zinc-700"
              >
                {busy ? "..." : "send"}
              </button>
            </div>
          </div>
        </div>

        <div className="flex min-h-0 flex-col gap-3 overflow-y-auto">
          <div className="flex min-h-0 flex-col border border-zinc-800 bg-zinc-950">
            <div className="border-b border-zinc-800 px-4 py-2 text-sm font-medium text-zinc-300">
              session
            </div>
            <div className="space-y-2 px-3 py-3">
              <Row label="turns" value={String(stats.turns)} />
              <Row label="elapsed" value={fmtElapsed(now - startedAt.current)} />
              <Row label="inbound" value={`${fmtChars(stats.inChars)} ch`} />
              <Row label="outbound" value={`${fmtChars(stats.outChars)} ch`} />
              <Row
                label="state"
                value={busy ? "WORKING" : "IDLE"}
                tone={busy ? "text-zinc-100" : "text-zinc-500"}
              />
              <Row
                label="last call"
                value={`${stats.lastStatus} · ${fmtMs(stats.lastMs)}`}
                tone={stats.lastStatus === "OK" ? "text-emerald-500/70" : "text-zinc-500"}
              />
              <Row label="model" value={info.model} tone="text-zinc-400" />
              <Row label="transport" value={info.transport} />
              <Row label="runtime" value={info.runtime} />
              <Row label="store" value={info.store} />
            </div>

            <div className="border-t border-zinc-800 px-3 py-2">
              <div className="flex flex-wrap gap-2">
                <Chip active={stamps} onClick={() => setStamps((v) => !v)}>
                  stamps
                </Chip>
                <Chip active={wrap} onClick={() => setWrap((v) => !v)}>
                  wrap
                </Chip>
                <Chip active={autoScroll} onClick={() => setAutoScroll((v) => !v)}>
                  autoscroll
                </Chip>
                <Chip active={false} onClick={clear}>
                  clear
                </Chip>
              </div>
            </div>
          </div>

          <WorkerPanel tasks={tasks} events={sysEvents} />
        </div>
      </div>

      <footer className="flex items-center justify-between border border-zinc-800 bg-zinc-950 px-4 py-2 text-[10px] text-zinc-700">
        <span>
          {shortModel(info.model)} · {info.transport} · {info.store}
        </span>
        <span>1 channel · {apiUp ? "linked" : "unlinked"}</span>
      </footer>
    </main>
  );
}