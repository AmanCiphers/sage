"use client";

import { ReactNode } from "react";

interface PanelProps {
  title: string;
  meta?: string;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  bodyClassName?: string;
}

export default function Panel({
  title,
  meta,
  actions,
  children,
  className,
  bodyClassName,
}: PanelProps) {
  return (
    <section className={`flex flex-col border border-zinc-800 bg-zinc-950 ${className}`}>
      <header className="flex items-center justify-between gap-2 border-b border-zinc-800 px-3 py-2">
        <span className="text-[10px] uppercase tracking-[0.25em] text-zinc-500">
          {title}
        </span>
        <span className="flex items-center gap-2">
          {meta && <span className="text-[10px] text-zinc-700">{meta}</span>}
          {actions}
        </span>
      </header>
      <div className={`flex-1 overflow-y-auto ${bodyClassName}`}>{children}</div>
    </section>
  );
}