"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useRef } from "react";

import type { RecordingSummary } from "@/app/openapi-client/types.gen";
import { AccessBadge } from "@/components/access/access-fields";
import { MediaIcon } from "@/components/library/cells";
import { speakerList, statusView } from "@/components/library/model";
import { rowClick, type RowProps } from "@/components/library/recording-table";
import { Badge, speakerColor } from "@/components/ui/badge";
import { Checkbox } from "@/components/ui/field";
import { Tooltip } from "@/components/ui/tooltip";
import { shortDate, tc } from "@/lib/format";
import { cn } from "@/lib/utils";

function meta(r: RecordingSummary, compactDate = false) {
  const date = shortDate(r.recorded_at);
  return [
    r.namespace,
    compactDate ? date.replace(/, \d\d:\d\d$/, "").replace(/ \d{4}$/, "") : date,
    r.duration_ms ? tc(r.duration_ms) : "—",
  ]
    .filter(Boolean)
    .join(" · ");
}

/** Library L3: one 40px line per recording, for scanning. Same filters and selection as the table. */
export function RecordingList({ rows, jobs, reviews, selected, onToggle, onOpen }: RowProps) {
  const router = useRouter();
  const shift = useRef(false);
  const any = selected.size > 0;
  return (
    <ul aria-label="Recordings" className="border-t border-border">
      {rows.map((r, i) => {
        const view = statusView(r, jobs.get(r.id), undefined, reviews?.get(r.id));
        const sel = selected.has(r.id);
        const spk = speakerList(r.speakers).slice(0, 3);
        const open = () => {
          onOpen?.(r);
          router.push(`/recordings/${r.id}`);
        };
        return (
          <li
            key={r.id}
            data-row-id={r.id}
            onClick={(e) => rowClick(e, open)}
            className={cn(
              "group grid h-10 cursor-pointer grid-cols-[18px_minmax(0,1fr)_auto_150px] items-center gap-3.5 border-b border-border px-6",
              sel ? "bg-hl" : "hover:bg-surface",
            )}
          >
            <span
              className="relative grid size-[18px] place-items-center"
              onClickCapture={(e) => (shift.current = e.shiftKey)}
            >
              <MediaIcon
                kind={r.media_kind}
                className={cn(
                  "size-[15px] text-fg-muted",
                  (any || sel) && "invisible",
                  "group-hover:invisible group-focus-within:invisible",
                )}
              />
              <span
                className={cn(
                  "absolute inset-0",
                  any || sel ? "opacity-100" : "opacity-0 focus-within:opacity-100 group-hover:opacity-100",
                )}
              >
                <Checkbox
                  checked={sel}
                  onCheckedChange={() => onToggle(r.id, i, shift.current)}
                  aria-label={`Select ${r.title ?? "recording"}`}
                />
              </span>
            </span>
            <span className="flex min-w-0 items-baseline gap-2.5">
              <Link
                href={`/recordings/${r.id}`}
                data-row-link
                onClick={() => onOpen?.(r)}
                className="truncate text-[13.5px] font-semibold leading-none text-fg hover:underline"
              >
                {r.title || "Untitled"}
              </Link>
              <span className="tabular shrink-0 whitespace-nowrap text-[12px] leading-none text-fg-muted">
                {meta(r)}
              </span>
            </span>
            <span className="hidden justify-end gap-[3px] lg:flex">
              {spk.map((s) => (
                <span
                  key={s.name}
                  className="inline-flex items-center gap-1 whitespace-nowrap pr-1.5 text-[11.5px] font-semibold leading-none text-fg-secondary"
                >
                  <span
                    aria-hidden
                    className="size-[9px] rounded-full"
                    style={
                      s.unnamed ? { border: "1.5px dashed var(--text-muted)" } : { background: speakerColor(s.index) }
                    }
                  />
                  {s.name}
                </span>
              ))}
            </span>
            <span className="flex justify-end">
              {view.sub ? (
                <Tooltip content={view.sub.title ?? view.sub.text}>
                  <span tabIndex={0}>
                    <Badge tone={view.tone} dot>
                      {view.label}
                    </Badge>
                  </span>
                </Tooltip>
              ) : (
                <Badge tone={view.tone} dot>
                  {view.label}
                </Badge>
              )}
            </span>
          </li>
        );
      })}
    </ul>
  );
}

/** Library L5 (phone): rows become two-line cards with the status on the right and a progress line while running. */
export function RecordingCards({ rows, jobs, reviews, selected, onOpen }: RowProps) {
  return (
    <ul aria-label="Recordings" className="border-t border-border">
      {rows.map((r) => {
        const view = statusView(r, jobs.get(r.id), undefined, reviews?.get(r.id));
        const spk = speakerList(r.speakers).slice(0, 4);
        return (
          <li key={r.id} data-row-id={r.id} className={cn("border-b border-border", selected.has(r.id) && "bg-hl")}>
            <Link
              href={`/recordings/${r.id}`}
              data-row-link
              onClick={() => onOpen?.(r)}
              className="flex flex-col gap-[7px] px-4 py-3 active:bg-surface"
            >
              <span className="flex items-start gap-2.5">
                <span className="flex-1 text-[15px] font-semibold leading-[1.3] text-fg">{r.title || "Untitled"}</span>
                <Badge tone={view.tone} dot className="mt-px">
                  {view.label}
                </Badge>
              </span>
              <span className="tabular flex items-center gap-2 text-[12.5px] leading-none text-fg-muted">
                <MediaIcon kind={r.media_kind} className="size-[13px]" />
                <span className="truncate">{meta(r, true)}</span>
                {r.access && r.access !== "private" && <AccessBadge value={r} compact className="text-fg-muted" />}
                <span className="flex-1" />
                {spk.map((s) => (
                  <span
                    key={s.name}
                    aria-hidden
                    className="size-2.5 shrink-0 rounded-full"
                    style={
                      s.unnamed ? { border: "1.5px dashed var(--text-muted)" } : { background: speakerColor(s.index) }
                    }
                  />
                ))}
              </span>
              {view.progress != null && (
                <span
                  className="h-[3px] overflow-hidden rounded-pill bg-surface-neutral"
                  role="progressbar"
                  aria-label={view.sub?.text}
                  aria-valuenow={Math.round(view.progress * 100)}
                >
                  <span className="block h-full bg-blue" style={{ width: `${Math.round(view.progress * 100)}%` }} />
                </span>
              )}
              {view.progress == null && view.sub && view.sub.tone !== "muted" && (
                <span
                  className={cn(
                    "text-[12px] font-medium",
                    view.sub.tone === "red" ? "text-red-dark" : "text-gold-dark",
                  )}
                >
                  {view.sub.text}
                </span>
              )}
            </Link>
          </li>
        );
      })}
    </ul>
  );
}
