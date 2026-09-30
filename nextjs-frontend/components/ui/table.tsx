"use client";

import { ArrowDown, ArrowUp, ChevronLeft, ChevronRight } from "lucide-react";
import type { HTMLAttributes, ReactNode, TdHTMLAttributes, ThHTMLAttributes } from "react";

import { count } from "@/lib/format";
import { cn } from "@/lib/utils";

/** Real table semantics; rows 53px, hairline dividers, compact header. */
export function Table({ className, children, ...props }: HTMLAttributes<HTMLTableElement>) {
  return (
    // relative: screen-reader-only text in the cells is positioned inside the scroller, so a wide table scrolls here
    // rather than widening the page.
    <div className="relative w-full overflow-x-auto">
      <table className={cn("w-full border-collapse text-[13.5px]", className)} {...props}>
        {children}
      </table>
    </div>
  );
}

export function THead({ children, className }: { children: ReactNode; className?: string }) {
  return <thead className={cn("border-y border-border bg-surface text-left", className)}>{children}</thead>;
}

export function Th({ className, children, ...props }: ThHTMLAttributes<HTMLTableCellElement>) {
  return (
    <th scope="col" className={cn("whitespace-nowrap px-3 py-2.5 text-[12px] font-bold text-fg-secondary first:pl-4 last:pr-4", className)} {...props}>
      {children}
    </th>
  );
}

/** A sortable header: a button with aria-sort on the cell. */
export function SortTh({
  children,
  active,
  dir,
  onSort,
  className,
}: {
  children: ReactNode;
  active: boolean;
  dir: "asc" | "desc";
  onSort: () => void;
  className?: string;
}) {
  return (
    <Th aria-sort={active ? (dir === "asc" ? "ascending" : "descending") : "none"} className={className}>
      <button type="button" onClick={onSort} className="inline-flex items-center gap-1 hover:text-fg">
        {children}
        {active && (dir === "asc" ? <ArrowUp className="size-3.5" aria-hidden /> : <ArrowDown className="size-3.5" aria-hidden />)}
      </button>
    </Th>
  );
}

export function Tr({ className, selected, children, ...props }: HTMLAttributes<HTMLTableRowElement> & { selected?: boolean }) {
  return (
    <tr aria-selected={selected || undefined} className={cn("border-b border-border transition-colors duration-fast hover:bg-surface", selected && "bg-hl hover:bg-hl", className)} {...props}>
      {children}
    </tr>
  );
}

export function Td({ className, children, ...props }: TdHTMLAttributes<HTMLTableCellElement>) {
  return (
    <td className={cn("px-3 py-2.5 align-middle first:pl-4 last:pr-4", className)} {...props}>
      {children}
    </td>
  );
}

/** Table footer pagination: "1–50 of 1,637", previous/next, first/last disabled at the ends. */
export function Pagination({
  offset,
  limit,
  total,
  onChange,
  loading,
  className,
}: {
  offset: number;
  limit: number;
  total?: number | null;
  onChange: (offset: number) => void;
  loading?: boolean;
  className?: string;
}) {
  const end = total != null ? Math.min(offset + limit, total) : offset + limit;
  const hasNext = total != null ? end < total : true;
  return (
    <nav aria-label="Pagination" className={cn("flex items-center gap-3 px-4 py-3 text-[13px] text-fg-secondary", className)}>
      <span className="tabular">
        {total === 0 ? "0" : `${count(offset + 1)}–${count(end)}`}
        {total != null && ` of ${count(total)}`}
      </span>
      {loading && <span className="text-fg-muted">Loading…</span>}
      <span className="ml-auto flex gap-1">
        <button
          type="button"
          aria-label="Previous page"
          disabled={offset <= 0}
          onClick={() => onChange(Math.max(0, offset - limit))}
          className="grid size-8 place-items-center rounded-full hover:bg-surface-neutral disabled:opacity-40"
        >
          <ChevronLeft className="size-4" />
        </button>
        <button
          type="button"
          aria-label="Next page"
          disabled={!hasNext}
          onClick={() => onChange(offset + limit)}
          className="grid size-8 place-items-center rounded-full hover:bg-surface-neutral disabled:opacity-40"
        >
          <ChevronRight className="size-4" />
        </button>
      </span>
    </nav>
  );
}
