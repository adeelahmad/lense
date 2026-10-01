"use client";

import { Check } from "lucide-react";
import Link from "next/link";

import type { Collection } from "@/app/openapi-client/types.gen";
import type { FacetValue, Facets } from "@/components/search/facets";
import type { SearchFilters } from "@/components/search/query";
import { speakerTone } from "@/components/speakers/format";
import { EMOJI } from "@/components/ui/badge";
import { Skeleton } from "@/components/ui/states";
import { count } from "@/lib/format";
import { cn } from "@/lib/utils";

type Group = { key: keyof SearchFilters; title: string; values: FacetValue[] };

function FacetRow({
  v,
  on,
  color,
  icon,
  onToggle,
}: {
  v: FacetValue;
  on: boolean;
  color?: string;
  icon?: string;
  onToggle: () => void;
}) {
  return (
    <li>
      <button
        type="button"
        role="checkbox"
        aria-checked={on}
        onClick={onToggle}
        className={cn(
          "grid h-[30px] w-full grid-cols-[18px_minmax(0,1fr)_auto] items-center gap-2 rounded-[6px] px-1.5 text-left text-[13px] font-medium text-fg transition-colors duration-fast",
          on ? "bg-blue-surface" : "hover:bg-surface-neutral",
        )}
      >
        <span
          aria-hidden
          className={cn(
            "grid size-[18px] place-items-center rounded-xs border-2",
            on ? "border-blue bg-blue text-white" : "border-fg-secondary bg-background",
          )}
        >
          {on && <Check className="size-3.5" strokeWidth={3} />}
        </span>
        <span className="flex min-w-0 items-center gap-1.5">
          {color && <span aria-hidden className="size-2 shrink-0 rounded-full" style={{ background: color }} />}
          {icon && (
            <span aria-hidden className="text-[12px]">
              {icon}
            </span>
          )}
          <span className="truncate">{v.label}</span>
          {v.sub && <span className="truncate text-[11.5px] font-normal text-fg-muted">{v.sub}</span>}
        </span>
        <span className="tabular text-[12px] text-fg-muted">{count(v.count)}</span>
      </button>
    </li>
  );
}

/**
 * Facets for the current words: namespace, speaker, emotion and recording, with how many moments each has. Counts
 * come from the words alone, so they don't vanish when you pick one; each facet is one filter, shared with its chip.
 */
export function FacetPanel({
  facets,
  filters,
  labels,
  loading,
  partial,
  onToggle,
  saved,
  className,
}: {
  facets: Facets | null;
  filters: SearchFilters;
  /** Names for the chosen filters (a speaker's name, a recording's title). */
  labels: Partial<Record<keyof SearchFilters, string>>;
  loading?: boolean;
  /** True when more moments match than the server counts (20,000). */
  partial?: boolean;
  onToggle: (key: keyof SearchFilters, value: string | number | undefined) => void;
  /** Saved searches (null while they load). */
  saved: Collection[] | null;
  className?: string;
}) {
  const groups: Group[] = facets
    ? [
        { key: "namespace", title: "Namespace", values: facets.namespaces },
        { key: "speaker", title: "Speaker", values: facets.speakers },
        { key: "emotion", title: "Emotion", values: facets.emotions },
        { key: "recording", title: "Recording", values: facets.recordings },
      ]
    : [];
  const current = (k: keyof SearchFilters) => (filters[k] == null ? undefined : String(filters[k]));
  return (
    <div className={cn("flex flex-col gap-4", className)}>
      {loading && (
        <div className="flex flex-col gap-3 px-1.5" aria-busy="true" aria-label="Loading filters">
          {[0, 1, 2].map((i) => (
            <div key={i} className="flex flex-col gap-2">
              <Skeleton className="h-2.5 w-20" />
              <Skeleton className="w-[80%]" />
              <Skeleton className="w-[60%]" />
            </div>
          ))}
        </div>
      )}
      {groups.map((g) => {
        const sel = current(g.key);
        const values = g.values.slice(0, 8);
        // A chosen value stays visible even when the words alone don't reach it.
        if (sel && !values.some((v) => v.key === sel))
          values.unshift({
            key: sel,
            label: labels[g.key] ?? sel,
            count: 0,
            id: Number(sel) || undefined,
          });
        if (!values.length) return null;
        return (
          <section key={g.key} aria-labelledby={`facet-${g.key}`} className="flex flex-col gap-0.5">
            <h3 id={`facet-${g.key}`} className="px-1.5 pb-1.5 label-caps">
              {g.title}
            </h3>
            <ul className="m-0 flex list-none flex-col gap-0.5 p-0">
              {values.map((v) => (
                <FacetRow
                  key={v.key}
                  v={v}
                  on={sel === v.key}
                  color={g.key === "speaker" && v.id != null ? speakerTone(v.id) : undefined}
                  icon={g.key === "emotion" ? (EMOJI[v.key] ?? "·") : undefined}
                  onToggle={() =>
                    onToggle(
                      g.key,
                      sel === v.key ? undefined : g.key === "speaker" || g.key === "recording" ? Number(v.key) : v.key,
                    )
                  }
                />
              ))}
            </ul>
          </section>
        );
      })}
      {partial && (
        <p className="m-0 px-1.5 text-[11.5px] leading-snug text-fg-muted">
          So many moments match that the counts cover 20,000 of them.
        </p>
      )}
      <section aria-labelledby="facet-saved" className="flex flex-col gap-1">
        <h3 id="facet-saved" className="px-1.5 pb-1.5 label-caps">
          Saved searches
        </h3>
        {saved?.length === 0 && (
          <p className="m-0 px-1.5 text-[12.5px] leading-snug text-fg-muted">
            Save a search to keep it here. It’s kept as a collection you can also chat with or run on.
          </p>
        )}
        {(saved ?? []).map((c) => {
          const f = (c.filter ?? {}) as {
            q?: string;
            namespaces?: string[];
            speakers?: number[];
          };
          const p = new URLSearchParams();
          if (f.q) p.set("q", f.q);
          if (f.namespaces?.length === 1) p.set("ns", f.namespaces[0]);
          if (f.speakers?.length === 1) p.set("speaker", String(f.speakers[0]));
          return (
            <Link
              key={c.id}
              href={`/search?${p}`}
              className="rounded-[6px] px-1.5 py-1.5 text-[13px] font-medium leading-snug text-fg-accent hover:bg-surface-neutral"
            >
              {c.name}
              {f.q && c.name !== f.q && <span className="font-normal text-fg-muted"> · {f.q}</span>}
            </Link>
          );
        })}
        <Link href="/collections" className="px-1.5 pt-1 text-[12.5px] font-semibold text-fg-secondary hover:text-fg">
          All collections →
        </Link>
      </section>
    </div>
  );
}
