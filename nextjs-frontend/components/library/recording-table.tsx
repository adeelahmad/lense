"use client";

import * as M from "@radix-ui/react-dropdown-menu";
import { Columns3 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type MouseEvent, type ReactNode } from "react";

import type { Job, RecordingSummary } from "@/app/openapi-client/types.gen";
import { AccessBadge } from "@/components/access/access-fields";
import {
  EmotionCell,
  ImportanceCell,
  MediaIcon,
  SpeakersCell,
  StatusCell,
  TextBadge,
} from "@/components/library/cells";
import { statusView, type SortDir, type SortKey } from "@/components/library/model";
import { Checkbox } from "@/components/ui/field";
import { Menu, MenuContent, MenuLabel, MenuTrigger } from "@/components/ui/menu";
import { SortTh, THead, Th } from "@/components/ui/table";
import { shortDate, tc } from "@/lib/format";
import { cn } from "@/lib/utils";

export type RowProps = {
  rows: RecordingSummary[];
  jobs: Map<number, Job>;
  /** Voice matches waiting for review, by recording. */
  reviews?: Map<number, number>;
  selected: Set<number>;
  onToggle: (id: number, index: number, shift: boolean) => void;
  onToggleAll: (on: boolean) => void;
  onRetry: (rec: RecordingSummary, view: ReturnType<typeof statusView>) => void;
  /** Why this person can't retry or reprocess in the row's namespace, or null when they can. */
  editReason: (ns: string | null | undefined) => string | null;
  onOpen?: (rec: RecordingSummary) => void;
};

/** Clicks on the row open the recording, except on its controls. */
export function rowClick(e: MouseEvent, open: () => void) {
  if ((e.target as HTMLElement).closest("a,button,input,label,[role=checkbox],[data-no-row-click]")) return;
  if (window.getSelection()?.toString()) return;
  open();
}

type Col = {
  key: "date" | "duration" | "speakers" | "emotion" | "importance";
  label: string;
  width: number;
  sort?: SortKey;
  right?: boolean;
  cell: (r: RecordingSummary) => ReactNode;
  cls?: string;
};

const COLUMNS: Col[] = [
  {
    key: "date",
    label: "Date",
    width: 110,
    sort: "date",
    cell: (r) => shortDate(r.recorded_at),
    cls: "tabular whitespace-nowrap text-fg-secondary",
  },
  {
    key: "duration",
    label: "Duration",
    width: 70,
    sort: "duration",
    right: true,
    cell: (r) => (r.duration_ms ? tc(r.duration_ms) : "—"),
    cls: "tabular text-right text-fg-secondary",
  },
  {
    key: "speakers",
    label: "Speakers",
    width: 164,
    sort: "speakers",
    cell: (r) => <SpeakersCell speakers={r.speakers} />,
    cls: "min-w-0",
  },
  {
    key: "emotion",
    label: "Emotion",
    width: 70,
    cell: (r) => <EmotionCell emotions={r.emotions} />,
  },
  {
    key: "importance",
    label: "Importance · tone",
    width: 150,
    sort: "importance",
    cell: (r) => <ImportanceCell importance={r.importance} sentiment={r.sentiment} />,
    cls: "min-w-0",
  },
];

const HIDDEN_KEY = "lens.library.hidden-columns";

/** Which optional columns this person hid (kept in this browser). */
function useHiddenColumns(): [Set<Col["key"]>, (k: Col["key"]) => void] {
  const [hidden, setHidden] = useState<Set<Col["key"]>>(new Set());
  useEffect(() => {
    try {
      const v = JSON.parse(localStorage.getItem(HIDDEN_KEY) || "[]");
      if (Array.isArray(v)) setHidden(new Set(v.filter((k) => COLUMNS.some((c) => c.key === k))));
    } catch {
      /* storage unavailable */
    }
  }, []);
  const toggle = (k: Col["key"]) =>
    setHidden((cur) => {
      const next = new Set(cur);
      if (next.has(k)) next.delete(k);
      else next.add(k);
      try {
        localStorage.setItem(HIDDEN_KEY, JSON.stringify([...next]));
      } catch {
        /* storage unavailable */
      }
      return next;
    });
  return [hidden, toggle];
}

/** Library L1: one table for everything, with real table semantics, sortable headers and a column menu. */
export function RecordingTable({
  rows,
  jobs,
  reviews,
  selected,
  onToggle,
  onToggleAll,
  onRetry,
  editReason,
  onOpen,
  sort,
  onSort,
}: RowProps & {
  sort: { key: SortKey; dir: SortDir };
  onSort: (key: SortKey) => void;
}) {
  const router = useRouter();
  const shift = useRef(false);
  const [hidden, toggleColumn] = useHiddenColumns();
  const all = rows.length > 0 && rows.every((r) => selected.has(r.id));
  const some = rows.some((r) => selected.has(r.id));
  const cols = COLUMNS.filter((c) => !hidden.has(c.key));
  const th = "h-9 px-[7px] py-0 text-[12px] font-semibold first:pl-6";
  const sortable = (key: SortKey, label: string, className?: string) => (
    <SortTh key={key} active={sort.key === key} dir={sort.dir} onSort={() => onSort(key)} className={cn(th, className)}>
      {label}
    </SortTh>
  );
  const status = (
    <SortTh active={sort.key === "status"} dir={sort.dir} onSort={() => onSort("status")} className={th}>
      Status
    </SortTh>
  );
  // Status sits between Speakers and Emotion, as in the design.
  const beforeStatus = cols.filter((c) => ["date", "duration", "speakers"].includes(c.key));
  const afterStatus = cols.filter((c) => !["date", "duration", "speakers"].includes(c.key));
  const header = (c: Col) =>
    c.sort ? (
      sortable(c.sort, c.label, c.right ? "text-right [&>button]:flex-row-reverse" : undefined)
    ) : (
      <Th key={c.key} className={th}>
        {c.label}
      </Th>
    );
  const cell = (c: Col, r: RecordingSummary) => (
    <td key={c.key} className={cn("px-[7px]", c.cls)}>
      {c.cell(r)}
    </td>
  );
  return (
    <div className="w-full overflow-x-auto">
      <table className="w-full min-w-[980px] table-fixed border-collapse text-[13px]" aria-label="Recordings">
        <colgroup>
          <col style={{ width: 50 }} />
          <col />
          {beforeStatus.map((c) => (
            <col key={c.key} style={{ width: c.width }} />
          ))}
          <col style={{ width: 172 }} />
          {afterStatus.map((c) => (
            <col key={c.key} style={{ width: c.width }} />
          ))}
          <col style={{ width: 52 }} />
        </colgroup>
        <THead className="border-t-0">
          <tr>
            <Th className={th}>
              <Checkbox
                checked={all ? true : some ? "indeterminate" : false}
                onCheckedChange={(v) => onToggleAll(v)}
                aria-label={all ? "Clear selection" : "Select all shown"}
              />
            </Th>
            {sortable("title", "Title · namespace")}
            {beforeStatus.map(header)}
            {status}
            {afterStatus.map(header)}
            <Th className="h-9 py-0 pr-4 text-right">
              <Menu>
                <MenuTrigger
                  className="inline-grid size-7 place-items-center rounded-full text-fg-secondary hover:bg-surface-neutral"
                  aria-label="Show or hide columns"
                >
                  <Columns3 className="size-4" aria-hidden />
                </MenuTrigger>
                <MenuContent align="end">
                  <MenuLabel>Columns</MenuLabel>
                  {COLUMNS.map((c) => (
                    <M.CheckboxItem
                      key={c.key}
                      checked={!hidden.has(c.key)}
                      onSelect={(e) => e.preventDefault()}
                      onCheckedChange={() => toggleColumn(c.key)}
                      className="flex h-9 cursor-pointer select-none items-center gap-2.5 rounded-sm px-2.5 text-[13.5px] text-fg outline-none data-[highlighted]:bg-surface-neutral"
                    >
                      <span
                        aria-hidden
                        className={cn(
                          "grid size-4 place-items-center rounded-[3px] border-2 text-[10px] font-bold text-white",
                          hidden.has(c.key) ? "border-fg-secondary" : "border-blue bg-blue",
                        )}
                      >
                        {hidden.has(c.key) ? "" : "✓"}
                      </span>
                      {c.label}
                    </M.CheckboxItem>
                  ))}
                </MenuContent>
              </Menu>
            </Th>
          </tr>
        </THead>
        <tbody>
          {rows.map((r, i) => {
            const view = statusView(r, jobs.get(r.id), undefined, reviews?.get(r.id));
            const sel = selected.has(r.id);
            const open = () => {
              onOpen?.(r);
              router.push(`/recordings/${r.id}`);
            };
            return (
              <tr
                key={r.id}
                data-row-id={r.id}
                aria-selected={sel}
                onClick={(e) => rowClick(e, open)}
                className={cn(
                  "h-[52px] cursor-pointer border-b border-border transition-colors duration-fast",
                  sel ? "bg-hl" : "hover:bg-surface",
                )}
              >
                <td className="pl-6 pr-[7px]" onClickCapture={(e) => (shift.current = e.shiftKey)}>
                  <Checkbox
                    checked={sel}
                    onCheckedChange={() => onToggle(r.id, i, shift.current)}
                    aria-label={`Select ${r.title ?? "recording"}`}
                  />
                </td>
                <td className="min-w-0 px-[7px]">
                  <span className="flex min-w-0 flex-col gap-[3px]">
                    <span className="flex min-w-0 items-center gap-2">
                      <Link
                        href={`/recordings/${r.id}`}
                        data-row-link
                        onClick={() => onOpen?.(r)}
                        className="truncate text-[13.5px] font-semibold leading-tight text-fg hover:underline"
                      >
                        {r.title || "Untitled"}
                      </Link>
                      {r.media_kind === "transcript" && <TextBadge />}
                    </span>
                    <span className="flex items-center gap-[5px] whitespace-nowrap text-[12px] leading-none text-fg-muted">
                      <MediaIcon kind={r.media_kind} />
                      {r.namespace}
                      {r.access && r.access !== "private" && (
                        <AccessBadge value={r} compact className="text-fg-muted" />
                      )}
                    </span>
                  </span>
                </td>
                {beforeStatus.map((c) => cell(c, r))}
                <td className="min-w-0 px-[7px] py-1.5">
                  <StatusCell
                    view={view}
                    onRetry={() => onRetry(r, view)}
                    retryDisabledReason={editReason(r.namespace) ?? undefined}
                  />
                </td>
                {afterStatus.map((c) => cell(c, r))}
                <td aria-hidden />
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}
