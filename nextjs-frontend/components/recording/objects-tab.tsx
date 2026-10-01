"use client";

import { Shapes } from "lucide-react";

import { usePlayerApi } from "@/components/player/media";
import { useRec } from "@/components/recording/context";
import { pageRef } from "@/components/recording/document/model";
import { howMuch, objectName, whereSeen } from "@/components/recording/objects-model";
import { EmptyState } from "@/components/ui/states";
import { tc } from "@/lib/format";
import { cn } from "@/lib/utils";

/**
 * The kinds of object seen in a video, a document or an image (the objects step), the most seen first. Choosing one
 * goes to where it's first seen (a moment of the video, or with `onPage` a page) and draws its boxes there; choosing
 * it again stops drawing them. `why` is what the step said when it was skipped (no detector on the server, …).
 */
export function ObjectsTab({
  selected,
  onSelect,
  onPage,
  why,
}: {
  selected: string | null;
  onSelect: (label: string | null) => void;
  onPage?: (page: number) => void;
  why?: string | null;
}) {
  const { model } = useRec();
  const api = usePlayerApi();
  if (!model.objects.length)
    return (
      <EmptyState icon={<Shapes />} title={onPage ? "No objects on its pages" : "No objects found"} className="py-10">
        {why
          ? `The Objects step was skipped: ${why}.`
          : "The Objects step looks for people, vehicles, animals and everyday things; it found none, or hasn’t run yet."}
      </EmptyState>
    );
  return (
    <ul className="m-0 flex list-none flex-col p-0" aria-label="Objects">
      {model.objects.map((o) => {
        const on = selected === o.label;
        const name = objectName(o.label);
        const first = o.paged ? pageRef(model.pages, o.firstMs) : tc(o.firstMs);
        return (
          <li key={o.label} className="border-b border-border py-1 last:border-b-0">
            <button
              type="button"
              aria-pressed={on}
              aria-label={on ? `${name}: stop showing its boxes` : `${name}: show its boxes, first seen ${first}`}
              onClick={() => {
                onSelect(on ? null : o.label);
                if (on) return;
                if (onPage) onPage(o.firstMs);
                else api.seek(o.firstMs, { manual: true });
              }}
              className={cn(
                "flex w-full flex-col items-start gap-0.5 rounded-sm px-2 py-2 text-left hover:bg-surface-neutral",
                on && "bg-blue-surface hover:bg-blue-surface",
              )}
            >
              <span className="flex w-full min-w-0 items-center gap-2">
                <span className="truncate text-[14px] font-bold leading-tight text-fg">{name}</span>
                {on && <span className="ml-auto shrink-0 text-[11.5px] font-semibold text-fg-accent">Boxes shown</span>}
              </span>
              <span className="text-[12.5px] leading-snug text-fg-secondary">{whereSeen(o, model.pages)}</span>
              <span className="text-[12px] leading-snug text-fg-muted">{howMuch(o)}</span>
            </button>
          </li>
        );
      })}
    </ul>
  );
}
