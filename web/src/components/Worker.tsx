"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import LogView from "./LogView";
import Panel from "./Panel";
import { LogRecord } from "@/lib/store";

const LIVE = new Set(["queued", "running", "needs_approval"]);

const fmtElapsed = (seconds: number) => {
  const s = Math.max(0, Math.floor(seconds || 0));
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.floor(s / 60)}m ${s % 60}s`;
  return `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
};

interface TimelineEvent {
  seq: number;
  kind: "timeline" | "log" | "state";
  level?: string;
  category?: string;
  message?: string;
  data?: unknown;
  status?: "queued" | "running" | "needs_approval" | "done" | "failed" | "cancelled";
  started_at?: number;
  elapsed?: number;
  stage?: string;
  progress?: string;
  result_preview?: string;
  result_chars?: number;
  error?: string;
}

interface Approval {
  id: string;
  description?: string;
  target?: string;
  worker_task_id?: string;
  fromTimeline?: boolean;
}

interface Task {
  task_id?: string;
  id?: string;
  title?: string;
  status?: string;
  started_at?: number;
}

interface WorkerProps {
  task: Task | null;
  approval: Approval | null;
  onAnswer: (id: string, approved: boolean) => void;
  onClose: () => void;
}

export default function Worker({ task, approval, onAnswer, onClose }: WorkerProps) {
  const [events, setEvents] = useState<TimelineEvent[]>([]);
  const [logs, setLogs] = useState<LogRecord[]>([]);
  const [live, setLive] = useState<TimelineEvent | null>(null);
  const [notice, setNotice] = useState("");
  const [stopped, setStopped] = useState(false);
  const sourceRef = useRef<AbortController | null>(null);
  const eventsRef = useRef<TimelineEvent[]>([]);
  const logsRef = useRef<LogRecord[]>([]);
  const answered = useRef(false);

  eventsRef.current = events;
  logsRef.current = logs;

  const taskId = task?.task_id || task?.id || null;

  const pendingApproval = useMemo(() => {
    if (approval) return approval;

    const event = [...events].reverse().find((e) => e.kind === "timeline" && e.message?.includes("approval"));

    if (!event) return null;

    const eventData = event.data as Record<string, unknown> | undefined;
    return {
      id: (eventData?.approval_id as string) || `approval_${event.seq}`,
      description: event.message,
      target: "",
      fromTimeline: true,
    };
  }, [approval, events]);

  const [streamRun, setStreamRun] = useState(0);

  const startedAt = live?.started_at || task?.started_at || null;
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (!startedAt || !LIVE.has(live?.status || "")) return undefined;
    const id = setInterval(() => setTick((n) => n + 1), 1000);
    return () => clearInterval(id);
  }, [startedAt, live?.status]);

  useEffect(() => {
    if (!taskId) {
      setEvents([]);
      setLogs([]);
      setLive(null);
      return undefined;
    }

    let cancelled = false;
    const controller = new AbortController();
    sourceRef.current = controller;
    setNotice("");
    setStopped(false);

    const after = eventsRef.current.length
      ? Math.max(...eventsRef.current.map((e) => e.seq || 0))
      : 0;

    const run = async () => {
      try {
        const res = await fetch(`/api/tasks/${taskId}/stream?after=${after}&seconds=3600`, {
          signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

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
            if (cancelled) return;

            const kindLine = frame.split("\n").find((l) => l.startsWith("event: "));
            const dataLine = frame.split("\n").find((l) => l.startsWith("data: "));
            if (!dataLine) continue;

            const kind = kindLine ? kindLine.slice(7) : "task";

            try {
              const payload = JSON.parse(dataLine.slice(6));
              if (kind === "timeline") {
                setEvents((cur) =>
                  cur.some((e) => e.seq === payload.seq) ? cur : [...cur, payload],
                );
              } else if (kind === "log") {
                setLogs((cur) => {
                  if (cur.some((r) => r.id === payload.id)) return cur;
                  return [...cur, payload].slice(-2000);
                });
              } else {
                setLive(payload);

                if (!LIVE.has(payload.status || "")) {
                  setNotice("stream closed · reopen to refresh");
                }
              }
            } catch {
              /* ignore malformed frame */
            }
          }
        }
      } catch (e) {
        if (!cancelled && e instanceof Error && e.name !== "AbortError") {
          setNotice(e.message);
        }
      }
    };

    run();

    return () => {
      cancelled = true;
      controller.abort();
      sourceRef.current = null;
    };
  }, [taskId, streamRun]);

  const cancel = async () => {
    if (!taskId) return;
    setStopped(true);
    try {
      const res = await fetch(`/api/tasks/${taskId}/cancel`, { method: "POST" });
      const data = await res.json();
      setLive((cur) => (data.task ? data.task : cur));
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
      setStopped(false);
    }
  };

  const answer = (approved: boolean) => {
    const id = pendingApproval?.id;
    if (!id) return;

    answered.current = true;
    setNotice("");
    onAnswer(id, approved);
  };

  const status = live?.status || task?.status || "queued";

  useEffect(() => {
    if (answered.current && (status === "running" || status === "needs_approval")) {
      answered.current = false;
      setStreamRun((n) => n + 1);
    }
  }, [status]);

  const running = status === "running" || status === "queued";
  const elapsed =
    live?.elapsed ??
    (startedAt ? Math.floor(Date.now() / 1000) - startedAt : 0);

  if (!task) {
    return (
      <Panel title="worker" meta="idle">
        <div className="px-4 py-8 text-center text-[12px] leading-relaxed text-zinc-700">
          Long work is handed to a worker.
          <br />
          It opens here when it does.
        </div>
      </Panel>
    );
  }

  return (
    <Panel
      title="worker"
      meta={`${status} · ${fmtElapsed(elapsed + tick * 0)}`}
      bodyClassName="p-0"
      actions={
        <>
          {running && (
            <button
              type="button"
              onClick={cancel}
              disabled={stopped}
              className="border border-zinc-700 px-2 py-0.5 text-[10px] uppercase tracking-[0.15em] text-zinc-400 hover:border-rose-500/60 hover:text-rose-300 disabled:opacity-50"
            >
              {stopped ? "stopping" : "stop"}
            </button>
          )}
          <button
            type="button"
            onClick={onClose}
            className="border border-zinc-800 px-2 py-0.5 text-[10px] uppercase tracking-[0.15em] text-zinc-600 hover:border-zinc-600 hover:text-zinc-400"
          >
            close
          </button>
        </>
      }
    >
      <div className="border-b border-zinc-900 px-3 py-2">
        <div className="text-[13px] leading-snug text-zinc-200">{task.title}</div>
        {live?.stage && (
          <div className="mt-1 text-[11px] text-zinc-600">
            stage · {live.stage}
            {live.progress ? ` · ${live.progress}` : ""}
          </div>
        )}
      </div>

      <div className="h-[46vh] lg:h-[calc(100vh-320px)]">
        <LogView records={logs} emptyNote="this worker has not logged anything yet" />
      </div>

      <div className="border-t border-zinc-900 px-3 py-2">
        {pendingApproval && (
          <div className="border border-amber-500/40 bg-amber-500/5 px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.2em] text-amber-400/80">
              approval required
            </div>
            <div className="mt-1 text-[12px] leading-relaxed text-zinc-200">
              {pendingApproval.description || pendingApproval.target}
            </div>
            {pendingApproval.target && pendingApproval.target !== pendingApproval.description && (
              <pre className="mt-2 overflow-x-auto border border-zinc-800 bg-zinc-950 px-2 py-1.5 text-[11px] text-zinc-300">
                {pendingApproval.target}
              </pre>
            )}
            <div className="mt-2 flex gap-2">
              <button
                type="button"
                onClick={() => answer(true)}
                className="border border-amber-400/60 px-2 py-1 text-[11px] text-amber-200 hover:bg-amber-400/10"
              >
                allow once
              </button>
              <button
                type="button"
                onClick={() => answer(false)}
                className="border border-zinc-700 px-2 py-1 text-[11px] text-zinc-400 hover:bg-zinc-800"
              >
                decline
              </button>
            </div>
          </div>
        )}

        {status === "done" && live?.result_preview && (
          <div className="mt-3 border border-zinc-800 bg-black px-3 py-2">
            <div className="text-[10px] uppercase tracking-[0.2em] text-zinc-600">
              result
            </div>
            <pre className="mt-1 max-h-72 overflow-auto whitespace-pre-wrap break-words text-[12px] leading-relaxed text-zinc-300">
              {live.result_preview}
            </pre>
            {(live.result_chars ?? 0) > live.result_preview.length && (
              <div className="mt-1 text-[10px] text-zinc-700">
                {live.result_chars} characters total · showing first{" "}
                {live.result_preview.length}
              </div>
            )}
          </div>
        )}

        {status === "failed" && live?.error && (
          <div className="mt-3 border border-rose-500/30 bg-rose-500/5 px-3 py-2 text-[12px] text-rose-300">
            {live.error}
          </div>
        )}

        {status === "cancelled" && (
          <div className="mt-3 px-3 text-[12px] text-zinc-600">
            stopped{live?.error ? ` · ${live.error}` : ""}
          </div>
        )}

        {notice && <div className="mt-2 px-1 text-[10px] text-zinc-700">{notice}</div>}
      </div>
    </Panel>
  );
}