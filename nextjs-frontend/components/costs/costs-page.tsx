"use client";

import { Wallet } from "lucide-react";
import Link from "next/link";

import type { BudgetMeter as MeterData, DaySpend, TopSpend } from "@/app/openapi-client/types.gen";
import {
  PERIOD_LABEL,
  PERIODS,
  ShareBar,
  useBudgetMeter,
  useViewPeriod,
  type ViewPeriod,
} from "@/components/costs/budget-meter";
import { BudgetBadge, BudgetPanel, Cost, tokens, usd, useBudgets } from "@/components/costs/costs";
import { Panel } from "@/components/ui/panel";
import { EmptyState, PageHeader, Skeleton, SkeletonRows } from "@/components/ui/states";
import { Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { Segmented } from "@/components/ui/tabs";
import { useArchive } from "@/lib/hooks/session";

const HREF: Record<string, (id: string) => string> = {
  routine: (id) => `/routines/${id}`,
  pipeline: (id) => `/pipelines/${id}`,
  workflow: (id) => `/workflows/${id}`,
  chat: (id) => `/chat/${id}`,
  recording: (id) => `/resources/${id}`,
  note_page: (id) => `/notes/${id}`,
};
const KIND: Record<string, string> = {
  routine: "Routine",
  pipeline: "Pipeline",
  workflow: "Workflow",
  chat: "Chat",
  recording: "Document",
  note_page: "Note",
  extension: "Extension",
  sensor: "Sensor",
  space: "Namespace",
};

function ResourceName({ r }: { r: TopSpend }) {
  const [table, id] = r.resource.split(":");
  const text = r.name ?? `${KIND[table] ?? table} ${id}`;
  const href = HREF[table]?.(id);
  return href ? (
    <Link href={href} className="font-semibold text-fg hover:underline">
      {text}
    </Link>
  ) : (
    <span className="font-semibold text-fg">{text}</span>
  );
}

/** Every day of the period as a bar (days with nothing are empty), cost when anything was priced, else tokens. */
function DayBars({ days, since, period }: { days: DaySpend[]; since?: string | null; period: ViewPeriod }) {
  const start = since ? new Date(since) : new Date();
  const today = new Date();
  const all: string[] = [];
  for (let d = new Date(start); d <= today && all.length < 31; d.setUTCDate(d.getUTCDate() + 1))
    all.push(d.toISOString().slice(0, 10));
  const by = Object.fromEntries(days.map((d) => [d.day, d]));
  const priced = days.some((d) => d.cost_usd > 0);
  const value = (d?: DaySpend) => (d ? (priced ? d.cost_usd : d.tokens) : 0);
  const max = Math.max(...all.map((k) => value(by[k])), 0) || 1;
  if (period === "day") return null;
  return (
    <Panel title="By day" subtitle={priced ? "Cost per day (UTC)" : "Tokens per day (UTC): no prices set"}>
      <div className="flex h-32 items-end gap-1" role="list" aria-label="Spend by day">
        {all.map((k) => {
          const d = by[k];
          const v = value(d);
          return (
            <div
              key={k}
              role="listitem"
              className="flex h-full min-w-0 flex-1 flex-col justify-end"
              title={`${k}: ${d ? `${usd(d.cost_usd)} · ${tokens(d.tokens)} tokens · ${d.calls} calls` : "nothing"}`}
            >
              <span
                className="block rounded-t-[3px] bg-blue"
                style={{ height: `${(v / max) * 100}%`, minHeight: v ? 2 : 0 }}
              />
            </div>
          );
        })}
      </div>
      <div className="mt-1 flex justify-between text-[11.5px] text-fg-muted">
        <span>{all[0]}</span>
        <span>{all[all.length - 1]}</span>
      </div>
    </Panel>
  );
}

function SpendTable({ title, rows, empty }: { title: string; rows: TopSpend[]; empty: string }) {
  return (
    <Panel title={title} bodyClassName="p-0">
      {rows.length === 0 ? (
        <p className="px-4 pb-4 text-[13px] text-fg-secondary">{empty}</p>
      ) : (
        <Table>
          <THead>
            <tr>
              <Th>Name</Th>
              <Th className="text-right">Calls</Th>
              <Th className="text-right">Tokens</Th>
              <Th className="text-right">Cost</Th>
            </tr>
          </THead>
          <tbody>
            {rows.map((r) => (
              <Tr key={r.resource}>
                <Td>
                  <ResourceName r={r} />
                  <span className="ml-2 text-[12px] text-fg-muted">{KIND[r.resource.split(":")[0]] ?? ""}</span>
                </Td>
                <Td className="text-right tabular-nums">{r.calls}</Td>
                <Td className="text-right tabular-nums">{tokens(r.tokens)}</Td>
                <Td className="text-right">
                  <Cost usd={r.cost_usd} />
                </Td>
              </Tr>
            ))}
          </tbody>
        </Table>
      )}
    </Panel>
  );
}

function Tile({ label, children, hint }: { label: string; children: React.ReactNode; hint?: React.ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1 rounded-md border border-border bg-background p-4">
      <span className="label-caps text-fg-muted">{label}</span>
      <span className="text-[22px] font-bold leading-tight text-fg">{children}</span>
      {hint && <span className="text-[12.5px] text-fg-secondary">{hint}</span>}
    </div>
  );
}

function Tiles({ m, head }: { m: MeterData; head?: MeterData }) {
  const st = head?.budget;
  const b = st?.budget;
  return (
    <div className="grid gap-3 sm:grid-cols-3">
      <Tile label={`Spent ${PERIOD_LABEL[m.period]}`} hint={`${tokens(m.spent.tokens)} tokens`}>
        <Cost usd={m.spent.usd} tokens={m.spent.tokens} estimate={m.spent.estimate} />
      </Tile>
      <Tile label="Calls" hint="Model calls, embeddings, the decision model, webhooks">
        {m.calls.toLocaleString()}
      </Tile>
      <Tile
        label={b ? `Left this ${b.period}` : "Budget"}
        hint={
          st && b ? (
            <span className="flex flex-col gap-1">
              <span>
                {Math.round((st.share ?? 0) * 100)}% of {b.usd != null ? usd(b.usd) : `${tokens(b.tokens)} tokens`} used{" "}
                <BudgetBadge status={st} />
              </span>
              <ShareBar share={st.share ?? 0} warnAt={b.warn_at} />
            </span>
          ) : (
            "No budget: it runs whatever it costs."
          )
        }
      >
        {st && b ? (b.usd != null ? usd(st.left?.usd) : `${tokens(st.left?.tokens)} tokens`) : "—"}
      </Tile>
    </div>
  );
}

/** /costs: what the namespace picked in the top bar (or all of Lens) spent, by day, model and resource, against its
 * budget; admins set the namespace's budget here. */
export function CostsPage() {
  const [period, setPeriod] = useViewPeriod();
  const { namespace, admin } = useArchive();
  const q = useBudgetMeter(period, true);
  const head = useBudgetMeter(null);
  const budgets = useBudgets();
  const where = namespace ?? "all of Lens";
  const m = q.data;
  const others = Object.values(budgets).filter((b) => b.resource !== m?.resource && b.budget?.period !== "run");

  if (!namespace && !admin)
    return (
      <div className="px-4 py-6 md:px-6">
        <EmptyState title="Pick a namespace" icon={<Wallet />}>
          Costs show for one namespace at a time. Pick one in the top bar.
        </EmptyState>
      </div>
    );

  return (
    <div className="flex flex-col gap-4 px-4 py-5 md:px-6">
      <PageHeader
        title="Costs"
        meta={`What ${where} spent ${PERIOD_LABEL[period]}`}
        actions={
          <Segmented label="Period" items={PERIODS} value={period} onChange={(v) => setPeriod(v as ViewPeriod)} />
        }
        className="mb-0"
      />
      {q.error ? (
        <p className="text-[13px] text-red-dark">{(q.error as Error).message}</p>
      ) : !m ? (
        <SkeletonRows rows={5} />
      ) : (
        <>
          <Tiles m={m} head={head.data} />
          <DayBars days={m.by_day ?? []} since={m.since} period={m.period} />
          <div className="grid gap-4 lg:grid-cols-2">
            <Panel title="By model" bodyClassName="p-0">
              {(m.by_model ?? []).length === 0 ? (
                <p className="px-4 pb-4 text-[13px] text-fg-secondary">No calls {PERIOD_LABEL[m.period]}.</p>
              ) : (
                <Table>
                  <THead>
                    <tr>
                      <Th>Model</Th>
                      <Th className="text-right">Calls</Th>
                      <Th className="text-right">Tokens</Th>
                      <Th className="text-right">Cost</Th>
                    </tr>
                  </THead>
                  <tbody>
                    {(m.by_model ?? []).map((r) => (
                      <Tr key={r.model ?? "-"}>
                        <Td className="font-semibold">{r.model ?? "Other calls (webhooks, web tools)"}</Td>
                        <Td className="text-right tabular-nums">{r.calls}</Td>
                        <Td className="text-right tabular-nums">{tokens(r.tokens)}</Td>
                        <Td className="text-right">
                          <Cost usd={r.cost_usd} tokens={r.cost_usd ? null : r.tokens} estimate={r.estimate} />
                        </Td>
                      </Tr>
                    ))}
                  </tbody>
                </Table>
              )}
            </Panel>
            <SpendTable
              title="What cost most"
              rows={m.top ?? []}
              empty={`Nothing in ${where} was costed ${PERIOD_LABEL[m.period]}.`}
            />
          </div>
          {m.by_namespace && (
            <SpendTable title="By namespace" rows={m.by_namespace} empty="No namespace spent anything yet." />
          )}
          {m.resource && <BudgetPanel resource={m.resource} what={`work in ${where}`} />}
          {admin && others.length > 0 && (
            <Panel
              title="Other budgets"
              subtitle="Routines, pipelines, workflows and namespaces with a cap"
              bodyClassName="p-0"
            >
              <Table>
                <THead>
                  <tr>
                    <Th>Budget for</Th>
                    <Th>Used</Th>
                    <Th className="text-right">Left</Th>
                  </tr>
                </THead>
                <tbody>
                  {others.map((b) => (
                    <Tr key={b.resource}>
                      <Td>
                        <ResourceName r={{ resource: b.resource, cost_usd: 0, tokens: 0, calls: 0 }} />{" "}
                        <BudgetBadge status={b} />
                      </Td>
                      <Td className="w-[40%]">
                        <ShareBar share={b.share ?? 0} warnAt={b.budget?.warn_at} />
                        <span className="text-[12px] text-fg-muted">
                          {Math.round((b.share ?? 0) * 100)}% this {b.budget?.period}
                        </span>
                      </Td>
                      <Td className="text-right">
                        {b.budget?.usd != null ? usd(b.left?.usd) : `${tokens(b.left?.tokens)} tokens`}
                      </Td>
                    </Tr>
                  ))}
                </tbody>
              </Table>
            </Panel>
          )}
        </>
      )}
      {!m && !q.error && <Skeleton className="h-32 w-full" />}
    </div>
  );
}
