"use client";

import { Highlighter, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import type { Highlight } from "@/app/openapi-client/types.gen";
import { usePlayerApi } from "@/components/player/media";
import { COLOURS, LABEL_MAX, colourOf, highlightTitle } from "@/components/recording/comments-model";
import { useRec } from "@/components/recording/context";
import { useHighlightActions, useHighlights } from "@/components/recording/hooks";
import { momentLabel, writer } from "@/components/recording/notes-model";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/field";
import { EmptyState, Skeleton } from "@/components/ui/states";
import { absolute, relative } from "@/lib/format";
import { needRole } from "@/lib/hooks/session";
import { cn } from "@/lib/utils";

const chip =
  "inline-flex h-[22px] shrink-0 items-center rounded-pill border border-blue-border bg-blue-surface px-2 font-sans text-[12px] font-semibold tabular-nums text-blue-dark hover:bg-blue hover:text-white";

/**
 * Highlights tab: the passages editors marked in colour, by moment, each with its label. Select words in the text and
 * choose Highlight to add one (editors); here its colour and label change, and it goes. One chosen on the text is
 * brought into view.
 */
export function HighlightsTab() {
  const { id, ns, canEdit, highlightFocus } = useRec();
  const q = useHighlights(id);
  const list = q.data ?? [];
  return (
    <>
      <p className="text-[12.5px] leading-snug text-fg-muted">
        {canEdit
          ? "Select words in the text and choose Highlight. Everyone who can read this sees highlights."
          : `Everyone who can read this sees highlights; ${needRole("editor", ns).replace(/ can do this$/, " make them")}.`}
      </p>
      {q.isLoading ? (
        <div className="flex flex-col gap-3" aria-busy>
          <Skeleton className="h-14 w-full" />
          <Skeleton className="h-14 w-full" />
        </div>
      ) : q.isError ? (
        <EmptyState tone="error" icon={<Highlighter />} title="Couldn’t load the highlights">
          {q.error.message}
        </EmptyState>
      ) : list.length === 0 ? (
        <EmptyState icon={<Highlighter />} title="No highlights yet" className="py-8">
          {canEdit ? "Select words in the text and choose Highlight." : needRole("editor", ns)}
        </EmptyState>
      ) : (
        <ul className="flex flex-col" aria-label="Highlights">
          {list.map((h) => (
            <HighlightRow key={h.id} h={h} focused={h.id === highlightFocus} />
          ))}
        </ul>
      )}
    </>
  );
}

/** One highlight: its colour (editors pick another), its moment (plays from there), its words, its label, who made it. */
function HighlightRow({ h, focused }: { h: Highlight; focused: boolean }) {
  const { id, ns, paged, where } = useRec();
  const api = usePlayerApi();
  const { update, remove } = useHighlightActions(id);
  const [label, setLabel] = useState(h.label ?? "");
  const row = useRef<HTMLLIElement>(null);
  useEffect(() => setLabel(h.label ?? ""), [h.label]);
  useEffect(() => {
    if (focused) row.current?.scrollIntoView?.({ block: "nearest" });
  }, [focused]);
  const moment = momentLabel(h, paged ? where : undefined);
  const colour = colourOf(h.colour);
  const saveLabel = () => {
    const next = label.trim();
    if (next !== (h.label ?? "")) update.mutate({ hid: h.id, label: next });
  };
  return (
    <li
      ref={row}
      data-highlight={h.id}
      aria-label={highlightTitle(h)}
      className={cn(
        "-mx-2 flex flex-col gap-1.5 border-t border-border px-2 py-3 first:border-t-0",
        focused && "rounded-md bg-hl",
      )}
    >
      <div className="flex min-w-0 items-center gap-2">
        {h.can_edit ? (
          <span role="group" aria-label="Colour" className="flex items-center gap-1">
            {COLOURS.map((c) => (
              <button
                key={c.value}
                type="button"
                aria-label={c.label}
                aria-pressed={c.value === h.colour}
                disabled={update.isPending}
                onClick={() => c.value !== h.colour && update.mutate({ hid: h.id, colour: c.value })}
                className={cn(
                  "size-4 rounded-full border-2 border-transparent",
                  c.swatch,
                  c.value === h.colour && "border-fg shadow-[0_0_0_2px_var(--background)_inset]",
                )}
              />
            ))}
          </span>
        ) : (
          <span className={cn("size-4 shrink-0 rounded-full", colour.swatch)} title={colour.label} aria-hidden />
        )}
        {moment && (
          <button
            type="button"
            className={chip}
            aria-label={`Play from ${moment}`}
            onClick={() => api.seek(h.t0, { manual: true })}
          >
            {moment}
          </button>
        )}
        <span className="min-w-0 flex-1 truncate text-[12px] text-fg-muted">
          {writer(h)} · <time title={absolute(h.created_at)}>{relative(h.created_at)}</time>
        </span>
        <Button
          size="xs"
          variant="ghost"
          icon={<Trash2 />}
          aria-label="Remove highlight"
          disabled={!h.can_edit || remove.isPending}
          disabledReason={h.can_edit ? undefined : needRole("editor", ns)}
          onClick={() => remove.mutate(h.id)}
          className="px-2"
        />
      </div>
      {h.quote && (
        <blockquote
          className={cn("line-clamp-3 rounded-[4px] px-1.5 py-0.5 text-[13.5px] leading-snug text-fg", colour.mark)}
        >
          {h.quote}
        </blockquote>
      )}
      {h.can_edit ? (
        <Input
          aria-label="Label"
          placeholder="Label (what it marks)"
          maxLength={LABEL_MAX}
          value={label}
          onChange={(e) => setLabel(e.target.value)}
          onBlur={saveLabel}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              saveLabel();
            }
          }}
          className="h-8 text-[13px]"
        />
      ) : (
        h.label && <p className="text-[13px] font-semibold text-fg-secondary">{h.label}</p>
      )}
    </li>
  );
}
