"use client";

import { useQuery } from "@tanstack/react-query";
import { ArrowRight, PiggyBank } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { Budgets } from "@/app/openapi-client";
import type { BudgetMeter as MeterData, BudgetStatus } from "@/app/openapi-client/types.gen";
import { BudgetBadge, Cost, tokens, usd } from "@/components/costs/costs";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { Skeleton } from "@/components/ui/states";
import { Segmented } from "@/components/ui/tabs";
import { data, useApiClient } from "@/lib/api/browser";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

export type ViewPeriod = "day" | "week" | "month";
const PERIOD_KEY = "lens.costs.period";
export const PERIOD_LABEL: Record<ViewPeriod, string> = { day: "today", week: "this week", month: "this month" };
export const PERIODS = [
  { value: "day", label: "Day" },
  { value: "week", label: "Week" },
  { value: "month", label: "Month" },
];

/** The period someone looks at spend over (day, week or month), remembered in this browser. */
export function useViewPeriod(): [ViewPeriod, (p: ViewPeriod) => void] {
  const [period, setPeriod] = useState<ViewPeriod>("month");
  useEffect(() => {
    try {
      const v = localStorage.getItem(PERIOD_KEY);
      if (v === "day" || v === "week" || v === "month") setPeriod(v);
    } catch {
      /* storage unavailable */
    }
  }, []);
  const set = (p: ViewPeriod) => {
    setPeriod(p);
    try {
      localStorage.setItem(PERIOD_KEY, p);
    } catch {
      /* storage unavailable */
    }
  };
  return [period, set];
}

/**
 * What the namespace picked in the top bar (or, for admins, all of Lens) spent, against its budget. `period` null
 * means the budget's own period (else a month). Members see their namespaces'; nothing for "all" unless admin.
 */
export function useBudgetMeter(period: ViewPeriod | null, detail = false) {
  const client = useApiClient();
  const { namespace, admin, isPartial } = useArchive();
  const ns = namespace ?? null;
  return useQuery({
    queryKey: ["budget-meter", ns, period, detail],
    queryFn: () =>
      data(
        Budgets.budgetMeter({
          client,
          query: { ns: ns ?? undefined, period: period ?? undefined, detail },
        }),
      ),
    enabled: ns ? !isPartial(ns) : admin,
    staleTime: 30_000,
    refetchInterval: 60_000,
  });
}

/** "$1.20 / $5", the budget's tokens when it caps tokens only, or what was spent when there's no budget. Plain text:
 * it sits inside the meter's button. "≈" marks an estimate. */
function usedText(m: MeterData): { used: string; left: string | null } {
  const st = m.budget;
  const b = st?.budget;
  if (st && b && st.spent) {
    if (b.usd != null)
      return {
        used: `${st.spent.estimate ? "≈" : ""}${usd(st.spent.usd)} / ${usd(b.usd)}`,
        left: `${usd(st.left?.usd ?? Math.max(0, b.usd - st.spent.usd))} left`,
      };
    if (b.tokens != null)
      return {
        used: `${tokens(st.spent.tokens)} / ${tokens(b.tokens)}`,
        left: `${tokens(st.left?.tokens ?? Math.max(0, b.tokens - st.spent.tokens))} tokens left`,
      };
  }
  const s = m.spent;
  return {
    used: s.usd > 0 || !s.tokens ? `${s.estimate ? "≈" : ""}${usd(s.usd)}` : `${tokens(s.tokens)} tokens`,
    left: null,
  };
}

const TONE: Record<BudgetStatus["state"], string> = {
  none: "border-border",
  ok: "border-border",
  near: "border-gold-border bg-gold-surface",
  over: "border-red-border bg-red-surface",
};

/** A thin bar of how much of a budget is used: green, gold once near, red when over. */
export function ShareBar({ share, warnAt = 0.8, className }: { share: number; warnAt?: number; className?: string }) {
  return (
    <span
      className={cn("block h-1 overflow-hidden rounded-pill bg-surface-neutral", className)}
      role="meter"
      aria-label="Budget used"
      aria-valuenow={Math.round(share * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span
        className={cn("block h-full rounded-pill", share >= 1 ? "bg-red" : share >= warnAt ? "bg-gold" : "bg-green")}
        style={{ width: `${Math.max(0, Math.min(1, share)) * 100}%` }}
      />
    </span>
  );
}

/** The top bar's budget meter: used vs left for the namespace picked (follows the switcher), highlighted when near or
 * over; opens the details. */
export function BudgetMeter() {
  const [open, setOpen] = useState(false);
  const [period, setPeriod] = useViewPeriod();
  const { namespace } = useArchive();
  // the button shows the budget's own period when there is one, else the period picked
  const head = useBudgetMeter(null);
  const hasBudget = Boolean(head.data?.budget?.budget && head.data.budget.budget.period !== "run");
  const own = useBudgetMeter(hasBudget ? null : period);
  const m = hasBudget ? head.data : own.data;
  if (!m) return head.isLoading ? <Skeleton className="hidden h-10 w-[120px] md:block" /> : null;
  const st = m.budget;
  const state = hasBudget && st ? st.state : "none";
  const { used, left } = usedText(m);
  const where = namespace ?? "all of Lens";
  const label = `Spent ${PERIOD_LABEL[m.period]} in ${where}`;

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className={cn(
          "hidden h-10 min-w-0 items-center gap-2 rounded-sm border bg-background px-2.5 text-left hover:bg-surface md:flex",
          TONE[state],
        )}
        aria-label={`${label}${left ? `, ${left}` : ""}. Details`}
      >
        <PiggyBank
          aria-hidden
          className={cn(
            "size-4 shrink-0",
            state === "over" ? "text-red-dark" : state === "near" ? "text-gold-dark" : "text-fg-secondary",
          )}
        />
        <span className="flex min-w-0 flex-col">
          <span className="text-[9.5px] font-bold uppercase leading-none tracking-[.06em] text-fg-muted">
            {hasBudget ? "Budget" : "Spent"} · {PERIOD_LABEL[m.period]}
          </span>
          <span className="whitespace-nowrap text-[13.5px] font-bold leading-tight tabular-nums text-fg">{used}</span>
          {hasBudget && st && <ShareBar share={st.share ?? 0} warnAt={st.budget?.warn_at} className="mt-0.5 w-full" />}
        </span>
      </PopoverTrigger>
      <PopoverContent align="end" className="w-[340px] p-0">
        <MeterDetails head={hasBudget ? m : null} period={period} setPeriod={setPeriod} where={where} />
      </PopoverContent>
    </Popover>
  );
}

function MeterDetails({
  head,
  period,
  setPeriod,
  where,
}: {
  head: MeterData | null;
  period: ViewPeriod;
  setPeriod: (p: ViewPeriod) => void;
  where: string;
}) {
  const q = useBudgetMeter(period, true);
  const { admin } = useArchive();
  const st = head?.budget;
  const b = st?.budget;
  const per = b ? `this ${b.period}` : "";
  const m = q.data;
  return (
    <div className="flex flex-col gap-3 p-3.5 text-[13px]">
      <div className="flex items-center gap-2">
        <span className="min-w-0 flex-1 truncate font-bold text-fg">Spending in {where}</span>
        <BudgetBadge status={st} />
      </div>

      {st && b && st.spent ? (
        <div className="flex flex-col gap-1.5 rounded-sm border border-border p-2.5">
          {b.usd != null && (
            <div className="flex items-baseline justify-between gap-2">
              <span>
                <Cost usd={st.spent.usd} estimate={st.spent.estimate} /> of {usd(b.usd)} {per}
              </span>
              <span className="text-fg-secondary">{usd(st.left?.usd)} left</span>
            </div>
          )}
          {b.tokens != null && (
            <div className="flex items-baseline justify-between gap-2">
              <span>
                {tokens(st.spent.tokens)} of {tokens(b.tokens)} tokens {per}
              </span>
              <span className="text-fg-secondary">{tokens(st.left?.tokens)} left</span>
            </div>
          )}
          <ShareBar share={st.share ?? 0} warnAt={b.warn_at} />
          <span className="text-[12px] text-fg-muted">
            {Math.round((st.share ?? 0) * 100)}% used · warns at {Math.round(b.warn_at * 100)}%
          </span>
        </div>
      ) : (
        <p className="text-fg-secondary">
          No budget for {where}: it runs whatever it costs.{admin ? " Set one on the Costs page." : ""}
        </p>
      )}

      <div className="flex items-center justify-between gap-2">
        <span className="font-semibold text-fg">Spent</span>
        <Segmented label="Period" items={PERIODS} value={period} onChange={(v) => setPeriod(v as ViewPeriod)} />
      </div>
      {!m ? (
        <Skeleton className="h-16 w-full" />
      ) : (
        <>
          <div className="flex items-baseline justify-between">
            <span className="text-[18px] font-bold">
              <Cost usd={m.spent.usd} tokens={m.spent.tokens} estimate={m.spent.estimate} />
            </span>
            <span className="text-fg-secondary">
              {m.calls} call{m.calls === 1 ? "" : "s"} · {tokens(m.spent.tokens)} tokens
            </span>
          </div>
          {(m.by_model ?? []).length > 0 && (
            <ul aria-label="By model" className="flex flex-col gap-1">
              {(m.by_model ?? []).slice(0, 4).map((r) => (
                <li key={r.model ?? "-"} className="flex items-baseline justify-between gap-2">
                  <span className="min-w-0 truncate text-fg-secondary">{r.model ?? "Other calls"}</span>
                  <Cost usd={r.cost_usd} tokens={r.tokens} estimate={r.estimate} />
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      <Link
        href="/costs"
        className="inline-flex items-center gap-1 self-start text-[13px] font-semibold text-blue hover:underline"
      >
        Open Costs <ArrowRight aria-hidden className="size-3.5" />
      </Link>
    </div>
  );
}
