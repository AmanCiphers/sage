"use client";

import { useEffect, useMemo, useState } from "react";
import Panel from "./Panel";
import { fmtClock } from "@/lib/format";

export interface EmployeeRow {
  worker: string;
  id: string;
  name: string;
  status: string;
  running_tasks: string[];
}

interface ActivityTask {
  task_id: string;
  objective: string;
  status: string;
  result?: string | null;
  error?: string | null;
  verification?: string | null;
  verification_detail?: string | null;
  created_at?: string;
  started_at?: string | null;
  completed_at?: string | null;
}

export interface EmployeeLogLine {
  id: string;
  at: number;
  kind: "assigned" | "stage" | "tool" | "finish" | "fail";
  text: string;
  taskId?: string;
}

const LIVE = new Set(["active", "running", "queued"]);

const STATUS_TONE: Record<string, string> = {
  running: "text-emerald-400",
  standby: "text-zinc-500",
  active: "text-emerald-400",
};

const ACTIVITY_TONE: Record<EmployeeLogLine["kind"], string> = {
  assigned: "text-zinc-100",
  stage: "text-zinc-400",
  tool: "text-zinc-300",
  finish: "text-emerald-300",
  fail: "text-rose-300",
};

const ACTIVITY_TAG: Record<EmployeeLogLine["kind"], string> = {
  assigned: "GOAL",
  stage: "···",
  tool: "TOOL",
  finish: "DONE",
  fail: "FAIL",
};

const TASK_TONE: Record<string, string> = {
  queued: "text-zinc-500",
  running: "text-emerald-400",
  completed: "text-zinc-400",
  failed: "text-rose-400",
  unknown: "text-amber-400",
};

interface EmployeePanelProps {
  employees: EmployeeRow[];
  /** Live activity lines keyed by employee worker key. */
  activityByWorker: Record<string, EmployeeLogLine[]>;
}

export default function EmployeePanel({ employees, activityByWorker }: EmployeePanelProps) {
  const [selected, setSelected] = useState<string | null>(null);
  const [history, setHistory] = useState<ActivityTask[]>([]);
  const [loadingHistory, setLoadingHistory] = useState(false);

  const activeKey = selected ?? employees[0]?.worker ?? null;
  const employee = useMemo(
    () => employees.find((e) => e.worker === activeKey) ?? null,
    [employees, activeKey],
  );

  const liveLines = activeKey ? activityByWorker[activeKey] ?? [] : [];

  // Backfill the selected employee's task history whenever the selection changes.
  useEffect(() => {
    if (!activeKey) {
      setHistory([]);
      return undefined;
    }
    let alive = true;
    setLoadingHistory(true);
    fetch(`/api/workers/${activeKey}/activity?limit=15`, { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : null))
      .then((data) => {
        if (alive && data && Array.isArray(data.activity)) setHistory(data.activity);
      })
      .catch(() => {})
      .finally(() => {
        if (alive) setLoadingHistory(false);
      });
    return () => {
      alive = false;
    };
  }, [activeKey]);

  return (
    <Panel
      title="employees"
      meta={`${employees.filter((e) => LIVE.has(e.status)).length} active`}
      bodyClassName="p-0"
    >
      {/* Roster sidebar-style list */}
      <div className="divide-y divide-zinc-800 border-b border-zinc-800">
        {employees.length === 0 ? (
          <div className="px-5 py-6 text-[13px] text-zinc-500">no employees</div>
        ) : (
          employees.map((e) => {
            const isActive = e.worker === activeKey;
            const working = LIVE.has(e.status);
            return (
              <button
                key={e.worker}
                type="button"
                onClick={() => setSelected(e.worker)}
                className={`flex w-full items-center justify-between gap-3 px-5 py-4 text-left transition-colors ${
                  isActive ? "bg-zinc-900/60" : "hover:bg-zinc-900/30"
                }`}
              >
                <span className="flex min-w-0 items-center gap-3">
                  <span
                    className={`h-2.5 w-2.5 shrink-0 rounded-full ${
                      working ? "animate-pulse bg-emerald-400" : "bg-zinc-600"
                    }`}
                  />
                  <span className="truncate text-[14px] font-medium text-zinc-100">{e.name}</span>
                  <span className="text-[11px] text-zinc-500 font-mono">{e.id}</span>
                </span>
                <span
                  className={`shrink-0 text-[11px] uppercase tracking-[0.1em] font-medium ${
                    STATUS_TONE[e.status] || "text-zinc-500"
                  }`}
                >
                  {e.status}
                </span>
              </button>
            );
          })
        )}
      </div>

      {/* Selected employee detail */}
      <div className="flex min-h-0 flex-col">
        <div className="border-b border-zinc-800 px-5 py-4">
          <div className="flex items-center justify-between gap-3">
            <span className="text-[15px] font-medium text-zinc-100">{employee?.name ?? "—"}</span>
            {employee && (
              <span className="text-[11px] uppercase tracking-[0.1em] text-zinc-500">
                {employee.running_tasks.length} running
              </span>
            )}
          </div>
        </div>

        {/* Live activity feed */}
        <div className="px-5 py-4 space-y-2 border-b border-zinc-800">
          {liveLines.length === 0 ? (
            <div className="py-6 text-center text-[13px] text-zinc-500">
              {employee ? "no live activity" : "select an employee"}
            </div>
          ) : (
            <div className="space-y-2">
              {liveLines.map((line) => (
                <div key={line.id} className="flex gap-3 text-[13px] leading-relaxed">
                  <span className="w-[6.5rem] shrink-0 text-right text-[12px] tabular-nums text-zinc-400 font-mono">
                    {fmtClock(line.at)}
                  </span>
                  <span className="w-11 shrink-0 text-[11px] uppercase tracking-[0.1em] text-zinc-500 font-medium">
                    {ACTIVITY_TAG[line.kind]}
                  </span>
                  <span className={`min-w-0 flex-1 break-words ${ACTIVITY_TONE[line.kind]}`}>
                    {line.text}
                  </span>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Task history */}
        <div className="border-t border-zinc-800 px-5 py-4">
          <div className="mb-3 text-[12px] uppercase tracking-[0.1em] text-zinc-500 font-medium">
            recent assignments
          </div>
          {loadingHistory && history.length === 0 ? (
            <div className="py-3 text-[13px] text-zinc-500">loading…</div>
          ) : history.length === 0 ? (
            <div className="py-3 text-[13px] text-zinc-500">nothing assigned yet</div>
          ) : (
            <div className="space-y-3">
              {history.map((t) => (
                <div key={t.task_id} className="border border-zinc-800 rounded-lg px-4 py-3 bg-zinc-900/50">
                  <div className="flex items-start justify-between gap-3">
                    <span className="min-w-0 flex-1 break-words text-[13px] leading-relaxed text-zinc-200">
                      {t.objective}
                    </span>
                    <span
                      className={`shrink-0 text-[11px] uppercase tracking-[0.1em] font-medium ${
                        TASK_TONE[t.status] || "text-zinc-500"
                      }`}
                    >
                      {t.status}
                    </span>
                  </div>
                  <div className="mt-2 flex items-center gap-2 text-[11px] text-zinc-400">
                    <span className="font-mono">{t.task_id}</span>
                    {t.verification && <span>· {t.verification}</span>}
                  </div>
                  {t.status === "completed" && t.result && (
                    <pre className="mt-3 max-h-48 overflow-y-auto whitespace-pre-wrap break-words border-l-2 border-zinc-700 bg-zinc-950/50 px-3 py-2 text-[12px] leading-relaxed text-zinc-300 rounded-r">
                      {t.result.slice(0, 1500)}
                    </pre>
                  )}
                  {t.status === "failed" && t.error && (
                    <div className="mt-2 break-words text-[12px] text-rose-300">{t.error}</div>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </Panel>
  );
}
