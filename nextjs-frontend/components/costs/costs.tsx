"use client";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ArrowDownLeft, ArrowUpRight, History, Pencil, PiggyBank, Play, RotateCcw } from "lucide-react";
import { useState } from "react";

import { Activity, Budgets } from "@/app/openapi-client";
import type { ActivityEntry, BudgetSet, BudgetStatus, ResourceSpend } from "@/app/openapi-client/types.gen";
import { OPS } from "@/components/routines/history-model";
import { Badge, type Tone } from "@/components/ui/badge";
import { Banner } from "@/components/ui/banner";
import { Button } from "@/components/ui/button";
import { Field, Input, Select } from "@/components/ui/field";
import { Panel } from "@/components/ui/panel";
import { DateTime, Skeleton, SkeletonRows } from "@/components/ui/states";
import { useToast } from "@/components/ui/toast";
import { Tooltip } from "@/components/ui/tooltip";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

export type Period = "day" | "week" | "month" | "all";
const PERIOD_TEXT: Record<Period, string> = { day: "today", week: "this week", month: "this month", all: "in all" };

/** USD the way a person reads it: cents for small sums, more digits below a cent. */
export function usd(v: number | null | undefined): string {
  if (v == null) return "—";
  if (v === 0) return "$0";
  if (v < 0.01) return `$${v.toPrecision(2)}`;
  if (v < 100) return `$${v.toFixed(2)}`;
  return `$${Math.round(v).toLocaleString()}`;
}

/** 950, 12.4k, 3.1M tokens. */
export function tokens(n: number | null | undefined): string {
  if (n == null) return "—";
  if (n < 1000) return String(n);
  if (n < 1_000_000) return `${(n / 1000).toFixed(n < 10_000 ? 1 : 0)}k`;
  return `${(n / 1_000_000).toFixed(1)}M`;
}

const ESTIMATE_WHY =
  "An estimate: some calls had a price but no token counts from the server, so the real cost is at least this.";

/**
 * A cost, honest about what it is: "$0.42", "≈$0.42" when it's an estimate (with why on hover), or the tokens alone
 * when nothing was priced (local models aren't costed unless they're given a price).
 */
export function Cost({
  usd: v,
  tokens: t,
  estimate,
  why,
  className,
}: {
  usd?: number | null;
  tokens?: number | null;
  estimate?: boolean | null;
  why?: string;
  className?: string;
}) {
  const priced = (v ?? 0) > 0;
  const text = priced ? usd(v) : t ? `${tokens(t)} tokens` : usd(v ?? 0);
  const title = [
    estimate ? (why ?? ESTIMATE_WHY) : null,
    priced && t ? `${tokens(t)} tokens` : null,
    !priced && t ? "No price set for these models, so they aren't costed (Settings › Telemetry › prices)." : null,
  ]
    .filter(Boolean)
    .join(" ");
  const body = (
    <span className={cn("whitespace-nowrap tabular-nums", estimate && "italic", className)}>
      {estimate ? (
        <abbr title="estimate" className="no-underline">
          ≈
        </abbr>
      ) : null}
      {text}
    </span>
  );
  return title ? (
    <Tooltip content={title}>
      <span tabIndex={0}>{body}</span>
    </Tooltip>
  ) : (
    body
  );
}

/** What each of many resources cost this period, in one request (for lists). */
export function useCosts(resources: string[], period: Period = "month") {
  const client = useApiClient();
  const list = [...new Set(resources)].sort();
  return useQuery({
    queryKey: ["activity-costs", period, list],
    queryFn: () => data(Activity.resourceCosts({ client, query: { resource: list, period } })),
    enabled: list.length > 0,
    staleTime: 30_000,
  });
}

/** One resource's cost this period, from a list's costs (or its own request). */
export function ResourceCost({
  resource,
  costs,
  period = "month",
  className,
}: {
  resource: string;
  costs?: Record<string, ResourceSpend>;
  period?: Period;
  className?: string;
}) {
  const own = useCosts(costs ? [] : [resource], period);
  const c = costs ? costs[resource] : own.data?.costs[resource];
  if (!c) return <span className="text-fg-muted">—</span>;
  return <Cost usd={c.cost_usd} tokens={c.tokens} estimate={c.estimate} className={className} />;
}

const KIND: Record<string, { label: string; icon: React.ReactNode; tone: Tone }> = {
  in: { label: "Request", icon: <ArrowDownLeft aria-hidden />, tone: "neutral" },
  out: { label: "Call out", icon: <ArrowUpRight aria-hidden />, tone: "intent" },
  run: { label: "Run", icon: <Play aria-hidden />, tone: "green" },
  change: { label: "Change", icon: <Pencil aria-hidden />, tone: "gate" },
};

function actionText(e: ActivityEntry): string {
  const a = e.action;
  if (a.startsWith("model."))
    return `${a.slice(6) === "chat" ? "Model call" : `Model ${a.slice(6)}`}${e.model ? ` · ${e.model}` : ""}`;
  if (a === "embeddings") return `Embeddings${e.model ? ` · ${e.model}` : ""}`;
  if (a === "decision") return `Decision model${e.model ? ` · ${e.model}` : ""}`;
  if (a.startsWith("notify.")) return `Notification · ${a.slice(7)}`;
  if (a === "tool.http") return "Web tool";
  if (a.startsWith("budget.")) return a === "budget.over" ? "Budget used up" : "Budget nearly used";
  if (a.startsWith("graph.")) {
    const d = (e.detail ?? {}) as { version?: number; why?: string | null };
    return `Graph · ${OPS[a.slice(6)] ?? a.slice(6)}${d.version != null ? ` · v${d.version}` : ""}${d.why ? ` · ${d.why}` : ""}`;
  }
  return a;
}

/** A resource's history: requests that changed it, calls made for it, runs and audit entries, with their cost. */
export function ActivityPanel({ resource, title = "Activity" }: { resource: string; title?: string }) {
  const client = useApiClient();
  const [limit, setLimit] = useState(50);
  const [period, setPeriod] = useState<Period>("month");
  const q = useQuery({
    queryKey: ["activity", resource, limit],
    queryFn: () => data(Activity.resourceHistory({ client, query: { resource, limit } })),
    staleTime: 15_000,
  });
  const totals = useQuery({
    queryKey: ["activity-totals", resource, period],
    queryFn: () => data(Activity.resourceTotals({ client, query: { resource, period } })),
    staleTime: 30_000,
  });
  const t = totals.data;
  return (
    <Panel
      title={title}
      subtitle={
        t ? (
          <>
            <Cost usd={t.cost_usd} tokens={t.tokens_in + t.tokens_out} estimate={t.estimate} /> {PERIOD_TEXT[period]}
            {" · "}
            {t.by_kind.out?.calls ?? 0} calls out · {t.by_kind.in?.calls ?? 0} changes
            {t.failed ? ` · ${t.failed} failed` : ""}
          </>
        ) : (
          <Skeleton className="inline-block h-4 w-48" />
        )
      }
      actions={
        <Select
          size="sm"
          aria-label="Period"
          className="w-[130px]"
          value={period}
          onChange={(e) => setPeriod(e.target.value as Period)}
          options={[
            { value: "day", label: "Today" },
            { value: "week", label: "This week" },
            { value: "month", label: "This month" },
            { value: "all", label: "All time" },
          ]}
        />
      }
    >
      {q.isLoading ? (
        <SkeletonRows rows={4} />
      ) : q.error ? (
        <p className="text-[12.5px] text-red-dark">{(q.error as Error).message}</p>
      ) : !q.data?.length ? (
        <p className="flex items-center gap-2 text-[13px] text-fg-secondary">
          <History aria-hidden className="size-4" /> Nothing yet. Changes, calls and runs show here as they happen.
        </p>
      ) : (
        <>
          <ul aria-label="History" className="flex flex-col divide-y divide-border">
            {q.data.map((e, i) => {
              const k = KIND[e.kind] ?? KIND.in;
              return (
                <li
                  key={e.id ?? `${e.at}-${i}`}
                  className="flex flex-wrap items-center gap-x-3 gap-y-1 py-2 text-[13px]"
                >
                  <span className="w-[150px] shrink-0 text-[12.5px] text-fg-muted">
                    <DateTime iso={e.at} />
                  </span>
                  <Badge tone={e.ok ? k.tone : "red"}>{k.label}</Badge>
                  <span className={cn("min-w-0 flex-1 truncate", e.ok ? "text-fg" : "text-red-dark")}>
                    {actionText(e)}
                    {e.email ? <span className="text-fg-muted"> · {e.email}</span> : null}
                    {!e.ok && e.error ? <span> · {e.error}</span> : null}
                  </span>
                  {e.ms != null && e.kind !== "change" ? (
                    <span className="text-[12px] tabular-nums text-fg-muted">{(e.ms / 1000).toFixed(1)}s</span>
                  ) : null}
                  {e.cost_usd != null || e.tokens_in != null || e.tokens_out != null ? (
                    <Cost
                      usd={e.cost_usd}
                      tokens={(e.tokens_in ?? 0) + (e.tokens_out ?? 0) || null}
                      estimate={Boolean((e.detail as { estimate?: boolean } | null)?.estimate)}
                      className="w-[90px] text-right text-[12.5px]"
                    />
                  ) : (
                    <span className="w-[90px]" />
                  )}
                </li>
              );
            })}
          </ul>
          {q.data.length >= limit && (
            <Button size="xs" variant="ghost" className="mt-2" onClick={() => setLimit(limit + 100)}>
              Show more
            </Button>
          )}
        </>
      )}
    </Panel>
  );
}

const STATE: Record<BudgetStatus["state"], { label: string; tone: Tone }> = {
  none: { label: "No budget", tone: "neutral" },
  ok: { label: "Within budget", tone: "green" },
  near: { label: "Nearly used", tone: "gate" },
  over: { label: "Over budget", tone: "red" },
};

export function BudgetBadge({ status }: { status?: BudgetStatus | null }) {
  if (!status || status.state === "none") return null;
  const s = STATE[status.state];
  return (
    <Badge tone={s.tone} dot>
      {s.label}
    </Badge>
  );
}

export function useBudget(resource: string) {
  const client = useApiClient();
  const { admin } = useArchive();
  return useQuery({
    queryKey: ["budget", resource],
    queryFn: () => data(Budgets.budgetStatus({ client, query: { resource } })),
    enabled: admin,
    staleTime: 30_000,
  });
}

const ON_OVER = [
  { value: "ask", label: "Hold the run and ask me" },
  { value: "skip", label: "Skip the run" },
  { value: "assistant", label: "Let the assistant decide (asks when unsure)" },
];

/** Where a resource's budget stands, its next run's estimate, and (admins) setting or removing it. */
export function BudgetPanel({ resource, what = "it" }: { resource: string; what?: string }) {
  const client = useApiClient();
  const qc = useQueryClient();
  const toast = useToast();
  const { admin } = useArchive();
  const q = useBudget(resource);
  const [editing, setEditing] = useState(false);
  const [form, setForm] = useState<{ usd: string; tokens: string; period: string; on_over: string; warn: string }>({
    usd: "",
    tokens: "",
    period: "month",
    on_over: "ask",
    warn: "80",
  });
  const refresh = () => {
    void qc.invalidateQueries({ queryKey: ["budget", resource] });
    void qc.invalidateQueries({ queryKey: ["budgets"] });
  };
  const save = useMutation({
    mutationFn: (body: BudgetSet) => data(Budgets.setBudget({ client, query: { resource }, body })),
    onSuccess: () => {
      refresh();
      setEditing(false);
      toast({ tone: "green", title: "Budget saved" });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t save the budget", body: e.message }),
  });
  const remove = useMutation({
    mutationFn: () => data(Budgets.removeBudget({ client, query: { resource } })),
    onSuccess: () => {
      refresh();
      toast({ title: "Budget removed" });
    },
    onError: (e: Error) => toast({ tone: "red", title: "Couldn’t remove the budget", body: e.message }),
  });
  if (!admin) return null;
  if (q.isLoading) return <Skeleton className="h-24 w-full" />;
  if (q.error) return <p className="text-[12.5px] text-red-dark">{(q.error as Error).message}</p>;
  const st = q.data!;
  const b = st.budget;
  const per = b?.period === "run" ? "per run" : `this ${b?.period ?? "month"}`;

  const start = () => {
    setForm({
      usd: b?.usd != null ? String(b.usd) : "",
      tokens: b?.tokens != null ? String(b.tokens) : "",
      period: b?.period ?? "month",
      on_over: b?.on_over ?? "ask",
      warn: String(Math.round((b?.warn_at ?? 0.8) * 100)),
    });
    setEditing(true);
  };
  const submit = () => {
    const usdV = form.usd.trim() ? Number(form.usd) : null;
    const tokV = form.tokens.trim() ? Math.round(Number(form.tokens)) : null;
    if (usdV == null && tokV == null) {
      toast({ tone: "red", title: "Give the budget a cap", body: "Set USD, tokens or both." });
      return;
    }
    save.mutate({
      usd: usdV,
      tokens: tokV,
      period: form.period as BudgetSet["period"],
      on_over: form.on_over as BudgetSet["on_over"],
      warn_at: Math.min(1, Math.max(0.1, Number(form.warn) / 100)),
    });
  };

  return (
    <Panel
      title={
        <span className="flex items-center gap-2">
          <PiggyBank aria-hidden className="size-4" /> Budget <BudgetBadge status={st} />
        </span>
      }
      subtitle={b ? undefined : `No budget: ${what} runs whatever it costs.`}
      actions={
        !editing && (
          <>
            <Button size="xs" variant="secondary" onClick={start}>
              {b ? "Change" : "Set a budget"}
            </Button>
            {b && (
              <Button size="xs" variant="danger-ghost" disabled={remove.isPending} onClick={() => remove.mutate()}>
                Remove
              </Button>
            )}
          </>
        )
      }
    >
      {b && st.spent && !editing && (
        <div className="flex flex-col gap-2 text-[13px]">
          {b.usd != null && (
            <Meter
              label={
                <>
                  <Cost usd={st.spent.usd} estimate={st.spent.estimate} /> of {usd(b.usd)} {per}
                </>
              }
              share={b.usd ? st.spent.usd / b.usd : 1}
            />
          )}
          {b.tokens != null && (
            <Meter
              label={`${tokens(st.spent.tokens)} of ${tokens(b.tokens)} tokens ${per}`}
              share={b.tokens ? st.spent.tokens / b.tokens : 1}
            />
          )}
          <p className="text-fg-secondary">
            Over budget: {ON_OVER.find((o) => o.value === b.on_over)?.label.toLowerCase()}. Warns at{" "}
            {Math.round(b.warn_at * 100)}%.
          </p>
        </div>
      )}
      {!editing && (
        <p className="mt-2 text-[12.5px] text-fg-muted">
          Next run:{" "}
          {st.next.runs ? (
            <>
              <Cost usd={st.next.usd} tokens={st.next.tokens} estimate why={`An estimate: ${st.next.basis}.`} /> (
              {st.next.basis})
            </>
          ) : (
            st.next.basis
          )}
        </p>
      )}
      {editing && (
        <form
          className="grid gap-3 sm:grid-cols-2"
          onSubmit={(e) => {
            e.preventDefault();
            submit();
          }}
        >
          <Field label="Cap in USD" optional hint="Counted from the prices in Settings › Telemetry.">
            {({ id }) => (
              <Input
                id={id}
                inputMode="decimal"
                value={form.usd}
                onChange={(e) => setForm({ ...form, usd: e.target.value })}
                placeholder="5.00"
              />
            )}
          </Field>
          <Field label="Cap in tokens" optional hint="In and out; counts local models too.">
            {({ id }) => (
              <Input
                id={id}
                inputMode="numeric"
                value={form.tokens}
                onChange={(e) => setForm({ ...form, tokens: e.target.value })}
                placeholder="1000000"
              />
            )}
          </Field>
          <Field label="For">
            {({ id }) => (
              <Select
                id={id}
                value={form.period}
                onChange={(e) => setForm({ ...form, period: e.target.value })}
                options={[
                  { value: "run", label: "Each run" },
                  { value: "day", label: "A day" },
                  { value: "week", label: "A week" },
                  { value: "month", label: "A month" },
                ]}
              />
            )}
          </Field>
          <Field label="Warn at (%)">
            {({ id }) => (
              <Input
                id={id}
                inputMode="numeric"
                value={form.warn}
                onChange={(e) => setForm({ ...form, warn: e.target.value })}
              />
            )}
          </Field>
          <Field label="When a run would go over" className="sm:col-span-2">
            {({ id }) => (
              <Select
                id={id}
                value={form.on_over}
                onChange={(e) => setForm({ ...form, on_over: e.target.value })}
                options={ON_OVER}
              />
            )}
          </Field>
          <div className="flex gap-2 sm:col-span-2">
            <Button type="submit" size="sm" variant="primary" disabled={save.isPending}>
              Save budget
            </Button>
            <Button type="button" size="sm" variant="ghost" onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </div>
        </form>
      )}
    </Panel>
  );
}

function Meter({ label, share }: { label: React.ReactNode; share: number }) {
  const pct = Math.max(0, Math.min(1, share)) * 100;
  return (
    <div className="flex flex-col gap-1">
      <span className="text-fg">{label}</span>
      <span
        className="h-1.5 overflow-hidden rounded-pill bg-surface-neutral"
        role="meter"
        aria-valuenow={Math.round(share * 100)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <span
          className={cn("block h-full rounded-pill", share >= 1 ? "bg-red" : share >= 0.8 ? "bg-gold" : "bg-green")}
          style={{ width: `${pct}%` }}
        />
      </span>
    </div>
  );
}

/** A run held over budget: why, and the pick (run it once, or skip it). */
export function HeldNotice({
  hold,
  onRun,
  onSkip,
  busy,
}: {
  hold: { why?: string; missed?: number; decided?: unknown } | null | undefined;
  onRun: () => void;
  onSkip: () => void;
  busy?: boolean;
}) {
  const { admin } = useArchive();
  if (!hold?.why || hold.decided) return null;
  return (
    <Banner
      tone="warning"
      title="Waiting for you: over budget."
      action={
        admin ? (
          <span className="flex gap-2">
            <Button size="xs" variant="primary" icon={<Play />} disabled={busy} onClick={onRun}>
              Run once
            </Button>
            <Button size="xs" variant="ghost" icon={<RotateCcw />} disabled={busy} onClick={onSkip}>
              Skip
            </Button>
          </span>
        ) : undefined
      }
    >
      {hold.why}
      {hold.missed ? ` It came due ${hold.missed} more time${hold.missed === 1 ? "" : "s"} while waiting.` : ""}
    </Banner>
  );
}

/** Every budget's status, by resource (admins; empty for others). */
export function useBudgets() {
  const client = useApiClient();
  const { admin } = useArchive();
  const q = useQuery({
    queryKey: ["budgets"],
    queryFn: () => data(Budgets.listBudgets({ client })),
    enabled: admin,
    staleTime: 30_000,
  });
  const byResource: Record<string, BudgetStatus> = {};
  for (const b of q.data ?? []) byResource[b.resource] = b;
  return byResource;
}

/** A list's cost cell: what it cost this period, and where its budget stands when it has one. */
export function CostCell({
  resource,
  costs,
  budgets,
}: {
  resource: string;
  costs?: Record<string, ResourceSpend>;
  budgets?: Record<string, BudgetStatus>;
}) {
  const b = budgets?.[resource];
  const cap = b?.budget;
  const per = cap?.period === "run" ? "per run" : `this ${cap?.period}`;
  return (
    <span className="inline-flex flex-col items-end gap-0.5">
      <ResourceCost resource={resource} costs={costs} />
      {cap && b && b.state !== "none" && (
        <Tooltip
          content={`${cap.usd != null ? `${usd(cap.usd)} ` : ""}${cap.usd != null && cap.tokens != null ? "and " : ""}${
            cap.tokens != null ? `${tokens(cap.tokens)} tokens ` : ""
          }${per}; ${Math.round((b.share ?? 0) * 100)}% used`}
        >
          <span tabIndex={0}>
            <BudgetBadge status={b} />
          </span>
        </Tooltip>
      )}
    </span>
  );
}
