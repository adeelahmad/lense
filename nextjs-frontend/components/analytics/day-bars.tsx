"use client";

import { useState } from "react";

import type { DayCounts } from "@/app/openapi-client/types.gen";
import { ACTION_LABEL, ACTIONS, dayLabel, tickDays, type Action } from "@/components/analytics/model";
import { Tooltip } from "@/components/ui/tooltip";
import { count } from "@/lib/format";
import { cn } from "@/lib/utils";

const HEIGHT = 64;

/**
 * What was done per day. Five measures of different sizes are five small charts on one axis of days, never one chart
 * with several scales; one colour, since each chart is one series named by its title. The largest day is labelled,
 * the rest are in the tooltip and the table view.
 */
export function DayBars({ days, actions = ACTIONS }: { days: DayCounts[]; actions?: readonly Action[] }) {
  const [table, setTable] = useState(false);
  const ticks = tickDays(days);
  return (
    <div className="flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <h2 className="flex-1 text-[14px] font-bold leading-none text-fg">Per day</h2>
        <button
          type="button"
          onClick={() => setTable((t) => !t)}
          aria-pressed={table}
          className="text-[12px] font-semibold text-fg-accent hover:underline"
        >
          {table ? "Show charts" : "Show table"}
        </button>
      </div>
      {table ? (
        <div className="max-h-[360px] overflow-auto rounded-md border border-border">
          <table className="w-full text-[13px]">
            <thead className="sticky top-0 bg-background">
              <tr className="border-b border-border text-left text-[12px] text-fg-secondary">
                <th scope="col" className="px-3 py-1.5 font-semibold">
                  Day
                </th>
                {actions.map((a) => (
                  <th key={a} scope="col" className="px-3 py-1.5 text-right font-semibold">
                    {ACTION_LABEL[a].many}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...days].reverse().map((d) => (
                <tr key={d.day} className="border-b border-border last:border-b-0">
                  <th scope="row" className="tabular whitespace-nowrap px-3 py-1.5 text-left font-medium text-fg">
                    {dayLabel(d.day)}
                  </th>
                  {actions.map((a) => (
                    <td key={a} className="tabular px-3 py-1.5 text-right text-fg-secondary">
                      {count(d[a] ?? 0)}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {actions.map((a) => {
            const values = days.map((d) => d[a] ?? 0);
            const max = Math.max(0, ...values);
            const top = values.indexOf(max);
            return (
              <div key={a} role="group" aria-label={`${ACTION_LABEL[a].many} per day`} className="flex flex-col gap-1">
                <span className="text-[11.5px] font-medium text-fg-secondary">{ACTION_LABEL[a].many}</span>
                <div className="flex items-end gap-[2px] border-b border-border" style={{ height: HEIGHT }}>
                  {days.map((d, i) => {
                    const v = values[i];
                    const h = max ? Math.max(v ? 2 : 0, Math.round((v / max) * (HEIGHT - 16))) : 0;
                    return (
                      <div key={d.day} className="flex h-full min-w-0 flex-1 flex-col items-center justify-end">
                        {i === top && max > 0 && (
                          <span className="tabular mb-0.5 text-[11px] font-semibold leading-none text-fg-secondary">
                            {count(v)}
                          </span>
                        )}
                        <Tooltip
                          content={
                            <span>
                              <b className="font-bold">
                                {count(v)} {v === 1 ? ACTION_LABEL[a].one : ACTION_LABEL[a].many.toLowerCase()}
                              </b>{" "}
                              · {dayLabel(d.day)}
                            </span>
                          }
                        >
                          <span
                            tabIndex={0}
                            role="img"
                            aria-label={`${dayLabel(d.day)}: ${count(v)} ${v === 1 ? ACTION_LABEL[a].one : ACTION_LABEL[a].many.toLowerCase()}`}
                            className="group/bar flex w-full max-w-[28px] cursor-default justify-center outline-offset-2"
                            style={{ height: Math.max(h, 8), alignItems: "flex-end" }}
                          >
                            <span
                              className={cn(
                                "block w-full max-w-[18px] rounded-t-[4px] bg-blue transition-opacity group-hover/bar:opacity-80",
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
          <div aria-hidden className="flex gap-[2px] text-[11px] leading-none text-fg-muted">
            {days.map((d) => (
              <span key={d.day} className="relative min-w-0 flex-1">
                {ticks.has(d.day) && (
                  <span className="tabular absolute left-1/2 -translate-x-1/2 whitespace-nowrap">
                    {dayLabel(d.day)}
                  </span>
                )}
              </span>
            ))}
          </div>
          <span className="h-3" />
        </div>
      )}
    </div>
  );
}
