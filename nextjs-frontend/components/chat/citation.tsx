"use client";

import * as T from "@radix-ui/react-tooltip";
import Link from "next/link";

import type { Passage } from "@/app/openapi-client/types.gen";
import {
  citeLabel,
  isPage,
  isWeb,
  passageAction,
  passageHref,
  passageTime,
  passageTitle,
  quoteOf,
  shortTitle,
  siteOf,
} from "@/components/chat/cite";
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

/** A web page's chip, "example.com · Harbour news": hover shows what was read; click opens the page in a new tab. */
function WebChip({
  passage,
  n,
  active,
  onHover,
  onPreview,
  compact,
  chip,
}: {
  passage: Passage;
  n: number;
  active?: boolean;
  onHover?: (n: number | null) => void;
  onPreview?: (p: Passage) => void;
  compact?: boolean;
  chip: string;
}) {
  return (
    <T.Root delayDuration={120}>
      <T.Trigger asChild>
        <a
          href={passageHref(passage)}
          target="_blank"
          rel="noopener noreferrer"
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
          aria-label={`Source ${n}: ${citeLabel(passage)}, a web page. Opens it in a new tab.`}
          className={cn(
            chip,
            "bg-surface-neutral text-fg-strong hover:border-blue",
            active ? "border-blue" : "border-border",
          )}
        >
          <span className="truncate">{compact ? siteOf(passage) : citeLabel(passage)}</span>
        </a>
      </T.Trigger>
      <T.Portal>
        <T.Content
          side="bottom"
          align="start"
          sideOffset={6}
          collisionPadding={16}
          className="z-200 flex w-[380px] max-w-[calc(100vw-32px)] flex-col gap-1.5 rounded-md border border-border bg-background px-3.5 py-3 shadow-3 animate-fade-in"
        >
          <span className="text-[12px] font-semibold text-fg-secondary">
            {[siteOf(passage), shortTitle(passage.title, 48)].filter(Boolean).join(" · ")}
          </span>
          <span className="font-serif text-[15px] leading-normal text-fg">“{passage.text}”</span>
          <span className="text-[12px] font-medium text-fg-muted">From the web · opens the page in a new tab</span>
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}

/**
 * A citation chip, "Ep. 12 · 14:29 · Host B". Hover or focus shows the quoted segment; click opens the asset at
 * that moment (⌘-click for a new tab). On a phone, a tap previews it in a sheet instead. A web page's chip shows its
 * site and opens the page in a new tab, and so does a section of Lens's docs ("Docs · MCP server › Connecting").
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
  const chip =
    "mx-0.5 inline-flex h-[22px] max-w-full items-center whitespace-nowrap rounded-pill border px-2 align-[2px] font-sans text-[11.5px] font-semibold leading-none";
  if (!passage)
    return (
      <span
        className={cn(chip, "border-border bg-surface-neutral text-fg-muted")}
        title="This source isn’t available to you any more"
      >
        [{n}]
      </span>
    );
  if (isWeb(passage))
    return (
      <WebChip
        passage={passage}
        n={n}
        active={active}
        onHover={onHover}
        onPreview={onPreview}
        compact={compact}
        chip={chip}
      />
    );
  const quote = quoteOf(passage);
  const color = nameTone(quote.speaker ?? passage.speaker);
  const page = isPage(passage);
  return (
    <T.Root delayDuration={120}>
      <T.Trigger asChild>
        <Link
          href={passageHref(passage)}
          {...(page ? { target: "_blank", rel: "noopener noreferrer" } : {})}
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
          aria-label={`Source ${n}: ${citeLabel(passage)}. ${passageAction(passage)}.`}
          className={cn(
            chip,
            "bg-blue-surface text-fg-accent hover:border-blue",
            active ? "border-blue" : "border-blue-border",
          )}
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
          className="z-200 flex w-[380px] max-w-[calc(100vw-32px)] flex-col gap-1.5 rounded-md border border-border bg-background px-3.5 py-3 shadow-3 animate-fade-in"
        >
          <span className="flex items-center gap-1.5 text-[12px] font-semibold" style={{ color }}>
            <span aria-hidden className="size-2 rounded-[2px]" style={{ background: color }} />
            {[quote.speaker ?? passage.speaker, passageTime(passage), passageTitle(passage, 40)]
              .filter(Boolean)
              .join(" · ")}
          </span>
          <span className="font-serif text-[15px] leading-normal text-fg">“{quote.text}”</span>
          <span className="text-[12px] font-medium text-fg-muted">
            {page ? `${passageAction(passage)} · Enter` : "Click to open the player here · Enter"}
          </span>
        </T.Content>
      </T.Portal>
    </T.Root>
  );
}
