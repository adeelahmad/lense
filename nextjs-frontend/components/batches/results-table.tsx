"use client";

import { Download } from "lucide-react";
import Link from "next/link";
import { useSession } from "next-auth/react";
import { useMemo, useState } from "react";

import type { BatchResults } from "@/app/openapi-client/types.gen";
import { recordingHref } from "@/components/search/links";
import { Button } from "@/components/ui/button";
import { SearchInput } from "@/components/ui/field";
import { Pagination, SortTh, Table, Td, THead, Tr } from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import { count, shortDate } from "@/lib/format";

const PAGE = 25;

function cellText(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

function label(col: string): string {
  const s = col.replace(/_/g, " ");
  return s[0]?.toUpperCase() + s.slice(1);
}

/** Download the results as a file, with the session's token (a plain link can't send it). */
export function useExport(id: number) {
  const { data: session } = useSession();
  const toast = useToast();
  return async (fmt: "csv" | "md") => {
    try {
      const r = await fetch(`/api/v1/batches/${id}/results.${fmt}`, {
        headers: session?.accessToken ? { Authorization: `Bearer ${session.accessToken}` } : {},
      });
      if (!r.ok) throw new Error(`The server answered ${r.status}`);
      const blob = await r.blob();
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `batch-${id}.${fmt}`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
    } catch (e) {
      toast({
        title: "Couldn’t download the results",
        body: e instanceof Error ? e.message : undefined,
        tone: "red",
      });
    }
  };
}

/** BA3 (right): the run's outputs as one table, one row per item; filter, sort, page, and export as CSV or Markdown. */
export function ResultsTable({ id, results, recordings }: { id: number; results: BatchResults; recordings: number }) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ key: string; dir: "asc" | "desc" }>({
    key: "date",
    dir: "desc",
  });
  const [offset, setOffset] = useState(0);
  const exp = useExport(id);
  const cols = results.columns.filter((c) => c !== "recording" && c !== "title" && c !== "date");
  const rows = useMemo(() => {
    const t = q.trim().toLowerCase();
    const f = t
      ? results.rows.filter((r) => Object.values(r).some((v) => cellText(v).toLowerCase().includes(t)))
      : results.rows;
    return [...f].sort((a, b) => {
      const x = cellText(a[sort.key]).toLowerCase();
      const y = cellText(b[sort.key]).toLowerCase();
      const c = x < y ? -1 : x > y ? 1 : 0;
      return sort.dir === "asc" ? c : -c;
    });
  }, [results.rows, q, sort]);
  const th = (key: string) => ({
    active: sort.key === key,
    dir: sort.dir,
    onSort: () =>
      setSort((s) => ({
        key,
        dir: s.key === key && s.dir === "desc" ? "asc" : "desc",
      })),
  });
  const page = rows.slice(offset, offset + PAGE);
  return (
    <section aria-labelledby="results" className="flex flex-col gap-3 rounded-lg border border-border p-5">
      <div className="flex flex-wrap items-center gap-2">
        <h2 id="results" className="flex-1 text-[17px] font-bold text-fg">
          Results · {results.key ?? "outputs"} from {count(recordings)} {recordings === 1 ? "recording" : "recordings"}
        </h2>
        <Button
          size="sm"
          variant="secondary"
          icon={<Download />}
          onClick={() => exp("csv")}
          disabled={!results.rows.length}
        >
          CSV
        </Button>
        <Button
          size="sm"
          variant="secondary"
          icon={<Download />}
          onClick={() => exp("md")}
          disabled={!results.rows.length}
        >
          Markdown
        </Button>
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <SearchInput
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOffset(0);
          }}
          placeholder="Filter results"
          aria-label="Filter results"
          className="w-60"
        />
        <span className="ml-auto text-[12px] text-fg-muted">
          {count(rows.length)} {rows.length === 1 ? "item" : "items"}
          {rows.length > PAGE ? ` · ${count(page.length)} shown` : ""}
        </span>
      </div>
      {results.rows.length === 0 ? (
        <p className="m-0 text-[13.5px] text-fg-secondary">No results yet. They appear as each recording finishes.</p>
      ) : (
        <div className="overflow-hidden rounded-md border border-border">
          <Table aria-label="Results">
            <THead className="border-t-0">
              <tr>
                {cols.map((c) => (
                  <SortTh key={c} {...th(c)}>
                    {label(c)}
                  </SortTh>
                ))}
                <SortTh {...th("title")}>Recording</SortTh>
                <SortTh {...th("date")}>Date</SortTh>
              </tr>
            </THead>
            <tbody>
              {page.map((r, i) => (
                <Tr key={i}>
                  {cols.map((c, j) => (
                    <Td key={c} className={j === 0 ? "min-w-[200px] text-fg" : "text-fg-strong"}>
                      {cellText(r[c])}
                    </Td>
                  ))}
                  <Td className="max-w-[220px]">
                    <Link
                      href={recordingHref(Number(r.recording))}
                      className="block truncate text-fg-secondary hover:text-fg-accent hover:underline"
                    >
                      {cellText(r.title) || `Recording ${cellText(r.recording)}`}
                    </Link>
                  </Td>
                  <Td className="tabular whitespace-nowrap text-fg-secondary">
                    {r.date ? shortDate(String(r.date)) : ""}
                  </Td>
                </Tr>
              ))}
            </tbody>
          </Table>
          {rows.length > PAGE && (
            <Pagination
              offset={offset}
              limit={PAGE}
              total={rows.length}
              onChange={setOffset}
              className="border-t border-border"
            />
          )}
        </div>
      )}
    </section>
  );
}
