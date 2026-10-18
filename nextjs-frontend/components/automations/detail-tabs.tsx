"use client";

/** What an automation has done and will do: its runs (newest first, a page at a time, each opening to its log and what
 * it did), the times it runs next, the variables it keeps between runs, and what it costs. */
import { useInfiniteQuery, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { CalendarClock, ChevronDown, ChevronRight, History, Variable } from "lucide-react";
import { useState } from "react";

import { Automations } from "@/app/openapi-client";
import type { AutomationRun, AutomationVar } from "@/app/openapi-client/types.gen";
import { useActions } from "@/components/automations/data";
import { runStatus } from "@/components/automations/model";
import { ActivityPanel, BudgetPanel, Cost } from "@/components/costs/costs";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { DateTime, EmptyState, Skeleton, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { didDo, type ActionDef } from "@/components/workflows/workflow-model";
import { data, useApiClient } from "@/lib/api/browser";
import { plural } from "@/lib/format";

export const PAGE = 20;

/** What a run did, in a few words: "3 done, 1 waiting", or why it didn't run. */
export function runCounts(r: Pick<AutomationRun, "counts" | "why" | "error">): string {
  const parts = Object.entries(r.counts ?? {})
    .filter(([, n]) => n > 0)
    .map(([k, n]) => `${n} ${k}`);
  return r.error ?? (parts.length ? parts.join(", ") : (r.why ?? "Nothing to do"));
}

/** A variable's value as one line of JSON to edit, and back: text that isn't JSON is kept as a string. */
export function varText(v: unknown): string {
  return typeof v === "string" ? v : JSON.stringify(v ?? null);
}
export function varFrom(text: string): unknown {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

function RunDetail({ id, actions }: { id: number; actions?: ActionDef[] }) {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["automation-run", id],
    queryFn: () => data(Automations.getRun({ client, path: { run: id } })),
  });
  if (q.isLoading) return <Skeleton className="h-16 w-full" />;
  if (q.error) return <p className="text-[12.5px] text-red-dark">{(q.error as Error).message}</p>;
  const intents = q.data?.intents ?? [];
  const log = q.data?.log ?? [];
  return (
    <div className="flex flex-col gap-2">
      {intents.length > 0 && (
        <ul className="flex flex-col gap-0.5 text-[12.5px] text-fg" aria-label="What it did">
          {intents.map((i, n) => (
            <li key={n}>{didDo(i, actions)}</li>
          ))}
        </ul>
      )}
      {log.length ? (
        <pre className="max-h-[320px] overflow-auto rounded-sm bg-term-bg p-3 font-mono text-[12px] leading-relaxed text-term-fg">
          {log.join("\n")}
        </pre>
      ) : (
        <p className="text-[12.5px] text-fg-muted">Nothing in the log.</p>
      )}
    </div>
  );
}

function RunRow({ run, actions, aid }: { run: AutomationRun; actions?: ActionDef[]; aid: number }) {
  const [open, setOpen] = useState(false);
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const st = runStatus(run.status);
  const decide = useMutation({
    mutationFn: (go: boolean) => data(Automations.decideHeldRun({ client, path: { run: run.id }, body: { run: go } })),
    onSuccess: (_, go) => {
      void qc.invalidateQueries({ queryKey: ["automation-runs", aid] });
      toast({ tone: "green", title: go ? "Let through: it runs now" : "Skipped" });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t decide", body: e.message }),
  });
  return (
    <li className="border-b border-border py-2 last:border-b-0">
      <div className="flex flex-wrap items-center gap-2 text-[13px]">
        <button
          type="button"
          aria-expanded={open}
          aria-label={`Run ${run.id}`}
          onClick={() => setOpen(!open)}
          className="inline-flex items-center gap-1 text-fg-secondary hover:text-fg"
        >
          {open ? <ChevronDown className="size-4" /> : <ChevronRight className="size-4" />}
          <DateTime iso={run.started_at} />
        </button>
        <Badge tone={st.tone}>{st.label}</Badge>
        {run.dry && <Badge tone="neutral">Test</Badge>}
        {run.trigger && <span className="text-fg-muted">by {run.trigger}</span>}
        <span className="min-w-0 flex-1 truncate text-fg">{runCounts(run)}</span>
        {(run.cost_usd || run.tokens) && (
          <Cost usd={run.cost_usd} tokens={run.tokens} estimate={run.cost_estimate} className="text-fg-muted" />
        )}
        {run.status === "held" && (
          <span className="flex gap-1">
            <Button size="sm" disabled={decide.isPending} onClick={() => decide.mutate(true)}>
              Run it once
            </Button>
            <Button size="sm" variant="ghost" disabled={decide.isPending} onClick={() => decide.mutate(false)}>
              Skip
            </Button>
          </span>
        )}
      </div>
      {open && (
        <div className="mt-2 pl-5">
          <RunDetail id={run.id} actions={actions} />
        </div>
      )}
    </li>
  );
}

export function RunsTab({ aid }: { aid: number }) {
  const client = useApiClient();
  const actions = useActions().data;
  const q = useInfiniteQuery({
    queryKey: ["automation-runs", aid],
    initialPageParam: null as number | null,
    queryFn: ({ pageParam }) =>
      data(Automations.listRuns({ client, path: { aid }, query: { before: pageParam, limit: PAGE } })),
    getNextPageParam: (last) => last.next ?? null,
  });
  if (q.isLoading) return <SkeletonRows rows={3} />;
  if (q.error)
    return (
      <EmptyState
        tone="error"
        icon={<History />}
        title="Couldn’t load the runs"
        actions={<Button onClick={() => q.refetch()}>Try again</Button>}
      >
        {(q.error as Error).message}
      </EmptyState>
    );
  const runs = q.data?.pages.flatMap((p) => p.runs) ?? [];
  if (!runs.length)
    return (
      <EmptyState icon={<History />} title="No runs yet">
        It runs when its trigger fires, or when you press Run now.
      </EmptyState>
    );
  return (
    <div className="flex flex-col gap-2">
      <ul aria-label="Runs">
        {runs.map((r) => (
          <RunRow key={r.id} run={r} actions={actions} aid={aid} />
        ))}
      </ul>
      {q.hasNextPage && (
        <Button
          variant="secondary"
          size="sm"
          className="self-start"
          disabled={q.isFetchingNextPage}
          onClick={() => q.fetchNextPage()}
        >
          Older runs
        </Button>
      )}
    </div>
  );
}

type Upcoming = { key?: string; at?: string; description?: string; trigger?: string | null; untrusted?: boolean };

export function UpcomingTab({ aid, enabled }: { aid: number; enabled: boolean }) {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["automation-upcoming", aid],
    queryFn: () => data(Automations.upcoming({ client, path: { aid }, query: { n: 20 } })),
  });
  if (q.isLoading) return <SkeletonRows rows={2} />;
  if (q.error) return <p className="text-[13px] text-red-dark">{(q.error as Error).message}</p>;
  const times = (q.data?.times ?? []) as Upcoming[];
  if (!times.length)
    return (
      <EmptyState icon={<CalendarClock />} title="Nothing coming up">
        {enabled
          ? "It has no schedule, time or wait coming up. It still runs when its events arrive."
          : "It’s off, so nothing is coming up. Switch it on to set its times."}
      </EmptyState>
    );
  return (
    <ul aria-label="Coming up" className="flex flex-col">
      {times.map((t, n) => (
        <li key={t.key ?? n} className="flex flex-wrap gap-2 border-b border-border py-2 text-[13px] last:border-b-0">
          <DateTime iso={t.at} mode="absolute" className="font-medium text-fg" />
          <span className="text-fg-secondary">{t.description}</span>
          {t.trigger && <span className="text-fg-muted">· {t.trigger}</span>}
          {t.untrusted && <Badge tone="gate">Quotes outside text</Badge>}
        </li>
      ))}
    </ul>
  );
}

function VarRow({ v, aid }: { v: AutomationVar; aid: number }) {
  const [text, setText] = useState(varText(v.value));
  const set = useSetVars(aid);
  const changed = text !== varText(v.value);
  return (
    <li className="flex flex-wrap items-center gap-2 border-b border-border py-2 last:border-b-0">
      <span className="w-40 shrink-0 truncate font-mono text-[12.5px] text-fg">{v.name}</span>
      <Input
        aria-label={`Value of ${v.name}`}
        className="min-w-0 flex-1 font-mono text-[12.5px]"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter" && changed) set.mutate({ vars: { [v.name]: varFrom(text) } });
          if (e.key === "Escape") setText(varText(v.value));
        }}
      />
      {v.untrusted && <Badge tone="gate">From outside text</Badge>}
      {v.updated_at && <DateTime iso={v.updated_at} className="text-[12px] text-fg-muted" />}
      <Button
        size="sm"
        variant="secondary"
        disabled={!changed || set.isPending}
        onClick={() => set.mutate({ vars: { [v.name]: varFrom(text) } })}
      >
        Set
      </Button>
      <Button
        size="sm"
        variant="danger-ghost"
        disabled={set.isPending}
        onClick={() => set.mutate({ vars: { [v.name]: null }, undo: { [v.name]: v.value } })}
      >
        Clear
      </Button>
    </li>
  );
}

function useSetVars(aid: number) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const m = useMutation({
    mutationFn: ({ vars }: { vars: Record<string, unknown>; undo?: Record<string, unknown> }) =>
      data(Automations.putAutomationVars({ client, path: { aid }, body: { vars } })),
    onSuccess: (got, { undo }) => {
      qc.setQueryData(["automation-vars", aid], got);
      if (undo)
        toast({
          tone: "intent",
          title: `Cleared ${Object.keys(undo).join(", ")}`,
          action: { label: "Undo", onClick: () => m.mutate({ vars: undo }) },
        });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t set it", body: e.message }),
  });
  return m;
}

export function VariablesTab({ aid }: { aid: number }) {
  const client = useApiClient();
  const q = useQuery({
    queryKey: ["automation-vars", aid],
    queryFn: () => data(Automations.getAutomationVars({ client, path: { aid } })),
  });
  const set = useSetVars(aid);
  const [name, setName] = useState("");
  const [value, setValue] = useState("");
  if (q.isLoading) return <SkeletonRows rows={2} />;
  if (q.error) return <p className="text-[13px] text-red-dark">{(q.error as Error).message}</p>;
  const vars = q.data?.vars ?? [];
  const add = () => {
    if (!name.trim()) return;
    set.mutate({ vars: { [name.trim()]: varFrom(value) } }, { onSuccess: () => (setName(""), setValue("")) });
  };
  return (
    <div className="flex flex-col gap-3">
      <p className="text-[13px] text-fg-secondary">
        What it remembers between runs: its actions set these, and its conditions and templates read them as{" "}
        <code className="font-mono">{"{{ vars.name }}"}</code>.
      </p>
      {vars.length ? (
        <ul aria-label="Variables">
          {vars.map((v) => (
            <VarRow key={`${v.name}-${v.updated_at}`} v={v} aid={aid} />
          ))}
        </ul>
      ) : (
        <EmptyState icon={<Variable />} title="No variables yet">
          It has nothing kept between runs.
        </EmptyState>
      )}
      <div className="flex flex-wrap items-center gap-2">
        <Input
          aria-label="New variable name"
          placeholder="name"
          className="w-40 font-mono text-[12.5px]"
          value={name}
          onChange={(e) => setName(e.target.value)}
        />
        <Input
          aria-label="New variable value"
          placeholder="value (text or JSON)"
          className="min-w-0 flex-1 font-mono text-[12.5px]"
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && add()}
        />
        <Button size="sm" variant="secondary" disabled={!name.trim() || set.isPending} onClick={add}>
          Add
        </Button>
      </div>
      {vars.length > 0 && <p className="text-[12px] text-fg-muted">{plural(vars.length, "variable")}</p>}
    </div>
  );
}

export function CostsTab({ aid }: { aid: number }) {
  return (
    <div className="flex flex-col gap-4">
      <BudgetPanel resource={`automation:${aid}`} what="this automation" />
      <ActivityPanel resource={`automation:${aid}`} />
    </div>
  );
}
