"use client";

import { useInfiniteQuery } from "@tanstack/react-query";
import { RefreshCw } from "lucide-react";
import { useState } from "react";

import { Admin } from "@/app/openapi-client";
import type { LogLine } from "@/app/openapi-client/types.gen";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { SearchInput, Select, Switch } from "@/components/ui/field";
import { Skeleton } from "@/components/ui/states";
import { data, useApiClient } from "@/lib/api/browser";
import { cn } from "@/lib/utils";

const PAGE = 200;
type Level = "debug" | "info" | "warning" | "error" | "critical";

const TONE: Record<string, string> = {
  debug: "text-fg-muted",
  info: "text-fg-secondary",
  warning: "text-gold-dark",
  error: "text-red-dark",
  critical: "text-red-dark font-bold",
};

function Line({ l }: { l: LogLine }) {
  const [open, setOpen] = useState(false);
  const more = Boolean(l.exc || (l.fields && Object.keys(l.fields).length));
  return (
    <li className="border-b border-border py-1.5 last:border-0">
      <button
        type="button"
        className={cn(
          "grid w-full grid-cols-[auto_auto_1fr] gap-x-2.5 text-left",
          more ? "cursor-pointer" : "cursor-default",
        )}
        onClick={() => more && setOpen(!open)}
        aria-expanded={more ? open : undefined}
      >
        <span className="text-fg-muted" title={l.ts}>
          {new Date(l.ts).toLocaleTimeString()}
        </span>
        <span className={cn("w-[62px] uppercase", TONE[l.level])}>{l.level}</span>
        <span className="min-w-0 break-words">
          <span className="text-fg-muted">
            {l.process} {l.logger}:
          </span>{" "}
          {l.msg}
          {more && !open && <span className="text-fg-muted"> …</span>}
        </span>
      </button>
      {open && (
        <pre className="mt-1 overflow-x-auto whitespace-pre-wrap pl-[calc(62px+5.5rem)] text-[11.5px] text-fg-secondary">
          {[
            l.fields
              ? Object.entries(l.fields)
                  .map(([k, v]) => `${k}=${typeof v === "string" ? v : JSON.stringify(v)}`)
                  .join(" ")
              : "",
            l.exc ?? "",
          ]
            .filter(Boolean)
            .join("\n")}
        </pre>
      )}
    </li>
  );
}

/** The latest lines from the server and every worker (their log files), newest first, with filters. */
export function LogViewer() {
  const client = useApiClient();
  const [level, setLevel] = useState<"" | Level>("");
  const [q, setQ] = useState("");
  const [process, setProcess] = useState("");
  const [follow, setFollow] = useState(true);
  const lines = useInfiniteQuery({
    queryKey: ["logs", level, q, process],
    queryFn: ({ pageParam }) =>
      data(
        Admin.readLogs({
          client,
          query: {
            limit: PAGE,
            level: level || undefined,
            q: q.trim() || undefined,
            process: process || undefined,
            before: pageParam || undefined,
          },
        }),
      ),
    initialPageParam: "",
    getNextPageParam: (last) => (last.length === PAGE ? last[last.length - 1].ts : undefined),
    refetchInterval: follow ? 5000 : false,
  });
  const rows = lines.data?.pages.flat() ?? [];
  return (
    <section className="flex flex-col gap-2.5">
      <div className="flex items-center justify-between gap-2">
        <h3 className="text-[14px] font-bold text-fg-strong">Recent lines</h3>
        <div className="flex items-center gap-2.5">
          <Switch checked={follow} onCheckedChange={setFollow} label="Follow" />
          <Button size="sm" variant="ghost" icon={<RefreshCw />} onClick={() => lines.refetch()}>
            Refresh
          </Button>
        </div>
      </div>
      <div className="grid gap-2 sm:grid-cols-[1fr_150px_150px]">
        <SearchInput
          placeholder="Search messages and errors"
          value={q}
          onChange={(e) => setQ(e.target.value)}
          aria-label="Search the log"
        />
        <Select
          size="sm"
          aria-label="Level"
          value={level}
          onChange={(e) => setLevel(e.target.value as "" | Level)}
          options={[
            { value: "", label: "All levels" },
            { value: "info", label: "Info and up" },
            { value: "warning", label: "Warnings and up" },
            { value: "error", label: "Errors only" },
          ]}
        />
        <Select
          size="sm"
          aria-label="Process"
          value={process}
          onChange={(e) => setProcess(e.target.value)}
          options={[
            { value: "", label: "Every process" },
            { value: "api", label: "Server" },
            { value: "worker", label: "Workers" },
            { value: "watcher", label: "Watcher" },
          ]}
        />
      </div>
      {lines.isError && <Banner tone="error">{lines.error.message}</Banner>}
      {lines.isPending ? (
        <Skeleton className="h-40" />
      ) : rows.length ? (
        <ul className="max-h-[480px] overflow-y-auto rounded-md border border-border bg-surface-neutral px-3 font-mono text-[12px] leading-[1.45]">
          {rows.map((l, i) => (
            <Line key={`${l.ts}-${l.pid}-${i}`} l={l} />
          ))}
        </ul>
      ) : (
        <p className="text-[12.5px] text-fg-muted">
          {q || level || process
            ? "No lines match."
            : "No lines yet. With log files off, nothing is kept to show here."}
        </p>
      )}
      {lines.hasNextPage && (
        <Button size="sm" variant="ghost" onClick={() => lines.fetchNextPage()} disabled={lines.isFetchingNextPage}>
          {lines.isFetchingNextPage ? "Loading…" : "Older lines"}
        </Button>
      )}
    </section>
  );
}
