"use client";

import { Play } from "lucide-react";
import Link from "next/link";

import type { Passage } from "@/app/openapi-client/types.gen";
import { citeLabel, passageHref, passageTime, quoteOf, shortTitle } from "@/components/chat/cite";
import { nameTone } from "@/components/chat/citation";
import { BottomSheet } from "@/components/chat/sheet";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

function SourceCard({ p, active, onHover }: { p: Passage; active: boolean; onHover?: (n: number | null) => void }) {
  const quote = quoteOf(p);
  const color = nameTone(quote.speaker ?? p.speaker);
  const cited = p.used !== false;
  return (
    <li>
      <Link
        href={passageHref(p)}
        onMouseEnter={() => onHover?.(p.n)}
        onMouseLeave={() => onHover?.(null)}
        aria-label={`Source ${p.n}: ${citeLabel(p)}${cited ? "" : ", not cited"}`}
        className={cn(
          "flex flex-col gap-1.5 rounded-md border p-3 transition-colors duration-fast",
          active ? "border-blue bg-hl" : "border-border bg-background hover:border-blue-border",
          !cited && !active && "opacity-70",
        )}
      >
        <span className="flex items-center gap-1.5 text-[12px] font-semibold leading-tight text-fg">
          <span className="tabular shrink-0 text-fg-muted">{p.n}</span>
          <span className="min-w-0 flex-1 truncate">{shortTitle(p.title, 36)}</span>
          <span className="tabular text-fg-secondary">{passageTime(p)}</span>
        </span>
        {(quote.speaker ?? p.speaker) && (
          <span className="flex items-center gap-[5px] text-[11.5px] font-semibold" style={{ color }}>
            <span aria-hidden className="size-[7px] rounded-[2px]" style={{ background: color }} />
            {quote.speaker ?? p.speaker}
          </span>
        )}
        <span className="font-serif text-[14px] leading-[1.45] text-fg-strong">“{quote.text}”</span>
        {!cited && <span className="text-[11.5px] font-medium text-fg-muted">Sent to the model, not cited</span>}
      </Link>
    </li>
  );
}

/** Every passage sent to the model for an answer, in citation order; the hovered chip's passage is outlined. */
export function SourcesList({
  passages,
  hover,
  onHover,
}: {
  passages: Passage[];
  hover: number | null;
  onHover?: (n: number | null) => void;
}) {
  const sorted = [...passages].sort((a, b) => a.n - b.n);
  return (
    <ul className="m-0 flex list-none flex-col gap-3 p-0">
      {sorted.map((p) => (
        <SourceCard key={p.n} p={p} active={hover === p.n} onHover={onHover} />
      ))}
    </ul>
  );
}

export function sourcesCount(passages: Passage[] | null | undefined): string {
  const all = passages ?? [];
  const used = all.filter((p) => p.used !== false).length;
  if (!all.length) return "No passages";
  if (used === all.length) return `${all.length} ${all.length === 1 ? "passage" : "passages"} used`;
  return `${all.length} passages · ${used} cited`;
}

export function SourcesPanel({
  passages,
  hover,
  onHover,
  loading,
}: {
  passages: Passage[] | null;
  hover: number | null;
  onHover?: (n: number | null) => void;
  loading?: boolean;
}) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-baseline gap-2">
        <h2 className="flex-1 text-[14px] font-bold text-fg">Sources</h2>
        <span className="text-[12px] text-fg-muted">
          {passages ? sourcesCount(passages) : loading ? "Looking…" : ""}
        </span>
      </div>
      {passages && passages.length > 0 && <SourcesList passages={passages} hover={hover} onHover={onHover} />}
      {passages && passages.length === 0 && (
        <p className="m-0 text-[13px] leading-snug text-fg-secondary">
          Nothing in scope matched this question, so no passages were sent.
        </p>
      )}
      {!passages && !loading && (
        <p className="m-0 text-[13px] leading-snug text-fg-secondary">
          The passages an answer is based on show here, in citation order.
        </p>
      )}
    </div>
  );
}

/** Phone: a tapped citation, with Play opening the recording there. */
export function CitationSheet({ passage, onClose }: { passage: Passage | null; onClose: () => void }) {
  const quote = passage ? quoteOf(passage) : null;
  return (
    <BottomSheet open={!!passage} onOpenChange={(o) => !o && onClose()} title="Source">
      {passage && quote && (
        <>
          <span className="text-[15px] font-bold text-fg">{citeLabel(passage)}</span>
          <span className="font-serif text-[17px] leading-normal text-fg">“{quote.text}”</span>
          <Button asChild variant="primary" size="lg" className="w-full">
            <Link href={passageHref(passage)}>
              <Play /> Play from {passageTime(passage)}
            </Link>
          </Button>
          <p className="m-0 text-center text-[12.5px] text-fg-secondary">
            Tap a citation to preview it; Play opens the recording.
          </p>
        </>
      )}
    </BottomSheet>
  );
}

export function SourcesSheet({
  open,
  onOpenChange,
  passages,
}: {
  open: boolean;
  onOpenChange: (o: boolean) => void;
  passages: Passage[] | null;
}) {
  return (
    <BottomSheet open={open} onOpenChange={onOpenChange} title="Sources">
      <div className="flex items-baseline gap-2">
        <h2 className="flex-1 text-[15px] font-bold text-fg">Sources</h2>
        <span className="text-[12px] text-fg-muted">{sourcesCount(passages)}</span>
      </div>
      {passages && passages.length > 0 ? (
        <SourcesList passages={passages} hover={null} />
      ) : (
        <p className="m-0 text-[13.5px] text-fg-secondary">No passages for this answer.</p>
      )}
    </BottomSheet>
  );
}
