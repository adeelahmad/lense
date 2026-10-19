"use client";

import { useQuery } from "@tanstack/react-query";
import { Fragment, type ReactNode } from "react";

import { Search } from "@/app/openapi-client";
import { dateRange, phrases, prefixWords, replacePrefix, type SearchFilters } from "@/components/search/query";
import { data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";

type Hint = { key: string; node: ReactNode };

/** "interp*": prefix search isn't supported, so offer whole words that start with it ("Try interpretability"). */
function PrefixHint({
  q,
  word,
  namespace,
  onSearch,
}: {
  q: string;
  word: string;
  namespace?: string;
  onSearch: (q: string) => void;
}) {
  const client = useApiClient();
  const words = useQuery({
    queryKey: ["search-terms", word, namespace ?? null],
    queryFn: () => data(Search.suggestTerms({ client, query: { prefix: word, ns: namespace ?? null, limit: 5 } })),
    enabled: word.length >= 2,
    staleTime: 60_000,
  });
  const found = words.data ?? [];
  return (
    <>
      <code className="font-mono">{word}*</code> — prefix search isn’t supported, so this looks for the whole word “
      {word}
      ”.{" "}
      {found.length ? (
        <>
          Try{" "}
          {found.map((w, i) => (
            <Fragment key={w.word}>
              {i > 0 && (i === found.length - 1 ? " or " : ", ")}
              <button
                type="button"
                className={LINK}
                title={`Found ${plural(w.count, "time")} in ${plural(w.recordings, "asset")}`}
                onClick={() => onSearch(replacePrefix(q, word, w.word))}
              >
                {w.word}
              </button>
            </Fragment>
          ))}
          .
        </>
      ) : (
        "Type the full word you mean."
      )}
    </>
  );
}

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
  onClearFilter: (key: keyof SearchFilters | (keyof SearchFilters)[] | "all") => void;
}) {
  const hints: Hint[] = [];
  for (const w of prefixWords(q)) {
    hints.push({
      key: `p-${w}`,
      node: <PrefixHint q={q} word={w} namespace={filters.namespace} onSearch={onSearch} />,
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
          {nsRecordings != null ? ` to ${plural(nsRecordings, "asset")}` : ""}.{" "}
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
          The filter <b>speaker: {labels.speaker ?? filters.speaker}</b> keeps only their lines.{" "}
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
  if (filters.event)
    hints.push({
      key: "evt",
      node: (
        <>
          The filter <b>event: {labels.event ?? filters.event}</b> keeps only segments where it’s heard.{" "}
          <button type="button" className={LINK} onClick={() => onClearFilter("event")}>
            Any sound
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
          The filter <b>recording: {labels.recording ?? filters.recording}</b> looks in one asset.{" "}
          <button type="button" className={LINK} onClick={() => onClearFilter("recording")}>
            Search every asset
          </button>
          .
        </>
      ),
    });
  const when = dateRange(filters.from, filters.to);
  if (when)
    hints.push({
      key: "dates",
      node: (
        <>
          The filter <b>date: {when}</b> keeps to the assets recorded then.{" "}
          <button type="button" className={LINK} onClick={() => onClearFilter(["from", "to"])}>
            Any time
          </button>
          .
        </>
      ),
    });
  for (const [key, name, what, any] of [
    ["tag", "tag", "tagged so", "Any tag"],
    ["origin", "source", "that came from there", "Any source"],
    ["language", "language", "in it", "Any language"],
  ] as const)
    if (filters[key])
      hints.push({
        key,
        node: (
          <>
            The filter{" "}
            <b>
              {name}: {labels[key] ?? filters[key]}
            </b>{" "}
            keeps to the assets {what}.{" "}
            <button type="button" className={LINK} onClick={() => onClearFilter(key)}>
              {any}
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
