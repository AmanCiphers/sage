"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import EmployeePanel, { EmployeeRow, EmployeeLogLine } from "./EmployeePanel";
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
  worker?: string;
  name?: string;
  taskId?: string;
  stage?: string;
  tool?: string;
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
  const [employees, setEmployees] = useState<EmployeeRow[]>([]);
  const [activityByWorker, setActivityByWorker] = useState<Record<string, EmployeeLogLine[]>>({});
  const [now, setNow] = useState(0);
  const noticesPolledRef = useRef(false);

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
        console.log("[ws] connected");
        setApiUp(true);
      };

      ws.onmessage = (event) => {
        try {
          const msg = JSON.parse(event.data);
          if (msg.type === "event") {
            console.log("[ws] event:", msg.event_type, msg.worker, msg.summary);
          }
          handleWsMessage(msg);
        } catch (e) {
          console.error("[ws] parse error:", e);
        }
      };

      ws.onclose = () => {
        console.log("[ws] disconnected, reconnecting...");
        setApiUp(false);
        setTimeout(connect, 2000);
      };

      ws.onerror = (err) => {
        console.error("[ws] error:", err);
      };
    };

    connect();
    return () => {
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, []);

  const appendEmployeeActivity = useCallback((ev: SysEvent) => {
    const worker = ev.worker;
    if (!worker) return;

    const kind: EmployeeLogLine["kind"] | null =
      ev.eventType === "employee_assigned"
        ? "assigned"
        : ev.eventType === "employee_finished"
          ? "finish"
          : ev.eventType === "task_failed"
            ? "fail"
            : ev.eventType === "employee_activity"
              ? ev.tool
                ? "tool"
                : "stage"
              : null;

    if (!kind) return;

    const line: EmployeeLogLine = {
      id: ev.id,
      at: ev.at,
      kind,
      text: ev.summary,
      taskId: ev.taskId,
    };

    setActivityByWorker((cur) => {
      const existing = cur[worker] ?? [];
      if (existing.some((l) => l.id === line.id)) return cur;
      return { ...cur, [worker]: [...existing, line].slice(-300) };
    });
  }, []);

  const handleWsMessage = useCallback((msg: any) => {
    if (msg.type === "event") {
      // Raw system event: keep it out of chat, route to panels by source.
      const data = msg.data || {};
      const payload = data.payload || {};
      const ev: SysEvent = {
        id: data.event_id || `${Date.now()}-${Math.random().toString(36).slice(2)}`,
        at: Date.now(),
        source: msg.source || data.source || "system",
        eventType: msg.event_type || data.event_type || "",
        summary: msg.summary || (msg.event_type || "").replace(/_/g, " "),
        correlationId: data.correlation_id,
        worker: msg.worker ?? payload.worker,
        name: msg.name ?? payload.name,
        taskId: msg.task_id ?? payload.task_id,
        stage: msg.stage ?? payload.stage,
        tool: msg.tool ?? payload.tool,
      };
      appendEmployeeActivity(ev);

      // Also surface employee completion/failure in main chat
      if (ev.eventType === "employee_finished" || ev.eventType === "task_failed") {
        const name = ev.name || ev.worker || "An employee";
        const taskId = ev.taskId || "";
        if (ev.eventType === "task_failed") {
          const error = payload.error || "unknown error";
          push("sys", `[SAGE] ${name} failed ${taskId}: ${error}`);
        } else {
          const summary = payload.summary || "finished the assigned work";
          push("sys", `[SAGE] ${name} finished ${taskId} — ${summary}`);
        }
      }
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
  }, [push, append, seal, appendEmployeeActivity]);

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

  // Employee roster polling for the employees panel.
  useEffect(() => {
    let alive = true;
    const load = async () => {
      try {
        const res = await fetch("/api/workers", { cache: "no-store" });
        if (!res.ok) {
          console.warn("[employees] roster fetch failed:", res.status);
          return;
        }
        const data = await res.json();
        if (alive && Array.isArray(data.workers)) {
          console.log("[employees] roster:", data.workers.map((w: any) => w.name));
          setEmployees(data.workers);
        }
      } catch (e) {
        console.warn("[employees] roster fetch error:", e);
      }
    };
    load();
    const id = setInterval(load, 3000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, []);

  // Poll completion notices when idle (not busy)
  useEffect(() => {
    if (busy) return;
    let alive = true;
    const pollNotices = async () => {
      try {
        const res = await fetch("/api/notices", { cache: "no-store" });
        if (alive && res.ok) {
          const data = await res.json();
          if (data.notices && data.notices.length > 0) {
            for (const notice of data.notices) {
              push("sys", `[SAGE] ${notice}`);
            }
          }
        }
      } catch (e) {
        console.warn("[notices] poll error:", e);
      }
    };
    pollNotices();
    const id = setInterval(pollNotices, 3000);
    return () => {
      alive = false;
      clearInterval(id);
    };
  }, [busy, push]);

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
    <main className="scanlines vignette relative mx-auto flex min-h-screen max-w-[1500px] flex-col gap-3 p-4 bg-zinc-950">
      <header className="flex items-center justify-between border border-zinc-800 bg-zinc-950/80 backdrop-blur-sm px-4 py-3">
        <div className="flex items-center gap-3">
          <span className="grid h-7 w-7 place-items-center bg-zinc-100 text-[13px] font-bold text-black rounded">
            S
          </span>
          <span className="text-base font-bold tracking-[0.2em] text-zinc-100">SAGE</span>
          <span className="hidden text-sm text-zinc-500 sm:inline">:: PERSONAL INTELLIGENCE</span>
        </div>
        <div className="flex items-center gap-4 text-[12px] text-zinc-400">
          <span className="hidden md:inline font-mono">{shortModel(info.model)}</span>
          <span className="hidden md:inline">up {fmtElapsed(now - startedAt.current)}</span>
          <span className="font-mono tabular-nums">{fmtClock(now)}</span>
          <span className={apiUp ? "text-emerald-400" : "text-rose-400"}>
            {apiUp ? "● API OK" : "● API DOWN"}
          </span>
        </div>
      </header>

      <div className="grid min-h-0 flex-1 grid-cols-1 lg:grid-cols-[1fr_320px] gap-3">
        {/* Main column: chat + employees */}
        <div className="flex min-h-0 flex-col overflow-hidden">
          <div className="border border-zinc-800 bg-zinc-950 flex flex-col min-h-0 h-[60vh] ">
            <div
              ref={logRef}
              className="flex-1 overflow-y-auto px-5 py-4 text-[14px] leading-relaxed "
            >
              {showWelcome && (
                <div className="mb-8 border border-zinc-800 bg-zinc-900/50 px-6 py-8 text-center">
                  <span className="mx-auto mb-4 grid h-12 w-12 place-items-center bg-zinc-100 text-xl font-bold text-black rounded">
                    S
                  </span>
                  <h2 className="text-lg font-medium tracking-[0.1em] text-zinc-100">SAGE</h2>
                  <p className="mt-3 text-[13px] text-zinc-500 max-w-md mx-auto">
                    Personal intelligence. Ask anything, or describe a goal.
                  </p>
                </div>
              )}

              <div className="tape space-y-3">
                {lines.map((line) => (
                  <div key={line.id} className="flex gap-3 min-w-0">
                    {stamps && (
                      <span className="w-[78px] shrink-0 select-none text-[11px] leading-[1.8] text-zinc-500 font-mono tabular-nums">
                        {fmtClock(line.at)}
                      </span>
                    )}
                    <span
                      className={`w-11 shrink-0 select-none text-right text-[11px] leading-[1.8] font-medium ${
                        TONE[line.kind].tag
                      }`}
                    >
                      {TAG[line.kind]}
                    </span>
                    <span className={`min-w-0 flex-1 break-words overflow-wrap-anywhere ${TONE[line.kind].text}`}>
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
                  <div className="mt-2 flex gap-3 text-[14px] text-zinc-500">
                    <span className="w-11 shrink-0 text-right text-[12px] text-zinc-500">sage</span>
                    <span className="inline-block h-4 w-2.5 animate-blink bg-zinc-400 rounded" />
                  </div>
                )
              )}
            </div>

            {approval && (
              <div className="border-t border-amber-500/40 bg-amber-500/10 px-5 py-4">
                <div className="text-[11px] tracking-[0.15em] text-amber-300 uppercase">
                  APPROVAL REQUIRED
                </div>
                <div className="mt-2 text-[14px] leading-relaxed text-zinc-100">
                  {approval.description || approval.target}
                </div>
                {approval.target && approval.target !== approval.description && (
                  <pre className="mt-3 overflow-x-auto border border-zinc-800 bg-zinc-900 px-3 py-2 text-[12px] text-zinc-300 rounded">
                    {approval.target}
                  </pre>
                )}
                <div className="mt-3 flex gap-2">
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => handleApprove(true)}
                    className="border border-amber-400/60 px-4 py-1.5 text-[13px] font-medium text-amber-200 hover:bg-amber-400/10 disabled:opacity-50 transition-colors rounded"
                  >
                    Allow
                  </button>
                  <button
                    type="button"
                    disabled={busy}
                    onClick={() => handleApprove(false)}
                    className="border border-zinc-700 px-4 py-1.5 text-[13px] font-medium text-zinc-300 hover:bg-zinc-800 disabled:opacity-50 transition-colors rounded"
                  >
                    Decline
                  </button>
                </div>
              </div>
            )}

            <div className="composer flex items-start gap-3 border-t border-zinc-800 px-5 py-4">
              <span className="pt-2 text-zinc-500 font-mono text-[13px]">{">>"}</span>
              <textarea
                ref={inputRef}
                value={input}
                onChange={(e) => setInput(e.target.value)}
                onKeyDown={onKeyDown}
                rows={1}
                disabled={busy}
                spellCheck={false}
                placeholder={busy ? "sage is working..." : "talk to sage  ·  enter to send  ·  shift+enter for newline"}
                className="max-h-48 w-full resize-none bg-zinc-900/50 py-2.5 px-3 text-base leading-relaxed text-zinc-100 caret-zinc-100 outline-none placeholder:text-zinc-600 disabled:opacity-50 rounded border border-zinc-800 focus:border-zinc-600 focus:ring-1 focus:ring-zinc-600"
              />
              <button
                type="button"
                onClick={handleSendMessage}
                disabled={busy || !input.trim()}
                className="mt-1 shrink-0 border border-zinc-700 px-4 py-1.5 text-[12px] uppercase tracking-[0.1em] font-medium text-zinc-100 transition-colors hover:border-zinc-400 hover:bg-zinc-800/50 disabled:border-zinc-900 disabled:text-zinc-700 disabled:hover:bg-transparent rounded"
              >
                {busy ? "..." : "SEND"}
              </button>
            </div>
          </div>

          {/* Employees panel below chat */}
          <div className="mt-4 min-h-0">
            <EmployeePanel employees={employees} activityByWorker={activityByWorker} />
          </div>
        </div>

        {/* Right sidebar: session stats */}
        <aside className="hidden lg:flex flex-col gap-3">
          <div className="border border-zinc-800 bg-zinc-950">
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
        </aside>
      </div>

      <footer className="flex items-center justify-between border border-zinc-800 bg-zinc-950/80 backdrop-blur-sm px-4 py-2 text-[11px] text-zinc-400">
        <span className="font-mono">
          {shortModel(info.model)} · {info.transport} · {info.store}
        </span>
        <span>1 channel · {apiUp ? "linked" : "unlinked"}</span>
      </footer>
    </main>
  );
}