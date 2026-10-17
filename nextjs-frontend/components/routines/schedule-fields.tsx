"use client";

import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useMemo, useState } from "react";

import { Routines } from "@/app/openapi-client";
import { Input } from "@/components/ui/field";
import { data, useApiClient } from "@/lib/api/browser";

/** When things run: the time zone fields and the schedule preview shared by the routine editor and the Schedules
 * page. */
export function browserZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";
  } catch {
    return "UTC";
  }
}

export function zones(): string[] {
  try {
    const f = (Intl as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf;
    return f ? ["UTC", ...f("timeZone")] : ["UTC"];
  } catch {
    return ["UTC"];
  }
}

/** A time zone, typed or picked from every zone this browser knows. */
export function ZoneInput({ id, value, onChange }: { id: string; value: string; onChange: (v: string) => void }) {
  const list = useId();
  const all = useMemo(zones, []);
  return (
    <>
      <Input id={id} list={list} value={value} onChange={(e) => onChange(e.target.value)} />
      <datalist id={list}>
        {all.map((z) => (
          <option key={z} value={z} />
        ))}
      </datalist>
    </>
  );
}

/** "Fri 3 Oct, 03:00" in the routine's time zone. */
function when(iso: string, tz: string): string {
  const o: Intl.DateTimeFormatOptions = {
    weekday: "short",
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
  };
  try {
    return new Date(iso).toLocaleString("en-GB", { ...o, timeZone: tz });
  } catch {
    return new Date(iso).toLocaleString("en-GB", o);
  }
}

function useDebounced<T>(value: T, ms = 400): T {
  const [v, setV] = useState(value);
  useEffect(() => {
    const t = setTimeout(() => setV(value), ms);
    return () => clearTimeout(t);
  }, [value, ms]);
  return v;
}

/** What the schedule means and its next five runs, from the server, as it's typed. */
export function SchedulePreview({
  schedule,
  timezone,
  none = "Runs only when someone presses Run now.",
}: {
  schedule: string | null;
  timezone: string;
  /** What having no schedule means here. */
  none?: string;
}) {
  const client = useApiClient();
  const s = useDebounced(schedule);
  const tz = useDebounced(timezone);
  const q = useQuery({
    queryKey: ["schedule-preview", s, tz],
    queryFn: () => data(Routines.previewSchedule({ client, query: { schedule: s!, timezone: tz || "UTC" } })),
    enabled: Boolean(s),
    retry: false,
    staleTime: 60_000,
  });
  if (!schedule) return <p className="text-[12.5px] text-fg-muted">{none}</p>;
  if (q.isLoading || s !== schedule || tz !== timezone)
    return <p className="text-[12.5px] text-fg-muted">Checking the schedule…</p>;
  if (q.error)
    return (
      <p role="alert" className="text-[12.5px] text-red-dark">
        {(q.error as Error).message}
      </p>
    );
  if (!q.data) return null;
  return (
    <div className="rounded-sm border border-border bg-surface px-3 py-2 text-[12.5px]">
      <p className="font-semibold text-fg">Runs {q.data.text}</p>
      {q.data.next.length ? (
        <ul className="mt-1 flex flex-col gap-0.5 text-fg-secondary">
          {q.data.next.map((t) => (
            <li key={t} className="tabular">
              {when(t, q.data.timezone)}
            </li>
          ))}
        </ul>
      ) : (
        <p className="mt-1 text-fg-muted">It never comes round.</p>
      )}
    </div>
  );
}
