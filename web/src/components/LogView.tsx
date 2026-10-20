"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { fmtStamp } from "@/lib/format";
import { LogRecord } from "@/lib/store";

const LEVELS = ["error", "warning", "info", "debug"] as const;

const LEVEL_TONE = {
  error: "text-rose-400",
  warning: "text-amber-300/90",
  info: "text-zinc-400",
  debug: "text-zinc-600",
} as const;

const LEVEL_DOT = {
  error: "bg-rose-500/80",
  warning: "bg-amber-400/80",
  info: "bg-zinc-700",
  debug: "bg-zinc-800",
} as const;

function pretty(data: unknown) {
  try {
    return JSON.stringify(data, null, 2);
  } catch {
    return String(data);
  }
}

function searchable(record: { message?: string; category?: string; data?: unknown }) {
  if (record.message) return record.message;
  return "";
}

interface LogViewProps {
  records?: LogRecord[];
  showTask?: boolean;
  maxRows?: number;
  emptyNote?: string;
  className?: string;
}

export default function LogView({
  records = [],
  showTask = false,
  maxRows = 600,
  emptyNote = "nothing recorded yet",
  className = "",
}: LogViewProps) {
  const [query, setQuery] = useState("");
  const [level, setLevel] = useState("");
  const [category, setCategory] = useState("");
  const [follow, setFollow] = useState(true);
  const [open, setOpen] = useState(() => new Set<string | number>());
  const bodyRef = useRef<HTMLDivElement>(null);

  const categories = useMemo(() => {
    const seen = new Set<string>();
    for (const record of records) {
      if (record.category) seen.add(record.category);
    }
    return [...seen].sort();
  }, [records]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return records.filter((record) => {
      if (level && record.level !== level) return false;
      if (category && record.category !== category) return false;
      if (!needle) return true;
      return `${searchable(record)} ${record.category} ${pretty(record.data)}`
        .toLowerCase()
        .includes(needle);
    });
  }, [records, query, level, category]);

  const shown = useMemo(
    () => (filtered.length > maxRows ? filtered.slice(-maxRows) : filtered),
    [filtered, maxRows]
  );

  useEffect(() => {
    if (!follow) return;
    const el = bodyRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [shown, follow]);

  const onScroll = useCallback(() => {
    const el = bodyRef.current;
    if (!el) return;
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 24;
    if (!atBottom && follow) setFollow(false);
  }, [follow]);

  const toggle = (id: string | number) => {
    setOpen((cur) => {
      const next = new Set(cur);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  return (
    <div className={`flex min-h-0 flex-col ${className}`}>
      <div className="flex flex-wrap items-center gap-2 border-b border-zinc-900 px-3 py-1.5">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="search"
          className="min-w-0 flex-1 border border-zinc-800 bg-black px-2 py-0.5 text-[11px] text-zinc-300 placeholder:text-zinc-700 focus:border-zinc-700 focus:outline-none"
        />

        <select
          value={level}
          onChange={(e) => setLevel(e.target.value)}
          className="border border-zinc-800 bg-black px-1 py-0.5 text-[10px] uppercase tracking-[0.15em] text-zinc-500 focus:outline-none"
        >
          <option value="">all</option>
          {LEVELS.map((name) => (
            <option key={name} value={name}>
              {name}
            </option>
          ))}
        </select>

        {categories.length > 1 && (
          <select
            value={category}
            onChange={(e) => setCategory(e.target.value)}
            className="border border-zinc-800 bg-black px-1 py-0.5 text-[10px] uppercase tracking-[0.15em] text-zinc-500 focus:outline-none"
          >
            <option value="">all</option>
            {categories.map((name) => (
              <option key={name} value={name}>
                {name}
              </option>
            ))}
          </select>
        )}

        <button
          type="button"
          onClick={() => setFollow((v) => !v)}
          className={`border px-2 py-0.5 text-[10px] uppercase tracking-[0.15em] ${
            follow
              ? "border-zinc-700 text-zinc-400"
              : "border-zinc-800 text-zinc-700 hover:text-zinc-500"
          }`}
        >
          {follow ? "following" : "paused"}
        </button>

        <span className="text-[10px] text-zinc-700">
          {shown.length === filtered.length
            ? `${filtered.length}`
            : `${shown.length} of ${filtered.length}`}
        </span>
      </div>

      <div
        ref={bodyRef}
        onScroll={onScroll}
        className="min-h-0 flex-1 overflow-y-auto px-3 py-2"
      >
        {shown.length === 0 && (
          <div className="py-4 text-[11px] text-zinc-700">
            {records.length === 0 ? emptyNote : "nothing matches that filter"}
          </div>
        )}

        {shown.map((record) => {
          const key = record.id ?? record.seq ?? `${record.task_id}:${record.seq}`;
          const hasData = record.data !== null && record.data !== undefined;
          const expanded = open.has(key);

          return (
            <div key={String(key)} className="py-0.5 text-[11px] leading-relaxed">
              <div className="flex gap-2">
                <span className="w-[5.75rem] shrink-0 truncate text-right text-[10px] tabular-nums text-zinc-800">
                  {record.ts ? fmtStamp(record.ts * 1000) : ""}
                </span>

                <span
                  title={record.level}
                  className={`mt-1.5 h-1 w-1 shrink-0 rounded-full ${
                    LEVEL_DOT[record.level] || "bg-zinc-800"
                  }`}
                />

                <span className="w-16 shrink-0 truncate text-[10px] uppercase tracking-[0.1em] text-zinc-700">
                  {showTask && record.task_id
                    ? `${record.task_id}/${record.category}`
                    : record.category}
                </span>

                <span
                  className={`min-w-0 flex-1 break-words ${
                    LEVEL_TONE[record.level] || "text-zinc-400"
                  }`}
                >
                  {record.message}
                </span>

                {hasData && (
                  <button
                    type="button"
                    onClick={() => toggle(key)}
                    className="shrink-0 px-1 text-[10px] text-zinc-800 hover:text-zinc-500"
                  >
                    {expanded ? "▾" : "▸"}
                  </button>
                )}
              </div>

              {expanded && hasData && (
                <pre className="mt-1 overflow-x-auto border-l border-zinc-800 bg-black px-2 py-1.5 text-[10px] leading-relaxed text-zinc-500">
                  {pretty(record.data)}
                </pre>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}