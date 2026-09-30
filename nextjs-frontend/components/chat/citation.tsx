"use client";

import * as T from "@radix-ui/react-tooltip";
import Link from "next/link";

import type { Passage } from "@/app/openapi-client/types.gen";
import { citeLabel, passageHref, passageTime, quoteOf, shortTitle } from "@/components/chat/cite";
import { speakerColor } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

/** A speaker's colour when only the name is known (chat passages carry names, not ids): stable per name. */
export function nameTone(name: string | null | undefined): string {
  if (!name) return "var(--text-secondary)";
  let h = 0;
  for (const ch of name) h = (h * 31 + ch.codePointAt(0)!) >>> 0;
  return speakerColor(h % 8);
}

export function isNarrow(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(max-width: 767px)").matches;
}

/**
 * A citation chip, "Ep. 12 · 14:29 · Host B". Hover or focus shows the quoted segment; click opens the recording at
 * that moment (⌘-click for a new tab). On a phone, a tap previews it in a sheet instead.
 */
export function CitationChip({
  passage,
  n,
  active,
  onHover,
  onPreview,
  compact,
}: {
  passage: Passage | undefined;
  n: number;
  active?: boolean;
  onHover?: (n: number | null) => void;
  onPreview?: (p: Passage) => void;
  compact?: boolean;
}) {
  const chip = "mx-0.5 inline-flex h-[22px] max-w-full items-center whitespace-nowrap rounded-pill border px-2 align-[2px] font-sans text-[11.5px] font-semibold leading-none";
  if (!passage)
    return (
      <span className={cn(chip, "border-border bg-surface-neutral text-fg-muted")} title="This source isn’t available to you any more">
        [{n}]
      </span>
    );
  const quote = quoteOf(passage);
  const color = nameTone(quote.speaker ?? passage.speaker);
  return (
    <T.Root delayDuration={120}>
      <T.Trigger asChild>
        <Link
          href={passageHref(passage)}
          onClick={(e) => {
            if (onPreview && isNarrow() && !e.metaKey && !e.ctrlKey) {
              e.preventDefault();
              onPreview(passage);
            }
          }}
          onMouseEnter={() => onHover?.(n)}
          onMouseLeave={() => onHover?.(null)}
          onFocus={() => onHover?.(n)}
          onBlur={() => onHover?.(null)}
          aria-label={`Source ${n}: ${citeLabel(passage)}. Opens the recording there.`}
          className={cn(chip, "bg-blue-surface text-fg-accent hover:border-blue", active ? "border-blue" : "border-blue-border")}
        >
          {compact ? (
            <span className="truncate">{citeLabel(passage, false)}</span>
          ) : (
            <>
              <span className="truncate md:hidden">{citeLabel(passage, false)}</span>
              <span className="hidden truncate md:inline">{citeLabel(passage)}</span>
            </>
          )}
        </Link>
      </T.Trigger>
      <T.Portal>
        <T.Content
          side="bottom"
          align="start"
          sideOffset={6}
          collisionPadding={16}
          className="z-[200] flex w-[380px] max-w-[calc(100vw-32px)] flex-col gap-1.5 rounded-md border border-border bg-background px-3.5 py-3 shadow-3 animate-fade-in"
        >
          <span className="flex items-center gap-1.5 text-[12px] font-semibold" style={{ color }}>
            <span aria-hidden className="size-2 rounded-[2px]" style={{ background: color }} />
            {[quote.speaker ?? passage.speaker, passageTime(passage), shortTitle(passage.title, 40)].filter(Boolean).join(" · ")}
          </span>
          <span className="font-serif text-[15px] leading-normal text-fg">“{quote.text}”</span>
          <span className="text-[12px] font-medium text-fg-muted">Click to open the player here · Enter</span>
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
