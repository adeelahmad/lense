"use client";

import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, Download, ScrollText, X } from "lucide-react";
import Link from "next/link";
import { Fragment, useMemo, useState, type ReactNode } from "react";

import { Admin } from "@/app/openapi-client";
import { AdminFrame, usePeople } from "@/components/admin/admin-frame";
import { actionGroup, detailText, filterEntries, GROUPS, personText, targetHref, targetText, toCsv, type AuditEntry, type Filters, type Lookup } from "@/components/admin/audit-model";
import { isUnreachable } from "@/components/errors/error-states";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/field";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/menu";
import { EmptyState, SkeletonRows } from "@/components/ui/states";
import { Pagination, SortTh, Table, Td, Th, THead, Tr } from "@/components/ui/table";
import { data, useApiClient } from "@/lib/api/browser";
import { absolute, count } from "@/lib/format";
import { useArchive } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const PAGE = 25;
const LIMIT = 1000;
const RANGES: { days: number | null; label: string }[] = [
  { days: 1, label: "Last 24 hours" },
  { days: 7, label: "Last 7 days" },
  { days: 30, label: "Last 30 days" },
  { days: 90, label: "Last 90 days" },
  { days: null, label: "All time" },
];

function when(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : `${d.toLocaleDateString("en-GB", { day: "numeric", month: "short" })}, ${d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" })}`;
}

function FilterPill({ label, active, onClear, children }: { label: ReactNode; active: boolean; onClear: () => void; children: ReactNode }) {
  return (
    <span className={cn("inline-flex h-[30px] items-center rounded-pill border text-[12.5px] font-semibold", active ? "border-blue-border bg-blue-surface text-fg-accent" : "border-border bg-background text-fg-strong")}>
      <Popover>
        <PopoverTrigger className={cn("inline-flex h-full items-center gap-[5px] rounded-pill pl-[11px]", active ? "pr-1" : "pr-2 hover:bg-surface")}>
          {label}
          {!active && <ChevronDown aria-hidden className="size-[13px]" />}
        </PopoverTrigger>
        <PopoverContent className="w-[240px] p-1.5">{children}</PopoverContent>
      </Popover>
      {active && (
        <button type="button" aria-label="Clear this filter" onClick={onClear} className="mr-1 grid size-6 place-items-center rounded-full hover:bg-blue-border/40">
          <X className="size-[13px]" />
        </button>
      )}
    </span>
  );
}

function Option({ on, onClick, children }: { on: boolean; onClick: () => void; children: ReactNode }) {
  return (
    <button type="button" role="menuitemradio" aria-checked={on} onClick={onClick} className={cn("flex h-8 w-full items-center gap-2 rounded-sm px-2.5 text-left text-[13px]", on ? "bg-blue-surface font-semibold text-fg-accent" : "hover:bg-surface-neutral")}>
      {children}
    </button>
  );
}

/** The audit log (Admin AD4): who changed what, filtered by person, kind of action and date, with details per row. */
export function AuditPage() {
  const client = useApiClient();
  const { namespaces } = useArchive();
  const people = usePeople();
  const audit = useQuery({ queryKey: ["audit"], queryFn: () => data(Admin.listAudit({ client, query: { limit: LIMIT } })) as Promise<AuditEntry[]> });
  const [filters, setFilters] = useState<Filters>({ person: null, groups: [], sinceDays: 30 });
  const [asc, setAsc] = useState(false);
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<Set<number>>(new Set());

  const look: Lookup = useMemo(
    () => ({
      people: Object.fromEntries((people.data ?? []).map((p) => [p.email.toLowerCase(), p.name || p.email])),
      accounts: Object.fromEntries((people.data ?? []).map((p) => [p.id, p.name || p.email])),
      namespaces: Object.fromEntries(namespaces.map((n) => [n.id, n.name])),
    }),
    [people.data, namespaces],
  );
  const all = audit.data ?? [];
  const shown = useMemo(() => {
    const f = filterEntries(all, filters);
    return asc ? f.slice().reverse() : f;
  }, [all, filters, asc]);
  const page = shown.slice(offset, offset + PAGE);
  const emails = [...new Set(all.map((e) => e.email).filter(Boolean) as string[])].sort();
  const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const set = (f: Partial<Filters>) => {
    setFilters((x) => ({ ...x, ...f }));
    setOffset(0);
    setOpen(new Set());
  };
  const filtered = Boolean(filters.person || filters.groups.length || filters.sinceDays);

  const exportCsv = () => {
    const blob = new Blob([toCsv(shown, look)], { type: "text/csv;charset=utf-8" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `audit-log-${new Date().toISOString().slice(0, 10)}.csv`;
    a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 1000);
  };

  return (
    <AdminFrame
      tab="audit"
      title="Audit log"
      actions={
        <Button variant="ghost" size="sm" icon={<Download />} disabled={!shown.length} onClick={exportCsv}>
          Export CSV
        </Button>
      }
    >
      <section className="flex flex-col gap-3.5">
        <div className="flex flex-wrap items-center gap-1.5">
          <FilterPill label={filters.person ? personText(filters.person, look) : "Person"} active={Boolean(filters.person)} onClear={() => set({ person: null })}>
            <Option on={!filters.person} onClick={() => set({ person: null })}>
              Anyone
            </Option>
            {emails.map((e) => (
              <Option key={e} on={filters.person === e} onClick={() => set({ person: e })}>
                <span className="truncate">{personText(e, look)}</span>
              </Option>
            ))}
          </FilterPill>
          <FilterPill label={filters.groups.length ? `Action: ${filters.groups.join(", ")}` : "Action"} active={filters.groups.length > 0} onClear={() => set({ groups: [] })}>
            <div className="flex flex-col gap-2 p-1.5">
              {GROUPS.map((g) => (
                <Checkbox
                  key={g.value}
                  label={g.label}
                  checked={filters.groups.includes(g.value)}
                  onCheckedChange={(on) => set({ groups: on ? [...filters.groups, g.value] : filters.groups.filter((x) => x !== g.value) })}
                />
              ))}
            </div>
          </FilterPill>
          <FilterPill label={RANGES.find((r) => r.days === filters.sinceDays)?.label ?? "Date"} active={filters.sinceDays != null} onClear={() => set({ sinceDays: null })}>
            {RANGES.map((r) => (
              <Option key={r.label} on={filters.sinceDays === r.days} onClick={() => set({ sinceDays: r.days })}>
                {r.label}
              </Option>
            ))}
          </FilterPill>
          <span className="flex-1" />
          <span className="text-[12.5px] text-fg-muted">
            {count(shown.length)} entr{shown.length === 1 ? "y" : "ies"} · times in {tz}
          </span>
        </div>
        {audit.isPending ? (
          <div className="rounded-md border border-border">
            <SkeletonRows rows={6} />
          </div>
        ) : audit.isError ? (
          <EmptyState tone="error" icon={<ScrollText />} title={isUnreachable(audit.error) ? "Can’t reach the server" : "Couldn’t load the audit log"} actions={<Button onClick={() => audit.refetch()}>Try again</Button>}>
            {audit.error.message}
          </EmptyState>
        ) : !shown.length ? (
          <EmptyState
            icon={<ScrollText />}
            title={filtered && all.length ? "No entries match these filters" : "Nothing recorded yet"}
            actions={filtered && all.length ? <Button onClick={() => set({ person: null, groups: [], sinceDays: null })}>Clear filters</Button> : undefined}
          >
            {filtered && all.length ? undefined : "Sign-ins aren’t logged; changes to accounts, settings, sources, sharing and content are."}
          </EmptyState>
        ) : (
          <div className="overflow-hidden rounded-md border border-border">
            <Table aria-label="Audit log entries" className="table-fixed text-[13px]">
              <THead className="border-t-0">
                <tr>
                  <SortTh active dir={asc ? "asc" : "desc"} onSort={() => setAsc((a) => !a)} className="w-[150px]">
                    Time
                  </SortTh>
                  <Th className="w-[160px]">Person</Th>
                  <Th className="w-[150px]">Action</Th>
                  <Th className="w-[26%]">Target</Th>
                  <Th>Details</Th>
                </tr>
              </THead>
              <tbody>
                {page.map((e, i) => {
                  const key = offset + i;
                  const expanded = open.has(key);
                  const href = targetHref(e.target, e.action, look);
                  const detail = detailText(e, look);
                  const toggle = () =>
                    setOpen((s) => {
                      const n = new Set(s);
                      if (n.has(key)) n.delete(key);
                      else n.add(key);
                      return n;
                    });
                  return (
                    <Fragment key={key}>
                      <Tr className={cn("h-11 cursor-pointer", expanded && "bg-hl hover:bg-hl")} onClick={toggle}>
                        <Td className="tabular whitespace-nowrap text-fg-secondary">
                          <button
                            type="button"
                            aria-expanded={expanded}
                            aria-label={`${expanded ? "Hide" : "Show"} details of ${e.action} at ${absolute(e.at)}`}
                            onClick={(ev) => {
                              ev.stopPropagation();
                              toggle();
                            }}
                            className="inline-flex items-center gap-1 rounded-xs"
                          >
                            {expanded ? <ChevronDown aria-hidden className="size-3.5" /> : <ChevronRight aria-hidden className="size-3.5" />}
                            {when(e.at)}
                          </button>
                        </Td>
                        <Td className="truncate font-semibold">{personText(e.email, look)}</Td>
                        <Td>
                          <code className={cn("font-mono text-[12px] font-medium", actionGroup(e.action) === "sharing" ? "text-fg-accent" : "text-fg-strong")}>{e.action}</code>
                        </Td>
                        <Td className="truncate">{targetText(e.target, e.action, look)}</Td>
                        <Td className="truncate text-fg-secondary">{detail || "—"}</Td>
                      </Tr>
                      {expanded && (
                        <tr className="border-b border-dashed border-border bg-hl">
                          <td colSpan={5} className="px-4 pb-3.5 pt-3 sm:pl-[330px]">
                            <dl className="grid gap-1.5 text-[13px] leading-normal sm:grid-cols-[110px_minmax(0,1fr)]">
                              <dt className="font-bold">Target</dt>
                              <dd>
                                {href ? (
                                  <Link href={href} className="font-semibold text-fg-accent hover:underline" onClick={(ev) => ev.stopPropagation()}>
                                    {targetText(e.target, e.action, look)}
                                  </Link>
                                ) : (
                                  targetText(e.target, e.action, look)
                                )}
                                {e.target && e.target !== targetText(e.target, e.action, look) && <code className="ml-2 font-mono text-[11.5px] text-fg-muted">{e.target}</code>}
                              </dd>
                              <dt className="font-bold">Details</dt>
                              <dd className="break-words">{detail || "No details recorded"}</dd>
                              <dt className="font-bold">When</dt>
                              <dd className="tabular">{absolute(e.at)}</dd>
                              <dt className="font-bold">Who</dt>
                              <dd>{e.email ?? "the system"}</dd>
                            </dl>
                            <p className="mt-2 font-mono text-[11.5px] text-fg-muted">Values before a change and the request’s address aren’t recorded yet.</p>
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  );
                })}
              </tbody>
            </Table>
            <Pagination offset={offset} limit={PAGE} total={shown.length} onChange={(o) => (setOffset(o), setOpen(new Set()))} className="justify-end border-t border-border" />
          </div>
        )}
        {all.length >= LIMIT && <p className="text-[12px] text-fg-muted">Showing the newest {count(LIMIT)} entries; older ones are kept on the server.</p>}
      </section>
    </AdminFrame>
  );
}
