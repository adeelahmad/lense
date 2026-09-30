"use client";

import { useState } from "react";

import type { MonthBucket } from "@/components/reports/model";
import { talkTime } from "@/components/reports/model";
import { Tooltip } from "@/components/ui/tooltip";
import { count } from "@/lib/format";
import { cn } from "@/lib/utils";

type Series = {
  key: "recordings" | "ms";
  title: string;
  height: number;
  format: (v: number) => string;
  unit: (v: number) => string;
};

const SERIES: Series[] = [
  {
    key: "recordings",
    title: "Recordings",
    height: 84,
    format: (v) => count(v),
    unit: (v) => `${count(v)} ${v === 1 ? "recording" : "recordings"}`,
  },
  {
    key: "ms",
    title: "Hours of audio",
    height: 60,
    format: (v) => (v ? talkTime(v) : "0"),
    unit: (v) => (v ? talkTime(v) : "none"),
  },
];

/**
 * Recordings and hours per month (RP1). Two measures with different scales are two small charts on one month axis,
 * never one chart with two scales. Columns are capped at 24px with 4px rounded tops; the largest value is labelled,
 * the rest are in the tooltip and the table view.
 */
export function MonthBars({ months }: { months: MonthBucket[] }) {
  const [table, setTable] = useState(false);
  const multiYear = new Set(months.map((m) => m.year)).size > 1;
  const name = (m: MonthBucket) => `${m.label} ${m.year}`;
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h3 className="flex-1 text-[13px] font-bold leading-none text-fg">Recordings and hours per month</h3>
        <button
          type="button"
          onClick={() => setTable((t) => !t)}
          aria-pressed={table}
          className="text-[12px] font-semibold text-fg-accent hover:underline print:hidden"
        >
          {table ? "Show chart" : "Show table"}
        </button>
      </div>
      {table ? (
        <table className="w-full text-[13px]">
          <thead>
            <tr className="border-b border-border text-left text-[12px] text-fg-secondary">
              <th scope="col" className="py-1.5 font-semibold">
                Month
              </th>
              <th scope="col" className="py-1.5 text-right font-semibold">
                Recordings
              </th>
              <th scope="col" className="py-1.5 text-right font-semibold">
                Audio
              </th>
            </tr>
          </thead>
          <tbody>
            {months.map((m) => (
              <tr key={m.key} className="border-b border-border">
                <th scope="row" className="py-1.5 text-left font-medium text-fg">
                  {name(m)}
                </th>
                <td className="tabular py-1.5 text-right text-fg-secondary">{count(m.recordings)}</td>
                <td className="tabular py-1.5 text-right text-fg-secondary">{m.ms ? talkTime(m.ms) : "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      ) : (
        <div className="flex flex-col gap-2.5">
          {SERIES.map((s) => {
            const values = months.map((m) => (s.key === "recordings" ? m.recordings : m.ms));
            const max = Math.max(0, ...values);
            const top = values.indexOf(max);
            return (
              <div key={s.key} role="group" aria-label={`${s.title} per month`} className="flex flex-col gap-1">
                <span className="text-[11.5px] font-medium text-fg-secondary">{s.title}</span>
                <div className="flex items-end gap-1 border-b border-border" style={{ height: s.height }}>
                  {months.map((m, i) => {
                    const v = values[i];
                    const h = max ? Math.max(v ? 2 : 0, Math.round((v / max) * (s.height - 16))) : 0;
                    return (
                      <div key={m.key} className="flex h-full min-w-0 flex-1 flex-col items-center justify-end">
                        {i === top && max > 0 && (
                          <span className="tabular mb-0.5 text-[11px] font-semibold leading-none text-fg-secondary">
                            {s.format(v)}
                          </span>
                        )}
                        <Tooltip
                          content={
                            <span>
                              <b className="font-bold">{s.unit(v)}</b> · {name(m)}
                            </span>
                          }
                        >
                          <span
                            tabIndex={0}
                            role="img"
                            aria-label={`${name(m)}: ${s.unit(v)}`}
                            className="group/bar flex w-full max-w-[40px] cursor-default justify-center outline-offset-2"
                            style={{
                              height: Math.max(h, 8),
                              alignItems: "flex-end",
                            }}
                          >
                            <span
                              className={cn(
                                "block w-full max-w-[24px] rounded-t-[4px] bg-blue transition-opacity group-hover/bar:opacity-80",
                                !v && "bg-transparent",
                              )}
                              style={{ height: h }}
                            />
                          </span>
                        </Tooltip>
                      </div>
                    );
                  })}
                </div>
              </div>
            );
          })}
          <div className="flex gap-1" aria-hidden>
            {months.map((m, i) => (
              <span key={m.key} className="min-w-0 flex-1 truncate text-center text-[11.5px] font-medium text-fg-muted">
                {m.label}
                {multiYear && (i === 0 || m.label === "Jan") ? ` ${String(m.year).slice(2)}` : ""}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

/** Top speakers by talk time: one series, so one colour; names and values in text colours. */
export function SpeakerBars({ rows }: { rows: { name: string; ms: number }[] }) {
  const max = Math.max(1, ...rows.map((r) => r.ms));
  return (
    <ul className="flex flex-col gap-2" aria-label="Top speakers by talk time">
      {rows.map((r) => (
        <li
          key={r.name}
          className="grid grid-cols-[minmax(0,120px)_minmax(0,1fr)_56px] items-center gap-2.5 text-[13px] font-medium leading-none"
        >
          <span className="truncate text-fg">{r.name}</span>
          <span className="h-2 overflow-hidden rounded-pill bg-surface-neutral">
            <span
              className="block h-full rounded-pill bg-blue"
              style={{ width: `${Math.max(2, (r.ms / max) * 100)}%` }}
            />
          </span>
          <span className="tabular text-right text-fg-secondary">{talkTime(r.ms)}</span>
        </li>
      ))}
    </ul>
  );
}
