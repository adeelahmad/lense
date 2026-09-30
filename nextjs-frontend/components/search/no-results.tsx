"use client";

import type { ReactNode } from "react";

import { phrases, prefixWords, type SearchFilters } from "@/components/search/query";
import { plural } from "@/lib/format";

type Hint = { key: string; node: ReactNode };

const LINK = "font-medium text-fg-accent underline underline-offset-2 hover:text-blue-dark";

/**
 * No moments match: say what may be in the way — prefix search, exact phrases, and each filter, with how many
 * moments the words find without it.
 */
export function NoResults({
  q,
  filters,
  labels,
  baseTotal,
  nsRecordings,
  onSearch,
  onClearFilter,
}: {
  q: string;
  filters: SearchFilters;
  labels: Partial<Record<keyof SearchFilters, string>>;
  /** Moments the words find with no filters (null while unknown). */
  baseTotal: number | null;
  /** Recordings in the filtered namespace. */
  nsRecordings?: number;
  onSearch: (q: string) => void;
  onClearFilter: (key: keyof SearchFilters | "all") => void;
}) {
  const hints: Hint[] = [];
  for (const w of prefixWords(q)) {
    hints.push({
      key: `p-${w}`,
      node: (
        <>
          <code className="font-mono">{w}*</code> — prefix search isn’t supported, so this looks for the whole word “{w}
          ”. Type the full word you mean.
        </>
      ),
    });
  }
  for (const p of phrases(q)) {
    hints.push({
      key: `q-${p}`,
      node: (
        <>
          <code className="font-mono">&quot;{p}&quot;</code> must appear exactly as written.{" "}
          <button type="button" className={LINK} onClick={() => onSearch(q.replace(`"${p}"`, p))}>
            Search the words anywhere
          </button>
          .
        </>
      ),
    });
  }
  const found = baseTotal != null && baseTotal > 0 ? ` finds ${plural(baseTotal, "moment")}` : "";
  if (filters.namespace)
    hints.push({
      key: "ns",
      node: (
        <>
          The filter <b>namespace: {filters.namespace}</b> limits this
          {nsRecordings != null ? ` to ${plural(nsRecordings, "recording")}` : ""}.{" "}
          <button type="button" className={LINK} onClick={() => onClearFilter("namespace")}>
            Search all namespaces
          </button>
          {found}.
        </>
      ),
    });
  if (filters.speaker != null)
    hints.push({
      key: "spk",
      node: (
        <>
          The filter <b>speaker: {labels.speaker ?? filters.speaker}</b> keeps only what they said.{" "}
          <button type="button" className={LINK} onClick={() => onClearFilter("speaker")}>
            Search everyone
          </button>
          {filters.namespace ? "" : found}.
        </>
      ),
    });
  if (filters.emotion)
    hints.push({
      key: "emo",
      node: (
        <>
          The filter <b>emotion: {filters.emotion}</b> keeps only segments tagged {filters.emotion}.{" "}
          <button type="button" className={LINK} onClick={() => onClearFilter("emotion")}>
            Any emotion
          </button>
          .
        </>
      ),
    });
  if (filters.recording != null)
    hints.push({
      key: "rec",
      node: (
        <>
          The filter <b>recording: {labels.recording ?? filters.recording}</b> looks in one recording.{" "}
          <button type="button" className={LINK} onClick={() => onClearFilter("recording")}>
            Search every recording
          </button>
          .
        </>
      ),
    });
  const words = q.split(/\s+/).filter((w) => w && w.toUpperCase() !== "OR");
  if (!hints.length || (baseTotal === 0 && words.length > 1 && !q.includes(" OR ")))
    hints.push({
      key: "or",
      node: (
        <>
          Every word has to appear in the same segment.{" "}
          {words.length > 1 ? (
            <button type="button" className={LINK} onClick={() => onSearch(words.join(" OR "))}>
              Match any of the words
            </button>
          ) : (
            "Try another word for it"
          )}
          , or try fewer words.
        </>
      ),
    });
  return (
    <div role="status" className="flex max-w-[560px] flex-col gap-3 py-16">
      <h2 className="text-[20px] font-bold leading-tight text-fg">No moments match</h2>
      <p className="m-0 text-[14px] leading-normal text-fg-secondary">
        Every word has to appear, and here’s what may be getting in the way:
      </p>
      <ul className="m-0 flex list-none flex-col gap-2 p-0 text-[14px] leading-normal text-fg">
        {hints.map((h) => (
          <li key={h.key} className="flex gap-2">
            <span aria-hidden className="text-fg">
              ◆
            </span>
            <span>{h.node}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}
